from __future__ import annotations

import asyncio
import dataclasses
import json
import queue
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lingua_calc.config import get_settings
from lingua_calc.models import AnalyzeError
from lingua_calc.pipeline import (
    analyze_docx_bytes,
    analyze_docx_files,
    open_store,
    reports_from_run,
)
from lingua_calc.store import TokenStore

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"


class DeleteRunsRequest(BaseModel):
    """Body of the bulk-delete route: the run ids the user selected."""

    ids: list[str]


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Lingua Calc", version="0.1.0")

    @app.post("/api/analyze")
    async def analyze(request: Request) -> JSONResponse:
        # Parse any multipart form and collect UploadFile instances from any field
        form = await request.form()
        # Use multi_items to handle repeated keys (multiple files under same field)
        files_values: list[UploadFile] = []
        for name, value in form.multi_items():
            # value will be an UploadFile for file fields
            if hasattr(value, "filename") and value.filename:
                files_values.append(value)

        if not files_values:
            raise HTTPException(status_code=400, detail="Please upload one or more .docx files.")

        payloads: list[tuple[str, bytes]] = []
        for file in files_values:
            if not file.filename or not file.filename.lower().endswith(".docx"):
                raise HTTPException(status_code=400, detail="All uploads must be .docx files.")
            data = await file.read()
            if not data:
                raise HTTPException(status_code=400, detail=f"Empty file: {file.filename}")
            payloads.append((file.filename, data))

        # Stream newline-delimited JSON events so the UI can show per-chapter
        # progress while the (slow) Bedrock calls run. The analysis is blocking
        # and runs in a worker thread; its progress callback (invoked from the
        # pool's threads) hands events to this async generator via a queue.
        events: "queue.Queue[dict | None]" = queue.Queue()

        def run_analysis() -> None:
            def on_progress(done: int, total: int, title: str | None) -> None:
                events.put({"event": "progress", "done": done, "total": total, "title": title})

            try:
                report = analyze_docx_files(payloads, progress=on_progress)
                events.put({"event": "result", "data": report.model_dump()})
            except Exception as e:  # noqa: BLE001 — surface to UI for prototype
                detail = traceback.format_exc() if settings.debug_tracebacks else None
                events.put(AnalyzeError(error=str(e), detail=detail).model_dump() | {"event": "error"})
            finally:
                events.put(None)  # sentinel: stream complete

        async def event_stream():
            loop = asyncio.get_running_loop()
            worker = threading.Thread(target=run_analysis, daemon=True)
            worker.start()
            while True:
                item = await loop.run_in_executor(None, events.get)
                if item is None:
                    break
                yield json.dumps(item, ensure_ascii=False) + "\n"

        return StreamingResponse(event_stream(), media_type="application/x-ndjson")

    # -- run history ----------------------------------------------------
    #
    # Every analysis is already persisted as an immutable run (store.py), but
    # until now nothing read it back, so history was reachable only from Python.
    # These four routes are that surface: two reads, and two that destroy
    # TokenFact rows the store documents as recoverable only by paying for
    # another Bedrock run. They must be declared before the StaticFiles mount
    # below, which otherwise swallows "/api/…".
    #
    # Declared `def`, not `async def`, on purpose. Every one of them does
    # blocking work — SQLite I/O, a full CorpusIndex rebuild, VACUUM — and
    # FastAPI runs sync handlers in its threadpool, so the event loop stays free
    # to keep the /api/analyze progress stream flowing. `analyze` offloads to a
    # worker thread for the same reason.

    def _require_store() -> TokenStore:
        """The store, or an HTTP error that distinguishes *off* from *broken*.

        ``open_store`` returns ``None`` for both "history is switched off" and
        "opening it failed", which are not the same thing to a caller: the first
        is a setting, the second is a fault that should be reported rather than
        rendered as an empty history.
        """
        store = open_store(settings)
        if store is not None:
            return store
        if not settings.persist_runs:
            raise HTTPException(status_code=409, detail="Run history is disabled.")
        raise HTTPException(status_code=503, detail="Could not open the run store.")

    @app.get("/api/runs")
    def list_runs(limit: int = 10, offset: int = 0) -> JSONResponse:
        """One page of stored runs, newest first.

        ``total`` accompanies the page so the UI can show the range against the
        real count. Without it a truncated list is indistinguishable from the
        end of the history, and runs past the limit go missing with nothing on
        screen to say so.
        """
        if not settings.persist_runs:
            return JSONResponse({"persistence": False, "runs": [], "total": 0})
        store = _require_store()
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        runs = [dataclasses.asdict(r) for r in store.list_runs(limit=limit, offset=offset)]
        return JSONResponse(
            {
                "persistence": True,
                "runs": runs,
                "total": store.count_runs(),
                "limit": limit,
                "offset": offset,
            }
        )

    @app.get("/api/runs/{run_id}/report")
    def run_report(run_id: str) -> JSONResponse:
        """Re-derive a stored run's report. No provider call, so this is free."""
        report = reports_from_run(run_id, settings=settings)
        if report is None:
            raise HTTPException(status_code=404, detail="No such run.")
        return JSONResponse(json.loads(report.model_dump_json()))

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str) -> JSONResponse:
        """Delete a run and reclaim its disk. Irreversible — the UI confirms first."""
        store = _require_store()
        if store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="No such run.")
        deleted = store.delete_runs([run_id])
        return JSONResponse({"deleted": deleted, "count": len(deleted)})

    @app.post("/api/runs/delete")
    def delete_runs(payload: DeleteRunsRequest) -> JSONResponse:
        """Delete several runs in one pass, with a single VACUUM.

        A POST rather than a DELETE because the id list belongs in a body and
        DELETE-with-body is inconsistently supported. ``deleted`` reports the
        ids that were actually removed, not the ids asked for: a selection made
        against a stale page still succeeds for the rows that are really there,
        and the caller can see which ones those were.
        """
        store = _require_store()
        if not payload.ids:
            raise HTTPException(status_code=400, detail="No runs selected.")
        deleted = store.delete_runs(payload.ids)
        return JSONResponse({"deleted": deleted, "count": len(deleted)})

    if UI_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

    return app


app = create_app()

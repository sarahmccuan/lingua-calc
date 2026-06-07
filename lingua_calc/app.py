from __future__ import annotations

import asyncio
import json
import queue
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from lingua_calc.config import get_settings
from lingua_calc.models import AnalyzeError
from lingua_calc.pipeline import analyze_docx_bytes, analyze_docx_files

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"


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

    if UI_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

    return app


app = create_app()

"""Local persistence for the token stream.

Analysis output used to live only inside ``_analyze_chapters`` and was discarded
once reports were rendered, so every new statistic meant paying for another
Bedrock run. Storing the facts makes reporting re-derivable offline (issues
#3/#4/#5) and makes two runs over the same text comparable, which is what the
Haiku-vs-Sonnet benchmark in issue #1 needs.

SQLite is stdlib, so this adds nothing to the PyInstaller build, and it gives
the ad-hoc ``GROUP BY`` surface that grouped exports and queries like
"occurrences of aorists per chapter" want.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from lingua_calc.corpus import CorpusIndex
from lingua_calc.models import TokenFact

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    filenames     TEXT NOT NULL,
    chapter_count INTEGER NOT NULL,
    token_count   INTEGER NOT NULL,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS token_facts (
    run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,
    chapter_index INTEGER NOT NULL,
    chapter_id    TEXT NOT NULL,
    chapter_title TEXT NOT NULL,
    position      INTEGER NOT NULL,
    type          TEXT NOT NULL,
    lemma         TEXT NOT NULL,
    form          TEXT NOT NULL,
    parse         TEXT NOT NULL,
    PRIMARY KEY (run_id, chapter_index, position)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS ix_facts_lemma   ON token_facts (run_id, lemma);
CREATE INDEX IF NOT EXISTS ix_facts_parse   ON token_facts (run_id, lemma, parse);
CREATE INDEX IF NOT EXISTS ix_facts_form    ON token_facts (run_id, lemma, form);
CREATE INDEX IF NOT EXISTS ix_facts_chapter ON token_facts (run_id, chapter_index);
"""

_FACT_COLUMNS = (
    "filename",
    "chapter_index",
    "chapter_id",
    "chapter_title",
    "position",
    "type",
    "lemma",
    "form",
    "parse",
)


@dataclass(frozen=True)
class RunInfo:
    id: str
    created_at: str
    model_id: str
    filenames: list[str]
    chapter_count: int
    token_count: int
    note: str | None = None


class TokenStore:
    """SQLite-backed store of ``TokenFact`` rows, grouped into runs.

    A connection is opened per operation rather than held open: analysis runs on
    a worker thread and SQLite connections are not shareable across threads.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- writing --------------------------------------------------------

    def save_run(
        self,
        facts: Sequence[TokenFact],
        *,
        model_id: str,
        filenames: Sequence[str],
        note: str | None = None,
    ) -> str:
        run_id = uuid.uuid4().hex
        chapter_count = len({f.chapter_index for f in facts})
        created_at = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (id, created_at, model_id, filenames, chapter_count, token_count, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    created_at,
                    model_id,
                    json.dumps(list(filenames), ensure_ascii=False),
                    chapter_count,
                    len(facts),
                    note,
                ),
            )
            conn.executemany(
                f"INSERT INTO token_facts (run_id, {', '.join(_FACT_COLUMNS)})"
                f" VALUES (?, {', '.join('?' * len(_FACT_COLUMNS))})",
                [(run_id, *(getattr(f, c) for c in _FACT_COLUMNS)) for f in facts],
            )
        return run_id

    def delete_run(self, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM token_facts WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    # -- reading --------------------------------------------------------

    def list_runs(self, limit: int = 50) -> list[RunInfo]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_run_info(r) for r in rows]

    def get_run(self, run_id: str) -> RunInfo | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _run_info(row) if row else None

    def latest_run_id(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM runs ORDER BY created_at DESC LIMIT 1").fetchone()
        return row["id"] if row else None

    def load_facts(self, run_id: str) -> list[TokenFact]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_FACT_COLUMNS)} FROM token_facts WHERE run_id = ?"
                " ORDER BY chapter_index, position",
                (run_id,),
            ).fetchall()
        return [TokenFact(**dict(r)) for r in rows]

    def load_index(self, run_id: str) -> CorpusIndex:
        """Rebuild a queryable index from a stored run — no provider call."""
        return CorpusIndex(self.load_facts(run_id))


def _run_info(row: sqlite3.Row) -> RunInfo:
    return RunInfo(
        id=row["id"],
        created_at=row["created_at"],
        model_id=row["model_id"],
        filenames=json.loads(row["filenames"]),
        chapter_count=row["chapter_count"],
        token_count=row["token_count"],
        note=row["note"],
    )


def save_run_safely(
    store: TokenStore | None,
    facts: Iterable[TokenFact],
    *,
    model_id: str,
    filenames: Sequence[str],
) -> str | None:
    """Persist a run, but never fail the analysis because persistence failed.

    A read-only install directory should degrade to "no history", not to a lost
    run the user already paid Bedrock for.
    """
    if store is None:
        return None
    try:
        return store.save_run(list(facts), model_id=model_id, filenames=filenames)
    except Exception:
        logger.exception("Could not persist run to %s; continuing without history", store.path)
        return None

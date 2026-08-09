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

_TABLES = """
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
    feat_tense    TEXT,
    feat_voice    TEXT,
    feat_mood     TEXT,
    feat_case     TEXT,
    feat_number   TEXT,
    feat_gender   TEXT,
    feat_person   TEXT,
    feat_degree   TEXT,
    feat_status   TEXT,
    feat_deponent INTEGER,
    PRIMARY KEY (run_id, chapter_index, position)
) WITHOUT ROWID;
"""

# Applied after _migrate, because an index cannot reference a feature column
# that a pre-morphology database has not been given yet.
_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_facts_lemma   ON token_facts (run_id, lemma);
CREATE INDEX IF NOT EXISTS ix_facts_parse   ON token_facts (run_id, lemma, parse);
CREATE INDEX IF NOT EXISTS ix_facts_form    ON token_facts (run_id, lemma, form);
CREATE INDEX IF NOT EXISTS ix_facts_chapter ON token_facts (run_id, chapter_index);
CREATE INDEX IF NOT EXISTS ix_facts_tense   ON token_facts (run_id, feat_tense);
CREATE INDEX IF NOT EXISTS ix_facts_case    ON token_facts (run_id, feat_case);
"""

# Columns that define a fact. Reads use only these — morphology is re-derived on
# load, so a normalizer improvement applies to already-stored runs.
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

# Denormalized copy of the decoded features. Written for ad-hoc SQL ("select
# feat_tense, count(*) ... group by 1"); never read back into a TokenFact, so it
# cannot become the source of a stale value. `reindex_run` refreshes it after
# morphology.py changes.
_FEATURE_COLUMNS = (
    "feat_tense",
    "feat_voice",
    "feat_mood",
    "feat_case",
    "feat_number",
    "feat_gender",
    "feat_person",
    "feat_degree",
    "feat_status",
    "feat_deponent",
)

# Everything here is TEXT except where noted, so `_migrate` can add a column to
# an older database with the same type the schema declares.
_FEATURE_COLUMN_TYPES: dict[str, str] = {"feat_deponent": "INTEGER"}


def _feature_values(fact: TokenFact) -> tuple:
    morph = fact.morph
    return (
        morph.tense,
        morph.voice,
        morph.mood,
        morph.case,
        morph.number,
        morph.gender,
        morph.person,
        morph.degree,
        morph.status.value,
        # 0/1 rather than a bool, so "... WHERE feat_deponent = 1" reads the way
        # an ad-hoc SQL query would expect.
        int(morph.is_deponent),
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
            conn.executescript(_TABLES)
            _migrate(conn)
            conn.executescript(_INDEXES)

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
            columns = _FACT_COLUMNS + _FEATURE_COLUMNS
            conn.executemany(
                f"INSERT INTO token_facts (run_id, {', '.join(columns)})"
                f" VALUES (?, {', '.join('?' * len(columns))})",
                [
                    (run_id, *(getattr(f, c) for c in _FACT_COLUMNS), *_feature_values(f))
                    for f in facts
                ],
            )
        return run_id

    def reindex_run(self, run_id: str) -> int:
        """Recompute the denormalized feature columns from stored labels.

        Run after ``morphology.py`` learns a new abbreviation, so ad-hoc SQL
        against an old run sees the same features the index does. Returns the
        number of rows refreshed.
        """
        facts = self.load_facts(run_id)
        if not facts:
            return 0
        with self._connect() as conn:
            conn.executemany(
                f"UPDATE token_facts SET {', '.join(f'{c} = ?' for c in _FEATURE_COLUMNS)}"
                " WHERE run_id = ? AND chapter_index = ? AND position = ?",
                [(*_feature_values(f), run_id, f.chapter_index, f.position) for f in facts],
            )
        return len(facts)

    def delete_run(self, run_id: str) -> None:
        """Remove one run and reclaim its disk."""
        self.delete_runs([run_id])

    def delete_runs(self, run_ids: Sequence[str]) -> list[str]:
        """Remove several runs, then reclaim disk **once**.

        The ``VACUUM`` is the point of deleting at all: a bare ``DELETE`` moves
        the freed pages onto SQLite's freelist for reuse but leaves the file
        exactly as large as it was, so a run the user was told is gone would free
        nothing they can see.

        It is also why this takes a list. Vacuuming rewrites the whole database,
        so doing it per run would make clearing ten runs ten full rewrites of a
        file that is only getting smaller — quadratic work for no benefit. One
        transaction, one rewrite.

        Returns the ids actually removed, which is not always the ids asked for:
        ones that no longer exist are skipped rather than raising, so a stale
        selection cannot fail the whole batch. Callers report this list rather
        than their own request, or they claim deletions that never happened.
        """
        ids = list(dict.fromkeys(run_ids))  # de-dupe, keep order
        if not ids:
            return []

        placeholders = ", ".join("?" * len(ids))
        with self._connect() as conn:
            present = [
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM runs WHERE id IN ({placeholders})", ids
                )
            ]
            if present:
                marks = ", ".join("?" * len(present))
                conn.execute(f"DELETE FROM token_facts WHERE run_id IN ({marks})", present)
                conn.execute(f"DELETE FROM runs WHERE id IN ({marks})", present)
        if present:
            self.vacuum()
        return present

    def vacuum(self) -> None:
        """Rewrite the database file, dropping freed pages.

        Needs its own connection with autocommit on: ``_connect`` leaves an
        implicit transaction open after DML, and SQLite refuses to VACUUM from
        inside a transaction.

        Failures are logged and swallowed, for the same reason
        ``save_run_safely`` swallows write failures: this runs *after* the
        delete has committed, so raising here would report "delete failed" for
        rows that are already gone — the one message guaranteed to be wrong.
        A database that could not be vacuumed is merely larger than it needs to
        be, and the next successful delete reclaims the space.
        """
        try:
            conn = sqlite3.connect(self.path, isolation_level=None)
            try:
                conn.execute("VACUUM")
            finally:
                conn.close()
        except Exception:
            logger.exception("Could not vacuum %s; space will be reclaimed later", self.path)

    # -- reading --------------------------------------------------------

    def count_runs(self) -> int:
        """Total stored runs, independent of any page. Needed so a paged view can
        show its range against the real count rather than silently ending at
        whatever the limit was."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunInfo]:
        # `id` breaks ties: the clock granularity on Windows is coarse enough
        # (~15ms) that runs saved back-to-back can share a timestamp to the
        # microsecond, and `created_at` alone would then order them arbitrarily
        # — which shows up as history rows swapping places between reloads. With
        # paging it would be worse than cosmetic: an unstable sort can show the
        # same run on two pages and hide another entirely.
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
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
        """Rebuild a queryable index from a stored run — no provider call.

        A chapter that produced no tokens does not survive the round trip, and
        that is fine: chapter identity rides on the fact rows, so a heading-only
        section has nowhere to be recorded and simply does not come back. **A
        chapter with no tokens is not a chapter** — there is nothing in it to
        count, and no statistic this store exists to serve has an answer for it.

        So this is a decision, not a gap to close. Do not add a chapters table
        to "fix" it. The live path is the inconsistent one — it passes
        ``chapters=`` from the placements it still holds and therefore renders
        an empty card — but that is a cosmetic quirk of the fresh render, not a
        loss here.
        """
        return CorpusIndex(self.load_facts(run_id))


def _migrate(conn: sqlite3.Connection) -> None:
    """Add feature columns to a database created before morphology existed.

    ``CREATE TABLE IF NOT EXISTS`` silently leaves an older table alone, so a
    store written by the previous version would otherwise fail every insert.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(token_facts)")}
    for column in _FEATURE_COLUMNS:
        if column not in existing:
            column_type = _FEATURE_COLUMN_TYPES.get(column, "TEXT")
            conn.execute(f"ALTER TABLE token_facts ADD COLUMN {column} {column_type}")


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

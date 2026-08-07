from __future__ import annotations

import pytest

from lingua_calc.store import TokenStore, save_run_safely

from .conftest import GEN, LOGOS, LOGOU, NOM, facts_from

FACTS = facts_from(
    [
        (LOGOS, LOGOS, NOM, 0),
        (LOGOS, LOGOS, NOM, 0),
        ("θεός", "θεοῦ", GEN, 1),
        (LOGOS, LOGOU, GEN, 1),
    ]
)


@pytest.fixture
def store(tmp_path):
    return TokenStore(tmp_path / "runs.sqlite3")


def test_round_trip_preserves_every_fact_and_its_order(store):
    run_id = store.save_run(FACTS, model_id="sonnet", filenames=["a.docx"])
    loaded = store.load_facts(run_id)

    assert loaded == FACTS


def test_run_metadata_records_the_model(store):
    """Issue #1 compares Haiku against Sonnet over the same text, so the model
    that produced a run has to be part of the record."""
    run_id = store.save_run(FACTS, model_id="haiku", filenames=["a.docx", "b.docx"])
    info = store.get_run(run_id)

    assert info.model_id == "haiku"
    assert info.filenames == ["a.docx", "b.docx"]
    assert info.chapter_count == 2
    assert info.token_count == 4


def test_index_rebuilds_from_storage_without_a_provider(store):
    run_id = store.save_run(FACTS, model_id="sonnet", filenames=["a.docx"])
    index = store.load_index(run_id)

    assert index.lemma(LOGOS).total == 3
    assert index.lemma(LOGOS).chapters == (0, 1)
    assert index.total_tokens == 4


def test_runs_are_isolated_from_each_other(store):
    first = store.save_run(FACTS, model_id="sonnet", filenames=["a.docx"])
    second = store.save_run(FACTS[:1], model_id="haiku", filenames=["a.docx"])

    assert len(store.load_facts(first)) == 4
    assert len(store.load_facts(second)) == 1
    assert {r.id for r in store.list_runs()} == {first, second}


def test_delete_removes_the_facts_too(store):
    run_id = store.save_run(FACTS, model_id="sonnet", filenames=["a.docx"])
    store.delete_run(run_id)

    assert store.get_run(run_id) is None
    assert store.load_facts(run_id) == []


def test_missing_run_reads_as_empty(store):
    assert store.get_run("nope") is None
    assert store.load_facts("nope") == []
    assert store.latest_run_id() is None


def test_reopening_an_existing_database_is_safe(tmp_path):
    path = tmp_path / "runs.sqlite3"
    run_id = TokenStore(path).save_run(FACTS, model_id="sonnet", filenames=["a.docx"])

    assert len(TokenStore(path).load_facts(run_id)) == 4


def test_persistence_failure_does_not_lose_the_analysis(store, monkeypatch):
    """A run the user already paid Bedrock for must survive a broken store."""
    monkeypatch.setattr(
        TokenStore, "save_run", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    )

    assert save_run_safely(store, FACTS, model_id="sonnet", filenames=["a.docx"]) is None


def test_no_store_configured_is_not_an_error():
    assert save_run_safely(None, FACTS, model_id="sonnet", filenames=["a.docx"]) is None


# --- morphology ------------------------------------------------------------

GRAMMAR_FACTS = facts_from(
    [
        ("λέγω", "λέγει", "pres. act. ind. 3sg", 0),
        ("γράφω", "γράφει", "pres. ind. 3sg", 0),
        ("λύω", "ἔλυσε", "aor. act. ind. 3sg", 1),
    ]
)


def test_features_are_queryable_from_sql(store):
    """The denormalized columns exist so grouped exports and ad-hoc questions
    can be answered in SQL rather than in Python."""
    run_id = store.save_run(GRAMMAR_FACTS, model_id="sonnet", filenames=["a.docx"])

    with store._connect() as conn:
        rows = conn.execute(
            "SELECT feat_tense, count(*) AS n FROM token_facts WHERE run_id = ?"
            " GROUP BY feat_tense ORDER BY n DESC",
            (run_id,),
        ).fetchall()

    assert [(r["feat_tense"], r["n"]) for r in rows] == [("pres", 2), ("aor", 1)]


def test_omitted_voice_is_stored_as_null_not_a_guess(store):
    run_id = store.save_run(GRAMMAR_FACTS, model_id="sonnet", filenames=["a.docx"])

    with store._connect() as conn:
        nulls = conn.execute(
            "SELECT count(*) AS n FROM token_facts WHERE run_id = ? AND feat_voice IS NULL",
            (run_id,),
        ).fetchone()["n"]

    assert nulls == 1


def test_deponency_is_queryable_as_an_integer_flag(store):
    """A deponent is stored as a middle, which is its form. The lexical class
    needs its own column or SQL cannot tell the two apart — and 0/1 rather than
    a string so "WHERE feat_deponent = 1" behaves."""
    facts = facts_from(
        [
            ("ἔρχομαι", "ἦλθεν", "aor. ind. 3sg deponent", 0),
            ("λύω", "ἐλύσατο", "aor. mid. ind. 3sg", 0),
        ]
    )
    run_id = store.save_run(facts, model_id="sonnet", filenames=["a.docx"])

    with store._connect() as conn:
        rows = conn.execute(
            "SELECT feat_voice, feat_deponent FROM token_facts WHERE run_id = ?"
            " ORDER BY position",
            (run_id,),
        ).fetchall()

    assert [(r["feat_voice"], r["feat_deponent"]) for r in rows] == [("mid", 1), ("mid", 0)]


def test_morphology_is_rederived_on_load(store):
    """Features are recomputed from the stored label, so improving the
    normalizer re-counts runs that were already paid for."""
    run_id = store.save_run(GRAMMAR_FACTS, model_id="sonnet", filenames=["a.docx"])
    loaded = store.load_facts(run_id)

    assert loaded == GRAMMAR_FACTS
    assert loaded[0].morph.tense == "pres"
    assert loaded[1].morph.voice is None


def test_index_from_storage_answers_grammar_questions(store):
    run_id = store.save_run(GRAMMAR_FACTS, model_id="sonnet", filenames=["a.docx"])
    index = store.load_index(run_id)

    assert index.feature_any("tense", "pres").count_in(0) == 2
    assert index.feature_any("tense", "aor").count_in(1) == 1


def test_reindex_refreshes_the_denormalized_columns(store, monkeypatch):
    run_id = store.save_run(GRAMMAR_FACTS, model_id="sonnet", filenames=["a.docx"])

    with store._connect() as conn:
        conn.execute("UPDATE token_facts SET feat_tense = 'stale' WHERE run_id = ?", (run_id,))

    assert store.reindex_run(run_id) == 3

    with store._connect() as conn:
        tenses = {r["feat_tense"] for r in conn.execute(
            "SELECT feat_tense FROM token_facts WHERE run_id = ?", (run_id,)
        )}

    assert tenses == {"pres", "aor"}


def test_database_written_before_morphology_existed_is_migrated(tmp_path):
    """CREATE TABLE IF NOT EXISTS leaves an older table alone, so without a
    migration every insert against an existing store would fail."""
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, model_id TEXT NOT NULL,
            filenames TEXT NOT NULL, chapter_count INTEGER NOT NULL,
            token_count INTEGER NOT NULL, note TEXT
        );
        CREATE TABLE token_facts (
            run_id TEXT NOT NULL, filename TEXT NOT NULL, chapter_index INTEGER NOT NULL,
            chapter_id TEXT NOT NULL, chapter_title TEXT NOT NULL, position INTEGER NOT NULL,
            type TEXT NOT NULL, lemma TEXT NOT NULL, form TEXT NOT NULL, parse TEXT NOT NULL,
            PRIMARY KEY (run_id, chapter_index, position)
        );
        """
    )
    conn.commit()
    conn.close()

    store = TokenStore(path)
    run_id = store.save_run(GRAMMAR_FACTS, model_id="sonnet", filenames=["a.docx"])

    assert store.load_index(run_id).feature_any("tense", "pres").total == 2

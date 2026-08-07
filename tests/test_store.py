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

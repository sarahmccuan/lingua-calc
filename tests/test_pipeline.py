from __future__ import annotations

from lingua_calc.pipeline import analyze_docx_bytes, analyze_docx_files, reports_from_run
from lingua_calc.store import TokenStore

from .conftest import LOGOS, WordProvider, make_docx


def run(files, settings, provider=None):
    return analyze_docx_files(files, provider=provider or WordProvider(), settings=settings)


def test_headings_split_chapters_and_ids_are_numbered(settings):
    docx = make_docx([("Chapter 1", LOGOS), ("Chapter 2", "θεός")])
    report = run([("book.docx", docx)], settings)

    chapters = report.file_reports[0].chapters
    assert [c.summary.title for c in chapters] == ["Chapter 1", "Chapter 2"]
    assert [c.summary.id for c in chapters] == ["1-chapter-1", "2-chapter-2"]
    assert [c.summary.chapter_index for c in chapters] == [0, 1]


def test_files_are_ordered_naturally_not_lexicographically(settings):
    """"Chapter 10" must follow "Chapter 2" — chapter indexes, and every
    statistic keyed by them, depend on this order."""
    files = [
        ("Chapter 10.docx", make_docx([(None, "δέκα")])),
        ("Chapter 2.docx", make_docx([(None, "δύο")])),
    ]
    report = run(files, settings)

    assert [f.filename for f in report.file_reports] == ["Chapter 2.docx", "Chapter 10.docx"]
    assert [f.chapters[0].summary.chapter_index for f in report.file_reports] == [0, 1]


def test_untitled_single_chapter_takes_the_filename_stem(settings):
    report = run([("Chapter 4.docx", make_docx([(None, LOGOS)]))], settings)
    summary = report.file_reports[0].chapters[0].summary

    assert summary.title == "Chapter 4"
    assert summary.id == "1-chapter-4"


def test_ids_stay_unique_across_files(settings):
    files = [
        ("A.docx", make_docx([("Alpha", "α"), ("Beta", "β")])),
        ("B.docx", make_docx([("Gamma", "γ")])),
    ]
    report = run(files, settings)

    ids = [c.summary.id for f in report.file_reports for c in f.chapters]
    assert ids == ["1-alpha", "2-beta", "3-gamma"]
    assert len(set(ids)) == 3


def test_vocabulary_tracking_spans_files(settings):
    """First/last occurrence is a corpus-wide fact, so a lemma appearing in two
    files must not look like a first occurrence in both."""
    files = [
        ("Chapter 1.docx", make_docx([(None, LOGOS)])),
        ("Chapter 2.docx", make_docx([(None, LOGOS)])),
    ]
    report = run(files, settings)

    first_row = report.file_reports[0].chapters[0].rows[0]
    second_row = report.file_reports[1].chapters[0].rows[0]

    assert (first_row.first_occ_lemma, first_row.last_occ_lemma) == (True, False)
    assert (second_row.first_occ_lemma, second_row.last_occ_lemma) == (False, True)
    assert second_row.lemma_first_chapter == 0


def test_form_variants_are_preserved_end_to_end(settings):
    """Two surface forms of one lemma in one chapter must both reach the row."""
    provider = WordProvider(lemma_map={"οὐκ": "οὐ", "οὐ": "οὐ"})
    report = run([("a.docx", make_docx([(None, "οὐ οὐκ οὐ")]))], settings, provider)

    row = report.file_reports[0].chapters[0].rows[0]
    assert row.lemma == "οὐ"
    assert {f.form: f.occ for f in row.forms} == {"οὐ": 2, "οὐκ": 1}
    assert row.form == "οὐ"


def test_chapters_are_analyzed_once_each(settings):
    provider = WordProvider()
    docx = make_docx([("Chapter 1", "α"), ("Chapter 2", "β"), ("Chapter 3", "γ")])
    run([("book.docx", docx)], settings, provider)

    assert sorted(provider.calls) == ["Chapter 1", "Chapter 2", "Chapter 3"]


def test_progress_is_reported_per_chapter(settings):
    events = []
    docx = make_docx([("Chapter 1", "α"), ("Chapter 2", "β")])
    analyze_docx_files(
        [("book.docx", docx)],
        provider=WordProvider(),
        settings=settings,
        progress=lambda done, total, title: events.append((done, total)),
    )

    assert events[0] == (0, 2)
    assert [d for d, _ in events] == [0, 1, 2]


def test_run_is_persisted_and_replayable(settings):
    """Re-deriving reports from the store must match the live run exactly —
    that is what lets new statistics be built without re-paying Bedrock."""
    docx = make_docx([("Chapter 1", f"{LOGOS} θεός"), ("Chapter 2", LOGOS)])
    report = run([("book.docx", docx)], settings)

    assert report.run_id is not None

    replayed = reports_from_run(report.run_id, settings=settings)
    assert replayed is not None
    assert replayed.model_dump() == report.model_dump()


def test_stored_run_records_the_model_id(settings):
    report = run([("a.docx", make_docx([(None, LOGOS)]))], settings)
    info = TokenStore(settings.db_path).get_run(report.run_id)

    assert info.model_id == settings.bedrock_model_id
    assert info.filenames == ["a.docx"]


def test_persistence_can_be_turned_off(settings):
    disabled = settings.model_copy(update={"persist_runs": False})
    report = run([("a.docx", make_docx([(None, LOGOS)]))], disabled)

    assert report.run_id is None


def test_unknown_run_id_returns_none(settings):
    assert reports_from_run("does-not-exist", settings=settings) is None


def test_single_file_wrapper_returns_a_document_report(settings):
    docx = make_docx([("Chapter 1", LOGOS)])
    report = analyze_docx_bytes("book.docx", docx, provider=WordProvider(), settings=settings)

    assert report.filename == "book.docx"
    assert [c.summary.title for c in report.chapters] == ["Chapter 1"]


def test_empty_document_does_not_crash(settings):
    report = run([("empty.docx", make_docx([(None, "")]))], settings)

    chapters = report.file_reports[0].chapters
    assert chapters[0].rows == []
    assert chapters[0].summary.token_count == 0

from __future__ import annotations

from lingua_calc.corpus import CorpusIndex
from lingua_calc.stats import build_chapter_report

from .conftest import GEN, HO, HO_CAP, LOGOS, LOGOU, NOM, TOU, facts_from

# One chapter where the article ὁ appears three times under the same parse but
# with two different surface forms (ὁ, and capitalised Ὁ at a sentence start).
CHAPTER = facts_from(
    [
        (HO, HO, NOM, 0),
        (LOGOS, LOGOS, NOM, 0),
        (HO, TOU, GEN, 0),
        (LOGOS, LOGOU, GEN, 0),
        (HO, HO, NOM, 0),
        (HO, HO_CAP, NOM, 0),
    ]
)


def report(facts=CHAPTER, chapter_index=0):
    return build_chapter_report(chapter_index, CorpusIndex(facts))


def test_rows_are_grouped_by_lemma_and_parse_in_first_appearance_order():
    rows = report().rows

    assert [(r.lemma, r.parse) for r in rows] == [
        (HO, NOM),
        (LOGOS, NOM),
        (HO, GEN),
        (LOGOS, GEN),
    ]


def test_every_surface_form_survives_on_the_row():
    """The old implementation kept only the most frequent form per group, which
    made per-form statistics underivable."""
    row = report().rows[0]

    assert row.lemma == HO and row.parse == NOM
    assert {f.form: f.occ for f in row.forms} == {HO: 2, HO_CAP: 1}


def test_representative_form_is_the_most_frequent():
    row = report().rows[0]

    assert row.form == HO
    assert row.form_occ == 2
    assert row.forms[0].form == HO, "forms are ranked, most frequent first"


def test_representative_form_ties_break_on_first_appearance():
    facts = facts_from([(HO, HO_CAP, NOM, 0), (HO, HO, NOM, 0)])
    row = build_chapter_report(0, CorpusIndex(facts)).rows[0]

    assert row.form == HO_CAP


def test_form_breakdown_reconciles_with_the_unique_forms_headline():
    """Previously `unique_forms` counted (lemma, form) pairs over all tokens
    while the table showed one form per group, so the headline could claim more
    forms than the rows accounted for."""
    result = report()
    forms_in_rows = sum(len(r.forms) for r in result.rows)

    assert forms_in_rows == result.summary.unique_forms == 5


def test_chapter_counts():
    result = report()

    assert result.summary.token_count == 6
    assert result.summary.unique_lemmas == 2
    assert result.summary.chapter_index == 0

    article_nom = result.rows[0]
    assert article_nom.lemma_occ == 4, "ὁ appears 4 times across both parses"
    assert article_nom.parse_occ == 3, "3 of them are nom. sg."


def test_occurrence_chapters_are_recorded_as_indexes():
    """Storing the chapter index rather than a boolean is what makes
    'which chapter' (issue #5) and 'chapters since' derivable."""
    facts = facts_from([(LOGOS, LOGOS, NOM, 0), (LOGOS, LOGOU, NOM, 2)])
    index = CorpusIndex(facts)

    first = build_chapter_report(0, index).rows[0]
    last = build_chapter_report(2, index).rows[0]

    assert first.lemma_first_chapter == 0
    assert first.lemma_last_chapter == 2
    assert last.lemma_first_chapter == 0
    assert last.lemma_last_chapter == 2


def test_legacy_boolean_columns_are_derived_from_the_indexes():
    """The UI still reads these; they must stay a faithful projection."""
    facts = facts_from([(LOGOS, LOGOS, NOM, 0), (LOGOS, LOGOU, NOM, 2)])
    index = CorpusIndex(facts)

    first = build_chapter_report(0, index).rows[0]
    last = build_chapter_report(2, index).rows[0]

    assert (first.first_occ_lemma, first.last_occ_lemma) == (True, False)
    assert (last.first_occ_lemma, last.last_occ_lemma) == (False, True)

    dumped = first.model_dump()
    assert dumped["first_occ_lemma"] is True
    assert dumped["last_occ_parse"] is False


def test_form_level_first_and_last_chapters_are_tracked_independently():
    """λόγος spans chapters 0-2, but the form λόγου only appears in chapter 2."""
    facts = facts_from([(LOGOS, LOGOS, NOM, 0), (LOGOS, LOGOU, NOM, 2)])
    row = build_chapter_report(2, CorpusIndex(facts)).rows[0]

    assert (row.lemma_first_chapter, row.lemma_last_chapter) == (0, 2)
    assert (row.form_first_chapter, row.form_last_chapter) == (2, 2)


def test_empty_chapter_produces_an_empty_report_not_a_crash():
    from lingua_calc.corpus import ChapterRef

    refs = [ChapterRef(index=0, id="1-intro", title="Intro", filename="a.docx")]
    result = build_chapter_report(0, CorpusIndex([], chapters=refs))

    assert result.rows == []
    assert result.summary.title == "Intro"
    assert result.summary.token_count == 0

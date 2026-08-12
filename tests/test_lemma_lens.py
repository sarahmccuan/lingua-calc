"""The lemma lens (issue #16).

One word at a time: what it totals broken out by parse, which chapters it lives
in, and where it is actually used. The tests that matter here are the ones about
what the report says when the answer is an *absence* — a chapter the word skips,
an occurrence list that had to be cut short — because those are the readings a
narrower implementation would silently lose.
"""

from __future__ import annotations

import pytest

from lingua_calc.corpus import ChapterRef, CorpusIndex
from lingua_calc.stats import build_lemma_report

from .conftest import GEN, HO, LOGOI, LOGOS, LOGOU, NOM, VOC, fact

# λόγος in chapters 0 and 3, absent from 1 and 2 — the gap is the point. Chapter
# 3 also spells the nominative and vocative the same way, which is what gives the
# form table something to say that the parse table cannot.
CORPUS = [
    fact(HO, HO, NOM, 0, 0, type="article"),
    fact(LOGOS, LOGOS, NOM, 0, 1),
    fact(LOGOS, LOGOU, GEN, 0, 2),
    fact(HO, HO, NOM, 1, 0, type="article"),
    fact(HO, HO, NOM, 2, 0, type="article"),
    fact(LOGOS, LOGOI, NOM, 3, 0),
    fact(LOGOS, LOGOI, VOC, 3, 1),
    fact(LOGOS, LOGOS, NOM, 3, 2),
]


@pytest.fixture
def index():
    return CorpusIndex(CORPUS)


def report(index, lemma=LOGOS, **kwargs):
    built = build_lemma_report(index, lemma, **kwargs)
    assert built is not None
    return built


# -- totals, broken out by parse and by form --------------------------------


def test_parse_rows_break_the_lemma_total_out(index):
    r = report(index)

    assert r.summary.total == 5
    assert [(p.parse, p.occ) for p in r.parses] == [(NOM, 3), (GEN, 1), (VOC, 1)]
    assert sum(p.occ for p in r.parses) == r.summary.total


def test_parse_and_form_counts_come_off_the_index_not_a_rescan(index):
    """A figure here and the same figure in the text lens have to be one number.
    Both read the same tracks, so this asserts they were not re-derived."""
    r = report(index)

    nominative = next(p for p in r.parses if p.parse == NOM)
    assert nominative.occ == index.parse(LOGOS, NOM).total
    assert nominative.chapter_count == index.parse(LOGOS, NOM).chapter_count
    assert nominative.form_count == 2, "λόγος and λόγοι are both nominative here"


def test_a_form_lists_every_parse_it_spells(index):
    """The transpose of the parse table, and the reason both exist: λόγοι is one
    spelling doing two jobs, which the parse table splits apart."""
    r = report(index)

    logoi = next(f for f in r.forms if f.form == LOGOI)
    assert logoi.occ == 2
    assert sorted(logoi.parses) == sorted([NOM, VOC])


# -- which chapters it occurs in --------------------------------------------


def test_every_chapter_gets_a_row_including_the_ones_without_it(index):
    """A lemma's distribution is as much about where it stops as where it
    appears. A list of only the chapters containing it cannot show a gap."""
    r = report(index)

    assert [c.chapter_index for c in r.chapters] == [0, 1, 2, 3]
    assert [c.occ for c in r.chapters] == [2, 0, 0, 3]
    assert [c.cumulative for c in r.chapters] == [2, 2, 2, 5]


def test_gap_is_reported_on_the_chapter_that_ends_it(index):
    r = report(index)
    by_chapter = {c.chapter_index: c for c in r.chapters}

    assert by_chapter[0].gap_before is None, "nothing to measure from on the first appearance"
    assert by_chapter[1].gap_before is None, "an empty chapter has nothing to measure to"
    assert by_chapter[3].gap_before == 3
    assert r.summary.longest_gap == 3


def test_chapter_rows_carry_their_own_parse_breakdown(index):
    """Issue #16 asks for the chapter list *with parses*, not as a bare tally."""
    r = report(index)
    fourth = next(c for c in r.chapters if c.chapter_index == 3)

    assert [(p.parse, p.occ) for p in fourth.parses] == [(NOM, 2), (VOC, 1)]
    assert sum(p.occ for p in fourth.parses) == fourth.occ


def test_chapter_refs_let_indexes_be_read_as_titles(index):
    r = report(index)

    assert [c.chapter_index for c in r.chapter_refs] == [0, 1, 2, 3]
    assert r.chapter_refs[0].title == "Chapter 1"


# -- occurrences -------------------------------------------------------------


def test_occurrences_carry_the_words_either_side(index):
    r = report(index)
    first = r.occurrences[0]

    assert (first.chapter_index, first.position, first.form) == (0, 1, LOGOS)
    assert first.before == [HO]
    assert first.after == [LOGOU]


def test_the_window_stops_at_the_chapter_edge(index):
    """Sliced out of the chapter's own facts, so it can never run into the
    previous chapter's last words — and it is short there rather than padded."""
    r = report(index)
    opening = next(o for o in r.occurrences if o.chapter_index == 3 and o.position == 0)

    assert opening.before == []
    assert opening.after == [LOGOI, LOGOS]


def test_window_size_is_the_caller_s_to_set(index):
    r = report(index, context=1)
    last = next(o for o in r.occurrences if o.chapter_index == 3 and o.position == 2)

    assert last.before == [LOGOI]
    assert r.context_window == 1


def test_a_truncated_list_still_reports_the_real_total(index):
    """The cap exists because καί is thousands of lines. What must never happen
    is a page presenting itself as the whole — so the total travels beside it."""
    r = report(index, limit=2)

    assert len(r.occurrences) == 2
    assert r.occurrences_total == 5


def test_chapter_filter_narrows_occurrences_but_not_the_tables(index):
    """Re-scoping the tables to one chapter would make this the chapter lens with
    extra steps. The lens is about where a word lives across the whole text."""
    r = report(index, chapter_index=3)

    assert {o.chapter_index for o in r.occurrences} == {3}
    assert r.occurrences_total == 3
    assert r.chapter_filter == 3
    assert r.summary.total == 5
    assert [c.occ for c in r.chapters] == [2, 0, 0, 3]


# -- summary and edges -------------------------------------------------------


def test_summary_spans_and_counts(index):
    r = report(index)

    assert (r.summary.first_chapter, r.summary.last_chapter) == (0, 3)
    assert r.summary.chapter_count == 2, "appears in two of the four it spans"
    assert (r.summary.parse_count, r.summary.form_count) == (3, 3)
    assert r.summary.corpus_tokens == len(CORPUS)
    assert r.summary.corpus_chapters == 4


def test_type_is_the_most_frequent_the_provider_gave(index):
    assert report(index, HO).summary.type == "article"


def test_a_lemma_the_corpus_lacks_has_no_report(index):
    assert build_lemma_report(index, "οὐδείς") is None


def test_a_lemma_in_one_chapter_has_no_gap_to_report(index):
    assert report(index, HO).summary.longest_gap == 1
    assert build_lemma_report(CorpusIndex(CORPUS[:3]), LOGOS).summary.longest_gap is None


def test_an_empty_chapter_still_gets_a_row():
    """Registered-but-empty chapters are real — a heading the extractor found
    nothing under — and closing the gap in the distribution would hide one."""
    index = CorpusIndex(
        CORPUS[:3],
        chapters=[
            ChapterRef(index=0, id="1-ch", title="Chapter 1", filename="a.docx"),
            ChapterRef(index=1, id="2-ch", title="Chapter 2", filename="a.docx"),
        ],
    )

    r = build_lemma_report(index, LOGOS)

    assert [(c.chapter_index, c.occ) for c in r.chapters] == [(0, 2), (1, 0)]

from __future__ import annotations

from lingua_calc.corpus import ChapterRef, CorpusIndex

from .conftest import GEN, LOGOS, LOGOU, NOM, facts_from

# λόγος appears in chapters 0 and 2 but not 1 — the gap is what makes
# "how long since the last occurrence" a real question.
CORPUS = facts_from(
    [
        (LOGOS, LOGOS, NOM, 0),
        (LOGOS, LOGOS, NOM, 0),
        ("θεός", "θεός", NOM, 0),
        ("θεός", "θεοῦ", GEN, 1),
        (LOGOS, LOGOU, GEN, 2),
        (LOGOS, LOGOU, GEN, 2),
        (LOGOS, LOGOU, GEN, 2),
    ]
)


def test_lemma_totals_and_span():
    index = CorpusIndex(CORPUS)
    track = index.lemma(LOGOS)

    assert track.total == 5
    assert track.chapters == (0, 2)
    assert track.chapter_count == 2
    assert track.first_chapter == 0
    assert track.last_chapter == 2


def test_count_in_is_per_chapter():
    track = CorpusIndex(CORPUS).lemma(LOGOS)

    assert track.count_in(0) == 2
    assert track.count_in(1) == 0
    assert track.count_in(2) == 3


def test_cumulative_through_carries_across_absent_chapters():
    """Issue #4's cumulative columns: a chapter with no occurrences must hold
    the running total, not reset or skip it."""
    track = CorpusIndex(CORPUS).lemma(LOGOS)

    assert track.cumulative_through(0) == 2
    assert track.cumulative_through(1) == 2
    assert track.cumulative_through(2) == 5
    assert track.cumulative_through(99) == 5


def test_cumulative_before_first_appearance_is_zero():
    track = CorpusIndex(CORPUS).lemma(LOGOS)
    assert track.cumulative_through(-1) == 0


def test_gap_and_neighbours():
    track = CorpusIndex(CORPUS).lemma(LOGOS)

    assert track.gap_before(0) is None, "first appearance has no gap, which is not a gap of 0"
    assert track.gap_before(2) == 2
    assert track.previous_chapter(2) == 0
    assert track.next_chapter(0) == 2
    assert track.next_chapter(2) is None


def test_parse_and_form_tracks_are_separate_dimensions():
    index = CorpusIndex(CORPUS)

    assert index.parse(LOGOS, NOM).total == 2
    assert index.parse(LOGOS, GEN).total == 3
    assert index.form(LOGOS, LOGOU).chapters == (2,)
    assert index.form_parse(LOGOS, GEN, LOGOU).total == 3


def test_unknown_keys_return_an_empty_track():
    index = CorpusIndex(CORPUS)
    missing = index.lemma("οὐκ ἔστιν")

    assert missing.total == 0
    assert missing.chapters == ()
    assert missing.first_chapter is None
    assert missing.gap_before(3) is None
    assert missing.cumulative_through(3) == 0


def test_chapter_stats_count_unique_lemmas_and_forms():
    index = CorpusIndex(CORPUS)

    chapter0 = index.chapter_stats(0)
    assert chapter0.token_count == 3
    assert chapter0.unique_lemmas == 2
    assert chapter0.unique_forms == 2


def test_corpus_totals():
    index = CorpusIndex(CORPUS)

    assert index.total_tokens == 7
    assert index.unique_lemmas == 2
    assert index.unique_forms == 4
    assert index.chapter_indexes == (0, 1, 2)


def test_empty_chapters_keep_their_identity():
    """A chapter that produced no tokens must still be reportable — otherwise it
    silently vanishes from the output."""
    refs = [ChapterRef(index=i, id=f"{i + 1}-ch", title=f"Chapter {i + 1}", filename="a.docx") for i in range(4)]
    index = CorpusIndex(CORPUS, chapters=refs)

    assert index.chapter_indexes == (0, 1, 2, 3)
    assert index.chapter_ref(3).title == "Chapter 4"
    assert index.chapter_stats(3).token_count == 0
    assert index.facts_in(3) == []


def test_iteration_is_sorted_and_complete():
    index = CorpusIndex(CORPUS)

    assert [k for k, _ in index.iter_lemmas()] == sorted([LOGOS, "θεός"])
    assert len(list(index.iter_form_parses())) == 4
    assert [k for k, _ in index.iter_types()] == ["noun"]


def test_track_stays_correct_when_queried_between_additions():
    """The frozen chapter/prefix cache must invalidate on further adds."""
    from lingua_calc.corpus import Track

    track = Track()
    track.add(0)
    assert track.cumulative_through(5) == 1

    track.add(3)
    assert track.chapters == (0, 3)
    assert track.cumulative_through(5) == 2

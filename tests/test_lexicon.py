"""The lexicon lens: matching a text's lemmas to a ranked word list, and
reporting how much of the list the text has taught.

Two halves, and they fail differently. The matcher's failure mode is a *silent*
one — a lemma that should have matched and did not simply lands in the off-list
bucket, where it looks like a deliberate authorial choice rather than a bug — so
most of what is tested here is the boundary between "not taught" and "not
matched", and the report's obligation to say which is which.

The report's failure mode is arithmetic that stops adding up: the per-chapter
token split has to partition the corpus exactly, and `new_entries` summed over
the chapters has to equal the number of entries covered, or the curve on the tab
is telling a story the tables underneath it contradict.
"""

from __future__ import annotations


import pytest

from lingua_calc.corpus import CorpusIndex
from lingua_calc.lexicon import (
    ALIAS,
    EXACT,
    FOLDED,
    Alias,
    Lexicon,
    LexiconEntry,
    LexiconMeta,
    available,
    fold_key,
    is_proper_noun,
    load,
    match_key,
)
from lingua_calc.stats import build_lexicon_report

from .conftest import facts_from

NOM = "nom. sg."


def entry(rank: int, lemma: str, ref_count: int = 100, kind: str = "L") -> LexiconEntry:
    return LexiconEntry(rank=rank, lemma=lemma, gloss=f"gloss {rank}", kind=kind, ref_count=ref_count)


def lexicon(entries: list[LexiconEntry], aliases: list[Alias] | None = None, tokens: int = 1000) -> Lexicon:
    meta = LexiconMeta(
        id="test",
        name="Test list",
        short_name="Test",
        description="",
        entry_count=len(entries),
        reference_tokens=tokens,
    )
    return Lexicon(meta, entries, aliases)


# -- normalisation ----------------------------------------------------------


def test_match_key_strips_quantity_marks_and_homograph_digits():
    """The list cites headwords with macrons and disambiguates homographs with a
    trailing digit; the provider does neither, so both have to come off before
    the two can be compared at all."""
    assert match_key("σῠ́") == match_key("σύ")
    assert match_key("ὅς2") == match_key("ὅς")
    assert match_key("Ὁ") == "ὁ"


def test_match_key_keeps_accents_and_breathings():
    """These are not decoration. Collapsing them is what turned an earlier,
    greedier normaliser into a five-way false match on οὐ, and it would merge
    three accent-distinguished pairs the list deliberately separates."""
    assert match_key("τίς") != match_key("τις")
    assert match_key("βιός") != match_key("βίος")  # a bow / a life
    assert match_key("νομός") != match_key("νόμος")  # a district / a law


def test_fold_key_handles_attic_koine_alternations():
    assert fold_key("πράττω") == fold_key("πράσσω")
    assert fold_key("θάλαττα") == fold_key("θάλασσα")
    # The accent sits between γ and γν, so a plain substring replace misses it.
    assert fold_key("γίνομαι") == fold_key("γίγνομαι")


def test_fold_key_does_not_merge_accent_distinguished_words():
    """The fold is orthographic only. If it ever starts stripping accents, this
    is the test that says so."""
    assert fold_key("τίς") != fold_key("τις")
    assert fold_key("οὔ") != fold_key("οὖς")


def test_is_proper_noun_reads_the_lemma_not_the_form():
    assert is_proper_noun("Γρηγόριος")
    assert not is_proper_noun("λόγος")
    assert not is_proper_noun("")


# -- lookup precedence ------------------------------------------------------


def test_lookup_prefers_exact_over_alias_and_fold():
    """Layer order is the whole safety argument: a stricter rule must never be
    overruled by a looser one, or a curated alias could quietly redirect a word
    that already matched a different entry correctly."""
    lex = lexicon(
        [entry(1, "πράσσω"), entry(2, "πράττω")],
        [Alias(variant="πράττω", lemma="πράσσω", kind="orthographic", note="")],
    )
    match = lex.lookup("πράττω")
    assert match.how == EXACT
    assert [e.rank for e in match.entries] == [2]


def test_lookup_falls_through_to_alias_then_fold():
    lex = lexicon(
        [entry(1, "ἐθέλω"), entry(2, "πράσσω")],
        [Alias(variant="θέλω", lemma="ἐθέλω", kind="morphological", note="")],
    )
    assert lex.lookup("θέλω").how == ALIAS
    assert lex.lookup("πράττω").how == FOLDED
    assert lex.lookup("οὐδείς") is None


def test_homograph_key_credits_every_entry_it_names():
    """ὅς and ὅς2 are one string to the provider. Crediting both over-counts by
    a hair; crediting neither would make the second entry permanently
    unreachable and silently cap the coverage any text could reach."""
    lex = lexicon([entry(1, "ὅς"), entry(2, "ὅς2")])
    match = lex.lookup("ὅς")
    assert match.ambiguous
    assert [e.rank for e in match.entries] == [1, 2]


def test_alias_pointing_at_a_missing_headword_is_dropped_not_fatal():
    """A hand-edited CSV will eventually contain a typo. It should cost that row
    and nothing else."""
    lex = lexicon([entry(1, "ἐθέλω")], [Alias(variant="θέλω", lemma="οὐκ ἔστιν", kind="", note="")])
    assert lex.lookup("θέλω") is None
    assert lex.lookup("ἐθέλω").how == EXACT


# -- the report -------------------------------------------------------------

# λόγος is taught in chapter 0, καί only in chapter 1, and Γρηγόριος is a name
# no list can contain. δαίμων is real off-list vocabulary. The list also holds
# one entry (ἵππος) the text never uses, which is what the gap table is for.
CORPUS = facts_from(
    [
        ("λόγος", "λόγος", NOM, 0),
        ("λόγος", "λόγου", "gen. sg.", 0),
        ("Γρηγόριος", "Γρηγόριος", NOM, 0),
        ("δαίμων", "δαίμων", NOM, 0),
        ("καί", "καί", "-", 1),
        ("λόγος", "λόγος", NOM, 1),
    ]
)

LIST = [entry(1, "καί", ref_count=500), entry(2, "λόγος", ref_count=300), entry(3, "ἵππος", ref_count=100)]


@pytest.fixture
def report():
    return build_lexicon_report(CorpusIndex(CORPUS), lexicon(LIST, tokens=1000))


def test_covered_counts_entries_the_text_uses(report):
    assert report.summary.entries == 3
    assert report.summary.covered == 2
    assert report.summary.covered_share == pytest.approx(2 / 3)


def test_ref_share_weights_entries_by_how_common_they_are(report):
    """The headline that answers "how much running Greek does this unlock". It
    is not the entry count in disguise: two of three entries covered is 67%,
    while what they are worth is 80%."""
    assert report.summary.ref_share_covered == pytest.approx(0.8)
    assert report.summary.ref_share_total == pytest.approx(0.9)


def test_names_are_bucketed_apart_from_off_list_vocabulary(report):
    """This list has no entry for Γρηγόριος, so charging the text for it would
    measure the text against a target that does not exist — here a sixth of the
    corpus. A name the list *does* contain matches like any other word and never
    reaches this bucket; see `test_a_name_the_list_contains_is_taught_not_set_aside`."""
    assert report.summary.tokens_proper == 1
    assert report.summary.tokens_off_list == 1
    assert report.match.proper_lemmas == 1
    assert report.match.unmatched_lemmas == 1  # δαίμων only

    off = {row.lemma: row for row in report.off_list}
    assert off["Γρηγόριος"].proper is True
    assert off["δαίμων"].proper is False


def test_chapter_token_split_partitions_the_corpus(report):
    """Every token is on the list, off it, or a name — exactly one of the three.
    If these stop summing, the stacked bars are drawing a corpus that does not
    exist."""
    index = CorpusIndex(CORPUS)
    for point in report.progress:
        assert point.tokens_on_list + point.tokens_off_list + point.tokens_proper == point.tokens
        assert point.tokens == len(index.facts_in(point.chapter_index))
    assert sum(p.tokens for p in report.progress) == index.total_tokens


def test_new_entries_sum_to_the_covered_total(report):
    """An entry is taught in exactly one chapter — its first — so the curve's
    increments have to add up to the headline it is drawing toward."""
    assert sum(p.new_entries for p in report.progress) == report.summary.covered
    assert [p.cumulative_entries for p in report.progress] == [1, 2]


def test_an_entry_records_the_chapter_that_first_taught_it(report):
    rows = {row.lemma: row for row in report.entries}
    assert rows["λόγος"].first_chapter == 0
    assert rows["λόγος"].occ == 3
    assert rows["καί"].first_chapter == 1
    # Sparse: only the chapters that actually contain it.
    assert [(c.chapter_index, c.occ) for c in rows["λόγος"].chapters] == [(0, 2), (1, 1)]


def test_untaught_entries_are_rows_not_omissions(report):
    """The zero rows are half the point of the table — a word the list says
    matters that the text has not reached."""
    rows = {row.lemma: row for row in report.entries}
    assert rows["ἵππος"].occ == 0
    assert rows["ἵππος"].first_chapter is None
    assert rows["ἵππος"].matched_by == ""
    assert [g.lemma for g in report.gaps] == ["ἵππος"]


def test_new_vocabulary_split_partitions_the_corpus(report):
    """The growth chart's four segments. Types must account for every distinct
    lemma exactly once — each is new in precisely one chapter — and the token
    split must account for every token on the page.

    Off-list here **includes names**, unlike `tokens_off_list`; the chart folds
    them in so its bars total the real page. If these two views of the same
    corpus ever disagree, the chart is drawing a text that does not exist.
    """
    index = CorpusIndex(CORPUS)

    on_types = sum(p.new_on_list_types for p in report.progress)
    off_types = sum(p.new_off_list_types for p in report.progress)
    assert on_types + off_types == index.unique_lemmas
    assert on_types == report.match.matched_lemmas

    for point in report.progress:
        assert point.tokens_off_list_with_names == point.tokens_off_list + point.tokens_proper
        assert point.tokens_on_list + point.tokens_off_list_with_names == point.tokens
        # A chapter cannot introduce more of its own tokens than it contains.
        assert point.new_on_list_tokens <= point.tokens_on_list
        assert point.new_off_list_tokens <= point.tokens_off_list_with_names


def test_new_vocabulary_is_credited_to_the_chapter_that_introduces_it(report):
    """λόγος and Γρηγόριος and δαίμων all debut in chapter 0; only καί is new in
    chapter 1. A lemma appearing again later must not be counted new twice, or
    the cumulative bar overshoots the vocabulary the text actually has."""
    first, second = report.progress
    assert (first.new_on_list_types, first.new_off_list_types) == (1, 2)  # λόγος | Γρηγόριος, δαίμων
    assert (second.new_on_list_types, second.new_off_list_types) == (1, 0)  # καί
    # λόγος recurs in chapter 1 but is not new there.
    assert second.new_on_list_tokens == 1


def test_gap_and_off_list_heads_report_their_true_totals():
    """A truncated list must never read as a complete one. Both heads carry the
    total they were cut from, so the caption can say "200 of 4,446" — without it
    a reader takes the head for the whole worklist."""
    big = [entry(i, f"λ{i}") for i in range(1, 51)]
    report = build_lexicon_report(
        CorpusIndex(CORPUS), lexicon(big, tokens=100000), gap_limit=5, off_list_limit=2
    )
    assert len(report.gaps) == 5
    assert report.gaps_total == 50  # this list shares no word with the corpus, so all of it is a gap
    assert len(report.off_list) == 2
    assert report.off_list_total == 4  # λόγος, Γρηγόριος, δαίμων, καί


def test_gaps_total_is_the_entries_the_text_never_reached(report):
    """Untruncated, it must still agree with the headline it sits under."""
    assert report.gaps_total == len(report.gaps)
    assert report.gaps_total == report.summary.entries - report.summary.covered


def test_a_matched_entry_records_the_text_lemma_that_reached_it():
    """The headword is not always a word the text contains, so the row carries
    the lemma that actually matched: the lemma lens is keyed by what the parser
    returned, and linking `ἐθέλω` for a text that only ever says `θέλω` lands on
    nothing. Untaught rows carry no lemma at all, which is what makes the cell
    un-clickable rather than clickable-and-broken."""
    facts = facts_from([("θέλω", "θέλω", "-", 0), ("πράττω", "πράττω", "-", 0)])
    lex = lexicon(
        [entry(1, "ἐθέλω"), entry(2, "πράσσω"), entry(3, "ἵππος")],
        [Alias(variant="θέλω", lemma="ἐθέλω", kind="", note="")],
    )
    rows = {r.lemma: r for r in build_lexicon_report(CorpusIndex(facts), lex).entries}

    assert rows["ἐθέλω"].source_lemma == "θέλω"  # via the alias
    assert rows["πράσσω"].source_lemma == "πράττω"  # via the dialect fold
    assert rows["ἵππος"].source_lemma == ""  # never met, so nothing to link


def test_source_lemma_prefers_the_commonest_of_several():
    """Plural sources are normal — `οὕτω` and `οὕτως` are one entry — and the
    link should open the spelling the reader will actually meet."""
    facts = facts_from(
        [("οὕτως", "οὕτως", "-", 0), ("οὕτως", "οὕτως", "-", 0), ("οὕτω", "οὕτω", "-", 0)]
    )
    lex = lexicon([entry(1, "οὕτως")], [Alias(variant="οὕτω", lemma="οὕτως", kind="", note="")])
    row = build_lexicon_report(CorpusIndex(facts), lex).entries[0]
    assert row.occ == 3
    assert row.source_lemma == "οὕτως"


def test_bands_span_the_ranks_not_the_entry_count():
    """Bands are cut on rank and filled from the entries, and the two are not
    the same number the moment a row is missing. A four-entry list ranked 1, 2,
    600, 1200 is three bands, not one band of four — banding by `len(lexicon)`
    would drop everything ranked past the count, and the bands would stop
    summing to the entry and coverage totals in the headline above them."""
    sparse = lexicon([entry(1, "α"), entry(2, "β"), entry(600, "γ"), entry(1200, "δ")], tokens=100000)
    facts = facts_from([("α", "α", "-", 0), ("γ", "γ", "-", 0)])
    report = build_lexicon_report(CorpusIndex(facts), sparse)

    assert [(b.start, b.end, b.entries) for b in report.bands] == [(1, 500, 2), (501, 1000, 1), (1001, 1200, 1)]
    assert sum(b.entries for b in report.bands) == report.summary.entries
    assert sum(b.covered for b in report.bands) == report.summary.covered


def test_empty_lexicon_does_not_divide_by_zero():
    report = build_lexicon_report(CorpusIndex(CORPUS), lexicon([], tokens=0))
    assert report.summary.covered == 0
    assert report.summary.covered_share == 0
    assert report.summary.ref_share_covered == 0
    assert report.bands == []


# -- the shipped lists ------------------------------------------------------
#
# Two lists now, and they disagree about something structural: the Core 5000 is
# a classical frequency list with no proper nouns in it, while the GNT list has
# 126 of them in its first 1,858 entries alone. That difference is the reason
# the name bucket is a *fallback* rather than a filter — see the behavioural
# test at the end of this block.

SHIPPED = ["greek-core-5k", "gnt-lemmas"]


@pytest.mark.parametrize("lexicon_id", SHIPPED)
def test_shipped_lexicon_loads_and_is_well_formed(lexicon_id):
    """Ranks must be dense and 1-based because every band and rank window
    slices on them."""
    lex = load("data/lexicons", lexicon_id)
    assert lex is not None
    assert len(lex) > 0
    assert [e.rank for e in lex.entries] == list(range(1, len(lex) + 1))
    assert len({e.lemma for e in lex.entries}) == len(lex)
    assert lex.entries[0].lemma == "ὁ"  # both corpora start the same way
    assert 0 < lex.total_ref_share <= 1.0


@pytest.mark.parametrize("lexicon_id", SHIPPED)
def test_shipped_aliases_all_point_at_real_headwords(lexicon_id):
    """`Lexicon` drops a dangling alias with a warning rather than raising, so
    without this a shipped file could rot silently."""
    lex = load("data/lexicons", lexicon_id)
    headwords = {match_key(e.lemma) for e in lex.entries}
    assert [a.variant for a in lex.aliases if match_key(a.lemma) not in headwords] == []


@pytest.mark.parametrize("lexicon_id", SHIPPED)
def test_shipped_lexicon_headwords_are_matchable(lexicon_id):
    """Every headword must resolve to itself. This catches citation conventions
    that no real text can match — the GNT data writes movable consonants as
    `οὕτω(ς)`, and left alone those four entries would be permanently
    unreachable."""
    lex = load("data/lexicons", lexicon_id)
    assert [e.lemma for e in lex.entries if lex.lookup(e.lemma) is None] == []


def test_core_5k_has_no_proper_nouns():
    """The premise behind bucketing names separately: a classical frequency list
    excludes them, so a name in the text can never match and counting it against
    the text measures it against a target that does not exist."""
    lex = load("data/lexicons", "greek-core-5k")
    assert [e.lemma for e in lex.entries if is_proper_noun(e.lemma)] == []


def test_gnt_list_does_contain_proper_nouns():
    """And the counter-example, which is why the rule above is not global."""
    lex = load("data/lexicons", "gnt-lemmas")
    names = [e.lemma for e in lex.entries if is_proper_noun(e.lemma)]
    assert "Ἰησοῦς" in names
    assert len(names) > 100


def test_a_name_the_list_contains_is_taught_not_set_aside():
    """The name bucket is a fallback for lemmas that did not match, never a
    filter applied first. Get that backwards and every list containing proper
    nouns silently loses them from its own coverage."""
    facts = facts_from([("Ἰησοῦς", "Ἰησοῦς", NOM, 0), ("Γρηγόριος", "Γρηγόριος", NOM, 0)])
    report = build_lexicon_report(CorpusIndex(facts), load("data/lexicons", "gnt-lemmas"))

    taught = {r.lemma for r in report.entries if r.occ}
    assert "Ἰησοῦς" in taught
    assert report.summary.tokens_on_list == 1
    # Γρηγόριος is in no list, so it stays a name set aside rather than a mark
    # against the text.
    assert report.summary.tokens_proper == 1
    assert report.match.proper_lemmas == 1


def test_manifest_lists_both_shipped_lexicons():
    metas = available("data/lexicons")
    assert [m.id for m in metas] == ["greek-core-5k", "gnt-lemmas"]
    assert metas[0].is_default  # default sorts first, so the UI can take metas[0]
    assert all(m.entry_count > 0 and m.reference_tokens > 0 for m in metas)


@pytest.mark.parametrize("lexicon_id", SHIPPED)
def test_advertised_entry_count_is_the_count_the_loader_produces(lexicon_id):
    """`available()` counts without building entries, and the picker shows that
    number while the tab's headline divides by the loaded list. Counting
    physical lines made the two disagree for any file with a skipped row or a
    quoted newline in a gloss, so the cheap count has to use the same rule."""
    meta = next(m for m in available("data/lexicons") if m.id == lexicon_id)
    assert meta.entry_count == len(load("data/lexicons", lexicon_id))


def test_entry_count_skips_the_rows_the_loader_skips(tmp_path):
    """The disagreement, made to happen: a blank lemma is a line in the file and
    not an entry, and a gloss containing a newline is one entry over two lines."""
    csv_path = tmp_path / "entries.csv"
    csv_path.write_text(
        'rank,lemma,gloss,kind,ref_count\n'
        '1,ὁ,the,L,500\n'
        '2,,orphaned row with no lemma,L,10\n'
        '3,καί,"and,\nalso",L,300\n',
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        '{"lexicons":[{"id":"t","name":"T","entries_file":"entries.csv","reference_tokens":1000,"default":true}]}',
        encoding="utf-8",
    )
    meta = available(tmp_path)[0]
    assert meta.entry_count == 2  # four lines of data, two entries
    assert meta.entry_count == len(load(tmp_path, "t"))


def test_unknown_lexicon_id_returns_none_rather_than_raising():
    """A stale id in a bookmarked URL should degrade to "pick a list", not a 500."""
    assert load("data/lexicons", "no-such-list") is None

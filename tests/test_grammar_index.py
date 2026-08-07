from __future__ import annotations

from lingua_calc.corpus import CorpusIndex
from lingua_calc.morphology import MorphStatus

from .conftest import facts_from

# Deliberately spelled inconsistently, the way the model actually writes them:
# chapter 0 has three presents written three different ways, chapter 2 has one.
# Counting these off the raw parse string would find three separate labels.
GRAMMAR = facts_from(
    [
        ("λέγω", "λέγει", "pres. act. ind. 3sg", 0),
        ("γράφω", "γράφει", "pres. ind. 3sg", 0),
        ("ἔχω", "ἔχει", "pres. act. ind. 3sg.", 0),
        ("λύω", "ἔλυσε", "aor. act. ind. 3sg", 0),
        ("ὁ", "τό", "def. art. nom./acc. sg. neut.", 0),
        ("λέγω", "λέγει", "pres. act. ind. 3sg", 2),
        ("λύω", "ἔλυσε", "aor. act. ind. 3sg", 2),
        ("λύω", "λυθήσεται", "fut. pass. ind. 3sg", 2),
    ]
)


def test_one_query_spans_every_spelling_of_a_tense():
    """The headline ask in issue #7: "21 present tenses in this passage"."""
    index = CorpusIndex(GRAMMAR)

    assert index.feature_any("tense", "pres").count_in(0) == 3
    assert index.feature_any("tense", "aor").count_in(0) == 1


def test_a_tense_absent_from_a_chapter_reads_as_zero():
    index = CorpusIndex(GRAMMAR)

    assert index.feature_any("tense", "fut").count_in(0) == 0
    assert index.feature_any("tense", "fut").count_in(2) == 1


def test_grammar_counts_are_cumulative_across_chapters():
    """Issue #7's "cumulative grammatical form occurrences" is the same Track
    machinery the vocabulary stats use."""
    tense = CorpusIndex(GRAMMAR).feature_any("tense", "pres")

    assert tense.total == 4
    assert tense.cumulative_through(0) == 3
    assert tense.cumulative_through(1) == 3
    assert tense.cumulative_through(2) == 4
    assert tense.first_chapter == 0
    assert tense.chapter_count == 2


def test_voice_query_works_despite_one_token_omitting_voice():
    index = CorpusIndex(GRAMMAR)

    assert index.feature_any("voice", "act").count_in(0) == 3
    assert index.feature_any("voice", "pass").count_in(2) == 1


def test_ambiguous_case_counts_toward_both_readings_by_default():
    index = CorpusIndex(GRAMMAR)

    assert index.feature_any("case", "nom").count_in(0) == 1
    assert index.feature_any("case", "acc").count_in(0) == 1


def test_exact_lookup_keeps_ambiguity_distinguishable():
    index = CorpusIndex(GRAMMAR)

    assert index.feature("case", "acc|nom").count_in(0) == 1
    assert index.feature("case", "acc").count_in(0) == 0, "the token was never unambiguously acc."


def test_iterating_a_dimension_lists_the_values_present():
    index = CorpusIndex(GRAMMAR)

    assert [v for v, _ in index.iter_feature_values("tense")] == ["aor", "fut", "pres"]
    assert [v for v, _ in index.iter_feature_values("mood")] == ["ind"]


def test_descriptors_are_tracked_separately_from_features():
    index = CorpusIndex(GRAMMAR)

    assert index.descriptor("def. art.").count_in(0) == 1
    assert [d for d, _ in index.iter_descriptors()] == ["def. art."]


def test_coverage_reports_what_was_understood():
    coverage = CorpusIndex(GRAMMAR).coverage()

    assert coverage.total == 8
    assert coverage.ok == 8
    assert coverage.needs_attention == 0
    assert coverage.understood_share == 1.0


def test_coverage_surfaces_unreadable_labels():
    """Grammar counts need error bars — an unreadable label must not read as
    absence of that grammar."""
    facts = facts_from(
        [
            ("λέγω", "λέγει", "pres. act. ind. 3sg", 0),
            ("τις", "τι", "abbreviation for πρῶτον", 0),
            ("δέ", "δέ", "-", 0),
            ("ὁ", "τοῦ", "gen. sg. (incomplete)", 0),
        ]
    )
    coverage = CorpusIndex(facts).coverage()

    assert coverage.total == 4
    assert coverage.ok == 1
    assert coverage.descriptive == 1
    assert coverage.not_applicable == 1
    assert coverage.partial == 1
    assert coverage.needs_attention == 1
    assert coverage.morphological == 3


def test_coverage_flags_the_voice_gap():
    coverage = CorpusIndex(GRAMMAR).coverage()

    # The one 'pres. ind. 3sg' token never stated voice; it is typed as a noun
    # by the fixture builder, so the gap is caught from the parse alone.
    assert coverage.verbs_missing_voice == 1


def test_the_voice_gap_ratio_is_measured_against_the_verbs_it_counted():
    """The gap is caught from the parse label, so the denominator has to come
    from the label too. Counting only tokens typed "verb" against it reported a
    0% gap on a corpus that is 100% gap — and could exceed 100% the other way."""
    facts = facts_from(
        [
            ("λέγω", "λέγει", "pres. ind. 3sg", 0),  # verbal label, typed "noun"
            ("λόγος", "λόγον", "acc. sg. masc.", 0),  # not a verb form at all
        ]
    )
    coverage = CorpusIndex(facts).coverage()

    assert coverage.verb_forms == 1
    assert coverage.verbs_missing_voice == 1
    assert coverage.voice_gap_share == 1.0


def test_a_deponent_counts_as_a_middle_and_stays_separately_countable():
    """Counting it as mid/pass would put passives in the chapter's profile that
    the text never contained, and the lexical class has to survive the mapping
    or "how many deponents" becomes unanswerable."""
    facts = facts_from(
        [
            ("ἔρχομαι", "ἦλθεν", "aor. ind. 3sg deponent", 0),
            ("λύω", "ἐλύσατο", "aor. mid. ind. 3sg", 0),
        ]
    )
    index = CorpusIndex(facts)

    assert index.feature_any("voice", "pass").count_in(0) == 0
    assert index.feature_any("voice", "mid").count_in(0) == 2
    assert index.deponents().count_in(0) == 1, "the plain middle is not a deponent"
    assert index.coverage(0).verbs_missing_voice == 0


def test_a_token_counts_once_toward_a_descriptor_it_states_twice():
    index = CorpusIndex(facts_from([("τίς", "τί", "interrog. adv. interrog. part.", 0)]))

    assert index.descriptor("interrogative").count_in(0) == 1


def test_coverage_can_be_scoped_to_one_chapter():
    index = CorpusIndex(GRAMMAR)

    assert index.coverage(0).total == 5
    assert index.coverage(2).total == 3
    assert index.coverage(2).ok == 3


def test_coverage_of_an_empty_corpus_does_not_divide_by_zero():
    coverage = CorpusIndex([]).coverage()

    assert coverage.total == 0
    assert coverage.understood_share == 1.0
    assert coverage.voice_gap_share == 0.0


def test_unknown_feature_values_return_an_empty_track():
    index = CorpusIndex(GRAMMAR)

    assert index.feature_any("tense", "plup").total == 0
    assert index.feature_any("nonsense", "x").total == 0
    assert index.morph_status(MorphStatus.UNPARSED).total == 0

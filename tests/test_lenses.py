"""The chapter and text lenses (issues #4, #5, #14, #15).

Covers the two things these tabs add over the existing chapter table: counts
that run cumulatively across chapters, and a grammar profile that can report a
form the text does not contain.
"""

from __future__ import annotations

from lingua_calc.corpus import ChapterRef, CorpusIndex
from lingua_calc.morphology import (
    Morphology,
    classify_combination,
    parse_morphology,
    signature,
)
from lingua_calc.stats import (
    build_chapter_report,
    build_combination_counts,
    build_form_combinations,
    build_grammar_groups,
    build_text_report,
)

from .conftest import GEN, HO, LOGOS, LOGOU, NOM, facts_from, fact

# λόγος in two chapters, ὁ only in the first. Two chapters is the minimum that
# makes "cumulative" mean anything at all.
TWO_CHAPTERS = facts_from(
    [
        (HO, HO, NOM, 0),
        (LOGOS, LOGOS, NOM, 0),
        (LOGOS, LOGOU, GEN, 0),
        (LOGOS, LOGOS, NOM, 1),
        (LOGOS, LOGOS, NOM, 1),
        (LOGOS, LOGOU, GEN, 1),
    ]
)


def group(groups, dimension):
    return next(g for g in groups if g.dimension == dimension)


def value(groups, dimension, name):
    return next(s for s in group(groups, dimension).stats if s.value == name)


# -- cumulative columns (issues #4 / #14) -----------------------------------


def test_cumulative_counts_run_across_chapters():
    index = CorpusIndex(TWO_CHAPTERS)

    first = build_chapter_report(0, index).rows
    second = build_chapter_report(1, index).rows

    logos_nom_1 = next(r for r in first if r.lemma == LOGOS and r.parse == NOM)
    logos_nom_2 = next(r for r in second if r.lemma == LOGOS and r.parse == NOM)

    assert (logos_nom_1.parse_occ, logos_nom_1.parse_cum) == (1, 1)
    assert (logos_nom_2.parse_occ, logos_nom_2.parse_cum) == (2, 3)
    assert (logos_nom_2.lemma_occ, logos_nom_2.lemma_cum) == (3, 5)


def test_cumulative_ignores_later_chapters():
    """The point of the column: what the reader has met *by here*, not the
    lemma's corpus total, which `first/last chapter` already covers."""
    index = CorpusIndex(TWO_CHAPTERS)

    row = next(r for r in build_chapter_report(0, index).rows if r.lemma == LOGOS and r.parse == NOM)

    assert row.parse_cum == 1
    assert index.parse(LOGOS, NOM).total == 3


# -- grammar profile (issues #7 / #14) --------------------------------------

AORIST = "aor. act. ind. 3sg"
PRESENT = "pres. act. ind. 3sg"


def verbs(rows):
    return [fact(lemma, lemma, parse, ci, i, type="verb") for i, (lemma, parse, ci) in enumerate(rows)]


def test_absent_values_are_reported_as_zero_not_omitted():
    """"0 future tenses" is the answer to the question issue #7 asks. Iterating
    what the index holds would drop the row and make it unanswerable."""
    index = CorpusIndex(verbs([("λύω", PRESENT, 0)]))

    tense = group(build_grammar_groups(index), "tense")

    assert {s.value for s in tense.stats} == {"pres", "impf", "fut", "aor", "perf", "plup"}
    assert value(build_grammar_groups(index), "tense", "fut").occ == 0
    assert value(build_grammar_groups(index), "tense", "pres").occ == 1


def test_a_dimension_the_corpus_never_states_is_dropped_entirely():
    """Zero-filling inside a dimension is informative; an all-zero Degree card
    on a text with no comparatives is furniture."""
    index = CorpusIndex(verbs([("λύω", PRESENT, 0)]))

    dimensions = {g.dimension for g in build_grammar_groups(index)}

    assert "tense" in dimensions
    assert "degree" not in dimensions
    assert "case" not in dimensions, "no token in this corpus carries a case"


def test_dimension_membership_does_not_change_between_chapters():
    """The cards are chosen corpus-wide on purpose — a set that changed as you
    paged between chapters would read as grammar appearing and vanishing."""
    index = CorpusIndex(verbs([("λύω", PRESENT, 0), ("λύω", AORIST, 1)]))

    first = [g.dimension for g in build_grammar_groups(index, 0)]
    second = [g.dimension for g in build_grammar_groups(index, 1)]

    assert first == second
    assert value(build_grammar_groups(index, 0), "tense", "aor").occ == 0


def test_grammar_counts_are_cumulative_per_chapter():
    index = CorpusIndex(verbs([("λύω", PRESENT, 0), ("λύω", PRESENT, 1), ("λύω", AORIST, 1)]))

    here = value(build_grammar_groups(index, 1), "tense", "pres")

    assert (here.occ, here.cumulative) == (1, 2)


def test_first_chapter_marks_where_grammar_is_introduced():
    index = CorpusIndex(verbs([("λύω", PRESENT, 0), ("λύω", AORIST, 1)]))

    assert value(build_grammar_groups(index, 1), "tense", "aor").first_chapter == 1
    assert value(build_grammar_groups(index, 1), "tense", "pres").first_chapter == 0


def test_stated_is_a_denominator_not_the_sum_of_its_rows():
    """A syncretic form is counted under both readings, so the rows can add up
    past the tokens involved. `stated` counts tokens, once each."""
    index = CorpusIndex(facts_from([(LOGOS, LOGOS, "nom./acc. sg. neut.", 0)]))

    case = group(build_grammar_groups(index), "case")

    assert value(build_grammar_groups(index), "case", "nom").occ == 1
    assert value(build_grammar_groups(index), "case", "acc").occ == 1
    assert sum(s.occ for s in case.stats) == 2
    assert case.stated == 1, "one token, two readings"


def test_coverage_travels_with_the_profile():
    """An unreadable label must never be able to read as absent grammar."""
    index = CorpusIndex(facts_from([(LOGOS, LOGOS, "abbreviation for πρῶτον", 0)]))

    report = build_chapter_report(0, index)

    assert report.coverage is not None
    assert report.coverage.total == 1


# -- form combinations ------------------------------------------------------


def sig(parse: str) -> str:
    return signature(parse_morphology(parse))


def combos(index) -> dict[str, dict[str, object]]:
    """Every combination row, flattened across groups and keyed by form."""
    table = build_form_combinations(index)
    return {r.form: r for g in table.groups for r in g.rows}


def chapter_combos(index, chapter_index) -> dict[str, tuple[int, int]]:
    """The join the client makes: a chapter's counts onto the corpus inventory,
    a form with no count of its own defaulting to (0, 0)."""
    counts = {c.order: (c.occ, c.cumulative) for c in build_combination_counts(index, chapter_index)}
    return {form: counts.get(row.order, (0, 0)) for form, row in combos(index).items()}


def form_class(parse: str) -> str:
    return classify_combination(parse_morphology(parse).features())


def test_a_combination_reads_back_the_way_the_label_was_written():
    """The point of the row label: an author comparing it against the `parse`
    column beneath should not have to translate between two spellings."""
    assert sig("pres. act. ind. 3sg") == "pres. act. ind. 3sg"
    assert sig("aor. mid. part. nom. sg. masc.") == "aor. mid. part. nom. sg. masc."
    assert sig("nom./acc. pl. masc./fem.") == "nom./acc. pl. masc./fem."


def test_person_and_number_fuse_only_on_a_finite_verb():
    """"3sg" on an indicative; a participle declines instead, so its number
    stays a separate term and there is no person to fuse with."""
    assert sig("pres. act. ind. 3 sg.") == "pres. act. ind. 3sg"
    assert sig("aor. act. part. nom. pl. masc.") == "aor. act. part. nom. pl. masc."


def test_a_syncretic_person_or_number_keeps_the_grammar_spelling():
    """Fusion contracts the atomic case and only that: interpolating the stored
    value spelled "3 sg./pl." as "3pl|sg", leaking both the `|` encoding and the
    canonical sort order into a displayed label — for exactly the ambiguous
    forms these rows exist to preserve."""
    assert sig("pres. act. ind. 3 sg./pl.") == "pres. act. ind. 3 sg./pl."
    assert sig("pres. act. ind. 2/3 sg.") == "pres. act. ind. 2/3 sg."
    assert sig("perf. mid./pass. ind. 3 sg./pl.") == "perf. mid./pass. ind. 3 sg./pl."


def test_an_unfused_person_is_spelled_the_same_way():
    """The branch a case sends person down. No period, because person is a bare
    numeral wherever it appears — "3sg" above, "nom. sg. 3" here."""
    assert signature(Morphology(case="nom", number="sg", person="2|3")) == "nom. sg. 2/3"
    assert signature(Morphology(case="nom", number="sg", person="3")) == "nom. sg. 3"


def test_spelling_drift_collapses_into_one_combination():
    """The whole reason this is built on decoded features rather than the raw
    `parse` string — four spellings of one form must be one row."""
    spellings = ["pres. act. ind. 3sg", "pres. ind. 3sg.", "present active indicative 3sg", "pres. act. ind. 3 sg"]

    assert len({sig(s) for s in spellings}) == 2, "only the voiceless one differs"
    assert sig("pres. ind. 3sg.") == "pres. ind. 3sg", "a missing voice is not invented"


def test_descriptors_are_not_part_of_the_combination():
    """"def. art." is a part of speech, not a paradigm cell — an article and a
    noun in the same cell belong on the same row, and `type` tells them apart."""
    assert sig("def. art. gen. sg. fem.") == sig("gen. sg. fem.")


def test_a_label_with_no_morphology_has_no_combination():
    assert sig("-") == ""
    assert sig("negative particle") == ""


def test_combinations_partition_the_morphology_bearing_tokens():
    """Unlike the per-dimension counts, these sum: one token, one row. A
    syncretic form lands in the row that says so rather than in both."""
    facts = facts_from(
        [
            (LOGOS, LOGOS, "nom./acc. sg. neut.", 0),
            (LOGOS, LOGOS, "nom. sg. masc.", 0),
            (HO, HO, "-", 0),
        ]
    )

    table = build_form_combinations(CorpusIndex(facts))

    assert sum(g.tokens for g in table.groups) == table.tokens == 2, "the unparsed token has no row"
    assert set(combos(CorpusIndex(facts))) == {"nom./acc. sg. neut.", "nom. sg. masc."}


def test_a_chapter_carries_a_zero_row_for_a_form_used_elsewhere():
    """"No aorists in this chapter" is an answer. The row set is corpus-wide so
    the question can be asked at all."""
    facts = facts_from(
        [(LOGOS, LOGOS, "nom. sg. masc.", 0), (LOGOS, LOGOS, "gen. sg. masc.", 1)]
    )
    index = CorpusIndex(facts)

    first = chapter_combos(index, 0)

    assert first["gen. sg. masc."] == (0, 0)
    assert combos(index)["gen. sg. masc."].first_chapter == 1, "still says where it does appear"
    assert first["nom. sg. masc."] == (1, 1)


def test_combination_counts_are_cumulative_per_chapter():
    facts = facts_from(
        [
            (LOGOS, LOGOS, "nom. sg. masc.", 0),
            (LOGOS, LOGOS, "nom. sg. masc.", 1),
            (LOGOS, LOGOS, "nom. sg. masc.", 1),
        ]
    )

    assert chapter_combos(CorpusIndex(facts), 1)["nom. sg. masc."] == (2, 3)


def test_rows_are_ordered_by_paradigm_not_alphabet():
    """Sorting the rendered strings would file aor. before impf. before pres."""
    labels = [
        "nom. sg. masc.",
        "aor. act. ind. 3sg",
        "pres. act. ind. 3sg",
        "impf. act. ind. 3sg",
        "gen. sg. masc.",
    ]
    facts = facts_from([(LOGOS, LOGOS, parse, 0) for parse in labels])

    table = build_form_combinations(CorpusIndex(facts))
    by_key = {g.key: [r.form for r in g.rows] for g in table.groups}

    assert by_key["verb"] == [
        "pres. act. ind. 3sg",
        "impf. act. ind. 3sg",
        "aor. act. ind. 3sg",
    ]
    assert by_key["nominal"] == ["nom. sg. masc.", "gen. sg. masc."]


def test_order_survives_a_scope_that_reorders_by_count():
    """`order` is paradigm position, not rank in any one scope, so it stays
    meaningful after the reader re-sorts a table by count — and a chapter's
    counts join back on it rather than renumbering by what that chapter holds."""
    facts = facts_from(
        [("λύω", "λύω", "pres. act. ind. 3sg", 0), ("λύω", "λύω", "aor. act. ind. 3sg", 1)]
    )
    index = CorpusIndex(facts)

    rows = build_form_combinations(index).groups[0].rows
    assert [(r.form, r.order) for r in rows] == [
        ("pres. act. ind. 3sg", 0),
        ("aor. act. ind. 3sg", 1),
    ]
    counts = build_combination_counts(index, 1)
    assert [(c.order, c.occ) for c in counts] == [(0, 0), (1, 1)], "the aorist keeps position 1"


# -- one table per form class -----------------------------------------------


def test_forms_are_classified_from_features_not_the_provider_type():
    """The `type` field is free text that drifts; the decoded features say what
    the form actually is."""
    assert form_class("pres. act. ind. 3sg") == "verb"
    assert form_class("aor. act. inf.") == "verb", "infinitives are verbs, not participles"
    assert form_class("aor. mid. part. nom. sg. masc.") == "participle"
    assert form_class("gen. sg. fem.") == "nominal"
    assert form_class("def. art. nom. sg. masc.") == "nominal", "an article is a declined form"


def test_a_participle_is_neither_a_verb_nor_a_noun():
    """It is verbal in tense and voice and declined in case and gender, so it
    would read as noise in either of the other tables."""
    assert form_class("pres. act. part. nom. sg. masc.") == "participle"
    assert form_class("pres. act. ind. 3sg") != "participle"
    assert form_class("nom. sg. masc.") != "participle"


def test_a_combination_with_no_verbal_feature_and_no_case_still_lands_somewhere():
    """"Other" is a real bucket. Dropping these would shrink a total that is
    supposed to add up."""
    assert form_class("superl.") == "other"

    facts = facts_from([(LOGOS, LOGOS, "superl.", 0), (LOGOS, LOGOS, "nom. sg. masc.", 0)])
    table = build_form_combinations(CorpusIndex(facts))

    assert {g.key for g in table.groups} == {"other", "nominal"}
    assert table.tokens == 2


def test_groups_are_in_display_order_and_carry_their_own_totals():
    facts = facts_from(
        [
            (LOGOS, LOGOS, "nom. sg. masc.", 0),
            ("λύω", "λύω", "pres. act. ind. 3sg", 0),
            ("λύω", "λύων", "pres. act. part. nom. sg. masc.", 0),
            ("λύω", "λύων", "pres. act. part. nom. sg. masc.", 0),
        ]
    )

    table = build_form_combinations(CorpusIndex(facts))

    assert [g.key for g in table.groups] == ["verb", "participle", "nominal"]
    assert [g.tokens for g in table.groups] == [1, 2, 1]
    assert sum(g.tokens for g in table.groups) == table.tokens == 4


def test_a_class_the_corpus_never_attests_gets_no_table():
    facts = facts_from([(LOGOS, LOGOS, "nom. sg. masc.", 0)])

    table = build_form_combinations(CorpusIndex(facts))

    assert [g.key for g in table.groups] == ["nominal"]


def test_the_set_of_tables_does_not_change_between_chapters():
    """Decided corpus-wide, like the dimension cards — a chapter with no
    participles must say so rather than lose the table."""
    facts = facts_from(
        [
            (LOGOS, LOGOS, "nom. sg. masc.", 0),
            ("λύω", "λύων", "aor. act. part. nom. sg. masc.", 1),
        ]
    )
    index = CorpusIndex(facts)

    table = build_form_combinations(index)

    assert [g.key for g in table.groups] == ["participle", "nominal"]
    assert len(next(g for g in table.groups if g.key == "participle").rows) == 1
    assert chapter_combos(index, 0)["aor. act. part. nom. sg. masc."] == (0, 0), "none here yet"


def test_combinations_ride_on_both_lenses():
    index = CorpusIndex(TWO_CHAPTERS)

    assert build_chapter_report(0, index).combination_counts
    assert build_text_report(index).form_combinations is not None


def test_a_chapters_counts_join_onto_the_text_inventory():
    """A chapter sends counts joined onto the text report's inventory rather
    than its own copy of the table. Spelled out for both chapters, because the
    running totals the join produces are the whole point of it."""
    index = CorpusIndex(TWO_CHAPTERS)

    assert combos(index)[NOM].occ == 4, "the inventory itself is corpus scope"
    assert chapter_combos(index, 0) == {NOM: (2, 2), GEN: (1, 1)}
    assert chapter_combos(index, 1) == {NOM: (2, 4), GEN: (1, 2)}


def test_only_combinations_the_text_has_not_reached_are_omitted():
    """The payload saving, stated as a rule: an omitted row is (0, 0), so a form
    absent *here* but met earlier still travels with its running total."""
    facts = facts_from(
        [
            (LOGOS, LOGOS, "nom. sg. masc.", 0),
            (LOGOS, LOGOU, "gen. sg. masc.", 1),
            ("λύω", "λύω", "aor. act. ind. 3sg", 2),
        ]
    )
    index = CorpusIndex(facts)
    order = {form: row.order for form, row in combos(index).items()}

    counts = {c.order: (c.occ, c.cumulative) for c in build_combination_counts(index, 1)}

    assert counts[order["nom. sg. masc."]] == (0, 1), "absent here, but its total travels"
    assert order["aor. act. ind. 3sg"] not in counts, "not reached by the end of chapter 1"
    assert all(occ or cumulative for occ, cumulative in counts.values())


# -- text lens (issues #5 / #15) --------------------------------------------


def test_text_rows_cover_both_grains():
    text = build_text_report(CorpusIndex(TWO_CHAPTERS))

    assert {r.lemma for r in text.lemma_rows} == {HO, LOGOS}
    assert {(r.lemma, r.parse) for r in text.parse_rows} == {
        (HO, NOM),
        (LOGOS, NOM),
        (LOGOS, GEN),
    }
    assert all(r.parse == "" for r in text.lemma_rows), "lemma rows carry no parse"


def test_text_rows_are_ordered_by_total():
    text = build_text_report(CorpusIndex(TWO_CHAPTERS))

    assert [r.total for r in text.lemma_rows] == sorted(
        (r.total for r in text.lemma_rows), reverse=True
    )
    assert text.lemma_rows[0].lemma == LOGOS


def test_first_and_last_chapter_and_distinct_chapter_count():
    text = build_text_report(CorpusIndex(TWO_CHAPTERS))
    by_lemma = {r.lemma: r for r in text.lemma_rows}

    assert (by_lemma[LOGOS].first_chapter, by_lemma[LOGOS].last_chapter) == (0, 1)
    assert by_lemma[LOGOS].chapter_count == 2
    assert (by_lemma[HO].first_chapter, by_lemma[HO].last_chapter) == (0, 0)
    assert by_lemma[HO].chapter_count == 1


def test_chapter_count_is_not_the_span():
    """A lemma in chapters 0 and 3 spans four and appears in two. Conflating
    them would report repetition that is not there."""
    facts = facts_from([(LOGOS, LOGOS, NOM, 0), (LOGOS, LOGOS, NOM, 3)])

    row = build_text_report(CorpusIndex(facts)).lemma_rows[0]

    assert (row.first_chapter, row.last_chapter, row.chapter_count) == (0, 3, 2)


def test_form_breakdown_is_corpus_wide():
    text = build_text_report(CorpusIndex(TWO_CHAPTERS))
    by_lemma = {r.lemma: r for r in text.lemma_rows}

    assert by_lemma[LOGOS].form_count == 2, "λόγος and λόγου"
    assert by_lemma[LOGOS].form == LOGOS, "most frequent form represents the row"
    assert by_lemma[LOGOS].total == 5

    by_parse = {(r.lemma, r.parse): r for r in text.parse_rows}
    assert by_parse[(LOGOS, GEN)].form == LOGOU
    assert by_parse[(LOGOS, GEN)].form_count == 1


def test_text_summary_matches_the_index():
    index = CorpusIndex(TWO_CHAPTERS)
    text = build_text_report(index)

    assert text.summary.chapter_count == 2
    assert text.summary.token_count == 6
    assert text.summary.unique_lemmas == 2
    assert text.summary.unique_parses == len(text.parse_rows)
    assert text.summary.unique_lemmas == len(text.lemma_rows)


def test_chapter_identity_travels_so_indexes_can_be_read_as_titles():
    text = build_text_report(CorpusIndex(TWO_CHAPTERS))

    assert [c.chapter_index for c in text.chapters] == [0, 1]
    assert text.chapters[0].title == "Chapter 1"
    assert text.chapters[0].filename == "a.docx"


def test_text_report_spans_files():
    """Chapter indexes are corpus-wide, so the text lens is one text even when
    the upload was several documents."""
    facts = facts_from([(LOGOS, LOGOS, NOM, 0)], filename="one.docx")
    facts += facts_from([(LOGOS, LOGOS, NOM, 1)], filename="two.docx")

    text = build_text_report(CorpusIndex(facts))

    assert text.lemma_rows[0].chapter_count == 2
    assert {c.filename for c in text.chapters} == {"one.docx", "two.docx"}


# -- new vs. repeated progression (issue #15's stacked bars) -----------------


def progress(text, grain="lemma"):
    return {p.chapter_index: p for p in getattr(text, f"{grain}_progress")}


def test_first_chapter_is_all_new():
    """Nothing can be repeated before there is something to repeat."""
    by_chapter = progress(build_text_report(CorpusIndex(TWO_CHAPTERS)))

    assert (by_chapter[0].repeated_types, by_chapter[0].repeated_tokens) == (0, 0)
    assert by_chapter[0].new_types == 2, "ὁ and λόγος"
    assert by_chapter[0].new_tokens == 3


def test_a_lemma_met_earlier_is_repeated_not_new():
    by_chapter = progress(build_text_report(CorpusIndex(TWO_CHAPTERS)))

    assert (by_chapter[1].new_types, by_chapter[1].new_tokens) == (0, 0)
    assert by_chapter[1].repeated_types == 1, "λόγος only; ὁ is not in this chapter"
    assert by_chapter[1].repeated_tokens == 3


def test_the_two_grains_disagree_which_is_the_point_of_the_toggle():
    """A known lemma in a form the reader has not met is repeated vocabulary
    and new grammar. The toggle is the only thing that tells them apart."""
    facts = facts_from([(LOGOS, LOGOS, NOM, 0), (LOGOS, LOGOU, GEN, 1)])
    text = build_text_report(CorpusIndex(facts))

    second_lemma = progress(text)[1]
    second_parse = progress(text, "parse")[1]

    assert (second_lemma.new_types, second_lemma.repeated_types) == (0, 1)
    assert (second_parse.new_types, second_parse.repeated_types) == (1, 0)


def test_the_split_partitions_the_chapter_at_either_grain():
    """The bars stack to the chapter, so the two halves must add to it exactly
    — a token counted in neither or in both would misstate the total the reader
    reads off the axis."""
    text = build_text_report(CorpusIndex(TWO_CHAPTERS))
    totals = {0: 3, 1: 3}  # tokens per chapter in the fixture

    for grain in ("lemma", "parse"):
        for point in getattr(text, f"{grain}_progress"):
            assert point.new_tokens + point.repeated_tokens == totals[point.chapter_index]

    lemma_first = progress(text)[0]
    assert lemma_first.new_types + lemma_first.repeated_types == 2, "unique lemmas in chapter 1"


def test_every_chapter_gets_a_bar_including_an_empty_one():
    """A heading the extractor found no tokens under is a fact about the text.
    Dropping its entry would close the gap the bars are there to show."""
    facts = facts_from([(LOGOS, LOGOS, NOM, 0), (LOGOS, LOGOS, NOM, 2)])
    chapters = [
        ChapterRef(index=i, id=f"{i + 1}-ch", title=f"Chapter {i + 1}", filename="a.docx")
        for i in range(3)
    ]

    text = build_text_report(CorpusIndex(facts, chapters))

    assert [p.chapter_index for p in text.lemma_progress] == [0, 1, 2]
    empty = text.lemma_progress[1]
    assert (empty.new_types, empty.repeated_types, empty.new_tokens, empty.repeated_tokens) == (
        0,
        0,
        0,
        0,
    )


def test_empty_corpus_produces_empty_tables_not_a_crash():
    text = build_text_report(CorpusIndex([]))

    assert text.lemma_rows == [] and text.parse_rows == []
    assert text.summary.token_count == 0
    assert text.grammar == []
    assert text.lemma_progress == [] and text.parse_progress == []

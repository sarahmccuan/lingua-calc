from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from lingua_calc.corpus import CorpusIndex, MorphCoverage, Track
from lingua_calc.models import (
    ChapterProgress,
    ChapterRefOut,
    ChapterReport,
    ChapterSummary,
    CombinationCount,
    CoverageReport,
    FormCombination,
    FormCombinationGroup,
    FormCombinationTable,
    FormStat,
    GrammarGroup,
    GrammarStat,
    LemmaChapterRow,
    LemmaFormRow,
    LemmaOccurrence,
    LemmaParseCount,
    LemmaParseRow,
    LemmaReport,
    LemmaSummary,
    TextReport,
    TextRow,
    TextSummary,
    TokenRow,
)
from lingua_calc.morphology import (
    DIMENSION_LABELS,
    FEATURE_DIMENSIONS,
    FEATURE_VALUES,
    FORM_CLASS_HINTS,
    FORM_CLASSES,
    classify_combination,
    feature_label,
    signature_sort_key,
)


@dataclass
class _FormAccum:
    form: str
    occ: int
    first_position: int


def build_chapter_report(chapter_index: int, index: CorpusIndex) -> ChapterReport:
    """Build one chapter's displayed table from the corpus index.

    Rows are at (lemma, parse) grain in first-appearance order, unchanged from
    before. What changed: every surface form in a group survives on
    ``TokenRow.forms`` instead of only the most frequent one, and first/last
    occurrence is carried as chapter indexes rather than booleans.
    """
    facts = index.facts_in(chapter_index)

    # Single pass, bucketing forms by their group. The previous implementation
    # rescanned every (lemma, parse, form) key once per group, which is
    # O(groups x forms) — fine per chapter, but the corpus-wide table in issue #5
    # has both terms sized by total vocabulary.
    group_first_position: dict[tuple[str, str], int] = {}
    group_type: dict[tuple[str, str], str] = {}
    group_forms: dict[tuple[str, str], dict[str, _FormAccum]] = defaultdict(dict)

    for fact in facts:
        key = fact.parse_key
        group_first_position.setdefault(key, fact.position)
        group_type.setdefault(key, fact.type)
        forms = group_forms[key]
        accum = forms.get(fact.form)
        if accum is None:
            forms[fact.form] = _FormAccum(form=fact.form, occ=1, first_position=fact.position)
        else:
            accum.occ += 1

    rows: list[TokenRow] = []
    for key in sorted(group_first_position, key=group_first_position.__getitem__):
        lemma, parse = key
        # Most frequent form wins; earliest appearance breaks ties. This picks
        # the row's representative `form` only — nothing is discarded.
        ranked = sorted(group_forms[key].values(), key=lambda a: (-a.occ, a.first_position))
        representative = ranked[0]

        lemma_track = index.lemma(lemma)
        parse_track = index.parse(lemma, parse)
        form_track = index.form(lemma, representative.form)

        rows.append(
            TokenRow(
                type=group_type[key],
                lemma=lemma,
                form=representative.form,
                parse=parse,
                chapter_index=chapter_index,
                lemma_occ=lemma_track.count_in(chapter_index),
                parse_occ=parse_track.count_in(chapter_index),
                form_occ=representative.occ,
                lemma_cum=lemma_track.cumulative_through(chapter_index),
                parse_cum=parse_track.cumulative_through(chapter_index),
                forms=[
                    FormStat(form=a.form, occ=a.occ, first_position=a.first_position) for a in ranked
                ],
                lemma_first_chapter=_chapter_or(lemma_track.first_chapter, chapter_index),
                lemma_last_chapter=_chapter_or(lemma_track.last_chapter, chapter_index),
                parse_first_chapter=_chapter_or(parse_track.first_chapter, chapter_index),
                parse_last_chapter=_chapter_or(parse_track.last_chapter, chapter_index),
                form_first_chapter=_chapter_or(form_track.first_chapter, chapter_index),
                form_last_chapter=_chapter_or(form_track.last_chapter, chapter_index),
            )
        )

    ref = index.chapter_ref(chapter_index)
    stats = index.chapter_stats(chapter_index)
    summary = ChapterSummary(
        id=ref.id if ref else f"ch-{chapter_index + 1}",
        title=ref.title if ref else "",
        chapter_index=chapter_index,
        unique_lemmas=stats.unique_lemmas,
        unique_forms=stats.unique_forms,
        token_count=stats.token_count,
    )
    return ChapterReport(
        summary=summary,
        rows=rows,
        grammar=build_grammar_groups(index, chapter_index),
        combination_counts=build_combination_counts(index, chapter_index),
        coverage=build_coverage(index.coverage(chapter_index)),
    )


def _chapter_or(value: int | None, fallback: int) -> int:
    """Coerce an optional chapter index.

    Keys are drawn from the chapter's own facts, so the track always has at
    least this chapter and ``value`` is never ``None`` in practice.
    """
    return fallback if value is None else value


# -- grammatical form profile (issues #7 / #14) -----------------------------


def build_grammar_groups(index: CorpusIndex, chapter_index: int | None = None) -> list[GrammarGroup]:
    """The "summary of forms and count of forms" for a chapter, or for the text.

    ``chapter_index`` of ``None`` means corpus scope, where ``occ`` and
    ``cumulative`` coincide.

    Two decisions worth keeping:

    - **Values come from ``FEATURE_VALUES``, not from the index.** Iterating what
      the index happens to hold would silently omit every value the corpus lacks,
      and "no future row" reads as "I didn't check" rather than "no futures".
      Zero rows are the answer to issue #7's actual question.
    - **A dimension the whole corpus never states is dropped.** Zero-filling
      inside a dimension is informative; a Degree card reading 0/0 for a text
      with no comparatives anywhere is just furniture. The test is corpus-wide,
      not per chapter, so the set of cards does not change as you page between
      chapters.
    """
    groups: list[GrammarGroup] = []
    for dimension in FEATURE_DIMENSIONS:
        dimension_track = index.feature_dimension(dimension)
        if dimension_track.total == 0:
            continue

        stats = []
        for value in FEATURE_VALUES[dimension]:
            track = index.feature_any(dimension, value)
            stats.append(
                GrammarStat(
                    dimension=dimension,
                    value=value,
                    label=feature_label(dimension, value),
                    occ=track.total if chapter_index is None else track.count_in(chapter_index),
                    cumulative=(
                        track.total
                        if chapter_index is None
                        else track.cumulative_through(chapter_index)
                    ),
                    first_chapter=track.first_chapter,
                    last_chapter=track.last_chapter,
                    chapter_count=track.chapter_count,
                )
            )

        groups.append(
            GrammarGroup(
                dimension=dimension,
                label=DIMENSION_LABELS.get(dimension, dimension),
                stats=stats,
                stated=(
                    dimension_track.total
                    if chapter_index is None
                    else dimension_track.count_in(chapter_index)
                ),
                stated_cumulative=(
                    dimension_track.total
                    if chapter_index is None
                    else dimension_track.cumulative_through(chapter_index)
                ),
            )
        )
    return groups


def _ordered_signatures(index: CorpusIndex) -> list[tuple[str, dict[str, str], Track]]:
    """Every attested combination in paradigm order.

    The single definition of that order. ``FormCombination.order`` is a position
    in this list and ``CombinationCount.order`` joins against it, so the two
    cannot disagree about which row a number belongs to.
    """
    return sorted(index.iter_signatures(), key=lambda e: signature_sort_key(e[1]))


def build_combination_counts(index: CorpusIndex, chapter_index: int) -> list[CombinationCount]:
    """One chapter's counts against the corpus combination inventory.

    The chapter half of what ``build_form_combinations`` used to return per
    chapter. The rows themselves are corpus-wide — that is the point of the
    table, and why a chapter can show an explicit zero for a form the text uses
    elsewhere — so they are built once for the text report and only these two
    numbers vary per chapter. Sending the whole table per chapter duplicated
    every label, paradigm position and chapter span once per chapter.

    Combinations the text has not reached by the end of this chapter are
    omitted: both numbers are zero and the client defaults a missing ``order``
    to (0, 0), so nothing is lost. A form absent *here* but seen earlier still
    travels, because its ``cumulative`` is not zero and is displayed.
    """
    counts = []
    for position, (_sig, _features, track) in enumerate(_ordered_signatures(index)):
        occ = track.count_in(chapter_index)
        cumulative = track.cumulative_through(chapter_index)
        if occ or cumulative:
            counts.append(CombinationCount(order=position, occ=occ, cumulative=cumulative))
    return counts


def build_form_combinations(index: CorpusIndex) -> FormCombinationTable:
    """The full inventory of paradigm cells, split into one table per form class.

    Rows are every combination attested **anywhere in the corpus**, so a chapter
    carries an explicit zero for a form the text uses elsewhere — "no aorist
    participles here" is as much an answer as a count. Zero-filling against
    what Greek permits rather than what the text attests is not an option: the
    cross product is thousands of cells, almost none of which any text contains.

    A class the corpus never attests is dropped, and the test is corpus-wide
    rather than per chapter — the same rule the dimension cards use, so the set
    of tables does not change as the author pages between chapters.

    The table is built once, at corpus scope: ``occ`` and ``cumulative`` are
    both the corpus total, and a chapter view is the client joining
    ``build_combination_counts`` onto these rows by ``order``.

    ``order`` is paradigm position, not the row's rank in any one scope, so it
    stays meaningful after the reader re-sorts a table by count. It is also the
    key that join runs on, which is why both read their ordering from
    ``_ordered_signatures`` rather than sorting independently.
    """
    entries = _ordered_signatures(index)

    grouped: dict[str, list[FormCombination]] = defaultdict(list)
    for position, (sig, features, track) in enumerate(entries):
        grouped[classify_combination(features)].append(
            FormCombination(
                form=sig,
                occ=track.total,
                cumulative=track.total,
                first_chapter=track.first_chapter,
                last_chapter=track.last_chapter,
                chapter_count=track.chapter_count,
                order=position,
            )
        )

    groups = [
        FormCombinationGroup(
            key=key,
            label=label,
            hint=FORM_CLASS_HINTS.get(key, ""),
            rows=grouped[key],
            tokens=sum(r.occ for r in grouped[key]),
            tokens_cumulative=sum(r.cumulative for r in grouped[key]),
        )
        for key, label in FORM_CLASSES
        if grouped[key]
    ]

    return FormCombinationTable(
        groups=groups,
        tokens=sum(g.tokens for g in groups),
        tokens_cumulative=sum(g.tokens_cumulative for g in groups),
    )


def build_coverage(coverage: MorphCoverage) -> CoverageReport:
    """Project the index's coverage audit onto the wire.

    Flattened rather than passed through because the shares are properties, and
    a grammar table that cannot show its own error bars is the failure mode this
    whole audit exists to prevent.
    """
    return CoverageReport(
        total=coverage.total,
        understood=coverage.understood,
        needs_attention=coverage.needs_attention,
        not_applicable=coverage.not_applicable,
        understood_share=coverage.understood_share,
        verb_forms=coverage.verb_forms,
        verbs_missing_voice=coverage.verbs_missing_voice,
        voice_gap_share=coverage.voice_gap_share,
    )


# -- corpus-wide vocabulary table (issues #5 / #15) -------------------------


@dataclass
class _TextAccum:
    """What one text row needs that a ``Track`` does not carry: type and forms."""

    type: str = ""
    forms: dict[str, int] = field(default_factory=dict)

    def add(self, type_: str, form: str) -> None:
        if not self.type:
            self.type = type_
        self.forms[form] = self.forms.get(form, 0) + 1

    @property
    def representative(self) -> str:
        """Most frequent surface form; insertion order breaks ties, so the
        earliest-seen form wins — the same rule the chapter table uses."""
        return max(self.forms, key=self.forms.__getitem__, default="")


def build_progression(index: CorpusIndex, tracks: Iterable[Track]) -> list[ChapterProgress]:
    """Split every chapter into first-encounter and already-met vocabulary.

    ``tracks`` is one appearance history per key — every lemma, or every
    lemma+parse pair. A key is *new* in the chapter that equals its
    ``first_chapter`` and repeated in every later chapter it appears in, which
    is the same rule the ``1st lemma`` / ``1st parse`` badges use on the chapter
    table; the bars are that column added up.

    Every chapter the index knows about gets an entry, including one that
    produced no tokens. A registered-but-empty chapter is a real fact about the
    text — a heading the extractor found nothing under — and dropping it would
    silently close the gap in the bars where the reader can see it.
    """
    per_chapter: dict[int, ChapterProgress] = {
        chapter_index: ChapterProgress(
            chapter_index=chapter_index,
            new_types=0,
            repeated_types=0,
            new_tokens=0,
            repeated_tokens=0,
        )
        for chapter_index in index.chapter_indexes
    }

    for track in tracks:
        first = track.first_chapter
        for chapter_index in track.chapters:
            point = per_chapter.get(chapter_index)
            if point is None:  # a key outside the registered chapter set
                continue
            occ = track.count_in(chapter_index)
            if chapter_index == first:
                point.new_types += 1
                point.new_tokens += occ
            else:
                point.repeated_types += 1
                point.repeated_tokens += occ

    return [per_chapter[chapter_index] for chapter_index in index.chapter_indexes]


def build_text_report(index: CorpusIndex) -> TextReport:
    """Build the corpus-wide tables behind the text tab.

    Both grains in one pass over the facts. The counts themselves come off the
    index — ``total``/``first_chapter``/``last_chapter``/``chapter_count`` are
    all ``Track`` properties — so this pass exists only for the two things a
    count cannot supply: the part of speech and the surface forms.

    ``chapter_count`` is deliberately not ``last - first + 1``. A lemma in
    chapters 1 and 20 spans twenty and appears in two, and the gap between those
    numbers is the repetition question issue #7 is asking.
    """
    lemma_accum: dict[str, _TextAccum] = defaultdict(_TextAccum)
    parse_accum: dict[tuple[str, str], _TextAccum] = defaultdict(_TextAccum)

    for fact in index.iter_facts():
        lemma_accum[fact.lemma].add(fact.type, fact.form)
        parse_accum[fact.parse_key].add(fact.type, fact.form)

    lemma_rows = [
        _text_row(accum, lemma, "", index.lemma(lemma))
        for lemma, accum in lemma_accum.items()
    ]
    parse_rows = [
        _text_row(accum, lemma, parse, index.parse(lemma, parse))
        for (lemma, parse), accum in parse_accum.items()
    ]

    # Most-frequent-first. The columns are all sortable in the UI, so this is a
    # default rather than a claim about the right reading; frequency is what
    # "what is the vocabulary of this text" asks for, and first_chapter is one
    # click away for the progression reading.
    order = lambda r: (-r.total, r.lemma, r.parse)  # noqa: E731
    lemma_rows.sort(key=order)
    parse_rows.sort(key=order)

    return TextReport(
        summary=TextSummary(
            chapter_count=index.chapter_count,
            token_count=index.total_tokens,
            unique_lemmas=index.unique_lemmas,
            unique_forms=index.unique_forms,
            unique_parses=index.unique_parses,
        ),
        chapters=build_chapter_refs(index),
        lemma_rows=lemma_rows,
        parse_rows=parse_rows,
        lemma_progress=build_progression(index, (t for _, t in index.iter_lemmas())),
        parse_progress=build_progression(index, (t for _, t in index.iter_parses())),
        grammar=build_grammar_groups(index),
        form_combinations=build_form_combinations(index),
        coverage=build_coverage(index.coverage()),
    )


def build_chapter_refs(index: CorpusIndex) -> list[ChapterRefOut]:
    """Chapter identity for every chapter in the run, in reading order.

    Ships with any report whose tables are keyed by chapter *index*, so a column
    of 0-based numbers can be read as titles without the client guessing.
    """
    refs = []
    for chapter_index in index.chapter_indexes:
        ref = index.chapter_ref(chapter_index)
        refs.append(
            ChapterRefOut(
                chapter_index=chapter_index,
                id=ref.id if ref else f"ch-{chapter_index + 1}",
                title=ref.title if ref else "",
                filename=ref.filename if ref else "",
            )
        )
    return refs


def _text_row(accum: _TextAccum, lemma: str, parse: str, track) -> TextRow:
    return TextRow(
        type=accum.type,
        lemma=lemma,
        parse=parse,
        form=accum.representative,
        total=track.total,
        form_count=len(accum.forms),
        # Every key here came from a fact, so the track is never empty and these
        # are never None; the `or 0` is a type narrowing, not a default.
        first_chapter=track.first_chapter or 0,
        last_chapter=track.last_chapter or 0,
        chapter_count=track.chapter_count,
    )


# -- lemma lens (issue #16) -------------------------------------------------


LEMMA_CONTEXT_TOKENS = 6
"""Tokens carried either side of an occurrence.

The provider is asked not to emit punctuation, so the fact stream has no
sentence boundary to cut on and the concordance window is a fixed number of
tokens instead. Six is about a line of Greek either side — enough to see what
governs the word, short enough that a hundred of them still scan as a list.
"""

LEMMA_OCCURRENCE_LIMIT = 400
"""Default cap on the occurrence list.

``καί`` in a full text is thousands of lines, and shipping them all is both a
slow response and a list nobody reads to the end of. The cap is reported
alongside ``occurrences_total`` so a truncated list can never present itself as
the whole; narrowing to a chapter is the way to see the rest.
"""


def build_lemma_report(
    index: CorpusIndex,
    lemma: str,
    *,
    limit: int = LEMMA_OCCURRENCE_LIMIT,
    chapter_index: int | None = None,
    context: int = LEMMA_CONTEXT_TOKENS,
) -> LemmaReport | None:
    """Everything issue #16 asks about one lemma. ``None`` if the corpus lacks it.

    Three answers off one pass over the chapters that contain the word:

    - **totals broken out by parse** (and its transpose, by surface form),
    - **which chapters it lives in**, each with its own parse breakdown,
    - **where it is actually used**, as concordance lines.

    Only the chapters the lemma appears in are scanned — ``Track.chapters`` is
    sparse — so this is proportional to the word, not to the corpus. The counts
    themselves still come off the index rather than being re-derived here, so a
    figure in this report and the same figure in the text lens are the same
    number and cannot drift.

    ``chapter_index`` narrows the *occurrence list* only. Every table stays
    corpus-wide: the point of the lens is where a word lives across the whole
    text, and re-scoping the tables to one chapter would make it the chapter
    lens with extra steps.
    """
    track = index.lemma(lemma)
    if track.total == 0:
        return None

    types: Counter[str] = Counter()
    parse_forms: dict[str, Counter[str]] = defaultdict(Counter)
    parse_types: dict[str, Counter[str]] = defaultdict(Counter)
    form_parses: dict[str, Counter[str]] = defaultdict(Counter)
    chapter_parses: dict[int, Counter[str]] = defaultdict(Counter)
    occurrences: list[LemmaOccurrence] = []

    for ci in track.chapters:
        facts = index.facts_in(ci)
        for i, f in enumerate(facts):
            if f.lemma != lemma:
                continue
            types[f.type] += 1
            parse_forms[f.parse][f.form] += 1
            parse_types[f.parse][f.type] += 1
            form_parses[f.form][f.parse] += 1
            chapter_parses[ci][f.parse] += 1

            if chapter_index is not None and ci != chapter_index:
                continue
            # The window is sliced out of this chapter's own fact list, so it
            # stops at the chapter edge rather than running into the previous
            # chapter's last words. It is short there, never padded: a blank
            # would read as a word the analysis missed.
            if len(occurrences) < limit:
                occurrences.append(
                    LemmaOccurrence(
                        chapter_index=ci,
                        position=f.position,
                        form=f.form,
                        parse=f.parse,
                        before=[n.form for n in facts[max(0, i - context) : i]],
                        after=[n.form for n in facts[i + 1 : i + 1 + context]],
                    )
                )

    parses: list[LemmaParseRow] = []
    for parse, spellings in parse_forms.items():
        parse_track = index.parse(lemma, parse)
        parses.append(
            LemmaParseRow(
                parse=parse,
                type=parse_types[parse].most_common(1)[0][0],
                occ=parse_track.total,
                # Most frequent spelling wins, as everywhere else; `Counter`
                # keeps insertion order on a tie, so the earliest seen breaks it.
                form=spellings.most_common(1)[0][0],
                form_count=len(spellings),
                first_chapter=parse_track.first_chapter or 0,
                last_chapter=parse_track.last_chapter or 0,
                chapter_count=parse_track.chapter_count,
            )
        )
    parses.sort(key=lambda r: (-r.occ, r.parse))

    forms: list[LemmaFormRow] = []
    for form, carried in form_parses.items():
        form_track = index.form(lemma, form)
        forms.append(
            LemmaFormRow(
                form=form,
                occ=form_track.total,
                parses=[p for p, _ in carried.most_common()],
                first_chapter=form_track.first_chapter or 0,
                last_chapter=form_track.last_chapter or 0,
                chapter_count=form_track.chapter_count,
            )
        )
    forms.sort(key=lambda r: (-r.occ, r.form))

    chapters = [
        LemmaChapterRow(
            chapter_index=ci,
            occ=track.count_in(ci),
            cumulative=track.cumulative_through(ci),
            # A gap belongs to the appearance that ends it. On a chapter with no
            # occurrence there is nothing yet to measure to, and on the first
            # appearance there is nothing to measure from — both are None rather
            # than a zero that would read as "no gap".
            gap_before=track.gap_before(ci) if track.count_in(ci) else None,
            parses=[
                LemmaParseCount(parse=parse, occ=occ)
                for parse, occ in chapter_parses[ci].most_common()
            ],
        )
        for ci in index.chapter_indexes
    ]

    appearances = track.chapters
    gaps = [b - a for a, b in zip(appearances, appearances[1:])]

    return LemmaReport(
        summary=LemmaSummary(
            lemma=lemma,
            type=types.most_common(1)[0][0],
            total=track.total,
            parse_count=len(parses),
            form_count=len(forms),
            chapter_count=track.chapter_count,
            first_chapter=track.first_chapter or 0,
            last_chapter=track.last_chapter or 0,
            longest_gap=max(gaps) if gaps else None,
            corpus_tokens=index.total_tokens,
            corpus_chapters=index.chapter_count,
        ),
        parses=parses,
        forms=forms,
        chapters=chapters,
        chapter_refs=build_chapter_refs(index),
        occurrences=occurrences,
        occurrences_total=(
            track.total if chapter_index is None else track.count_in(chapter_index)
        ),
        occurrence_limit=limit,
        context_window=context,
        chapter_filter=chapter_index,
    )

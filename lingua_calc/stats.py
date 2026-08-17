from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from lingua_calc import lexicon as lexicon_mod
from lingua_calc.corpus import CorpusIndex, MorphCoverage, Track
from lingua_calc.lexicon import Lexicon
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
    LexiconBand,
    LexiconChapterOcc,
    LexiconChapterProgress,
    LexiconEntryRow,
    LexiconGapRow,
    LexiconMatchReport,
    LexiconRef,
    LexiconReport,
    LexiconSummary,
    OffListRow,
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


# -- lexicon lens -----------------------------------------------------------
#
# Every other builder in this module reads the text and reports what is in it.
# This one starts from a fixed list and reports how much of it the text has
# delivered, which inverts every denominator: the interesting rows are as often
# the ones with `occ == 0` as the ones with counts, because an entry the text
# has never used is exactly the thing an author is looking for.

# Ranks are banded 500 at a time. Wide enough that a band is a meaningful
# stratum of the language rather than noise, narrow enough that ten of them fit
# on screen as a single readable row of bars.
LEXICON_BAND_SIZE = 500

# Heads of two unbounded tables. Both are ordered so that the head is the part
# worth reading — gaps by rank (commonest first), off-list by frequency — and
# both ship a total beside them so a truncated list never reads as a complete
# one.
LEXICON_GAP_LIMIT = 200
LEXICON_OFF_LIST_LIMIT = 200


@dataclass
class _EntryAccum:
    """What the text did with one list entry, accumulated across every text
    lemma that resolved to it.

    Plural sources are normal, not an edge case: ``οὕτω`` and ``οὕτως`` are two
    lemmas in the fact stream and one word in the list, so their counts add and
    the entry's first chapter is the earlier of the two.
    """

    occ: int = 0
    chapters: Counter = field(default_factory=Counter)
    sources: Counter = field(default_factory=Counter)
    matched_by: str = ""
    ambiguous: bool = False

    def add(self, lemma: str, track: Track, how: str, ambiguous: bool) -> None:
        self.occ += track.total
        self.sources[lemma] += track.total
        for chapter_index in track.chapters:
            self.chapters[chapter_index] += track.count_in(chapter_index)
        # Strictest wins, so a word reachable both exactly and by fold is
        # reported as exact — the report should credit the best evidence it has,
        # not the last one it happened to try.
        if not self.matched_by or _MATCH_STRENGTH[how] < _MATCH_STRENGTH[self.matched_by]:
            self.matched_by = how
        self.ambiguous = self.ambiguous or ambiguous

    @property
    def first_chapter(self) -> int | None:
        return min(self.chapters) if self.chapters else None

    @property
    def source_lemma(self) -> str:
        """The text lemma to send a reader to when they click this entry.

        The entry's own headword will not do: it may carry a homograph digit
        (``ὅς2``), or be the spelling the list cites rather than the one the
        text uses (``ἐθέλω`` for a text's ``θέλω``). Neither exists in the
        lemma lens, so linking the headword lands on nothing.

        Commonest first where several lemmas resolved here, with the lemma
        itself breaking ties so the same run always produces the same link.
        """
        if not self.sources:
            return ""
        return min(self.sources, key=lambda lemma: (-self.sources[lemma], lemma))


_MATCH_STRENGTH = {lexicon_mod.EXACT: 0, lexicon_mod.ALIAS: 1, lexicon_mod.FOLDED: 2}


def build_lexicon_report(
    index: CorpusIndex,
    lexicon: Lexicon,
    *,
    gap_limit: int = LEXICON_GAP_LIMIT,
    off_list_limit: int = LEXICON_OFF_LIST_LIMIT,
) -> LexiconReport:
    """Benchmark a run against a ranked vocabulary list.

    One pass over the index's lemmas resolves each against the list; everything
    else is arithmetic over the result. The counts come off ``Track`` exactly as
    they do everywhere else in this module, so a figure here agrees with the
    text tab by construction rather than by a second implementation.
    """
    accums: dict[int, _EntryAccum] = {}
    types = _dominant_types(index)

    off_rows: list[OffListRow] = []
    # Per-chapter token split. Names are tracked apart from other off-list
    # vocabulary because a list may have no entry for them — a classical
    # frequency list has none at all — and charging a text for those measures it
    # against a target that does not exist. Only *unmatched* names land here:
    # `gnt-lemmas` contains hundreds, and those match like any other word.
    on_list_tokens: Counter = Counter()
    off_list_tokens: Counter = Counter()
    proper_tokens: Counter = Counter()

    # First-encounter vocabulary, keyed by the chapter that introduces it. A
    # lemma lands in exactly one of these, so summing them over the text gives
    # the corpus's distinct-lemma count and the four chart segments partition.
    new_types: dict[bool, Counter] = {True: Counter(), False: Counter()}
    new_tokens: dict[bool, Counter] = {True: Counter(), False: Counter()}

    exact = alias = folded = ambiguous = 0
    matched_lemmas = 0
    unmatched_lemmas = unmatched_tokens = 0
    proper_lemmas = proper_token_total = 0
    text_lemmas = 0

    for lemma, track in index.iter_lemmas():
        text_lemmas += 1
        match = lexicon.lookup(lemma)

        if match is None:
            proper = lexicon_mod.is_proper_noun(lemma)
            bucket = proper_tokens if proper else off_list_tokens
            for chapter_index in track.chapters:
                bucket[chapter_index] += track.count_in(chapter_index)
            if proper:
                proper_lemmas += 1
                proper_token_total += track.total
            else:
                unmatched_lemmas += 1
                unmatched_tokens += track.total
            first = track.first_chapter
            if first is not None:
                new_types[False][first] += 1
                new_tokens[False][first] += track.count_in(first)
            off_rows.append(
                OffListRow(
                    lemma=lemma,
                    type=types.get(lemma, ""),
                    occ=track.total,
                    first_chapter=track.first_chapter or 0,
                    chapter_count=track.chapter_count,
                    proper=proper,
                )
            )
            continue

        matched_lemmas += 1
        if match.how == lexicon_mod.EXACT:
            exact += 1
        elif match.how == lexicon_mod.ALIAS:
            alias += 1
        else:
            folded += 1
        if match.ambiguous:
            ambiguous += 1

        for chapter_index in track.chapters:
            on_list_tokens[chapter_index] += track.count_in(chapter_index)
        first = track.first_chapter
        if first is not None:
            new_types[True][first] += 1
            new_tokens[True][first] += track.count_in(first)
        for entry in match.entries:
            accums.setdefault(entry.rank, _EntryAccum()).add(lemma, track, match.how, match.ambiguous)

    entries = [_lexicon_entry_row(entry, accums.get(entry.rank), lexicon) for entry in lexicon.entries]
    covered_ranks = set(accums)

    gap_rows = [
        LexiconGapRow(
            rank=entry.rank,
            lemma=entry.lemma,
            gloss=entry.gloss,
            kind=entry.kind,
            ref_share=lexicon.ref_share(entry),
        )
        for entry in lexicon.entries
        if entry.rank not in covered_ranks
    ]

    return LexiconReport(
        lexicon=_lexicon_ref(lexicon),
        summary=_lexicon_summary(index, lexicon, covered_ranks, on_list_tokens, off_list_tokens, proper_tokens),
        bands=_lexicon_bands(lexicon, covered_ranks),
        progress=_lexicon_progress(
            index, lexicon, accums, on_list_tokens, off_list_tokens, proper_tokens, new_types, new_tokens
        ),
        entries=entries,
        gaps=gap_rows[:gap_limit],
        gaps_total=len(gap_rows),
        off_list=sorted(off_rows, key=lambda r: (-r.occ, r.lemma))[:off_list_limit],
        off_list_total=len(off_rows),
        match=LexiconMatchReport(
            text_lemmas=text_lemmas,
            matched_lemmas=matched_lemmas,
            exact=exact,
            alias=alias,
            folded=folded,
            ambiguous=ambiguous,
            unmatched_lemmas=unmatched_lemmas,
            unmatched_tokens=unmatched_tokens,
            proper_lemmas=proper_lemmas,
            proper_tokens=proper_token_total,
            tokens=index.total_tokens,
            tokens_on_list=sum(on_list_tokens.values()),
        ),
        chapter_refs=build_chapter_refs(index),
    )


def _dominant_types(index: CorpusIndex) -> dict[str, str]:
    """The part of speech the provider most often gave each lemma.

    One pass for the whole corpus rather than a scan per lemma. The obvious
    spelling — filter `iter_facts()` inside the lemma loop — is quadratic, and
    on a 2,000-lemma / 14,000-token run that is 28 million comparisons to
    produce a column.

    Off-list rows carry it because "which words is this text spending its
    vocabulary budget on" reads very differently for a name, a particle and a
    noun, and the lemma alone does not say.
    """
    counts: dict[str, Counter] = defaultdict(Counter)
    for fact in index.iter_facts():
        counts[fact.lemma][fact.type] += 1
    return {lemma: c.most_common(1)[0][0] for lemma, c in counts.items()}


def _lexicon_ref(lexicon: Lexicon) -> LexiconRef:
    meta = lexicon.meta
    return LexiconRef(
        id=meta.id,
        name=meta.name,
        short_name=meta.short_name,
        description=meta.description,
        entry_count=meta.entry_count,
        reference_tokens=meta.reference_tokens,
        source=meta.source,
    )


def _lexicon_entry_row(entry, accum: _EntryAccum | None, lexicon: Lexicon) -> LexiconEntryRow:
    return LexiconEntryRow(
        rank=entry.rank,
        lemma=entry.lemma,
        gloss=entry.gloss,
        kind=entry.kind,
        ref_count=entry.ref_count,
        ref_share=lexicon.ref_share(entry),
        occ=accum.occ if accum else 0,
        first_chapter=accum.first_chapter if accum else None,
        chapter_count=len(accum.chapters) if accum else 0,
        chapters=(
            [
                LexiconChapterOcc(chapter_index=ci, occ=occ)
                for ci, occ in sorted(accum.chapters.items())
            ]
            if accum
            else []
        ),
        matched_by=accum.matched_by if accum else "",
        ambiguous=accum.ambiguous if accum else False,
        source_lemma=accum.source_lemma if accum else "",
    )


def _lexicon_summary(
    index: CorpusIndex,
    lexicon: Lexicon,
    covered_ranks: set[int],
    on_list: Counter,
    off_list: Counter,
    proper: Counter,
) -> LexiconSummary:
    covered_share_of_ref = sum(
        lexicon.ref_share(entry) for entry in lexicon.entries if entry.rank in covered_ranks
    )
    tokens = index.total_tokens
    on = sum(on_list.values())
    return LexiconSummary(
        entries=len(lexicon),
        covered=len(covered_ranks),
        covered_share=len(covered_ranks) / len(lexicon) if len(lexicon) else 0.0,
        ref_share_covered=covered_share_of_ref,
        ref_share_total=lexicon.total_ref_share,
        tokens=tokens,
        tokens_on_list=on,
        tokens_off_list=sum(off_list.values()),
        tokens_proper=sum(proper.values()),
        on_list_share=on / tokens if tokens else 0.0,
        chapter_count=index.chapter_count,
    )


def _lexicon_bands(lexicon: Lexicon, covered_ranks: set[int]) -> list[LexiconBand]:
    """The list sliced into rank bands, each with what the text has claimed of it.

    The bands span the list's *ranks*, not its entry count, and each one is
    filled by scanning the entries — so a list whose ranks are sparse (any row
    `_read_entries` skipped leaves a hole) or that stops short of a round number
    still bands correctly, and every entry lands in exactly one band. Walking to
    `len(lexicon)` instead would silently drop every entry ranked beyond the
    count, and the bands would no longer sum to `summary.covered`.

    Empty bands are dropped rather than shown at zero: a hole in the ranks is an
    artefact of the source file, not a stratum of the language the text failed.
    """
    bands: list[LexiconBand] = []
    last_rank = max((e.rank for e in lexicon.entries), default=0)
    for start in range(1, last_rank + 1, LEXICON_BAND_SIZE):
        end = min(start + LEXICON_BAND_SIZE - 1, last_rank)
        slice_ = [e for e in lexicon.entries if start <= e.rank <= end]
        if not slice_:
            continue
        bands.append(
            LexiconBand(
                start=start,
                end=end,
                entries=len(slice_),
                covered=sum(1 for e in slice_ if e.rank in covered_ranks),
                ref_share=sum(lexicon.ref_share(e) for e in slice_),
                ref_share_covered=sum(
                    lexicon.ref_share(e) for e in slice_ if e.rank in covered_ranks
                ),
            )
        )
    return bands


def _lexicon_progress(
    index: CorpusIndex,
    lexicon: Lexicon,
    accums: dict[int, _EntryAccum],
    on_list: Counter,
    off_list: Counter,
    proper: Counter,
    new_types: dict[bool, Counter],
    new_tokens: dict[bool, Counter],
) -> list[LexiconChapterProgress]:
    """The curve the tab leads with: goal vocabulary delivered, chapter by chapter.

    An entry counts as taught in the chapter that first uses it and never again,
    so ``new_entries`` summed over the text equals ``summary.covered`` exactly.
    ``cumulative_ref_share`` is the same series weighted by how common each word
    is, which is the one that answers "how much Greek can a reader handle by the
    end of chapter N".

    Every chapter gets a point, including one that taught nothing new. A flat
    stretch in the curve is the finding, and dropping the chapters that caused
    it would hide exactly what an author is looking for.
    """
    ref_share_by_rank = {e.rank: lexicon.ref_share(e) for e in lexicon.entries}

    new_counts: Counter = Counter()
    new_shares: dict[int, float] = defaultdict(float)
    for rank, accum in accums.items():
        first = accum.first_chapter
        if first is None:
            continue
        new_counts[first] += 1
        new_shares[first] += ref_share_by_rank.get(rank, 0.0)

    points: list[LexiconChapterProgress] = []
    running_entries = 0
    running_share = 0.0
    for chapter_index in index.chapter_indexes:
        running_entries += new_counts.get(chapter_index, 0)
        running_share += new_shares.get(chapter_index, 0.0)
        on = on_list.get(chapter_index, 0)
        off = off_list.get(chapter_index, 0)
        names = proper.get(chapter_index, 0)
        points.append(
            LexiconChapterProgress(
                chapter_index=chapter_index,
                new_entries=new_counts.get(chapter_index, 0),
                cumulative_entries=running_entries,
                new_ref_share=new_shares.get(chapter_index, 0.0),
                cumulative_ref_share=running_share,
                tokens=on + off + names,
                tokens_on_list=on,
                tokens_off_list=off,
                tokens_proper=names,
                new_on_list_types=new_types[True].get(chapter_index, 0),
                new_off_list_types=new_types[False].get(chapter_index, 0),
                new_on_list_tokens=new_tokens[True].get(chapter_index, 0),
                new_off_list_tokens=new_tokens[False].get(chapter_index, 0),
                tokens_off_list_with_names=off + names,
            )
        )
    return points

"""Corpus-wide index over the token stream.

Everything statistical is derived here, from one pass over ``TokenFact``
records. Previously each statistic meant another ``Counter`` and another
positional argument threaded from ``pipeline`` into ``build_chapter_report``;
adding the cross-chapter stats in issues #4/#5 would have taken that function to
a dozen parallel dicts. One index object with typed lookups replaces them.

The counting itself stays plain ``Counter``/``dict`` — at learner-text scale
(order 100k tokens) that is milliseconds, and it keeps the package
dependency-free. What changed is the shape, not the arithmetic.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from lingua_calc.models import TokenFact
from lingua_calc.morphology import FEATURE_DIMENSIONS, MorphStatus, is_verb_form, signature


class Track:
    """Appearance history of one key (a lemma, a form, a parse) across chapters.

    Built by repeated ``add`` calls during indexing, then frozen on first query.
    Chapter lists are kept sparse — a key that appears in 3 of 200 chapters
    stores 3 entries — and prefix-summed once so cumulative lookups are
    O(log n) rather than a rescan per row.
    """

    __slots__ = ("total", "_per_chapter", "_frozen")

    def __init__(self) -> None:
        self.total: int = 0
        self._per_chapter: dict[int, int] = {}
        self._frozen: tuple[tuple[int, ...], tuple[int, ...]] | None = None

    def add(self, chapter_index: int, count: int = 1) -> None:
        self.total += count
        self._per_chapter[chapter_index] = self._per_chapter.get(chapter_index, 0) + count
        self._frozen = None

    def _freeze(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """The chapter list and its prefix sums, computed on first query.

        Both halves are published in one assignment, and read back through a
        local, because an index outlives the request that built it now
        (``pipeline.load_run_index`` caches it) and the handlers that query it
        run in a threadpool. Storing the two tuples separately let a second
        thread see the chapter list already set and the prefix sums still
        ``None``. Two threads racing here both compute the same answer, which
        costs a duplicate sort and nothing else.
        """
        frozen = self._frozen
        if frozen is None:
            chapters = tuple(sorted(self._per_chapter))
            prefix = [0]
            running = 0
            for c in chapters:
                running += self._per_chapter[c]
                prefix.append(running)
            frozen = (chapters, tuple(prefix))
            self._frozen = frozen
        return frozen

    @property
    def chapters(self) -> tuple[int, ...]:
        """Chapter indexes this key appears in, ascending. Empty if never seen."""
        return self._freeze()[0]

    @property
    def chapter_count(self) -> int:
        """Number of distinct chapters this key appears in (issue #5)."""
        return len(self.chapters)

    @property
    def first_chapter(self) -> int | None:
        chapters = self.chapters
        return chapters[0] if chapters else None

    @property
    def last_chapter(self) -> int | None:
        chapters = self.chapters
        return chapters[-1] if chapters else None

    def count_in(self, chapter_index: int) -> int:
        return self._per_chapter.get(chapter_index, 0)

    def cumulative_through(self, chapter_index: int) -> int:
        """Total occurrences from the start of the corpus through ``chapter_index``.

        This is what issue #4's `cumulative occurrences` columns need.
        """
        chapters, prefix = self._freeze()
        return prefix[bisect_right(chapters, chapter_index)]

    def previous_chapter(self, chapter_index: int) -> int | None:
        """Nearest chapter strictly before ``chapter_index`` where this appeared."""
        chapters = self.chapters
        i = bisect_left(chapters, chapter_index)
        return chapters[i - 1] if i > 0 else None

    def next_chapter(self, chapter_index: int) -> int | None:
        """Nearest chapter strictly after ``chapter_index`` where this appeared."""
        chapters = self.chapters
        i = bisect_right(chapters, chapter_index)
        return chapters[i] if i < len(chapters) else None

    def gap_before(self, chapter_index: int) -> int | None:
        """Chapters elapsed since the previous appearance (CONTEXT.md item 4).

        ``None`` when this is the first appearance — there is no gap to report,
        which is a different thing from a gap of zero.
        """
        prev = self.previous_chapter(chapter_index)
        return None if prev is None else chapter_index - prev


_EMPTY_TRACK = Track()


@dataclass(frozen=True)
class ChapterRef:
    """Identity of a chapter, independent of its statistics."""

    index: int
    id: str
    title: str
    filename: str


@dataclass
class ChapterStats:
    unique_lemmas: int = 0
    unique_forms: int = 0
    token_count: int = 0
    _lemmas: set[str] = field(default_factory=set, repr=False)
    _forms: set[tuple[str, str]] = field(default_factory=set, repr=False)


@dataclass(frozen=True)
class MorphCoverage:
    """Audit of how much morphology was actually decoded.

    Exists so grammar counts can be reported with their own error bars. Without
    it, a label the normalizer failed to read is indistinguishable from a form
    the text genuinely does not contain — which is exactly the trap that made
    counting off raw parse strings unsafe.
    """

    total: int
    ok: int
    partial: int
    descriptive: int
    not_applicable: int
    unparsed: int

    verb_forms: int
    """Tokens where voice is expected, by label or by part of speech.

    Not the same as tokens typed "verb": the type is free text the provider
    chooses, and a token typed "participle" or mistyped outright still carries a
    verbal label. Counting only literal "verb" types here while the numerator
    was drawn from the label made the two figures incomparable.
    """

    verbs_missing_voice: int
    """Subset of ``verb_forms`` whose label never stated a voice."""

    @property
    def morphological(self) -> int:
        """Tokens that claimed to carry morphology, i.e. everything but "-"."""
        return self.total - self.not_applicable

    @property
    def understood(self) -> int:
        """Fully decoded, counting purely lexical labels as understood."""
        return self.ok + self.descriptive

    @property
    def needs_attention(self) -> int:
        """Labels the normalizer could not fully read."""
        return self.partial + self.unparsed

    @property
    def understood_share(self) -> float:
        """Fraction of morphology-bearing tokens fully decoded, 1.0 if none."""
        return 1.0 if self.morphological == 0 else self.understood / self.morphological

    @property
    def voice_gap_share(self) -> float:
        """Fraction of verb forms that never stated voice, 0.0 if there are none.

        Non-zero means voice counts understate — the model omitted the feature
        rather than the text lacking it. Both sides come from ``is_verb_form``,
        so this is always in [0.0, 1.0].
        """
        return 0.0 if self.verb_forms == 0 else self.verbs_missing_voice / self.verb_forms


class CorpusIndex:
    """Queryable index over every token in a run.

    Construct once per run and pass it wherever statistics are needed. Holds the
    token stream itself, so new statistics can be added without re-running the
    provider.
    """

    def __init__(
        self,
        facts: Iterable[TokenFact],
        chapters: Iterable[ChapterRef] | None = None,
    ) -> None:
        """``chapters`` registers chapter identity up front.

        Without it a chapter that produced no tokens would be invisible to the
        index and lose its id and title, so pass the full chapter list whenever
        it is known.
        """
        self._lemmas: dict[str, Track] = defaultdict(Track)
        self._parses: dict[tuple[str, str], Track] = defaultdict(Track)
        self._forms: dict[tuple[str, str], Track] = defaultdict(Track)
        self._form_parses: dict[tuple[str, str, str], Track] = defaultdict(Track)
        self._types: dict[str, Track] = defaultdict(Track)

        # Morphological features, keyed (dimension, value). Two indexes because
        # syncretism has two correct readings: `_features` holds the value
        # exactly as decoded ("acc|nom"), `_features_incl` additionally files an
        # ambiguous token under each alternative so a plain accusative count
        # does not silently drop syncretic forms.
        self._features: dict[tuple[str, str], Track] = defaultdict(Track)
        self._features_incl: dict[tuple[str, str], Track] = defaultdict(Track)
        # Tokens that stated a given dimension at all, regardless of value. This
        # is the honest denominator for a grammar table: the per-value counts
        # inside a dimension overlap (a syncretic form is counted under both its
        # readings), so summing them can exceed the number of tokens involved.
        self._dimensions: dict[str, Track] = defaultdict(Track)
        # Whole feature combinations — the paradigm cell a token occupies, not
        # its features counted separately in eight different places. Every token
        # carrying morphology has exactly one, so unlike the per-dimension
        # tracks these partition and can be summed.
        self._signatures: dict[str, Track] = defaultdict(Track)
        self._signature_features: dict[str, dict[str, str]] = {}
        self._morph_status: dict[str, Track] = defaultdict(Track)
        self._descriptors: dict[str, Track] = defaultdict(Track)
        # Numerator and denominator of the voice-gap figure, filled from the one
        # predicate so the gap set is a subset of the population by construction.
        self._verb_forms = Track()
        self._voice_gaps = Track()
        self._deponents = Track()

        by_chapter: dict[int, list[TokenFact]] = defaultdict(list)
        refs: dict[int, ChapterRef] = {}
        stats: dict[int, ChapterStats] = defaultdict(ChapterStats)

        for ref in chapters or ():
            refs[ref.index] = ref
            by_chapter.setdefault(ref.index, [])
            stats.setdefault(ref.index, ChapterStats())

        for fact in facts:
            ci = fact.chapter_index
            by_chapter[ci].append(fact)
            refs.setdefault(
                ci,
                ChapterRef(index=ci, id=fact.chapter_id, title=fact.chapter_title, filename=fact.filename),
            )

            self._lemmas[fact.lemma].add(ci)
            self._parses[fact.parse_key].add(ci)
            self._forms[fact.form_key].add(ci)
            self._form_parses[fact.form_parse_key].add(ci)
            self._types[fact.type].add(ci)

            morph = fact.morph
            features = morph.features()
            for dimension, value in features.items():
                self._features[(dimension, value)].add(ci)
                self._dimensions[dimension].add(ci)
                for alternative in value.split("|"):
                    self._features_incl[(dimension, alternative)].add(ci)
            if features:
                sig = signature(morph)
                self._signatures[sig].add(ci)
                self._signature_features.setdefault(sig, features)
            self._morph_status[morph.status.value].add(ci)
            for descriptor in morph.descriptors:
                self._descriptors[descriptor].add(ci)
            if is_verb_form(morph, fact.type):
                self._verb_forms.add(ci)
                if morph.voice is None:
                    self._voice_gaps.add(ci)
            if morph.is_deponent:
                self._deponents.add(ci)

            st = stats[ci]
            st.token_count += 1
            st._lemmas.add(fact.lemma)
            st._forms.add(fact.form_key)

        for st in stats.values():
            st.unique_lemmas = len(st._lemmas)
            st.unique_forms = len(st._forms)
            st._lemmas.clear()
            st._forms.clear()

        self._by_chapter = by_chapter
        self._refs = refs
        self._stats = stats
        self.chapter_indexes: tuple[int, ...] = tuple(sorted(by_chapter))

    # -- chapter access -------------------------------------------------

    @property
    def chapter_count(self) -> int:
        return len(self.chapter_indexes)

    def facts_in(self, chapter_index: int) -> list[TokenFact]:
        return self._by_chapter.get(chapter_index, [])

    def chapter_ref(self, chapter_index: int) -> ChapterRef | None:
        return self._refs.get(chapter_index)

    def chapter_stats(self, chapter_index: int) -> ChapterStats:
        return self._stats.get(chapter_index, ChapterStats())

    # -- key lookups ----------------------------------------------------

    def lemma(self, lemma: str) -> Track:
        return self._lemmas.get(lemma, _EMPTY_TRACK)

    def parse(self, lemma: str, parse: str) -> Track:
        return self._parses.get((lemma, parse), _EMPTY_TRACK)

    def form(self, lemma: str, form: str) -> Track:
        return self._forms.get((lemma, form), _EMPTY_TRACK)

    def form_parse(self, lemma: str, parse: str, form: str) -> Track:
        return self._form_parses.get((lemma, parse, form), _EMPTY_TRACK)

    def token_type(self, type_: str) -> Track:
        return self._types.get(type_, _EMPTY_TRACK)

    # -- morphological features ------------------------------------------
    #
    # The dimension for issue #7: "21 present tenses, 0 future tenses" is
    # `feature_any("tense", "pres").count_in(ch)`. Counting these off the raw
    # `parse` string does not work — see morphology.py.

    def feature_any(self, dimension: str, value: str) -> Track:
        """Tokens carrying ``value`` in ``dimension``, counting ambiguous forms.

        The right default for grammar counts: a token parsed ``nom./acc.``
        counts toward both nominatives and accusatives, because excluding
        syncretic forms would undercount both.
        """
        return self._features_incl.get((dimension, value), _EMPTY_TRACK)

    def feature(self, dimension: str, value: str) -> Track:
        """Tokens whose decoded value is exactly ``value``.

        Ambiguity is a distinct value here, so ``feature("case", "acc")``
        excludes ``"acc|nom"``. Use when the distinction itself matters.
        """
        return self._features.get((dimension, value), _EMPTY_TRACK)

    def iter_feature_values(self, dimension: str) -> Iterator[tuple[str, Track]]:
        """Every value seen in ``dimension``, sorted, ambiguity included.

        A grammar profile is this plus ``count_in``; note that a value never
        seen in the corpus is absent rather than zero, so callers reporting
        "0 futures" must supply the expected vocabulary themselves.

        These counts overlap and do not sum to a total. A token parsed
        ``nom./acc.`` is yielded under both ``"nom"`` and ``"acc"`` — correct for
        reading a row on its own, wrong for adding up, where it would push a case
        profile past the chapter's token count. The compound value ``"acc|nom"``
        is not yielded here at all; reach for ``feature()`` when the profile has
        to partition rather than overlap.
        """
        keys = sorted(value for dim, value in self._features_incl if dim == dimension)
        for value in keys:
            yield value, self._features_incl[(dimension, value)]

    def feature_dimension(self, dimension: str) -> Track:
        """Tokens whose label stated ``dimension`` at all, whatever the value.

        The denominator to print a per-value grammar breakdown against. Adding
        the values up instead would overcount: ``feature_any`` deliberately files
        a syncretic ``nom./acc.`` under both readings, so a case profile can sum
        past the number of tokens that actually carry a case.
        """
        return self._dimensions.get(dimension, _EMPTY_TRACK)

    def iter_feature_dimensions(self) -> Iterator[str]:
        return iter(FEATURE_DIMENSIONS)

    def signature(self, sig: str) -> Track:
        """One whole feature combination, e.g. ``"aor. act. part. nom. sg. masc."``."""
        return self._signatures.get(sig, _EMPTY_TRACK)

    def iter_signatures(self) -> Iterator[tuple[str, dict[str, str], Track]]:
        """Every combination attested anywhere in the corpus, with its features.

        Unordered — the paradigm ordering lives in ``morphology`` and is applied
        by whoever builds the table, because it is a display concern.

        Note this yields what the corpus *has*, not what Greek permits: the full
        cross product runs to thousands of cells that no text contains, so this
        dimension cannot be zero-filled the way a single feature can. A chapter
        table zero-fills against these attested rows instead.
        """
        for sig, track in self._signatures.items():
            yield sig, self._signature_features[sig], track

    def deponents(self) -> Track:
        """Tokens labelled deponent — middle in form, active in meaning.

        These are inside ``feature_any("voice", "mid")`` too, because that is what
        their morphology is. This track answers the separate lexical question, so
        a middle-voice count and a deponent count never have to be read off each
        other.
        """
        return self._deponents

    def descriptor(self, descriptor: str) -> Track:
        """Lexical labels that carry no morphology ("interrogative", "def. art.")."""
        return self._descriptors.get(descriptor, _EMPTY_TRACK)

    def iter_descriptors(self) -> Iterator[tuple[str, Track]]:
        for key in sorted(self._descriptors):
            yield key, self._descriptors[key]

    def morph_status(self, status: MorphStatus | str) -> Track:
        key = status.value if isinstance(status, MorphStatus) else status
        return self._morph_status.get(key, _EMPTY_TRACK)

    def coverage(self, chapter_index: int | None = None) -> MorphCoverage:
        """How much of the corpus the normalizer actually understood.

        Grammar counts are only as trustworthy as this. Report it alongside
        them rather than letting unparsed labels read as absence.
        """

        def count(track: Track) -> int:
            return track.total if chapter_index is None else track.count_in(chapter_index)

        total = (
            self.total_tokens
            if chapter_index is None
            else self.chapter_stats(chapter_index).token_count
        )
        return MorphCoverage(
            total=total,
            ok=count(self.morph_status(MorphStatus.OK)),
            partial=count(self.morph_status(MorphStatus.PARTIAL)),
            descriptive=count(self.morph_status(MorphStatus.DESCRIPTIVE)),
            not_applicable=count(self.morph_status(MorphStatus.NOT_APPLICABLE)),
            unparsed=count(self.morph_status(MorphStatus.UNPARSED)),
            verb_forms=count(self._verb_forms),
            verbs_missing_voice=count(self._voice_gaps),
        )

    # -- iteration ------------------------------------------------------
    #
    # Sorted so corpus-wide report output is deterministic. These are the entry
    # points for the cumulative summary table in issue #5 and for grouped
    # exports in issue #3.

    def iter_lemmas(self) -> Iterator[tuple[str, Track]]:
        for key in sorted(self._lemmas):
            yield key, self._lemmas[key]

    def iter_parses(self) -> Iterator[tuple[tuple[str, str], Track]]:
        for key in sorted(self._parses):
            yield key, self._parses[key]

    def iter_forms(self) -> Iterator[tuple[tuple[str, str], Track]]:
        for key in sorted(self._forms):
            yield key, self._forms[key]

    def iter_form_parses(self) -> Iterator[tuple[tuple[str, str, str], Track]]:
        for key in sorted(self._form_parses):
            yield key, self._form_parses[key]

    def iter_types(self) -> Iterator[tuple[str, Track]]:
        for key in sorted(self._types):
            yield key, self._types[key]

    def iter_facts(self) -> Iterator[TokenFact]:
        """Every token in the corpus, in reading order.

        The escape hatch for statistics that need something off the fact itself
        rather than off a count — the corpus-wide table in issue #5 uses it to
        pick each lemma's representative surface form and part of speech.
        """
        for chapter_index in self.chapter_indexes:
            yield from self._by_chapter[chapter_index]

    # -- corpus totals --------------------------------------------------

    @property
    def total_tokens(self) -> int:
        return sum(len(v) for v in self._by_chapter.values())

    @property
    def unique_lemmas(self) -> int:
        return len(self._lemmas)

    @property
    def unique_forms(self) -> int:
        return len(self._forms)

    @property
    def unique_parses(self) -> int:
        """Distinct (lemma, parse) pairs — the row count of the text-wide table."""
        return len(self._parses)

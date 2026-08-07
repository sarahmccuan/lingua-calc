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


class Track:
    """Appearance history of one key (a lemma, a form, a parse) across chapters.

    Built by repeated ``add`` calls during indexing, then frozen on first query.
    Chapter lists are kept sparse — a key that appears in 3 of 200 chapters
    stores 3 entries — and prefix-summed once so cumulative lookups are
    O(log n) rather than a rescan per row.
    """

    __slots__ = ("total", "_per_chapter", "_chapters", "_prefix")

    def __init__(self) -> None:
        self.total: int = 0
        self._per_chapter: dict[int, int] = {}
        self._chapters: tuple[int, ...] | None = None
        self._prefix: tuple[int, ...] | None = None

    def add(self, chapter_index: int, count: int = 1) -> None:
        self.total += count
        self._per_chapter[chapter_index] = self._per_chapter.get(chapter_index, 0) + count
        self._chapters = None
        self._prefix = None

    def _freeze(self) -> None:
        if self._chapters is not None:
            return
        chapters = tuple(sorted(self._per_chapter))
        prefix = [0]
        running = 0
        for c in chapters:
            running += self._per_chapter[c]
            prefix.append(running)
        self._chapters = chapters
        self._prefix = tuple(prefix)

    @property
    def chapters(self) -> tuple[int, ...]:
        """Chapter indexes this key appears in, ascending. Empty if never seen."""
        self._freeze()
        assert self._chapters is not None
        return self._chapters

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
        self._freeze()
        assert self._chapters is not None and self._prefix is not None
        return self._prefix[bisect_right(self._chapters, chapter_index)]

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

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from lingua_calc.corpus import CorpusIndex
from lingua_calc.models import ChapterReport, ChapterSummary, FormStat, TokenRow


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
    return ChapterReport(summary=summary, rows=rows)


def _chapter_or(value: int | None, fallback: int) -> int:
    """Coerce an optional chapter index.

    Keys are drawn from the chapter's own facts, so the track always has at
    least this chapter and ``value`` is never ``None`` in practice.
    """
    return fallback if value is None else value

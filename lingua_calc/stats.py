from __future__ import annotations

from collections import Counter

from lingua_calc.models import ChapterSummary, ChapterReport, ParsedToken, TokenRow


def build_chapter_report(
    chapter_id: str,
    title: str,
    tokens: list[ParsedToken],
    chapter_index: int,
    lemma_first_chapter: dict[str, int],
    lemma_last_chapter: dict[str, int],
    parse_first_chapter: dict[tuple[str, str], int],
    parse_last_chapter: dict[tuple[str, str], int],
) -> ChapterReport:
    lemma_counts: Counter[str] = Counter()
    parse_counts: Counter[tuple[str, str]] = Counter()
    form_key_counts: Counter[tuple[str, str, str]] = Counter()
    first_parse_index: dict[tuple[str, str], int] = {}
    first_form_index: dict[tuple[str, str], int] = {}
    last_parse_index: dict[tuple[str, str], int] = {}
    form_tokens: dict[tuple[str, str, str], ParsedToken] = {}

    for index, t in enumerate(tokens):
        lemma_counts[t.lemma] += 1
        parse_counts[(t.lemma, t.parse)] += 1
        form_key_counts[(t.lemma, t.parse, t.form)] += 1
        first_parse_index.setdefault((t.lemma, t.parse), index)
        first_form_index.setdefault((t.lemma, t.form), index)
        last_parse_index[(t.lemma, t.parse)] = index
        form_tokens.setdefault((t.lemma, t.parse, t.form), t)

    seen_groups = sorted(first_parse_index.keys(), key=lambda group: first_parse_index[group])
    rows: list[TokenRow] = []

    for lemma, parse in seen_groups:
        candidates = [((l, p, f), cnt) for (l, p, f), cnt in form_key_counts.items() if l == lemma and p == parse]
        if not candidates:
            continue
        candidates.sort(
            key=lambda x: (
                -x[1],
                first_form_index.get((lemma, x[0][2]), 0),
            )
        )
        (_, _, best_form), _ = candidates[0]
        source = form_tokens.get((lemma, parse, best_form))

        rows.append(
            TokenRow(
                type=source.type if source else "",
                lemma=lemma,
                form=best_form,
                parse=parse,
                lemma_occ=lemma_counts[lemma],
                parse_occ=parse_counts.get((lemma, parse), 0),
                first_occ_lemma=lemma_first_chapter.get(lemma) == chapter_index,
                first_occ_parse=parse_first_chapter.get((lemma, parse)) == chapter_index,
                last_occ_lemma=lemma_last_chapter.get(lemma) == chapter_index,
                last_occ_parse=parse_last_chapter.get((lemma, parse)) == chapter_index,
            )
        )

    unique_lemmas = len({t.lemma for t in tokens})
    unique_forms = len({(t.lemma, t.form) for t in tokens})

    summary = ChapterSummary(
        id=chapter_id,
        title=title,
        unique_lemmas=unique_lemmas,
        unique_forms=unique_forms,
    )
    return ChapterReport(summary=summary, rows=rows)

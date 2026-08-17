"""One-off: build the GNT lexicon CSV from jtauber/vocabulary-tools.

Run with UTF-8 mode on — `gnt_data` opens its token file without an explicit
encoding, so on Windows it decodes as cp1252 and dies:

    PYTHONUTF8=1 python prep_gnt.py

The checked-in CSV is the artefact; this is not wired into the app.
"""

import csv
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from gnt_data import get_tokens, TokenType

from lingua_calc import lexicon as lex_mod

OUT = Path(__file__).resolve().parent.parent / "data" / "lexicons" / "gnt-lemmas.csv"
ALIAS_OUT = OUT.with_name("gnt-lemmas.aliases.csv")

# MorphGNT part-of-speech codes, folded to the lexical/function split the Core
# 5k uses in its `kind` column so the two lists answer the same question. The
# closed classes are function words; nouns, verbs, adjectives and adverbs carry
# content. Interjections (18 tokens in the whole corpus) are counted lexical —
# ἰδού is a word a reader has to learn, not a grammatical joint.
POS_KIND = {
    "N-": "L",  # noun
    "V-": "L",  # verb
    "A-": "L",  # adjective
    "D-": "L",  # adverb
    "I-": "L",  # interjection
    "RA": "F",  # article
    "C-": "F",  # conjunction
    "P-": "F",  # preposition
    "RP": "F",  # personal pronoun
    "RD": "F",  # demonstrative
    "RR": "F",  # relative
    "RI": "F",  # interrogative / indefinite
    "X-": "F",  # particle
}

# `οὕτω(ς)`, `μέχρι(ς)` — MorphGNT cites a movable consonant in parentheses.
# The parens are not part of any spelling that appears in a text, so the entry
# takes the fuller form and the bare one becomes an alias; otherwise neither
# spelling a real text uses would ever match.
OPTIONAL_ENDING = re.compile(r"\(([^)]*)\)")


def main() -> None:
    lemmas = get_tokens(TokenType.lemma)
    poses = get_tokens(TokenType.pos)
    assert len(lemmas) == len(poses), "token streams must be parallel"

    counts = Counter(lemmas)
    pos_by_lemma: dict[str, Counter] = defaultdict(Counter)
    for lemma, pos in zip(lemmas, poses):
        pos_by_lemma[lemma][pos] += 1

    # Glosses are borrowed from the Core 5k where the two lists name the same
    # word. The GNT data carries no definitions at all, and an empty column is
    # worse than a borrowed one as long as the borrowing is stated. Where a key
    # names several 5k entries (its homograph digits), the best-ranked — most
    # frequent — sense wins, so the choice is deterministic rather than
    # whichever row happened to be read last.
    core = lex_mod.load(str(OUT.parent), "greek-core-5k")
    gloss_by_key: dict[str, tuple[int, str]] = {}
    for e in core.entries:
        key = lex_mod.match_key(e.lemma)
        if key not in gloss_by_key or e.rank < gloss_by_key[key][0]:
            gloss_by_key[key] = (e.rank, e.gloss)

    total = sum(counts.values())

    # Frequency desc, then lemma, so ranks are reproducible across runs rather
    # than depending on dict iteration order.
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    rows = []
    aliases = []
    cumulative = 0
    glossed = 0
    for rank, (raw, count) in enumerate(ordered, 1):
        lemma = unicodedata.normalize("NFC", OPTIONAL_ENDING.sub(r"\1", raw))
        if lemma != raw:
            bare = unicodedata.normalize("NFC", OPTIONAL_ENDING.sub("", raw))
            aliases.append([bare, lemma, "orthographic", "without the movable consonant"])

        cumulative += count
        gloss = gloss_by_key.get(lex_mod.match_key(lemma), (0, ""))[1]
        if gloss:
            glossed += 1
        pos = pos_by_lemma[raw].most_common(1)[0][0]
        rows.append([
            rank,
            lemma,
            gloss,
            POS_KIND.get(pos, ""),
            count,
            f"{100 * cumulative / total:.4f}",
        ])

    assert [r[0] for r in rows] == list(range(1, len(rows) + 1))
    assert len({r[1] for r in rows}) == len(rows), "lemmas must be unique after normalisation"

    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "lemma", "gloss", "kind", "ref_count", "ref_coverage"])
        w.writerows(rows)

    with ALIAS_OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["variant", "lemma", "kind", "note"])
        w.writerows(sorted(aliases))

    print(f"wrote {len(rows)} entries to {OUT}")
    print(f"  reference tokens {total}")
    print(f"  glossed from Core 5k: {glossed} ({glossed / len(rows):.1%})")
    print(f"  aliases: {len(aliases)} -> {ALIAS_OUT.name}")
    print(f"  final cumulative coverage {rows[-1][5]}%")


if __name__ == "__main__":
    main()

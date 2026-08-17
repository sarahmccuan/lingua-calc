"""One-off: turn the Google Sheet export into the checked-in lexicon CSV.

Kept out of the package on purpose — this ran once against a hand-curated
sheet, and the CSV it produced is the artefact under version control. Rerun
only if the sheet changes.
"""
import csv
import sys
import unicodedata
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "sheet_gid0.csv")
OUT = Path(__file__).resolve().parent.parent / "data" / "lexicons" / "greek-core-5k.csv"

RANK, LEMMA, COUNT, COVERAGE, GLOSS, KIND = 0, 1, 3, 4, 5, 6

rows = list(csv.reader(SRC.open(encoding="utf-8")))[1:]

out = []
for r in rows:
    lemma = unicodedata.normalize("NFC", r[LEMMA].strip())
    kind = r[KIND].strip()
    kind = kind if kind in ("L", "F") else ""
    out.append([
        int(r[RANK]),
        lemma,
        r[GLOSS].strip().replace("\n", " "),
        kind,
        int(r[COUNT]),
        r[COVERAGE].strip(),
    ])

out.sort(key=lambda x: x[0])
assert [x[0] for x in out] == list(range(1, len(out) + 1)), "ranks must be dense and 1-based"
assert len({x[1] for x in out}) == len(out), "lemmas must be unique"

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["rank", "lemma", "gloss", "kind", "ref_count", "ref_coverage"])
    w.writerows(out)

covered = sum(x[4] for x in out)
final_cov = float(out[-1][5]) / 100
print(f"wrote {len(out)} entries to {OUT}")
print(f"covered tokens {covered:,}; final cumulative coverage {final_cov:.4f}")
print(f"implied reference corpus size {round(covered / final_cov):,}")

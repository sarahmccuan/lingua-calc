"""Reference lexicons, and the business of matching a text's lemmas to them.

The other lenses ask what a text *contains*. This one asks what a text has
*taught*: given a ranked vocabulary list, how much of it has the author put in
front of a reader by the end of chapter N. The list is the goal; the text is
the thing being measured against it.

Two things live here, and only the second one is hard.

**A lexicon is a checked-in CSV**, not a database table. It is reference data:
small (5000 rows is ~400KB), immutable between edits, versioned in git, and
identical for every run. Putting it in SQLite would buy a join nothing performs
— every statistic in this codebase is derived in Python from a ``CorpusIndex``,
not in SQL — at the cost of a seeding step, a migration, and a cache that can
disagree with the file on disk. So: read the CSV, hold it in memory, cache by
id. Adding a lexicon is dropping a file beside ``manifest.json``.

**Matching is the part that can lie.** Bedrock returns a lemma; the list has a
headword; they agree most of the time and the disagreements are systematic. The
policy below is deliberately layered so that each layer can be audited
separately, and so that no layer can silently overrule a stricter one:

1. ``match_key`` — mechanical and safe. NFC, drop macrons and breves (the list's
   citation forms carry them, Bedrock's lemmas do not), strip the trailing digit
   that marks a homograph, lowercase.
2. an **alias file** — curated, human-edited, one row per known variant. This is
   where judgment lives, tagged by kind so a reader can tell an unarguable
   spelling variant from a decision to file an adverb under its adjective.
3. ``fold_key`` — the systematic Attic/Koine alternations (``-ττ-``/``-σσ-``,
   ``-ρρ-``/``-ρσ-``, ``γιγν-``/``γιν-``), tried last. Measured against the
   stored corpus these add ten types and not one false positive, because they
   preserve accents and breathings; the accent-stripping version of the same
   idea collapsed ``οὔ`` onto five different entries and was dropped.

What is left over after all three is reported, never hidden — see
``lemma_report`` callers and ``LexiconMatchReport``. A coverage figure is only
as honest as its match rate, the same reason ``CoverageReport`` travels beside
every grammar count.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Combining macron and breve. The list's GE-derived citation forms mark vowel
# quantity (`σῠ́`, `ῡ̔μός`); the provider never does, and quantity is not part of
# a lemma's identity for our purposes.
_QUANTITY_MARKS = "̄̆"

# Any combining mark, for patterns that must reach across an accented vowel.
_MARKS = r"[̀-ͯ]*"

# `ὅς2`, `ὅτι2` — the list disambiguates homographs with a trailing digit.
# The provider has no way to express that distinction, so it is stripped for
# matching and the resulting collision is reported rather than resolved.
_HOMOGRAPH_SUFFIX = re.compile(r"\d+$")

_GIGN = re.compile(r"γ(ι" + _MARKS + r")γν")


def match_key(lemma: str) -> str:
    """The strict key: two lemmas share one only if they are the same word.

    Safe enough to be the primary index. Everything it changes is either
    invisible to meaning (normal form, case) or an artefact of how the list
    cites its headwords (quantity marks, homograph digits).
    """
    decomposed = unicodedata.normalize("NFD", lemma.strip())
    stripped = "".join(c for c in decomposed if c not in _QUANTITY_MARKS)
    recomposed = unicodedata.normalize("NFC", stripped)
    return _HOMOGRAPH_SUFFIX.sub("", recomposed).strip().lower()


def fold_key(lemma: str) -> str:
    """``match_key`` plus the systematic dialect alternations.

    Accents and breathings survive on purpose. They are the only thing keeping
    ``τίς`` apart from ``τις`` and ``οὔ`` apart from ``οὖς``, and an earlier
    version of this function that stripped them turned a 0.6% coverage gain into
    a five-way false match on the commonest word in the language.
    """
    key = unicodedata.normalize("NFD", match_key(lemma))
    key = _GIGN.sub(r"γ\1ν", key)  # γίγνομαι -> γίνομαι
    key = unicodedata.normalize("NFC", key)
    return key.replace("ττ", "σσ").replace("ρρ", "ρσ")


def is_proper_noun(lemma: str) -> bool:
    """Whether a text lemma looks like a name.

    Used only as a **fallback for lemmas that did not match**, never as a filter
    applied first — that ordering is the whole of it. Classical frequency lists
    exclude proper nouns (`greek-core-5k` has zero capitalised headwords in
    5000), so counting names against a text measures it against a target that
    does not exist; in the stored Basil run that misjudgement would be 1,210
    tokens, 8.4% of the corpus. But `gnt-lemmas` *does* contain names, and there
    `Ἰησοῦς` must count as taught like any other word. Because lookup runs
    before this does, both behaviours fall out of the same code.

    The provider capitalises name lemmas (``Γρηγόριος``) and lowercases
    everything else, so the first character carries the distinction. Surface
    *forms* do not — one source text is set entirely in capitals — which is why
    this reads the lemma.
    """
    first = lemma[:1]
    return bool(first) and first.isalpha() and first != first.lower()


@dataclass(frozen=True)
class LexiconEntry:
    """One headword in a ranked vocabulary list."""

    rank: int = 0
    lemma: str = ""
    gloss: str = ""
    kind: str = ""
    ref_count: int = 0
    ref_coverage: float = 0.0

    @property
    def is_function_word(self) -> bool:
        return self.kind == "F"


@dataclass(frozen=True)
class LexiconMeta:
    """What the UI needs to offer a lexicon in a dropdown, without loading it."""

    id: str
    name: str
    short_name: str
    description: str
    entry_count: int
    reference_tokens: int
    source: str = ""
    is_default: bool = False


@dataclass(frozen=True)
class Alias:
    variant: str
    lemma: str
    kind: str
    note: str


# How a text lemma reached its entry. Carried through to the match report so a
# reader can see how much of the coverage rests on judgment rather than identity.
EXACT = "exact"
ALIAS = "alias"
FOLDED = "folded"


@dataclass(frozen=True)
class Match:
    """A text lemma resolved against the list.

    ``entries`` is plural because of the homograph digits: ``ὅς`` and ``ὅς2`` are
    two entries the provider cannot tell apart, so a text using either is
    credited with both. That over-credits by at most the 51 colliding keys in
    this list — about 1% — and the alternative under-credits by making those
    entries permanently unreachable, which is the worse lie because it silently
    caps the coverage a text can ever reach. ``ambiguous`` marks the cases so the
    report can say how often it happened.
    """

    entries: tuple[LexiconEntry, ...]
    how: str

    @property
    def ambiguous(self) -> bool:
        return len(self.entries) > 1


class Lexicon:
    """A ranked vocabulary list, indexed for lemma lookup.

    Entries are held in rank order and never re-sorted: rank *is* the list's
    meaning, and every band, curve and "what to teach next" question below reads
    down it.
    """

    def __init__(
        self,
        meta: LexiconMeta,
        entries: list[LexiconEntry],
        aliases: list[Alias] | None = None,
    ) -> None:
        self.meta = meta
        self.entries: tuple[LexiconEntry, ...] = tuple(entries)
        self.aliases: tuple[Alias, ...] = tuple(aliases or ())

        self._by_match: dict[str, list[LexiconEntry]] = {}
        self._by_fold: dict[str, list[LexiconEntry]] = {}
        for entry in self.entries:
            self._by_match.setdefault(match_key(entry.lemma), []).append(entry)
            self._by_fold.setdefault(fold_key(entry.lemma), []).append(entry)

        # An alias points at a headword, so it is resolved once here rather than
        # on every lookup. One that names a lemma the list does not contain is
        # dropped with a warning: a typo in a hand-edited file should cost that
        # row, not the whole lexicon.
        self._by_alias: dict[str, list[LexiconEntry]] = {}
        for alias in self.aliases:
            target = self._by_match.get(match_key(alias.lemma))
            if not target:
                logger.warning(
                    "Alias %r in %s points at %r, which is not in the list; ignoring it",
                    alias.variant,
                    meta.id,
                    alias.lemma,
                )
                continue
            self._by_alias.setdefault(match_key(alias.variant), []).extend(target)

        self._total_ref_count = sum(e.ref_count for e in self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def lookup(self, lemma: str) -> Match | None:
        """Resolve a text lemma, strictest rule first, or ``None``."""
        key = match_key(lemma)
        hit = self._by_match.get(key)
        if hit:
            return Match(tuple(hit), EXACT)
        hit = self._by_alias.get(key)
        if hit:
            return Match(tuple(dict.fromkeys(hit)), ALIAS)
        hit = self._by_fold.get(fold_key(lemma))
        if hit:
            return Match(tuple(hit), FOLDED)
        return None

    def ref_share(self, entry: LexiconEntry) -> float:
        """This entry's share of the reference corpus, as a fraction.

        The weight behind "how much Greek does knowing this word unlock". Read
        against ``meta.reference_tokens`` — the corpus the list was counted on —
        not against the list's own total, because the list stops at rank 5000 and
        the tail it omits is real text a reader still has to get through.
        """
        if not self.meta.reference_tokens:
            return 0.0
        return entry.ref_count / self.meta.reference_tokens

    @property
    def total_ref_share(self) -> float:
        """What the whole list is worth — its final cumulative coverage."""
        if not self.meta.reference_tokens:
            return 0.0
        return self._total_ref_count / self.meta.reference_tokens


# -- loading ----------------------------------------------------------------
#
# Lexicons are immutable reference data, so they are read once per process and
# cached forever. The lock guards the manifest read as well, which is what
# `available()` walks on every page load of the UI.

_lock = threading.Lock()
_cache: dict[tuple[str, str], Lexicon] = {}

MANIFEST_NAME = "manifest.json"


def _manifest_path(directory: Path) -> Path:
    return directory / MANIFEST_NAME


def _read_manifest(directory: Path) -> list[dict]:
    path = _manifest_path(directory)
    if not path.is_file():
        logger.warning("No lexicon manifest at %s; the lexicon lens will be empty", path)
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Could not read the lexicon manifest at %s", path)
        return []
    return [row for row in data.get("lexicons", []) if row.get("id")]


def _count_entries(path: Path) -> int:
    """How many entries the file will yield, by the same rule that reads it.

    Counting physical lines is cheaper and was wrong: it credits the blank-lemma
    rows `_read_entries` skips, and a quoted gloss containing a newline is two
    lines and one entry. This number is the size the picker advertises and the
    denominator `LexiconRef.entry_count` carries, so it has to agree with
    `len(lexicon)` rather than approximate it. Still one streaming pass, no
    `LexiconEntry` built.
    """
    with path.open(encoding="utf-8", newline="") as fh:
        return sum(1 for row in csv.DictReader(fh) if (row.get("lemma") or "").strip())


def _meta_from(row: dict, directory: Path) -> LexiconMeta | None:
    entries_file = directory / row.get("entries_file", "")
    if not entries_file.is_file():
        logger.warning("Lexicon %s names a missing file %s; skipping it", row["id"], entries_file)
        return None
    return LexiconMeta(
        id=row["id"],
        name=row.get("name", row["id"]),
        short_name=row.get("short_name", row.get("name", row["id"])),
        description=row.get("description", ""),
        entry_count=_count_entries(entries_file),
        reference_tokens=int(row.get("reference_tokens", 0)),
        source=row.get("source", ""),
        is_default=bool(row.get("default")),
    )


def available(directory: str | Path) -> list[LexiconMeta]:
    """Every lexicon the manifest offers, default first.

    Cheap by design — it counts rows rather than building entries — because the
    UI calls it to fill a dropdown before anything has been selected.
    """
    path = Path(directory)
    metas = [m for row in _read_manifest(path) if (m := _meta_from(row, path))]
    metas.sort(key=lambda m: (not m.is_default, m.name))
    return metas


def default_id(directory: str | Path) -> str | None:
    metas = available(directory)
    return metas[0].id if metas else None


def _read_entries(path: Path) -> list[LexiconEntry]:
    entries: list[LexiconEntry] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            lemma = (row.get("lemma") or "").strip()
            if not lemma:
                continue
            entries.append(
                LexiconEntry(
                    rank=int(row["rank"]),
                    lemma=unicodedata.normalize("NFC", lemma),
                    gloss=(row.get("gloss") or "").strip(),
                    kind=(row.get("kind") or "").strip(),
                    ref_count=int(row.get("ref_count") or 0),
                    ref_coverage=float(row.get("ref_coverage") or 0.0),
                )
            )
    entries.sort(key=lambda e: e.rank)
    return entries


def _read_aliases(path: Path) -> list[Alias]:
    if not path.is_file():
        return []
    aliases: list[Alias] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            variant = (row.get("variant") or "").strip()
            lemma = (row.get("lemma") or "").strip()
            if not variant or not lemma:
                continue
            aliases.append(
                Alias(
                    variant=unicodedata.normalize("NFC", variant),
                    lemma=unicodedata.normalize("NFC", lemma),
                    kind=(row.get("kind") or "").strip(),
                    note=(row.get("note") or "").strip(),
                )
            )
    return aliases


def load(directory: str | Path, lexicon_id: str | None = None) -> Lexicon | None:
    """Read a lexicon by id, or the default one. Cached per process.

    Returns ``None`` for an unknown id rather than raising, so a stale id in a
    bookmarked URL degrades to "pick a lexicon" instead of a 500.
    """
    path = Path(directory)
    rows = _read_manifest(path)
    if not rows:
        return None

    if lexicon_id is None:
        metas = available(path)
        if not metas:
            return None
        lexicon_id = metas[0].id

    row = next((r for r in rows if r["id"] == lexicon_id), None)
    if row is None:
        return None

    cache_key = (str(path.resolve()), lexicon_id)
    with _lock:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached

    meta = _meta_from(row, path)
    if meta is None:
        return None
    entries = _read_entries(path / row["entries_file"])
    aliases = _read_aliases(path / row["aliases_file"]) if row.get("aliases_file") else []
    lexicon = Lexicon(meta, entries, aliases)

    with _lock:
        _cache[cache_key] = lexicon
    return lexicon


def clear_cache() -> None:
    """Drop cached lexicons. For tests, and for editing an alias file in place."""
    with _lock:
        _cache.clear()

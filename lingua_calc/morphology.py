"""Normalize free-text parse labels into typed morphological features.

The provider returns ``parse`` as a compact human label (``"pres. act. ind.
3sg"``). Counting grammar from that string directly does not work: across 31,902
logged tokens the same fact appeared under four spellings (``pres. act. ind.
3sg``, ``pres. ind. 3sg``, ``pres. ind. 3sg.``, ``pres. act. ind. 3sg.``),
gender alternated between ``fem.`` and ``f.``, and 28% of verb tokens omitted
voice entirely. A query like "how many passives this chapter" was not merely
awkward, it was unanswerable, and "0 futures" was indistinguishable from "futures
the model spelled differently".

So parsing is deliberately *tolerant*: it accepts the drift already sitting in
stored runs rather than assuming the prompt is obeyed. The prompt was tightened
too (see ``nlp/bedrock.py``), but the normalizer is the safety net, and it is the
thing that lets a stored run be re-counted after this module improves.

Two rules the rest of the codebase depends on:

- ``None`` means *the label did not say*, never "not applicable". A verb with
  ``voice=None`` is a data gap, and ``MorphStatus`` makes that visible instead of
  letting it silently count as zero.
- Genuine ambiguity is preserved, not resolved. Greek syncretism (``nom./acc.``)
  becomes the canonical value ``"acc|nom"`` rather than a coin flip between them.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field

# Feature dimensions, in conventional citation order. This tuple is the
# authority — CorpusIndex builds one track per dimension from it.
FEATURE_DIMENSIONS: tuple[str, ...] = (
    "tense",
    "voice",
    "mood",
    "case",
    "number",
    "gender",
    "person",
    "degree",
)


class MorphStatus(str, Enum):
    """How much of a parse label the normalizer understood.

    Reported per token so coverage is auditable: a grammar count is only as
    trustworthy as the share of tokens that reached ``OK``.
    """

    OK = "ok"
    """Every part of the label mapped to a known feature."""

    PARTIAL = "partial"
    """Some features were recognised, some text was not (e.g. "dat. sg. (incomplete)")."""

    DESCRIPTIVE = "descriptive"
    """A lexical label carrying no morphology ("interrogative", "negative particle")."""

    NOT_APPLICABLE = "not_applicable"
    """The provider explicitly declined to parse, i.e. "-" or empty."""

    UNPARSED = "unparsed"
    """Nothing was recognised ("abbreviation for πρῶτον")."""


class Morphology(BaseModel):
    """Typed features decoded from one ``parse`` label.

    Every dimension is optional because absence is real information — see the
    module docstring. Ambiguous values are ``|``-joined and sorted, so
    ``nom./acc.`` is always ``"acc|nom"`` regardless of how it was written.
    """

    tense: str | None = None
    voice: str | None = None
    mood: str | None = None
    case: str | None = None
    number: str | None = None
    gender: str | None = None
    person: str | None = None
    degree: str | None = None

    status: MorphStatus = MorphStatus.NOT_APPLICABLE
    is_deponent: bool = Field(
        default=False,
        description=(
            'The label called this form deponent — middle in form, active in meaning. '
            "``voice`` carries the form ('mid'), which is what a morphology count "
            "measures; this flag carries the lexical class, so the two stay separately "
            "answerable rather than one being inferred from the other."
        ),
    )
    descriptors: list[str] = Field(
        default_factory=list,
        description='Lexical labels carrying no morphology, e.g. "def. art.", "interrogative".',
    )
    unparsed: list[str] = Field(
        default_factory=list,
        description="Label fragments the normalizer did not recognise. Non-empty means the label needs attention.",
    )

    def features(self) -> dict[str, str]:
        """Only the dimensions this label actually stated."""
        return {dim: value for dim in FEATURE_DIMENSIONS if (value := getattr(self, dim)) is not None}

    def has(self, dimension: str, value: str) -> bool:
        """Whether this token counts toward ``value``, including ambiguously.

        ``nom./acc.`` counts as both a nominative and an accusative here, which
        is the right default for "how many accusatives are in this chapter" —
        excluding syncretic forms would undercount.
        """
        actual = getattr(self, dimension, None)
        return actual is not None and value in actual.split("|")

    @property
    def is_verbal(self) -> bool:
        return self.tense is not None or self.mood is not None or self.voice is not None

    @property
    def is_nominal(self) -> bool:
        return self.case is not None


# --- lookup tables ---------------------------------------------------------
#
# Keys are matched after lowercasing and stripping trailing periods, so one
# entry covers "Aor.", "aor" and "aor.". Spelled-out variants are included
# because the model produced them (123 tokens said "definite article" where
# 2,370 said "def. art.").

_FEATURE_TOKENS: dict[str, tuple[str, str]] = {}


def _register(dimension: str, value: str, *spellings: str) -> None:
    for spelling in spellings:
        _FEATURE_TOKENS[spelling] = (dimension, value)


_register("case", "nom", "nom", "nominative")
_register("case", "gen", "gen", "genitive")
_register("case", "dat", "dat", "dative")
_register("case", "acc", "acc", "accusative")
_register("case", "voc", "voc", "vocative")

_register("number", "sg", "sg", "sing", "singular")
_register("number", "pl", "pl", "plur", "plural")
_register("number", "dual", "dual", "du")

# "m."/"f."/"n." are the short gender forms the model mixed in alongside
# "masc."/"fem."/"neut." (161 tokens said "nom. sg. f.").
_register("gender", "masc", "masc", "m", "masculine")
_register("gender", "fem", "fem", "f", "feminine")
_register("gender", "neut", "neut", "n", "neuter")

_register("tense", "pres", "pres", "present")
_register("tense", "impf", "impf", "imperf", "imperfect")
_register("tense", "fut", "fut", "future")
_register("tense", "aor", "aor", "aorist")
_register("tense", "perf", "perf", "perfect")
_register("tense", "plup", "plup", "pluperf", "pluperfect")

_register("voice", "act", "act", "active")
_register("voice", "mid", "mid", "middle")
_register("voice", "pass", "pass", "passive")
# "mp"/"midpass" is genuine mid/pass ambiguity — two readings, neither settled.
# "deponent" is not ambiguous at all and is handled separately below: it is
# middle in form, so it resolves to `mid` rather than being counted as a passive
# the text does not contain.
_register("voice", "mid|pass", "mp", "midpass")

# "part." is participle here, per the prompt's abbreviation list. The word
# "particle" is a part of speech and is handled as a descriptor below — the
# distinction rests on that trailing "icle", so keep both spellings explicit.
_register("mood", "ind", "ind", "indic", "indicative")
_register("mood", "subj", "subj", "subjunctive")
_register("mood", "opt", "opt", "optative")
_register("mood", "imp", "imp", "imper", "imperative")
_register("mood", "inf", "inf", "infin", "infinitive")
_register("mood", "part", "part", "ptc", "participle")

_register("degree", "comp", "comp", "compar", "comparative")
_register("degree", "superl", "superl", "sup", "superlative")

# The bare digit matters: the prompt that produced the stored runs asked for
# persons as "1 2 3", so labels like "pres. act. ind. 3 sg." are already on disk.
# Registering only the ordinal would drop their person *and* push them out of
# `OK`, understating coverage on exactly the data this module exists to absorb.
_register("person", "1", "1", "1st")
_register("person", "2", "2", "2nd")
_register("person", "3", "3", "3rd")

# Lexical labels that are not morphology. Recognising them keeps a token out of
# the "unparsed" bucket, which would otherwise overstate how bad the data is.
_DESCRIPTOR_TOKENS: dict[str, str] = {
    "interrogative": "interrogative",
    "interrog": "interrogative",
    "negative": "negative",
    "neg": "negative",
    "affirmative": "affirmative",
    "affirm": "affirmative",
    "demonstrative": "demonstrative",
    "dem": "demonstrative",
    "relative": "relative",
    "rel": "relative",
    "reflexive": "reflexive",
    "refl": "reflexive",
    "possessive": "possessive",
    "poss": "possessive",
    "indefinite": "indefinite",
    "indef": "indefinite",
    "indeclinable": "indeclinable",
    "indecl": "indeclinable",
    # All three spellings land on one descriptor. Splitting "adv." from "adverb"
    # would recreate the spelling-drift undercount this module removes.
    "adv": "adverb",
    "adverb": "adverb",
    "adverbial": "adverb",
    "particle": "particle",
    "conj": "conjunction",
    "prep": "preposition",
    "abbreviation": "abbreviation",
    "proper": "proper noun",
}

# Deponency is two facts at once: the form is middle, the meaning is active.
# Folding it into `voice` alone loses the lexical class; leaving voice unset
# loses the morphology and reports a voice gap that is not one. So it sets both
# — voice "mid" and the `is_deponent` flag — and "how many middles" and "how
# many deponents" stay independently answerable.
_DEPONENT_TOKENS = {"deponent", "depon", "dep"}

# Multi-word forms collapsed before tokenizing, so "definite article" and
# "def. art." land on one descriptor. Every multi-word label has to come through
# here: `_DESCRIPTOR_TOKENS` is consulted per whitespace-split word, so a key
# containing a space there could never match.
_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdef(?:inite)?\.?\s+art(?:icle)?\.?", re.I), " \x00def.art "),
    (re.compile(r"\bindef(?:inite)?\.?\s+art(?:icle)?\.?", re.I), " \x00indef.art "),
    (re.compile(r"\binterrog(?:ative)?\.?\s+adv(?:erb)?\.?", re.I), " \x00interrog.adv "),
    (re.compile(r"\binterrog(?:ative)?\.?\s+part(?:icle)?\.?", re.I), " \x00interrog.particle "),
    (re.compile(r"\bneg(?:ative)?\.?\s+part(?:icle)?\.?", re.I), " \x00negative.particle "),
    (re.compile(r"\baffirm(?:ative)?\.?\s+part(?:icle)?\.?", re.I), " \x00affirmative.particle "),
    (re.compile(r"\bproper\s+n(?:oun)?\.?", re.I), " \x00proper.noun "),
    # "noun" spelled out only — "indecl. n." would be an unresolvable clash with
    # the neuter abbreviation.
    (re.compile(r"\bindecl(?:inable)?\.?\s+noun\.?", re.I), " \x00indecl.noun "),
    # "abbreviation for πρῶτον" — the label is a lexical note, and the Greek word
    # that follows is not a feature, so consume the whole tail.
    (re.compile(r"\babbrev(?:iation)?\.?\s+(?:for\s+)?\S*", re.I), " \x00abbreviation "),
)

_PHRASE_DESCRIPTORS: dict[str, str] = {
    "\x00def.art": "def. art.",
    "\x00indef.art": "indef. art.",
    "\x00interrog.adv": "interrogative",
    "\x00interrog.particle": "interrogative",
    "\x00negative.particle": "negative",
    "\x00affirmative.particle": "affirmative",
    "\x00proper.noun": "proper noun",
    "\x00indecl.noun": "indeclinable",
    "\x00abbreviation": "abbreviation",
}

# "3sg", "3sg.", "1pl" — person and number fused into one token.
_PERSON_NUMBER = re.compile(r"^([123])(sg|pl|s|p)$")

_NOT_APPLICABLE = {"", "-", "--", "n/a", "na", "none", "unknown", "?"}


def _clean(token: str) -> str:
    """Strip the punctuation the model applied inconsistently.

    321 logged tokens carried a trailing period the prompt did not ask for
    ("pres. ind. 3sg."), which is why matching happens on the stripped form.
    """
    return token.strip().strip(".,;:").strip()


def parse_morphology(parse: str, token_type: str = "") -> Morphology:
    """Decode one parse label into typed features.

    ``token_type`` is the provider's part of speech. It is optional, and is used
    only to settle "part.", which is the participle abbreviation but also how the
    model shortens the particle part of speech.
    """
    raw = (parse or "").strip()
    if raw.lower() in _NOT_APPLICABLE:
        return Morphology(status=MorphStatus.NOT_APPLICABLE)

    working = raw
    phrase_descriptors: list[str] = []
    for pattern, placeholder in _PHRASES:
        if pattern.search(working):
            working = pattern.sub(placeholder, working)
    # Distinct sentinels can share a descriptor ("interrog. adv." and "interrog.
    # part." are both "interrogative"), so de-duplicate here the way the
    # single-token path does — otherwise CorpusIndex counts the token twice.
    for sentinel, descriptor in _PHRASE_DESCRIPTORS.items():
        if sentinel in working:
            if descriptor not in phrase_descriptors:
                phrase_descriptors.append(descriptor)
            working = working.replace(sentinel, " ")

    found: dict[str, list[str]] = {}
    descriptors: list[str] = list(phrase_descriptors)
    unparsed: list[str] = []
    is_deponent = False

    for word in working.split():
        cleaned = _clean(word)
        if not cleaned:
            continue

        # A slash marks genuine ambiguity ("nom./acc.", "m./n."). Each side is
        # resolved separately and the results merge into one value.
        alternatives = [_clean(part) for part in cleaned.split("/") if _clean(part)]
        if not alternatives:
            continue

        matched_any = False
        unmatched: list[str] = []
        for alternative in alternatives:
            key = alternative.lower()

            if match := _PERSON_NUMBER.match(key):
                person, number = match.groups()
                _add(found, "person", person)
                _add(found, "number", "sg" if number.startswith("s") else "pl")
                matched_any = True
                continue

            if key in _DEPONENT_TOKENS:
                is_deponent = True
                _add(found, "voice", "mid")
                matched_any = True
                continue

            if key in _FEATURE_TOKENS:
                dimension, value = _FEATURE_TOKENS[key]
                _add(found, dimension, value)
                matched_any = True
                continue

            if key in _DESCRIPTOR_TOKENS:
                descriptor = _DESCRIPTOR_TOKENS[key]
                if descriptor not in descriptors:
                    descriptors.append(descriptor)
                matched_any = True
                continue

            unmatched.append(alternative)

        if not matched_any:
            unparsed.append(word.strip())
        elif unmatched:
            # One side of an ambiguity resolved and the other did not. Reporting
            # only the readable half would claim full coverage while silently
            # discarding a reading — the opposite of preserving ambiguity.
            unparsed.extend(unmatched)

    # "part." means participle, except when the provider already told us the
    # token is a particle and the label states nothing else verbal — then it is
    # the part of speech abbreviated, and reading it as a mood would invent
    # participles that are not in the text.
    if (
        token_type == "particle"
        and found.get("mood") == ["part"]
        and not (found.keys() & {"tense", "voice", "person"})
    ):
        del found["mood"]
        if "particle" not in descriptors:
            descriptors.append("particle")

    # Split before rejoining: a collected value can already be compound ("mp"
    # decodes to "mid|pass"), and a label pairing it with an atomic reading of
    # the same dimension would otherwise nest into "mid|mid|pass".
    fields = {
        dimension: "|".join(sorted({reading for value in values for reading in value.split("|")}))
        for dimension, values in found.items()
    }

    if fields and unparsed:
        status = MorphStatus.PARTIAL
    elif fields:
        status = MorphStatus.OK
    elif descriptors and not unparsed:
        status = MorphStatus.DESCRIPTIVE
    elif descriptors:
        status = MorphStatus.PARTIAL
    else:
        status = MorphStatus.UNPARSED

    return Morphology(
        **fields,
        status=status,
        is_deponent=is_deponent,
        descriptors=descriptors,
        unparsed=unparsed,
    )


def _add(found: dict[str, list[str]], dimension: str, value: str) -> None:
    found.setdefault(dimension, []).append(value)


def is_verb_form(morph: Morphology, token_type: str = "") -> bool:
    """Whether this token is one where voice is expected.

    The prompt requires voice on *every* verb form, participles and infinitives
    included, so there is no finite/non-finite carve-out here — a voiceless
    participle is as much a gap as a voiceless indicative. Recognised either
    from the label (it states a tense or a mood) or from the provider's part of
    speech, because labels omit as readily as types do.

    This is the population ``missing_voice`` measures against; keep the two
    together so the voice-gap ratio can never exceed 1.0.
    """
    return token_type == "verb" or morph.tense is not None or morph.mood is not None


def missing_voice(morph: Morphology, token_type: str = "") -> bool:
    """Whether a verb form failed to state its voice.

    This was 28% of verb tokens in the logged sample, and it is the reason a
    voice query needs a coverage figure printed next to it.
    """
    return morph.voice is None and is_verb_form(morph, token_type)

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field, model_validator

from lingua_calc.morphology import Morphology, parse_morphology


class ParsedToken(BaseModel):
    type: str = Field(description="Part of speech or category, e.g. noun, verb, particle, punctuation, other")
    lemma: str
    form: str = Field(description="Surface form as it appears in the text")
    parse: str = Field(description="Short morphological summary, e.g. nom. sg., pres. act. ind.")


class TokenFact(ParsedToken):
    """One token occurrence, located in the corpus.

    This is the grain every statistic is derived from. Provider output
    (``ParsedToken``) plus the position it was found at. Keep this record
    lossless — anything dropped here cannot be recovered without paying for
    another Bedrock run.
    """

    filename: str
    chapter_index: int = Field(description="Corpus-wide chapter position, 0-based, reading order")
    chapter_id: str
    chapter_title: str
    position: int = Field(description="Token position within its chapter, 0-based, reading order")

    morph: Morphology = Field(
        default_factory=Morphology,
        description="Typed features decoded from `parse`. Always derived, never supplied.",
    )

    @model_validator(mode="after")
    def _derive_morphology(self) -> "TokenFact":
        """Recompute ``morph`` from ``parse`` on every construction.

        Deliberately ignores any supplied value, so morphology is a pure
        function of the label and can never go stale. This is what lets an
        improvement to ``morphology.py`` re-count an already-stored run: reload
        the facts and the features are current.
        """
        self.morph = parse_morphology(self.parse, self.type)
        return self

    @property
    def lemma_key(self) -> str:
        return self.lemma

    @property
    def parse_key(self) -> tuple[str, str]:
        return (self.lemma, self.parse)

    @property
    def form_key(self) -> tuple[str, str]:
        return (self.lemma, self.form)

    @property
    def form_parse_key(self) -> tuple[str, str, str]:
        return (self.lemma, self.parse, self.form)


class FormStat(BaseModel):
    """One surface form within a (lemma, parse) group, scoped to a chapter.

    Before this existed, ``build_chapter_report`` picked the most frequent form
    per group and discarded the rest, which made per-form statistics
    (issues #4/#5) underivable and let ``ChapterSummary.unique_forms`` report
    more forms than the table had rows to show.
    """

    form: str
    occ: int = Field(description="Occurrences of this exact form in this chapter")
    first_position: int = Field(description="Token position of its first appearance in this chapter")


class TokenRow(ParsedToken):
    """A displayed chapter row, at (lemma, parse) grain.

    ``form`` is the group's most frequent surface form — a representative, not a
    key. The full breakdown is in ``forms``.
    """

    chapter_index: int

    # Counts within this chapter.
    lemma_occ: int
    parse_occ: int
    form_occ: int = Field(description="Occurrences of the representative `form` in this chapter")
    forms: list[FormStat] = Field(default_factory=list)

    # Counts from the start of the corpus through this chapter (issues #4/#14).
    # Read alongside the `*_occ` above, the pair says both "how much of this is
    # here" and "how much of it the reader has met by now" — which is the
    # repetition question, not a second copy of the same number.
    lemma_cum: int = 0
    parse_cum: int = 0

    # Corpus-wide first/last appearance, as chapter indexes. Stored as indexes
    # rather than booleans so "which chapter" (issue #5) and "how many chapters
    # since" (CONTEXT.md item 4) stay derivable; the booleans below are just a
    # projection of these for the current UI.
    lemma_first_chapter: int
    lemma_last_chapter: int
    parse_first_chapter: int
    parse_last_chapter: int
    form_first_chapter: int
    form_last_chapter: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def first_occ_lemma(self) -> bool:
        return self.lemma_first_chapter == self.chapter_index

    @computed_field  # type: ignore[prop-decorator]
    @property
    def first_occ_parse(self) -> bool:
        return self.parse_first_chapter == self.chapter_index

    @computed_field  # type: ignore[prop-decorator]
    @property
    def last_occ_lemma(self) -> bool:
        return self.lemma_last_chapter == self.chapter_index

    @computed_field  # type: ignore[prop-decorator]
    @property
    def last_occ_parse(self) -> bool:
        return self.parse_last_chapter == self.chapter_index


class ChapterSummary(BaseModel):
    id: str
    title: str
    chapter_index: int = 0
    unique_lemmas: int
    unique_forms: int
    token_count: int = 0


class GrammarStat(BaseModel):
    """One grammatical feature value, counted in some scope.

    The scope is whatever built it: a chapter, or the whole text. ``occ`` is the
    count inside that scope and ``cumulative`` the running total through it, so
    at text scope the two are equal.

    A row exists even when ``occ`` is zero — that is the point. "0 future
    tenses" is a fact about the passage; the absence of a future row would only
    be a fact about the index.
    """

    dimension: str
    value: str
    label: str
    occ: int
    cumulative: int
    first_chapter: int | None = Field(
        default=None,
        description="Corpus-wide first appearance; equal to the chapter index means this grammar is new here.",
    )
    last_chapter: int | None = None
    chapter_count: int = Field(default=0, description="Distinct chapters this feature appears in")


class GrammarGroup(BaseModel):
    """One feature dimension's profile — a tense profile, a case profile.

    ``stated`` is the group's denominator and is **not** the sum of its rows.
    Ambiguity is counted under every reading it admits, so a syncretic
    ``nom./acc.`` adds to both the nominative and the accusative row while being
    one token here. Summing the rows instead would let a case profile exceed the
    chapter's token count.
    """

    dimension: str
    label: str
    stats: list[GrammarStat]
    stated: int = Field(description="Tokens in scope whose label stated this dimension at all")
    stated_cumulative: int = 0


class FormCombination(BaseModel):
    """One whole paradigm cell — "aor. act. part. nom. sg. masc." — and its count.

    The complement to ``GrammarGroup``: that one asks "how many aorists", this
    one asks "which forms are in play". A learner meets forms, not features, so
    an aorist middle participle and an aorist active indicative are two things
    to introduce even though the per-dimension tables count them as one aorist
    each.
    """

    form: str
    occ: int
    cumulative: int
    first_chapter: int | None = None
    last_chapter: int | None = None
    chapter_count: int = 0
    order: int = Field(
        description="Position in paradigm order; sorting on this reads down a conjugation, not an alphabet."
    )


class FormCombinationGroup(BaseModel):
    """One table's worth of combinations — the verbs, or the participles.

    Split because a single ranked list interleaves paradigms that were never
    meant to be compared: an author asking "which tenses am I using" and one
    asking "which cases am I using" are reading different tables, and a
    participle belongs to neither.
    """

    key: str
    label: str
    hint: str = ""
    rows: list[FormCombination] = Field(default_factory=list)
    tokens: int = Field(default=0, description="Tokens in scope in this class — the sum of `occ`")
    tokens_cumulative: int = 0


class FormCombinationTable(BaseModel):
    """Every combination the corpus attests, counted in one scope.

    Rows are the corpus's inventory, not the scope's, so a chapter carries a
    zero row for a form the text uses elsewhere — that is "no aorist
    participles *here*", which is the question. It cannot be zero-filled the way
    a single feature can: the full cross product of Greek's features is
    thousands of cells almost none of which any text contains, so what the
    corpus attests is the only bounded row set available.

    ``tokens`` is a true total, unlike ``GrammarGroup.stated``: each token has
    exactly one combination, so these rows partition and do sum — across the
    groups as well as within them.
    """

    groups: list[FormCombinationGroup] = Field(default_factory=list)
    tokens: int = Field(default=0, description="Tokens in scope carrying morphology — the sum of every group")
    tokens_cumulative: int = 0


class CombinationCount(BaseModel):
    """One chapter's two counts for one row of the corpus inventory.

    The inventory itself travels once, on ``TextReport.form_combinations``.
    Everything that identifies a row — the form label, its paradigm position,
    the chapters it spans — is corpus-wide by construction and therefore
    *identical in every chapter*, so a chapter sends only the two numbers that
    can actually differ and the client joins them on ``order``. Shipping the
    whole table per chapter cost chapters x signatures duplicated rows, which
    on a 60-chapter text was the second largest section of the payload.

    A row absent from a chapter's list is (0, 0) — a form the text introduces
    later. Rows that are zero *here* but nonzero so far still travel, because
    ``cumulative`` is the number the chapter view reads beside them.
    """

    order: int = Field(description="Position in `FormCombinationTable`, the join key")
    occ: int
    cumulative: int


class CoverageReport(BaseModel):
    """How much morphology was actually decoded in this scope.

    Shipped next to every grammar count on purpose. A label the normalizer could
    not read is otherwise indistinguishable from grammar the text does not
    contain, and "0 futures" is only trustworthy to the extent this is high.
    """

    total: int
    understood: int
    needs_attention: int
    not_applicable: int
    understood_share: float
    verb_forms: int
    verbs_missing_voice: int
    voice_gap_share: float


class ChapterReport(BaseModel):
    summary: ChapterSummary
    rows: list[TokenRow]
    grammar: list[GrammarGroup] = Field(
        default_factory=list,
        description="Per-dimension profile for this chapter (issue #14's form summary)",
    )
    combination_counts: list[CombinationCount] = Field(
        default_factory=list,
        description=(
            "The same grammar as whole paradigm cells rather than separate features, "
            "as counts against the inventory on TextReport.form_combinations"
        ),
    )
    coverage: CoverageReport | None = None


class ChapterRefOut(BaseModel):
    """Chapter identity, so a table of chapter *indexes* can be read as titles."""

    chapter_index: int
    id: str
    title: str
    filename: str


class TextRow(BaseModel):
    """One corpus-wide vocabulary row (issue #5 / the text tab in #15).

    The same shape serves both grains the tab toggles between: ``parse`` is
    empty on a lemma row. Keeping one model means one renderer and one sort,
    and means the two grains cannot drift into disagreeing about what a column
    called "total" means.
    """

    type: str
    lemma: str
    parse: str = Field(default="", description="Empty on lemma-grain rows")
    form: str = Field(description="Most frequent surface form in this row's scope, a representative")
    total: int
    form_count: int = Field(description="Distinct surface forms this row covers")
    first_chapter: int
    last_chapter: int
    chapter_count: int = Field(description="Distinct chapters this appears in — not last minus first")


class TextSummary(BaseModel):
    chapter_count: int
    token_count: int
    unique_lemmas: int
    unique_forms: int
    unique_parses: int


class TextReport(BaseModel):
    """The whole corpus as one lens, spanning every uploaded file.

    Chapter indexes are corpus-wide and files are ordered before indexing, so
    "cumulative" already means across the whole upload rather than within a
    file. This report is that view made explicit.
    """

    summary: TextSummary
    chapters: list[ChapterRefOut]
    lemma_rows: list[TextRow]
    parse_rows: list[TextRow]
    grammar: list[GrammarGroup] = Field(default_factory=list)
    form_combinations: FormCombinationTable | None = None
    coverage: CoverageReport | None = None


class FileReport(BaseModel):
    filename: str
    chapters: list[ChapterReport]


class DocumentReport(BaseModel):
    filename: str
    chapters: list[ChapterReport]


class MultiFileReport(BaseModel):
    """Response when analyzing multiple .docx files; tracks vocabulary progression across all."""

    file_reports: list[FileReport]
    text_report: TextReport | None = Field(
        default=None,
        description="Corpus-wide view across every file in the run; powers the text tab.",
    )
    run_id: str | None = Field(
        default=None,
        description="Row id in the local store; lets later reporting re-derive stats without re-running Bedrock.",
    )


class AnalyzeError(BaseModel):
    error: str
    detail: str | None = None

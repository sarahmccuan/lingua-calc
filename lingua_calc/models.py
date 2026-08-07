from __future__ import annotations

from pydantic import BaseModel, Field, computed_field


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


class ChapterReport(BaseModel):
    summary: ChapterSummary
    rows: list[TokenRow]


class FileReport(BaseModel):
    filename: str
    chapters: list[ChapterReport]


class DocumentReport(BaseModel):
    filename: str
    chapters: list[ChapterReport]


class MultiFileReport(BaseModel):
    """Response when analyzing multiple .docx files; tracks vocabulary progression across all."""

    file_reports: list[FileReport]
    run_id: str | None = Field(
        default=None,
        description="Row id in the local store; lets later reporting re-derive stats without re-running Bedrock.",
    )


class AnalyzeError(BaseModel):
    error: str
    detail: str | None = None

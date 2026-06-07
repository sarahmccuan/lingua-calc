from pydantic import BaseModel, Field


class ParsedToken(BaseModel):
    type: str = Field(description="Part of speech or category, e.g. noun, verb, particle, punctuation, other")
    lemma: str
    form: str = Field(description="Surface form as it appears in the text")
    parse: str = Field(description="Short morphological summary, e.g. nom. sg., pres. act. ind.")


class TokenRow(ParsedToken):
    lemma_occ: int
    parse_occ: int
    first_occ_lemma: bool
    first_occ_parse: bool
    last_occ_lemma: bool
    last_occ_parse: bool


class ChapterSummary(BaseModel):
    id: str
    title: str
    unique_lemmas: int
    unique_forms: int


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


class AnalyzeError(BaseModel):
    error: str
    detail: str | None = None

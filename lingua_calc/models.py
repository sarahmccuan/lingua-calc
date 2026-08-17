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


class ChapterProgress(BaseModel):
    """One chapter split into vocabulary met for the first time and vocabulary
    already known — the stacked bars on the text tab (issue #15).

    Built at both grains, because "new" means different things at each: a lemma
    the reader met in chapter 1 turning up in the aorist in chapter 9 is
    *repeated* vocabulary and a *new* form, and which of those a chapter is full
    of is the question the toggle exists to answer.

    Types and tokens are both carried because they answer different halves of
    it. Types are the vocabulary load — how many words to introduce; tokens are
    how much of the running text those words account for. A chapter with 5 new
    lemmas used 40 times is not the chapter its type count describes.

    ``new_tokens + repeated_tokens`` is the chapter's token count exactly, at
    either grain: every token has one lemma and one parse, so these partition.
    ``new_types + repeated_types`` is its distinct-key count, which is
    ``unique_lemmas`` at lemma grain.
    """

    chapter_index: int
    new_types: int = Field(description="Keys appearing here for the first time in the corpus")
    repeated_types: int = Field(description="Keys present here that the reader has already met")
    new_tokens: int = Field(description="Occurrences here of keys new to this chapter")
    repeated_tokens: int = Field(description="Occurrences here of keys met earlier")


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
    lemma_progress: list[ChapterProgress] = Field(
        default_factory=list,
        description="Per-chapter new-vs-repeated split at lemma grain, one entry per chapter in reading order",
    )
    parse_progress: list[ChapterProgress] = Field(
        default_factory=list,
        description="The same split at lemma+parse grain",
    )
    grammar: list[GrammarGroup] = Field(default_factory=list)
    form_combinations: FormCombinationTable | None = None
    coverage: CoverageReport | None = None


# -- lemma lens (issue #16) -------------------------------------------------
#
# The third lens narrows to one word: every parse it wears, every chapter it
# lives in, and every place it is actually used. Unlike the other two this is
# not part of the report payload — a concordance for every lemma is the token
# stream over again — so it is fetched per lemma and built on demand.


class LemmaParseRow(BaseModel):
    """One of a lemma's parses, counted across the whole corpus.

    The "break out by parses" half of issue #16, at the same grain the text
    lens's lemma+parse rows use, so a figure here matches the one there.
    """

    parse: str
    type: str
    occ: int
    form: str = Field(description="Most frequent surface form carrying this parse — a representative")
    form_count: int = Field(description="Distinct spellings this parse appears as")
    first_chapter: int
    last_chapter: int
    chapter_count: int


class LemmaFormRow(BaseModel):
    """One surface spelling of a lemma, with the parses it is used for.

    The transpose of ``LemmaParseRow``: a form can carry several parses
    (``λόγου`` is only genitive, but ``λόγοι`` is nominative or vocative), and an
    author asking "which endings has this word appeared in" is reading this
    table rather than the one above.
    """

    form: str
    occ: int
    parses: list[str] = Field(
        default_factory=list,
        description="Every parse this spelling carries here, most frequent first",
    )
    first_chapter: int
    last_chapter: int
    chapter_count: int


class LemmaParseCount(BaseModel):
    """One parse's count inside one chapter — the "w/ parses" of issue #16's
    second bullet, which asks for the chapter list broken down rather than as a
    bare tally."""

    parse: str
    occ: int


class LemmaChapterRow(BaseModel):
    """One chapter's share of a lemma — **including the chapters that have none**.

    Every chapter in the corpus gets a row. A lemma's distribution is as much
    about where it stops appearing as where it appears, and a list of only the
    chapters that contain it cannot show a gap; ``gap_before`` names the size of
    one on the row that ends it.
    """

    chapter_index: int
    occ: int
    cumulative: int = Field(description="Occurrences from the start of the corpus through this chapter")
    gap_before: int | None = Field(
        default=None,
        description="Chapters since its previous appearance. None on the first appearance and on chapters with no occurrence — a gap needs two ends.",
    )
    parses: list[LemmaParseCount] = Field(default_factory=list)


class LemmaOccurrence(BaseModel):
    """One token of the lemma, with the words either side of it.

    ``before``/``after`` are neighbouring **tokens, not a sentence**. The
    provider is asked not to emit punctuation (``nlp/bedrock.py``), so the fact
    stream carries no sentence boundary to cut on and a fixed window is the
    honest unit available. It never crosses a chapter boundary, and it is short
    of the window at the start and end of a chapter rather than padded.
    """

    chapter_index: int
    position: int
    form: str
    parse: str
    before: list[str] = Field(default_factory=list)
    after: list[str] = Field(default_factory=list)


class LemmaSummary(BaseModel):
    lemma: str
    type: str = Field(description="Most frequent part of speech the provider gave this lemma")
    total: int
    parse_count: int
    form_count: int
    chapter_count: int = Field(description="Distinct chapters it appears in — not last minus first")
    first_chapter: int
    last_chapter: int
    longest_gap: int | None = Field(
        default=None,
        description="Most chapters that ever passed between two appearances; None if it appears in only one",
    )
    corpus_tokens: int = Field(description="Tokens in the whole run, so a share can be shown against it")
    corpus_chapters: int


class LemmaReport(BaseModel):
    """Everything the lemma lens shows for one word.

    Built per request rather than shipped with the run: the occurrence list is
    the token stream sliced a different way, so carrying one for every lemma
    would roughly double a payload that is already megabytes.
    """

    summary: LemmaSummary
    parses: list[LemmaParseRow]
    forms: list[LemmaFormRow]
    chapters: list[LemmaChapterRow]
    chapter_refs: list[ChapterRefOut] = Field(
        default_factory=list,
        description="Chapter identity, so the tables above can be read as titles",
    )
    occurrences: list[LemmaOccurrence] = Field(default_factory=list)
    occurrences_total: int = Field(
        description="Occurrences in scope. Larger than len(occurrences) when the limit truncated the list — the caller must say so rather than presenting a page as the whole."
    )
    occurrence_limit: int
    context_window: int = Field(description="Tokens carried either side of each occurrence")
    chapter_filter: int | None = Field(
        default=None,
        description="Chapter the occurrence list was narrowed to; None means the whole corpus",
    )


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


# -- lexicon lens -----------------------------------------------------------
#
# The fourth lens turns the question around. The other three ask what a text
# contains; this one fixes a ranked vocabulary list as the goal and asks how
# much of it the text has taught, chapter by chapter. The text is the variable
# and the list is the constant, which is why every headline figure below has the
# list in its denominator.
#
# Like the lemma lens this is fetched per request rather than shipped with the
# report: it is a join against reference data the report knows nothing about,
# and a 5000-row table has no business riding along with runs nobody will open
# it for.


class LexiconRef(BaseModel):
    """One lexicon, as the picker needs it — no entries, so listing is cheap."""

    id: str
    name: str
    short_name: str
    description: str = ""
    entry_count: int
    reference_tokens: int = Field(
        description="Size of the corpus the list's frequencies were counted on — the denominator behind every share"
    )
    source: str = ""


class LexiconChapterOcc(BaseModel):
    """One chapter's occurrences of one entry. Sparse: chapters with none are absent.

    Nested inside the entry rather than shipped as a parallel table because a
    covered entry appears in a handful of chapters out of sixty, so the sparse
    form is an order of magnitude smaller than the dense one and needs no join.
    """

    chapter_index: int
    occ: int


class LexiconEntryRow(BaseModel):
    """One list entry and what the text has done with it.

    ``occ == 0`` is the interesting case as often as not: it is a word the goal
    list says matters that the text has not yet used, and the rows are ordered by
    rank so the untaught ones surface exactly where their frequency puts them.
    """

    rank: int
    lemma: str
    gloss: str = ""
    kind: str = Field(default="", description="L lexical / F function, as the list classifies it; may be blank")
    ref_count: int
    ref_share: float = Field(description="This entry's share of the reference corpus, as a fraction")

    occ: int = Field(default=0, description="Occurrences in the text, whole-corpus")
    first_chapter: int | None = Field(default=None, description="Where the text first teaches it; None if never")
    chapter_count: int = 0
    chapters: list[LexiconChapterOcc] = Field(default_factory=list)

    matched_by: str = Field(
        default="",
        description="How the text lemma reached this entry: exact, alias, folded — blank if untaught",
    )
    ambiguous: bool = Field(
        default=False,
        description="Credited via a homograph key the provider cannot disambiguate",
    )
    source_lemma: str = Field(
        default="",
        description=(
            "The text lemma that reached this entry, commonest first where several did. "
            "Blank if untaught. Differs from `lemma` wherever the match rested on a "
            "homograph digit, an alias or a fold, which is why the UI links this and not the headword"
        ),
    )


class LexiconBand(BaseModel):
    """A slice of the list by rank — 1-500, 501-1000, and so on.

    The shape of the progress, not just its size. A text covering 300 entries
    spread evenly through 5000 and one covering the first 300 are the same
    number and completely different pedagogy, and only the banding tells them
    apart.
    """

    start: int
    end: int
    entries: int
    covered: int
    ref_share: float = Field(description="What this band is worth in the reference corpus")
    ref_share_covered: float = Field(description="How much of that the text has claimed")


class LexiconChapterProgress(BaseModel):
    """One chapter's contribution to the goal.

    ``new_entries`` is the teaching rate — entries this chapter is the first to
    use. ``cumulative_entries`` is the total a reader has met by the end of it,
    which is the curve the tab leads with.

    The token split says what the chapter spent its words on: vocabulary on the
    list, vocabulary off it, and names. Names are their own bucket because a
    list may well have no entry for them — a classical frequency list has none
    at all — and counting those as off-list charges a text for words it could
    never have got credit for. A name the list *does* contain (``gnt-lemmas``
    has hundreds) matches like any other word and never reaches this bucket.
    """

    chapter_index: int
    new_entries: int
    cumulative_entries: int
    new_ref_share: float
    cumulative_ref_share: float
    tokens: int
    tokens_on_list: int
    tokens_off_list: int
    tokens_proper: int

    # The chapter's own vocabulary, split by whether it serves the goal and by
    # whether the reader is meeting it for the first time. Counted in the
    # *text's* words rather than in list entries, because only then do the four
    # numbers partition the chapter exactly — an entry can be reached by two
    # spellings and a spelling can credit two entries, so entries do not.
    #
    # "Off-list" here **includes proper nouns**, unlike `tokens_off_list` above.
    # The chart folds them in; the headline and the match report keep them
    # apart. Both readings are wanted, so both are carried rather than one being
    # re-derived and getting it subtly wrong.
    new_on_list_types: int = Field(default=0, description="On-list lemmas whose first appearance anywhere is here")
    new_off_list_types: int = Field(default=0, description="Unmatched lemmas, names included, first appearing here")
    new_on_list_tokens: int = Field(default=0, description="Occurrences here of the on-list lemmas new to this chapter")
    new_off_list_tokens: int = Field(default=0, description="Occurrences here of the off-list lemmas new to this chapter")
    tokens_off_list_with_names: int = Field(
        default=0, description="tokens_off_list + tokens_proper — the chart's off-list total"
    )


class LexiconGapRow(BaseModel):
    """A high-value entry the text has not taught yet.

    Ordered by rank, so this reads as a worklist: the commonest words in the
    language that this text still gives a reader no exposure to.
    """

    rank: int
    lemma: str
    gloss: str = ""
    kind: str = ""
    ref_share: float


class OffListRow(BaseModel):
    """A word the text teaches that the list does not ask for.

    Not a criticism — a text needs names, and a real author needs words outside
    any core list. It is here because vocabulary load spent off-list is load a
    reader carries without progressing toward the goal, and an author deciding
    whether that trade is worth it needs to see which words they are.
    """

    lemma: str
    type: str = ""
    occ: int
    first_chapter: int
    chapter_count: int
    proper: bool = Field(
        default=False,
        description="Looks like a name, and this list has no entry for it — so it is set aside rather than counted against the text",
    )


class LexiconMatchReport(BaseModel):
    """How much of the text the matcher could actually place.

    Shipped beside every figure in this lens for the same reason
    ``CoverageReport`` rides along with the grammar counts: a lemma the matcher
    failed to place is indistinguishable from a word genuinely off the list, and
    "this text covers 26% of the 5000" is only trustworthy to the extent that
    ``unmatched_tokens`` is small.
    """

    text_lemmas: int = Field(description="Distinct lemmas in the text")
    matched_lemmas: int
    exact: int
    alias: int = Field(description="Placed via the curated alias file — judgment, not identity")
    folded: int = Field(description="Placed via a dialect fold such as -ττ-/-σσ-")
    ambiguous: int = Field(description="Placed on a key that names more than one entry")

    unmatched_lemmas: int
    unmatched_tokens: int
    proper_lemmas: int = Field(description="Unmatched but capitalised, so excluded from the off-list count")
    proper_tokens: int

    tokens: int
    tokens_on_list: int


class LexiconSummary(BaseModel):
    """The headline: what fraction of the goal this text has delivered.

    Two numbers, because they answer different questions and a text can be
    strong on one and weak on the other. ``covered`` is how many words of the
    list a reader meets — the size of the vocabulary the text builds.
    ``ref_share_covered`` weights each of those by how common it actually is, so
    it reads as "a reader who learns everything this text teaches can then
    recognise this share of running Greek". A text teaching 300 of the top 500
    beats one teaching 600 scattered rarities on the second measure and loses on
    the first.
    """

    entries: int
    covered: int
    covered_share: float

    ref_share_covered: float
    ref_share_total: float = Field(description="What the whole list is worth — the ceiling for the figure above")

    tokens: int
    tokens_on_list: int
    tokens_off_list: int
    tokens_proper: int
    on_list_share: float = Field(description="Share of the text's tokens that are list vocabulary")

    chapter_count: int


class LexiconReport(BaseModel):
    """The lexicon lens over one run, against one list."""

    lexicon: LexiconRef
    summary: LexiconSummary
    bands: list[LexiconBand] = Field(default_factory=list)
    progress: list[LexiconChapterProgress] = Field(default_factory=list)
    entries: list[LexiconEntryRow] = Field(default_factory=list)
    gaps: list[LexiconGapRow] = Field(default_factory=list)
    gaps_total: int = Field(default=0, description="Entries the text never uses; `gaps` is the head of them")
    off_list: list[OffListRow] = Field(default_factory=list)
    off_list_total: int = Field(default=0, description="Distinct off-list lemmas; `off_list` is the head of them")
    match: LexiconMatchReport
    chapter_refs: list[ChapterRefOut] = Field(default_factory=list)

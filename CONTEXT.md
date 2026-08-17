Supplemental software for developing a greek learner text. LGPSI in this case. 

"What are the main problems you have / things you do manually today / things you would like to do but don't b/c it's too hard?

Really I want to know things like:
1. how many unique words (both lemma and forms) in this chapter?
2. first occurence of a word or a token
3. how often each word and token repeats per chapter
4. I'd probably like to track how long since the last occurence of a word, which I think will become important over the long haul
5. I'd probably also like to track things like "occurrences of aorists in a chapter", and track grammar/morphology across the project.


layout: image.png

## Implementation (prototype)

- **Stack:** Python 3.10+, FastAPI serves `ui/` + `POST /api/analyze`; **Amazon Bedrock** (Claude Sonnet 4.5 default, `BEDROCK_MODEL_ID`) for lemma/form/parse JSON; `python-docx` for text; Heading 1 splits chapters.
- **Run:** `pip install -e .` then `python -m lingua_calc` → browser at `http://127.0.0.1:8765/`.
- **Streaming:** `/api/analyze` returns newline-delimited JSON (`progress` events per chapter as they finish, then a final `result`/`error`). The blocking analysis runs in a worker thread; its progress callback feeds the async response via a `queue.Queue`. UI shows live per-chapter progress + an elapsed timer.
- **Next:** Phase 2 in-app AWS credentials for non-technical users; PyInstaller Win/Mac builds; optional CLTK provider behind same interface.

## Data model (issue #6)

Statistics are derived in one direction, from one grain:

```
provider → TokenFact  →  CorpusIndex  →  TokenRow / ChapterReport
           (store.py)     (corpus.py)      (stats.py)
```

- **`TokenFact` is the grain and the source of truth** — one row per token occurrence, carrying `filename`, `chapter_index`, `chapter_id`, `position` alongside the provider's `type/lemma/form/parse`. Nothing is aggregated here. Anything dropped at this layer can only be recovered by paying for another Bedrock run, so keep it lossless.
- **`CorpusIndex` (`corpus.py`) is the only place counting happens.** Built once per run from the fact stream; replaces the four parallel dicts that used to be threaded positionally into `build_chapter_report`. Lookups are typed by dimension — `index.lemma(l)`, `index.parse(l, p)`, `index.form(l, f)`, `index.form_parse(l, p, f)`, `index.token_type(t)` — and each returns a `Track`.
- **`Track` answers the recurring questions** for whatever key you looked up: `total`, `count_in(ch)`, `cumulative_through(ch)`, `first_chapter`, `last_chapter`, `chapter_count` (distinct chapters), `previous_chapter(ch)`, `next_chapter(ch)`, `gap_before(ch)`. Chapter lists are sparse and prefix-summed once, so cumulative lookups are O(log n) instead of a rescan per row.
- **Adding a statistic should mean calling the index, not adding a `Counter`.** Issue #4's cumulative columns are `cumulative_through`; issue #5's first/last/unique-chapters are `first_chapter`/`last_chapter`/`chapter_count`; "occurrences of aorists per chapter" is `iter_parses()` filtered on the parse label; "how long since last occurrence" is `gap_before`.

Two consequences worth knowing:

- **Form-level data survives.** `TokenRow` is still at (lemma, parse) grain, and `row.form` is still the most frequent surface form — but it is a *representative*, not a key, and the full breakdown is on `row.forms`. Previously the other forms were discarded, which made per-form stats underivable and let `unique_forms` report more forms than the table had rows to account for.
- **Occurrence is stored as a chapter index, not a boolean.** `lemma_first_chapter`, `parse_last_chapter`, `form_first_chapter`, … are ints. The `first_occ_*`/`last_occ_*` booleans the UI reads are pydantic computed fields projected from them, so the JSON shape is unchanged.

**Persistence (`store.py`).** Every run is written to SQLite (`LINGUA_DB_PATH`, default `data/lingua_calc.sqlite3`; `LINGUA_PERSIST_RUNS=false` to disable) with its `model_id`, so reporting can be re-derived offline via `pipeline.reports_from_run(run_id)` and two model runs over the same text are comparable — which is what the Haiku-vs-Sonnet benchmark in issue #1 needs. Persistence failures are logged and swallowed: a read-only install degrades to "no history", never to a lost run. SQLite is stdlib, so this costs nothing in the PyInstaller build.

**Deliberately not pandas.** At learner-text scale (order 100k tokens) `Counter`/`dict` is milliseconds; a dataframe dependency would add real weight to the packaged build and buy nothing. What was wrong was the shape, not the arithmetic.

## Run history

The store was write-only from the app's point of view — runs accumulated but nothing read them back, so history existed only for a Python caller. Four routes in `app.py` are that surface, and `ui/` renders it as a collapsed panel above the report:

| route | purpose |
| --- | --- |
| `GET /api/runs?limit=&offset=` | one page of runs, newest first, plus `total` |
| `GET /api/runs/{id}/report` | re-derive a stored run — no Bedrock call |
| `GET /api/runs/{id}/lemma?lemma=&chapter=&limit=` | one lemma's lens over a stored run (issue #16) |
| `DELETE /api/runs/{id}` | remove one run (API surface; the UI does not use it) |
| `POST /api/runs/delete` | remove a selection, one VACUUM for the batch |

Because this is authoring software, **the same filename is re-run constantly and the store only ever appends** — `save_run` mints a fresh uuid per run and never upserts, so every draft survives. The cost is that two runs of `Chapter 5.docx` are distinguishable only by timestamp: nothing records *which version* was analyzed. The `note` column exists and is still unused; a content hash plus a label is the intended fix, and until then the timestamp is the identifier the UI leads with.

Four decisions worth not re-litigating:

- **Paged at 10, with `total` alongside.** An unpaged list silently ended at its limit, which reads as "that's all of them". Any truncation the UI shows must say what it dropped.
- **`ORDER BY created_at DESC, id DESC`.** Windows' clock granularity (~15 ms) lets back-to-back runs share a timestamp to the microsecond. Without the tiebreak the sort is unstable, which under paging can show one run twice and hide another.
- **Deleting is selection-then-confirm, never a per-row button.** The confirm lists every run by timestamp and filename rather than a count — losing track of what is in a selection is the whole risk — and selection resets on page change so a bulk delete can never remove rows the user cannot see.
- **Delete vacuums, once per batch.** A bare `DELETE` frees pages to SQLite's freelist and leaves the file the same size, so "deleted" would reclaim nothing visible. VACUUM rewrites the file, so it runs once for the whole batch rather than per run. It runs *after* the commit and its failures are logged and swallowed: raising there would report "delete failed" for rows already gone.

**A chapter that produced no tokens does not survive the round trip — deliberately.** Chapter identity rides on the fact rows, so a heading-only section has nowhere to be recorded and does not come back when the run is re-derived. That is the right answer: a chapter with no tokens is not a chapter, and none of the statistics this store exists to serve have anything to say about it. **Do not add a chapters table to preserve them.** The asymmetry that remains runs the other way — the fresh-analysis path passes `chapters=` from the placements it still holds, so it renders an empty card the reloaded report correctly omits. Cosmetic, and on the live side; not worth chasing.

## Export (issue #3)

Every row of the run-history table carries an **Export XLSX** button beside its **View** button. One workbook, two sheets, because it answers two different questions.

**`Rows`** — the flat grain: **one row per chapter × lemma × parse**, the rows the report table shows, with the file and chapter they belong to spliced in so the whole corpus lands in a single sheet that pivots.

| group | columns |
| --- | --- |
| identity | `file`, `chapter_index`, `chapter_id`, `chapter_title` |
| row | `type`, `lemma`, `form`, `parse` |
| counts (this chapter) | `lemma_occ`, `parse_occ`, `form_occ` |
| occurrence (this chapter) | `first_occ_lemma`, `first_occ_parse`, `last_occ_lemma`, `last_occ_parse` |
| occurrence (corpus-wide) | `lemma_first_chapter`, `lemma_last_chapter`, `parse_first_chapter`, `parse_last_chapter` |

**`New lemmas by chapter`** — only the rows where a lemma appears for the first time in the corpus, collapsed to **one row per lemma**. A lemma is new exactly once, so this sheet is the vocabulary the text introduces, in the order it introduces it.

| group | columns |
| --- | --- |
| identity | `file`, `chapter_index`, `chapter_id`, `chapter_title` |
| word | `type`, `lemma`, `lemma_occ` (in this chapter) |
| shapes met here | `form_count`, `forms`, `parse_count`, `parses` |
| afterwards | `lemma_last_chapter`, `chapter_span` |

- **On the run, not on the report.** Exporting is a per-run action, so any stored run can be pulled without first rendering it, and doing so does not disturb whatever report is already on screen. The cost: with `LINGUA_PERSIST_RUNS=false` the history panel is hidden entirely, so there is no export path at all — re-enable persistence to export.
- **Built server-side now, by `GET /api/runs/{id}/export.xlsx` (`export.py`).** The CSV this replaces was assembled in the page from `GET /api/runs/{id}/report`, which an xlsx cannot be: it is a zip of XML parts, and hand-rolling a zip writer in a page script to save one round trip is a bad trade. The report is still re-derived from the store, so an export costs a rebuild and no provider call. What the move buys beyond the zip: both grains are now testable in the same suite as the statistics they carry (`tests/test_export.py`).
- **Fetched as a blob, not navigated to.** A navigation would surface a failed export as a blank tab or a saved error page; fetching keeps it in the status line beside the button that asked. The counts come back as `X-Export-Rows` / `X-Export-New-Lemmas` headers so the status line can name both sheets without reading back the workbook it just saved.
- **The second sheet is derivable from the first** — filter `first_occ_lemma`, then dedupe the parse rows down to the lemma — and is a sheet anyway, because that is a pivot the reader would rebuild every time, and "what does this chapter introduce" is the question the whole cumulative apparatus exists to answer.
- **`forms` comes from `TokenRow.forms`, not from `form`.** `form` is only the group's most frequent spelling, so a lemma met as `Ὁ` and `ὁ` under one parse would lose one of them. Joined with ` · ` rather than a comma: a cell full of commas reads as a CSV that failed to split.
- **`chapter_span` counts this chapter through `lemma_last_chapter` inclusive**, so `1` means the word is introduced and never seen again — the number an author scanning for one-offs is looking for.
- **The `*_chapter` columns are the model's raw 0-based indexes**, left uncooked so they compare against `chapter_index`: a row is a lemma's first appearance exactly when `lemma_first_chapter == chapter_index`. The booleans beside them are that comparison already done, because "is this the first time?" is the question actually being asked.
- **No BOM, no codepage, no line endings.** xlsx stores its strings as UTF-8 inside the zip, so the CSV's BOM-and-CRLF workarounds for Excel's mojibake are gone with it.
- **The filename goes out twice**, plain ASCII and RFC 5987 (`filename*`), because the source .docx may be named in Greek and the ASCII copy has had that stripped — `βίβλος.docx` exports as `lingua-calc-βίβλος-…xlsx` to anything modern and `lingua-calc--…xlsx` to anything else.
- **Report order, not screen order.** The column sort is a reading aid; a spreadsheet re-sorts anyway.
- **Not yet:** per-chapter or per-file downloads, a tab per chapter, and the form-level breakdown on `TokenRow.forms` at full grain (a third sheet, not more columns on the first).

Note `chapter_id` is near-useless for Greek titles: `pipeline._slugify` strips non-ASCII, so `Κεφάλαιον α’` becomes `1--`. It is exported anyway because it is the id the rest of the system keys by; `chapter_index` is the column to join on.

## Grammar / morphology (issue #7)

`parse` is a compact human label, and the model does not write it consistently. Across 31,902 logged tokens the same fact appeared under four spellings (`pres. act. ind. 3sg` / `pres. ind. 3sg` / `pres. ind. 3sg.` / `pres. act. ind. 3sg.`), gender alternated `fem.`/`f.`, articles alternated `def. art.`/`definite article`, and **28% of verb tokens omitted voice entirely**. Counting grammar by matching that string undercounts silently, and "0 futures" becomes indistinguishable from "futures spelled differently".

So `morphology.py` decodes each label into typed features — tense, voice, mood, case, number, gender, person, degree — and `CorpusIndex` indexes them as a real dimension:

```python
index.feature_any("tense", "aor").count_in(chapter)      # aorists in a chapter
index.feature_any("tense", "aor").cumulative_through(ch) # …running total
index.iter_feature_values("mood")                        # the grammar profile
```

Three rules the layer is built on:

- **`None` means the label did not say — never "not applicable".** A verb with `voice=None` is a data gap, not an active verb, and defaulting it would fabricate data.
- **Ambiguity is preserved, not resolved.** Greek syncretism (`nom./acc.`) becomes the canonical value `"acc|nom"`. `feature_any` counts it toward *both* nominative and accusative (right for "how many accusatives"); `feature` matches the exact value (use when the distinction matters).
- **Coverage is reportable.** `MorphStatus` tags every token `ok` / `partial` / `descriptive` / `not_applicable` / `unparsed`, and `index.coverage()` summarises it. Grammar counts should be shown with this next to them, so an unreadable label never reads as absence of that grammar.

**The prompt was tightened too** (`nlp/bedrock.py`): fixed field order, mandatory voice on every verb form, explicit gender abbreviations (previously the prompt never specified them at all, which is exactly why `fem.`/`f.` drifted), no trailing period, slash for genuine ambiguity. The normalizer stays deliberately tolerant regardless — it is the safety net for drift and for runs already in the store.

**Not structured JSON fields.** Emitting `{"tense": "pres", "voice": "act", …}` per token would roughly double output tokens, and output generation is the documented bottleneck (see below) — directly against issue #1. A strict canonical label plus a tolerant decoder gets the same data at the current token cost.

Measured on the stored `Basil To the Rich` run (4,213 tokens): 100% of morphology-bearing tokens decoded, 0 partial, 0 unparsed, 0.2% voice gap. Re-validating is `store.load_index(run_id).coverage()`.

**Improving the normalizer re-counts old runs for free.** `TokenFact.morph` is always derived from `parse` on construction, so reloading a stored run picks up new abbreviations automatically. The `feat_*` columns in SQLite are a denormalized convenience for ad-hoc SQL (`select feat_tense, count(*) … group by 1`), never read back into a fact; `store.reindex_run(run_id)` refreshes them.

**Tests:** `pip install -e ".[dev]"` then `pytest`. `tests/conftest.py` has a `WordProvider` stand-in and an in-memory `.docx` builder, so the pipeline is testable without touching Bedrock.

## Lenses (issues #14 / #15 / #16, plus the lexicon tab)

The report is three tabs over one index, not three reports: **Chapters** (is *this* chapter right?), **Text** (is the whole progression right?), **Lemma** (where does *this word* live?).

Tabs are lazy — the text table can be a couple of thousand rows, and building every lens on open would make the author pay for ones they never look at. The selected tab is module-level state in `ui/app.js`, so loading a stored run keeps you in the lens you were reading.

**File grouping lives inside the Chapters tab only.** Chapter indexes are corpus-wide and files are natural-sorted before indexing, so every cumulative figure already spans the whole upload; the text lens is one text even when the upload was several documents.

### Chapter tab

Two additions to the existing table, both straight off `Track`:

- **`parse cum` / `lemma cum`** (issue #4) are `cumulative_through(chapter_index)`. Shown *beside* the per-chapter counts rather than instead of them, because the repetition question needs the pair: 3 occurrences of a word met 40 times already is a very different chapter from 3 occurrences of a word met twice.
- **A grammatical form summary** (#14's "summary of the forms"), one card per feature dimension, plus a full form-combination table below them. "Forms" here means grammar — tense, mood, case — not surface spellings, which are already on `TokenRow.forms`.

### The grammar profile, and why `FEATURE_VALUES` exists

`CorpusIndex` cannot answer issue #7's question on its own. A value the corpus never contains is *absent* from the index, so iterating what it holds prints no future row at all — and "no row" reads as "I didn't check", not "no futures". `morphology.FEATURE_VALUES` supplies the expected vocabulary per dimension, in paradigm order rather than alphabetical, and `build_grammar_groups` zero-fills against it. **The zero rows are the feature.** They render dimmed but present; dropping them would make the question unaskable from the table.

Three rules that go with it:

- **A dimension the whole corpus never states is dropped**, tested corpus-wide rather than per chapter. Zero-filling inside a dimension is informative; an all-zero Degree card on a text with no comparatives is furniture — and a card set that changed as you paged between chapters would read as grammar appearing and vanishing.
- **`GrammarGroup.stated` is not the sum of its rows.** `feature_any` deliberately files a syncretic `nom./acc.` under both readings, so the rows can add past the tokens involved. `stated` comes from `index.feature_dimension(dim)`, which counts each token once — the honest denominator to print a breakdown against.
- **Coverage rides along with every profile.** `CoverageReport` is rendered next to the card grid, and turns red when labels need attention or the voice gap exceeds 5%. Without it an unparsed label is indistinguishable from grammar the text does not contain.

`first_chapter == chapter_index` is what puts the **new** badge on a row: that is "grammar introduced here", the slope question in #7 read one chapter at a time.

### Form combinations

The per-dimension cards say "212 aorists". The combination table says *which* — `aor. act. ind. 3sg` as one row and `aor. mid. part. nom. sg. masc.` as another. A learner meets whole forms, not features, so two cells of the paradigm are two things to introduce even though both count as one aorist above. This is the granularity issue #7's opening quote actually asks for ("15 aorist participles").

`morphology.signature()` renders a token's full feature set as a label; `CorpusIndex` tracks it as its own dimension. Two properties the per-dimension cards do not have:

- **These rows partition.** Every token carries exactly one combination, so the column sums to the tokens carrying morphology — `tokens` is a real total, not a `stated`-style denominator. A syncretic form lands in one row that says so (`nom./acc.`) rather than being counted under both.
- **They cannot be zero-filled against the language.** Greek's full cross product is thousands of cells almost none of which any text contains. So the row set is what the *corpus* attests: a chapter carries an explicit zero for a form the text uses elsewhere (the "no aorist participles here" reading), and forms the whole text lacks have no row at all. Those zero rows are behind a toggle that names its own count, because on a long text they outnumber the present ones several times over.

**The inventory travels once, not per chapter.** Because the row set is corpus-wide, everything identifying a row — the label, its paradigm `order`, the chapters it spans — is *identical in every chapter*, and shipping the whole table per chapter duplicated it chapters × signatures times. `TextReport.form_combinations` carries the rows; `ChapterReport.combination_counts` carries only the two numbers that can differ (`occ`, `cumulative`), joined on `order` by `chapterCombinations()` in the UI, which re-derives the group and table totals by summing — these rows partition, so a class's tokens are exactly the sum of its `occ`. A row the text has not reached yet is omitted and defaults to (0, 0). On a 60-chapter / 25k-token corpus that took the section from 1.71 MB to 0.48 MB and the whole report from 10.5 MB to 9.3 MB. `build_form_combinations` still returns a fully scoped table and is what the chapter join is tested against.

**One table per form class, not one list.** `morphology.classify_combination` sorts each combination into **Verbs** (finite forms and infinitives), **Participles**, **Nouns & adjectives** (any declined form — articles, pronouns and numerals included), or **Other**. A single ranked list interleaves paradigms that were never meant to be compared: "which tenses am I using" and "which cases am I using" are different questions, and a participle answers to neither, which is why it gets a table of its own rather than being filed under either. On `Basil To the Rich` that split is 92 verb forms / 84 participle forms / 50 nominal — the participles alone justify the separation.

- **Classified from the decoded features, never from the provider's `type`.** The type is free text that drifts (the same word arrives as "verb", "participle", or nothing); the features say what the form is. Same reasoning as everything else in this layer.
- **`"other"` is a real bucket, not a failure.** A bare `superl.` states neither a verbal feature nor a case and still has to land somewhere; dropping it would quietly shrink a total that is supposed to add up.
- **A class the corpus never attests gets no table, decided corpus-wide.** A chapter with no participles keeps the table and says "None in this chapter" — losing it would leave the author to notice something missing.
- **One toggle for all the tables.** "Should I see the forms this chapter lacks" is a single question; three checkboxes would make it look like three.

Two rendering decisions:

- **Signature order is citation order, not `FEATURE_DIMENSIONS` order.** Person and number fuse on a finite verb (`3sg`, not `sg. 3.`), and a participle states a case and declines instead. The result round-trips the provider's own spelling, so a row label matches the `parse` column beneath it rather than making the author translate between two notations. **Fusion contracts the atomic case only.** A syncretic person or number falls back to the spaced spelling a grammar prints — `3 sg./pl.`, `2/3 sg.` — because the contraction has nowhere to put the slash: interpolating the stored value wrote `3pl|sg`, leaking both the `|` encoding and its canonical sort order into a displayed label, and breaking the round-trip for exactly the ambiguous forms these rows exist to preserve. Person is the one dimension `feature_abbr` cannot render (it takes no period, so `person_abbr` handles it).
- **Sorting the `form` column sorts by `order`, a paradigm rank**, not by the string. Alphabetical would file `aor.` before `impf.` before `pres.` and scatter the nominal forms through the verbal ones; paradigm order reads down a conjugation and then a declension. `order` is corpus position, so it survives a scope that reorders by count.

Descriptors are deliberately *not* part of a combination: `def. art. gen. sg. fem.` and a noun's `gen. sg. fem.` are the same paradigm cell, and `type` is the column that tells an article from a noun. Measured inventory: 16 combinations across two LGPSI chapters, 41 across four, 226 on `Basil To the Rich` — bounded enough for one scrolling table with a sticky header.

**`buildTable` returns three nested elements, not two.** The sort indicator sits outside the scrolling wrap: inside it, it scrolls sideways with a wide table, and where the wrap also scrolls vertically it pushes the sticky header down and lets rows paint in the gap above it.

### Text tab

One table with a lemma / lemma+parse toggle, the same toggle the chart above it carries. The two grains answer different questions — "how much of this word is in the text" versus "how much of it *in this form*" — and a lemma with eight parses is eight rows in one and one in the other. `TextRow` is one model for both (`parse` is empty on a lemma row) so the two cannot drift into disagreeing about what `total` means.

- **`chapter_count` is deliberately not `last - first + 1`.** A lemma in chapters 1 and 20 spans twenty and appears in two, and that gap *is* the repetition question. Both are columns.
- **Default order is total descending.** Every column sorts, so this is a default rather than a claim; sorting by `1st ch` descending is the "what's new in the latest chapter" reading and is one click away.
- **`form` is a representative, not a key** — the most frequent surface form in the row's scope, with `forms` counting how many it stands for.

### The two charts (issue #15)

Both are hand-rolled SVG in `ui/app.js`. The package is dependency-free and two charts do not pay for a bundle, a build step and a second theming system; a library would have supplied a legend and an axis, which are twenty lines each.

**New vs. repeated vocabulary** — stacked columns per chapter, with the same lemma / lemma+parse toggle as the table. "New" is the chapter a key's `first_chapter` falls in, the same test as the `1st lemma` / `1st parse` badges on the chapter table, so the bars are that column added up. `TextReport.lemma_progress` / `parse_progress` carry it (`stats.build_progression`); the split partitions the chapter at either grain, which is what the test asserts.

- **The bars stack types, not tokens.** The question is vocabulary load — how many words a chapter asks the reader to learn. Tokens answer a different one (how much of the running text those words account for) and are on every point, in the tooltip and the table view; only the bar height had to choose.
- **Already-met sits at the baseline, new stacks on top** — the vocabulary the chapter can assume is the base it builds on, the new words are what it adds. The cost is that `new` no longer starts from a common baseline, so comparing it across chapters is comparing segment lengths rather than reading off the gridlines; the tooltip and the table view carry the exact counts for that reading.
- **A one-chapter run gets a sentence, not a chart.** Everything in the first chapter is new by definition; a single bar with axes drawn around it says nothing that the sentence does not.
- **Chapters divide the panel until they would be narrower than a hover target** (34px), and past that the plot scrolls sideways like the wide tables. A four-chapter text should not be a thumbnail in the corner of the card; a sixty-chapter one cannot be squeezed into it.

**The form treemap** — area is occurrences, one block per form class, one cell per paradigm cell. This is the chart that could only be drawn from `form_combinations`: those rows **partition**, so dividing a rectangle up is an honest picture of them. The per-dimension cards cannot be drawn this way — `feature_any` files a syncretic `nom./acc.` under both readings, and area would double-count it. Layout is squarified (Bruls, Huizing & van Wijk 2000); slice-and-dice turns 226 cells into unhoverable slivers.

Rules shared by both, each of which had a visible failure behind it:

- **Marks carry colour; text never does.** Labels, values and legends stay in the page's ink. The exception is a label set *inside* a fill, which takes `--series-N-ink` — white or near-black, whichever clears contrast on that fill.
- **Every label is measured before it is drawn** — cell labels against their cell, and a block's token count against the block. A clipped label crops exactly the features that identify a form, and an unmeasured block count printed itself across the *next* block's name, which reads as one label wearing the wrong colour.
- **A 2px gap in the surface separates touching marks**, never a stroke around them: at these sizes a border thickens a small cell by a third, and it is ink that is not data. The gap comes off the upper stacked segment, so the lower one keeps its baseline and the stack keeps its top edge.
- **Nothing is readable only by hovering.** The bars have a table view (which is also where the token figures live), the treemap has the form tables directly below it, and both readouts are on focus as well as hover.
- **The four fills are a validated set, not four colours that looked distinct** — checked for lightness band, chroma, contrast against *these* two surfaces, and separation under simulated protanopia and deuteranopia across all pairs (worst 9.2 ΔE light / 9.4 dark against a floor of 8; worst unsimulated 24.0 / 20.9 against a floor of 15). Slot 4 is a neutral grey because it is the treemap's "Other" bucket, and a fourth saturated hue would have to sit beside the orange at a separation full-colour readers cannot rely on. Re-run the check before changing a value.
- **Charts are laid out in pixels and drawn twice**: once at a default width, then again when a `ResizeObserver` tells the card how wide it actually is. A scaled `viewBox` would have been less code and would also have scaled an 11px label to whatever the panel felt like.

**Table sorting is now data-driven.** `buildTable` takes column descriptors carrying their own accessor and kind; the previous implementation read values back out of the DOM and kept numeric/boolean columns in hard-coded index sets, which mis-sorts silently the moment a column is inserted — and #4 inserts four.

### Lemma tab (issue #16)

One word at a time: how often it occurs broken out by parse, which chapters it lives in, and every place it is actually used. `stats.build_lemma_report` answers all three from one pass over `Track.chapters` — only the chapters containing the word are scanned — and every count still comes off the index, so a figure here and the same figure in the text lens are the same number.

**This is the one lens that is not in the report payload.** A concordance for every lemma *is* the token stream a second time; carrying one per lemma would roughly double a 9 MB payload to ship several thousand words the author never opens. So it is a route (`GET /api/runs/{id}/lemma`), fetched for the one word being read. **The cost is that this lens needs a stored run** — the same constraint the workbook export has, and with `LINGUA_PERSIST_RUNS=false` the tab says which setting is in the way rather than showing an empty pane.

Because a lemma is browsed a word at a time, `pipeline.load_run_index` caches the last two indexes in `index_cache`, keyed on `(database, run id)` — the run id alone would let an index built from one database answer for another. Runs are append-only, so a cached index cannot go stale; **deleting is the one operation that can, so `TokenStore.delete_runs` invalidates as part of deleting.** That lives in the store rather than in the two delete routes because `delete_run`/`delete_runs` are public: a caller that has to remember to invalidate is a caller that can forget, and forgetting means serving facts the database no longer has for the life of the process.

The cache is a separate module for one reason: `store` has to invalidate, and `store` cannot import `pipeline`, which imports `store`. `index_cache` knows nothing about how an index is built — only which database a cached one came from.

**Caching an index also makes it shared, which makes `Track`'s lazy freeze a concurrency question.** The report handlers are `def`, so FastAPI runs them in a threadpool, and an index now outlives the request that built it. `Track._freeze` therefore publishes its chapter list and its prefix sums in a single assignment: storing them in two slots let a second thread see the list already set and the sums still `None`, which is an intermittent 500 on the second browser tab.

- **The occurrence lines are windows, not sentences.** The provider is asked not to emit punctuation, so the fact stream has no sentence boundary to cut on. Six tokens either side is the honest unit available, it stops at the chapter edge, and it is short there rather than padded. The panel says so under the list — an unlabelled window that starts mid-clause reads as analysis that lost a word.
- **Every chapter gets a row, including the ones with none.** A lemma's distribution is as much about where it stops appearing as where it appears, and `gap_before` (a `Track` method already) names the size of a gap on the row that ends it. In the chart the empty chapters keep their slot and get a baseline tick; closing those gaps would erase what the `longest gap` figure is counting. They read out on hover but are not clickable, because the chapter `<select>` offers only chapters that contain the word — filtering to an empty one would leave a filter with no option to clear it from.
- **The tables stay corpus-wide when the occurrence list is filtered.** `chapter` narrows the concordance only — re-scoping the tables to one chapter would make this the chapter lens with extra steps.
- **A truncated list has to say so.** `καί` is thousands of lines, so the list is capped at 400 and the response carries `occurrences_total` beside it. Presenting a page as the whole is the one mistake this list can cause: "λόγος never appears after chapter 12" would be a false reading of a cut-off.
- **By parse and by spelling are both tables, because they are transposes.** A spelling can carry several parses (`λόγοι` is nominative or vocative) and a parse several spellings; either table alone loses one of those readings.
- **"Various kinds of organization"** is a group-by toolbar over the same lines — chapter, parse, spelling, or reading order — with reading order preserved inside every group, so a group is always a passage read forwards.

**`buildTable` columns can now carry a `link`.** That is what makes the lemma column of the chapter and text tables the way *into* this lens: "and where else does this one turn up?" is asked where the word already is, not by retyping it into a search box on another tab. One delegated listener per table, because a text table is a couple of thousand rows and the row set is replaced on every sort.

The picker is fed from `TextReport.lemma_rows`, so choosing a word costs no request, and search folds diacritics (NFD, strip combining marks) — an author reaching for `λόγος` types `λογος`, and a search that misses it reads as "that word is not in this text".

**Not done:** the export's `Rows` sheet (issue #3) still has its original columns; `lemma_cum`/`parse_cum`, the text-lens grain and the concordance are not in it.

### Lexicon tab

The fourth lens inverts the question. The other three read the text and report what is in it; this one fixes a ranked vocabulary list as the **goal** and asks how much of it the text has taught, chapter by chapter — so every headline figure has the *list* in its denominator, and a row with zero occurrences is as interesting as one with many. The framing is deliberate and it is the pedagogy: we are writing texts that teach the dictionary, not asking learners to memorise the dictionary and then meet the text.

**The headline reports its scope in words, and the words have to be exact.** The picker is cumulative — it reads "through <chapter>" — so only the opening chapter is both a single chapter and the whole scope. `scopePhrase` says "this chapter" there, "chapters 1–3" past it, and "this text" unscoped; calling a cumulative figure "this chapter" would be a plain misreport of a number that includes everything before it. The tooltip is phrased passively ("entries used at least once in …") so one string serves a singular and a plural subject.

**The headline is deliberately spare:** how many list words a reader meets, and how much of the text is list vocabulary at all. On the stored 4-chapter LGPSI run, 105 of 5000 entries and 81.9% of tokens.

**A third figure was cut, and the reasoning is worth keeping.** `ref_share_covered` weights the covered entries by how common each is in the reference corpus — real information, and the only thing that separates a text teaching the top 300 words from one teaching 300 rarities. It shipped as "49.4% of running Greek unlocked" and every part of that label was wrong:

- **Not "Greek" — whichever corpus the list came from.** The same LGPSI run reads 49.4% against Core 5000 and 55.0% against GNT. The text did not change; the yardstick did.
- **Not "unlocked".** Comfortable reading needs ~95–98% token coverage. At 49% roughly every other word is unknown.
- **The scale is so front-loaded that the percentage barely means anything.** `ὁ` alone is 11.3% of the Core 5000 corpus; the top ten words are 31%. Any text using the article, καί and εἰμί starts near 30% before teaching anything. Word #100 adds 0.12%, word #1000 adds 0.009%.

`LexiconSummary.ref_share_covered` / `ref_share_total` and the per-entry `ref_share` are still computed, still tested, and still shipped — the arithmetic was never the problem, the framing was. Whatever presents it next should probably show it against its ceiling (the best possible 105 words score 62.8%, so LGPSI's are good but not optimal), which says something actionable that a bare percentage does not. For now the band chart carries the weighting argument.

#### The list is a checked-in CSV, not a table

`data/lexicons/` holds `manifest.json`, one entries CSV per lexicon, and an optional aliases CSV. Adding a lexicon is dropping a file and adding a manifest row; nothing in the package hard-codes an id and the UI dropdown is built from `GET /api/lexicons`.

This reverses the guess in the original scoping. A lexicon is reference data — 5000 rows, ~400KB, immutable between edits, identical for every run — and putting it in SQLite would buy a join nothing performs (every statistic here is derived in Python from a `CorpusIndex`, never in SQL) at the cost of a seeding step, a migration, and a cache that can disagree with the file on disk. It would also put the alias file **inside** whatever gets packaged, and the alias file is meant to be hand-edited by the author. So: read the CSV, cache per process, `lexicon.clear_cache()` after editing one.

`.gitignore` needed `data/*` plus `!data/lexicons/`, not `data/` — git cannot re-include a path whose parent directory is itself excluded, so the "checked-in" CSV was silently not checked in until that changed.

`data/lexicons/greek-core-5k.csv` was generated once from the Google Sheet's `gid=0` (5000 rows: rank, lemma, gloss, kind, ref_count, ref_coverage). The sheet is several side-by-side blocks in one tab with blank spacer columns; only the first block is used. Rerun the prep by hand if the sheet changes — it is not wired into the app.

#### A second list, and what it broke

`gnt-lemmas` is every lemma in the Greek New Testament — **5,461 of them over 137,554 lemma tokens** — generated from James Tauber's [vocabulary-tools](https://github.com/jtauber/vocabulary-tools) via `tools/prep_gnt_lexicon.py`. Adding it was a CSV plus a manifest row, with no change to `lexicon.py`, `stats.py`, `app.py` or the panel; the dropdown enables itself once there are two. That was the design claim and it held.

**It also falsified an assumption worth being explicit about: the Core 5000 has no proper nouns, and the GNT list has hundreds.** `is_proper_noun` is a *fallback for lemmas that did not match*, never a filter applied before matching — so `Ἰησοῦς` counts as taught against the GNT list and `Γρηγόριος`, which no list contains, is still set aside. Both behaviours fall out of the same ordering, but nothing had tested it until there was a list to test it with. On the 4-chapter LGPSI run the difference is concrete:

| | Core 5000 | GNT |
|---|---|---|
| entries taught | 105 / 5,000 | 117 / 5,461 |
| text that is list vocabulary | 81.9% | 87.7% |
| names set aside | 33 lemmas / 633 tokens | 21 / 381 |

The 252-token gap is LGPSI's geography and cast — `Ἀσία`, `Συρία`, `Αἴγυπτος`, `Ἰταλία`, `Λιβύη`, `Δημήτριος`, `Τίτος`, `Φοίβη` — which the GNT list can credit and a classical frequency list never could.

Two further notes on this list. **Glosses are borrowed** from the Core 5000 where the two lists name the same word (51% of entries); the GNT data carries no definitions, and where a key names several 5k entries the best-ranked sense wins so the choice is deterministic. **`kind` is folded from MorphGNT part-of-speech codes** (`N-`/`V-`/`A-`/`D-`/`I-` lexical, the closed classes function), which is a more principled split than the Core 5000's own column, where 2,050 of 5,000 rows are blank.

`vendor/` (gitignored, ~97MB) holds the clone; the venv reaches it through a `.pth`, which is what `pip install -e` writes anyway — the repo is a set of scripts, not a PyPI package. **`PYTHONUTF8=1` is required** to run it: `gnt_data/main.py` opens its 8MB token file without an explicit encoding, so on Windows it decodes as cp1252 and dies on the first Greek line. `tools/README.md` has the full recipe, and regeneration is byte-reproducible.

#### Matching is the part that can lie

`lexicon.py` layers three rules so each can be audited separately and no looser rule can overrule a stricter one:

1. **`match_key`** — mechanical and safe. NFC, drop macrons/breves (the list's citation forms carry them, Bedrock's lemmas do not), strip the trailing digit that marks a homograph, lowercase. This alone places **85.8%** of tokens in the stored corpus.
2. **the alias file** — curated, hand-edited, tagged by `kind` so a reader can tell an unarguable spelling variant (`οὕτω`→`οὕτως`) from a judgment call (`μόνον`→`μόνος`, an adverb filed under its adjective). 29 rows shipped.
3. **`fold_key`** — the systematic Attic/Koine alternations (`-ττ-`/`-σσ-`, `-ρρ-`/`-ρσ-`, `γιγν-`/`γιν-`), tried last.

**Accents and breathings survive every layer, and that is load-bearing.** An earlier, greedier normaliser that stripped them plus movable nu/sigma bought +0.6% tokens and collapsed `οὔ` onto five different entries (`οὐ`, `οὖν`, `οὗ2`, `οὗ`, `οὖς`), mismatched `καλῶς`→`κάλως` ("rope") and `ψευδῶς`→`ψεύδω`. The list *also* separates three accent-distinguished pairs on purpose — `βιός`/`βίος` (a bow / a life), `νομός`/`νόμος`, `τροπός`/`τρόπος` — so stripping accents would silently merge real entries. The conservative fold adds ten types with **zero** false positives; that is the trade that was taken. `tests/test_lexicon.py` pins all of it.

**Names get their own bucket.** The list contains zero capitalised headwords out of 5000, so a proper noun can never match, and counting names as "vocabulary outside the goal" makes every text look worse than it is. On the LGPSI run that misjudgement would be **633 of 3,572 tokens — 18%**. Bedrock capitalises name lemmas and lowercases everything else, so `is_proper_noun` reads the lemma; surface *forms* cannot be used, since one source text is set entirely in capitals.

**Homograph keys credit every entry they name.** `ὅς` and `ὅς2` are one string to the provider. Crediting both over-counts by at most the 51 colliding keys (~1%); crediting neither makes those entries permanently unreachable and silently caps the coverage any text could ever reach, which is the worse lie. `Match.ambiguous` marks them and the match report counts them.

**`LexiconMatchReport` rides along with every figure**, for the same reason `CoverageReport` sits beside the grammar counts: a lemma the matcher failed to place is indistinguishable from a word genuinely off the list, and both land outside the numerator. The note turns amber below ~80% of tokens placed. **Growing the alias file from the off-list table is the intended maintenance loop** — it is a data-curation task, not a code change.

#### Payload and scoping

Its own route (`GET /api/runs/{id}/lexicon?lexicon_id=…`), not a section of the report — a 5000-row join against data the report knows nothing about has no business riding along with runs nobody opens it for. **Same constraint as the lemma lens: this needs a stored run,** and says which setting is in the way when `LINGUA_PERSIST_RUNS=false`.

Per-entry chapter counts are **sparse** (`chapters: [{chapter_index, occ}]`, absent where zero) — a covered entry appears in a handful of chapters out of sixty. That is what lets the whole scope control be client-side arithmetic (`lexiconScopeView`): scoping to a chapter on a 60-chapter text would otherwise be 60 fetches of a megabyte each. Scope is **cumulative** ("through chapter 8"), because what a reader has met by now is the question an author writing chapter 9 is asking; the chapter-only count rides along as a `here` column rather than replacing it.

#### Reading the tab

- **The growth chart** is cumulative and stacks four segments, bottom to top: list already met, off-list already met, list new here, off-list new here. It groups by novelty rather than by list membership, so everything the reader already had is the base and everything the chapter adds stacks on top — the same grammar the text tab's progression chart uses, and the growing edge of the bar is always what is new. The cost is that the two list segments are not adjacent, so cumulative list vocabulary is no longer readable as a single edge; it is in the tooltip, and the band chart below answers the same question standing still. Columns are clickable to scope the tables; the hit target is full-height so a chapter that taught nothing new is still reachable — a flat stretch is the finding.
- **Two dimensions, two channels: hue for new vs already met, saturation for on-list vs off-list.** `--series-1-muted` / `--series-2-muted` are washed-out twins of the existing blue and orange, so a reader learns one rule rather than a four-colour legend.
- **The muted pair recedes, and that is the meaning rather than the styling.** Off-list vocabulary is load a reader carries without progressing toward the goal, so it should read as the quieter thing in the bar. "Recedes" means toward the background in both themes — lighter on the light one, dimmer on the dark one, the same direction `--muted` moves from `--fg`. An earlier cut went *darker* on the light theme to protect contrast, which was legible and said the wrong thing: it made off-list the most emphatic block in the chart.
- **The stacking order is what makes that safe to do.** Receding costs contrast, but the muted orange is always *interior* — list-already-met below it, list-new above — so both its edges meet a saturated block and it never has to hold its own against the page. Only the muted blue sits on top of the stack, and it keeps the greater contrast of the two for exactly that reason. Reorder `GROWTH_SEGMENTS` and that stops being true.
- **The chart counts the text's own words, not list entries, and a toggle switches types/tokens.** Entries cannot carry the off-list dimension at all (an off-list word is by definition not an entry), and they do not partition: a spelling can credit two entries and two spellings can credit one. Counting distinct lemmas and tokens makes the four segments sum to the corpus exactly, which is what `test_new_vocabulary_split_partitions_the_corpus` pins. The authoritative entries-taught and %-of-Greek figures still ride in the tooltip, and can differ from the list block by a row or two for the reason just given.
- **Names are folded into off-list in this chart only.** Everywhere else on the tab they are a third bucket, because no list can contain them and charging a text for them is unfair. Here the bar has to total the real page, so `tokens_off_list_with_names` exists alongside `tokens_off_list` rather than one being re-derived from the other and getting it subtly wrong. Note that `gnt-lemmas` *does* contain names, so what lands in this segment is list-dependent.
- **`new` cumulates for tokens and does not for types, and the asymmetry is the correct one.** A lemma sits in the bar once, so splitting it into "debuted here" and "debuted earlier" partitions the bar and both labels are literally true. An occurrence sits in the bar once *per occurrence*, so the same rule mislabels history: taking only this chapter's first-encounter tokens as `new` sweeps every earlier chapter's first encounters into `already met`. On the LGPSI run that put 1,078 tokens under a label claiming the reader already knew those words when each was in fact a first meeting. Cumulating makes the split "read while the word was new" vs "read once it was known" — what the legend claims, and what the text tab's `new_tokens` means added up.
- **Both halves of that were bugs, found in opposite directions.** The first cut accumulated `new` in *both* units, which pinned already-met at zero in the types view (a tooltip read "105 — 105 new here, 0 already met" on the last chapter of four). Fixing it by making both units per-chapter then broke tokens the other way, silently — the arithmetic still balanced, which is why it survived a full numeric audit and was only caught by asking what the segments *meant*. Validation that only checks sums will not find this class of error.
- **The band chart** slices the list 500 at a time and draws one bar per block: how much of it the text teaches. It briefly drew a second bar for what each block is *worth* in the reference corpus — ranks 1–500 cover **81.3%** of it — which is real information but reads as noise beside the bar it qualifies, two lengths per row and neither the one being scanned. That weighting is now read deliberately in the `corpus ref %` column of the table rather than glanced at anywhere. `LexiconBand` still carries `ref_share`/`ref_share_covered`; they are simply not drawn.
- **Three tables.** *Matched to Lexicon*, the list in rank order (untaught rows dimmed but legible — they are half the point); **not taught yet**, which is the authoring worklist, deliberately whole-text so an early scope does not fill it with words chapter 30 already covers; and **off-list words**, the vocabulary load that does not move a reader toward the goal, names excepted.
- **The list table windows to the top 1000 by default.** All 5000 rows render in ~550ms, which is tolerable but not free, and the top 1000 covers ~88% of the reference corpus.

**The `match` column is the audit trail, and it names ambiguity out loud.** Blank means the text lemma *is* the list's headword; `alias` and `folded` mean the match rests on a judgment call; `ambiguous` means the list separates senses the parser cannot, so every entry sharing the key was credited. That last one is the case that inflates coverage rather than merely explaining it — on the LGPSI run it is 6 entry pairs (`κύριος`/`κύριος2` both credited from the same 17 tokens, likewise `ὅς`, `ὅτι`, `ἤ`, `πάρειμι`, `ἄπειμι`), about **6% of the covered total**. It was tooltip-only at first, which made the one number worth doubting the only one you could not see.

**Two sorting bugs the `match` column flushed out, both worth not repeating:**

- **`get` is the sort key *and* the displayed value; overriding `text` alone sorts on something invisible.** This column originally sorted on `matched_by` — `"exact"` for nearly every row — while displaying blank, so descending buried the handful of interesting rows under a thousand identical-looking ones. Fixed by deriving one string (`matchLabel`) and using it for both. If a column ever needs `text` to differ from `get`, `get` must still order the way the display reads.
- **Never use `Infinity` as a sort sentinel.** `compareBy`'s numeric branch subtracts, and `Infinity - Infinity` is `NaN`; a comparator returning `NaN` makes `Array.prototype.sort` undefined, so the 907 untaught rows in the `1st ch` column came out in arbitrary order. `Number.MAX_SAFE_INTEGER` subtracts to 0 and groups them correctly. The other two `Infinity` uses in `app.js` are a `Math.max` seed and a `<=` filter bound, both safe.

There is no JS test runner in this project, so both were verified in the browser by sorting every column in both directions and asserting the rendered values are monotonic — worth redoing by hand if the columns change.

**`buildTable` columns can now carry `html`.** One escape hatch, so a cell can hold a bar beside its number rather than a number alone; the column owns its own escaping. Bars are **square-rooted** — the article is 2,715 of 4,213 tokens in the Basil run, and against a linear scale every other row is one pixel wide, which is true and useless.

**Not done:** the workbook export has no lexicon sheet; there is no cross-run comparison (does chapter 40 of draft B teach more of the list than draft A?); and `LexiconEntryRow.chapters` is sparse but the entries array is not — a much longer list would want windowing server-side.

## Performance & throughput

Bedrock output generation is the bottleneck — Greek expands to **~7-9 JSON tokens per input char** (each token object repeats Greek in both `lemma` and `form`, plus a `parse` label). A ~5000-char chapter needs ~40k output tokens.

Three changes took a 2-chapter run from **~11m 50s → ~1m 35s**:

1. **Parallelism (no feedback loop in the analysis).** `analyze_chapter` is independent per chapter; cross-chapter first/last-occurrence indexes are aggregated *after* calls return, keyed by chapter index, so order is stable regardless of completion order. `pipeline._analyze_chapters` fans chapters out with a `ThreadPoolExecutor` + `as_completed` (drives progress events); `bedrock.analyze_chapter` fans chunks out the same way. boto3 clients are thread-safe; **adaptive retries** (`mode="adaptive"`) absorb throttling bursts.
2. **Chunking that never truncates.** `max_tokens` is 16000. A chunk over ~1500 chars can exceed it → the call is **truncated and discarded**, then re-split *sequentially* (in one sample ~40% of generated tokens were wasted this way). `bedrock._chunk_text` now pre-expands any over-limit paragraph via `_split_long_paragraph` (sentence enders `. · ; ! ?`, hard char-split as last resort), so **every chunk ≤ `max_chunk_chars`** and no call hits the cap.
3. **Defaults tuned for parallel small chunks:** `max_chunk_chars=1200` (~11k output tokens, safe margin under 16k) and `max_workers=8`. Both overridable via `LINGUA_MAX_CHUNK_CHARS` / `LINGUA_MAX_WORKERS`.

**Knobs & tradeoffs:**
- `LINGUA_MAX_WORKERS` caps fan-out *per level* (chapters × chunks), so worst-case concurrency ≈ `workers²`. More workers = faster until Bedrock throttles — watch `logs/bedrock_payloads.jsonl` for `ThrottlingException`; drop to 6 or request a TPM quota increase for large batches.
- `LINGUA_MAX_CHUNK_CHARS`: smaller = more parallel calls but more overhead; keep ×9 under 16000.
- **Healthy run = all `stop_reason: end_turn`, zero `max_tokens`** in the log. Any `max_tokens` means truncation/re-split waste returned → lower `max_chunk_chars`.
- **Biggest remaining lever:** swap `BEDROCK_MODEL_ID` to Claude Haiku (~3-5× faster generation) for a speed/accuracy tradeoff on morphology. Find available inference-profile IDs with `aws bedrock list-inference-profiles`.

## Priorities

1. Working end-to-end report from a real LGPSI `.docx`.
2. Tune prompts / chunk sizes if Bedrock truncates or drifts.
3. Packaging + friend-friendly setup.
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

Every row of the run-history table carries an **Export CSV** button beside its **View** button. One file, one grain: **one row per chapter × lemma × parse** — the rows the report table shows, with the file and chapter they belong to spliced in so the whole corpus lands in a single flat sheet that pivots.

| group | columns |
| --- | --- |
| identity | `file`, `chapter_index`, `chapter_id`, `chapter_title` |
| row | `type`, `lemma`, `form`, `parse` |
| counts (this chapter) | `lemma_occ`, `parse_occ`, `form_occ` |
| occurrence (this chapter) | `first_occ_lemma`, `first_occ_parse`, `last_occ_lemma`, `last_occ_parse` |
| occurrence (corpus-wide) | `lemma_first_chapter`, `lemma_last_chapter`, `parse_first_chapter`, `parse_last_chapter` |

- **On the run, not on the report.** Exporting is a per-run action, so any stored run can be pulled as CSV without first rendering it, and doing so does not disturb whatever report is already on screen. The cost: with `LINGUA_PERSIST_RUNS=false` the history panel is hidden entirely, so there is no export path at all — re-enable persistence to export.
- **Built in the browser from `GET /api/runs/{id}/report`, not by a dedicated route.** That response *is* the displayed grain, so there is nothing extra to derive server-side, and re-deriving it costs nothing — no provider call.
- **The `*_chapter` columns are the model's raw 0-based indexes**, left uncooked so they compare against `chapter_index`: a row is a lemma's first appearance exactly when `lemma_first_chapter == chapter_index`. The booleans beside them are that comparison already done, because "is this the first time?" is the question actually being asked.
- **UTF-8 with a BOM, CRLF rows.** Every lemma and form is Greek and Excel reads a BOM-less UTF-8 CSV as the local codepage, i.e. as mojibake. The BOM is what makes the file openable by double-click instead of through the import wizard.
- **Report order, not screen order.** The column sort is a reading aid; a spreadsheet re-sorts anyway.
- **Not yet:** per-chapter or per-file downloads, an Excel workbook with a tab per chapter, and the form-level breakdown on `TokenRow.forms` (a second grain, so a second file — not more columns on this one).

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
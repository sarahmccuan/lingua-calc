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
- **Next:** Phase 2 in-app AWS credentials for non-technical users; PyInstaller Win/Mac builds; optional CLTK provider behind same interface; gap-since-last-occurrence and aorist-style queries (see questions above).

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
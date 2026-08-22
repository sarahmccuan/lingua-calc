# Lingua Calc

Upload a `.docx` chapter (or document with Heading 1 sections), get per-chapter **lemma / form / parse** tables and counts via **Amazon Bedrock** (Claude).

## Setup

1. Install **Python 3.10+** (on Windows, check "Add Python to PATH" in the installer).
2. Open a terminal in this folder and install the app:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

3. **Add your AWS credentials** — copy the template and paste in the three values you were given:

```bash
copy .env.template .env
```

Then open `.env` in any text editor (Notepad is fine) and fill in:

```
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...your key...
AWS_SECRET_ACCESS_KEY=...your secret...
```

Save the file. The app reads it automatically every time it starts — **you only do this once.** Keep `.env` private (it's already excluded from git).

## Run

```bash
python -m lingua_calc
```

On Windows, if `python` is not on your PATH, use `.\.venv\Scripts\python.exe -m lingua_calc` from the project folder.

Opens `http://127.0.0.1:8765/` in your browser. Choose one or more `.docx` files, then **Process files**.

## Configuration (optional)

All set via `.env`. The credential values above are required; everything below has sensible defaults.

| Variable | Purpose |
|----------|---------|
| `AWS_REGION` (or `AWS_DEFAULT_REGION`) | Region for Bedrock (default `us-east-1`) |
| `BEDROCK_MODEL_ID` | Model ID (default Claude Sonnet 4.5) |
| `LINGUA_MAX_WORKERS` | Concurrent Bedrock calls per fan-out level (default `8`) |
| `LINGUA_MAX_CHUNK_CHARS` | Max chars per chunk sent to the model (default `1200`) |
| `LINGUA_DEBUG_TRACEBACKS` | `true` to include Python tracebacks in API error JSON (default off) |
| `LINGUA_DB_PATH` | Where run history is stored (default `data/lingua_calc.sqlite3`) |
| `LINGUA_PERSIST_RUNS` | `false` to disable saving run history |
| `LINGUA_HOST` / `LINGUA_PORT` | Where the local UI binds (default `127.0.0.1:8765`) |
| `LINGUA_OPEN_BROWSER` | `false` to skip opening a browser on launch |

The `LINGUA_` prefix is required on every app setting. Names like `DB_PATH`,
`PORT` and `MAX_WORKERS` are deliberately *not* read — other tools set those, and
an inherited value silently repointing the database or the Bedrock concurrency
would look like a normal run. Only the AWS and Bedrock variables above use their
standard unprefixed names, so an existing boto3 setup keeps working.

## Chapters

- Paragraphs styled as **Heading 1** in Word start a new chapter.
- If there are no Heading 1s, the whole document is one chapter titled **Document**.

## Project layout

- `lingua_calc/` — FastAPI app, docx extract, Bedrock provider, stats
- `ui/` — Static HTML/CSS/JS served from the same origin

## Contributing

Bug reports and pull requests are welcome at
[github.com/sarahmccuan/lingua-calc](https://github.com/sarahmccuan/lingua-calc).

1. Fork and branch off `main`.
2. Install the dev extras: `pip install -e ".[dev]"`.
3. Run the tests: `pytest`.
4. Open a pull request describing what changed and why.

By contributing you agree that your contributions are licensed under the MPL 2.0
(see below).

## License

Licensed under the [Mozilla Public License 2.0](LICENSE.txt).

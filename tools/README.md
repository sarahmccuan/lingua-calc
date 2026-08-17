# Lexicon generation

One script per reference list in `data/lexicons/`. The **CSV is the artefact** —
these are not imported by the app and only need running when a source changes.

## `prep_core5k_lexicon.py` — Core Greek 5000

Reads a CSV export of the Google Sheet named in `data/lexicons/manifest.json`
(`gid=0`) and keeps its first block of columns. The sheet is several side-by-side
tables in one tab separated by blank spacer columns; the rest are ignored.

```
curl -L -o sheet.csv "https://docs.google.com/spreadsheets/d/<ID>/export?format=csv&gid=0"
python tools/prep_core5k_lexicon.py sheet.csv
```

## `prep_gnt_lexicon.py` — Greek New Testament

Needs James Tauber's [vocabulary-tools](https://github.com/jtauber/vocabulary-tools),
which is a script repo rather than a package on PyPI:

```
git clone --depth 1 https://github.com/jtauber/vocabulary-tools.git vendor/vocabulary-tools
python -c "import sysconfig,pathlib; pathlib.Path(sysconfig.get_paths()['purelib'], 'vocabulary-tools.pth').write_text(str(pathlib.Path('vendor/vocabulary-tools').resolve()))"
PYTHONUTF8=1 python tools/prep_gnt_lexicon.py
```

The `.pth` is what `pip install -e` writes anyway, and `vendor/` is gitignored —
the clone is ~97MB because it bundles several other corpora we do not use.

**`PYTHONUTF8=1` is required, not optional.** `gnt_data/main.py` opens its 8MB
token file without an explicit encoding, so on Windows it decodes as cp1252 and
raises `UnicodeDecodeError` on the first Greek line. UTF-8 mode fixes it without
patching vendored source.

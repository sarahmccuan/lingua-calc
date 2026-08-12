from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Sequence

from lingua_calc import index_cache
from lingua_calc.clean_extracted_files import clean_chapters
from lingua_calc.config import Settings, get_settings
from lingua_calc.corpus import ChapterRef, CorpusIndex
from lingua_calc.docx_extract import TextChapter, extract_chapters_from_docx
from lingua_calc.models import (
    DocumentReport,
    FileReport,
    LemmaReport,
    MultiFileReport,
    ParsedToken,
    TokenFact,
)
from lingua_calc.nlp.base import LemmatizeParseProvider
from lingua_calc.nlp.bedrock import BedrockClaudeProvider
from lingua_calc.stats import (
    LEMMA_CONTEXT_TOKENS,
    LEMMA_OCCURRENCE_LIMIT,
    build_chapter_report,
    build_lemma_report,
    build_text_report,
)
from lingua_calc.store import TokenStore, save_run_safely

logger = logging.getLogger(__name__)

# progress(done, total, last_finished_title) — called once with done=0 when the
# total is known, then once per chapter as it completes (in completion order).
ProgressCb = Callable[[int, int, "str | None"], None]

# A chapter plus the file it came from, in corpus reading order. The list index
# is the chapter_index every statistic is keyed by.
Placement = tuple[str, TextChapter]


def _natural_key(name: str) -> list:
    """Sort key that orders numbers numerically, so "Chapter 2" precedes
    "Chapter 10" instead of sorting lexicographically."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _slugify(title: str, ordinal: int) -> str:
    """Build the corpus-unique chapter id. ``ordinal`` is 1-based."""
    slug = re.sub(r"\s+", "-", title.lower())
    slug = re.sub(r"[^a-z0-9-]", "", slug) or f"ch-{ordinal}"
    return f"{ordinal}-{slug}"[:64]


def _prepare_file_chapters(filename: str, data: bytes, offset: int) -> list[TextChapter]:
    """Extract one file's chapters and renumber their ids for the whole corpus.

    ``offset`` is how many chapters precede this file, so ids stay unique and in
    reading order across a multi-file upload.
    """
    chapters = extract_chapters_from_docx(data)
    # A .docx with no Heading 1 comes back as a single chapter titled
    # "Document"; the filename stem is a better title (e.g. "Chapter 1.docx").
    if len(chapters) == 1 and chapters[0].title == "Document":
        title = Path(filename).stem
        return [TextChapter(id=_slugify(title, offset + 1), title=title, text=chapters[0].text)]
    return [
        TextChapter(id=_slugify(ch.title, offset + i + 1), title=ch.title, text=ch.text)
        for i, ch in enumerate(chapters)
    ]


def _analyze_chapters(
    prov: LemmatizeParseProvider,
    cleaned_chapters: Sequence[TextChapter],
    settings: Settings,
    progress: ProgressCb | None = None,
) -> list[list[ParsedToken]]:
    """Run ``prov.analyze_chapter`` for every chapter concurrently.

    Each chapter's Bedrock call is independent, so they run in parallel. Results
    are stored by original index, so chapter order — and therefore every
    chapter_index derived from it — is stable regardless of which call finishes
    first. If ``progress`` is given it is invoked as each chapter completes.
    """
    total = len(cleaned_chapters)
    token_lists: list[list[ParsedToken]] = [[] for _ in range(total)]

    if progress:
        progress(0, total, None)

    if not cleaned_chapters:
        return token_lists

    workers = max(1, min(settings.max_workers, total))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_to_index = {
            ex.submit(prov.analyze_chapter, ch.text, ch.title): i
            for i, ch in enumerate(cleaned_chapters)
        }
        done = 0
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            token_lists[i] = future.result()
            done += 1
            if progress:
                progress(done, total, cleaned_chapters[i].title)

    return token_lists


def _build_facts(
    placements: Sequence[Placement],
    token_lists: Sequence[list[ParsedToken]],
) -> list[TokenFact]:
    """Locate raw provider output in the corpus.

    This is the lossless record every statistic derives from, so it keeps one
    row per token occurrence — no aggregation happens here.
    """
    facts: list[TokenFact] = []
    for chapter_index, ((filename, chapter), tokens) in enumerate(zip(placements, token_lists)):
        for position, token in enumerate(tokens):
            facts.append(
                TokenFact(
                    type=token.type,
                    lemma=token.lemma,
                    form=token.form,
                    parse=token.parse,
                    filename=filename,
                    chapter_index=chapter_index,
                    chapter_id=chapter.id,
                    chapter_title=chapter.title,
                    position=position,
                )
            )
    return facts


def _chapter_refs(placements: Sequence[Placement]) -> list[ChapterRef]:
    return [
        ChapterRef(index=i, id=chapter.id, title=chapter.title, filename=filename)
        for i, (filename, chapter) in enumerate(placements)
    ]


def build_file_reports(index: CorpusIndex) -> list[FileReport]:
    """Group every chapter in the index into one report per source file.

    Chapters are already in corpus reading order and files were processed in
    order, so consecutive runs of the same filename are exactly one file's
    chapters. Driven off the index alone, so it works equally on a fresh run and
    on one reloaded from the store.
    """
    file_reports: list[FileReport] = []
    current_name: str | None = None
    current: list = []

    for chapter_index in index.chapter_indexes:
        ref = index.chapter_ref(chapter_index)
        filename = ref.filename if ref else ""
        if filename != current_name:
            if current_name is not None:
                file_reports.append(FileReport(filename=current_name, chapters=current))
            current_name, current = filename, []
        current.append(build_chapter_report(chapter_index, index))

    if current_name is not None:
        file_reports.append(FileReport(filename=current_name, chapters=current))
    return file_reports


def open_store(settings: Settings) -> TokenStore | None:
    """Open the run store, or return ``None`` if history is off/unavailable.

    Public because the HTTP layer needs the same "persistence is optional"
    handling the pipeline uses — the run-history endpoints have to degrade to
    "no history" rather than 500 on a read-only install.
    """
    if not settings.persist_runs:
        return None
    try:
        return TokenStore(settings.db_path)
    except Exception:
        logger.exception("Could not open token store at %s; continuing without history", settings.db_path)
        return None


def analyze_docx_files(
    files: list[tuple[str, bytes]],
    provider: LemmatizeParseProvider | None = None,
    progress: ProgressCb | None = None,
    settings: Settings | None = None,
) -> MultiFileReport:
    """Analyze .docx files; returns one report per file with shared vocabulary tracking.

    ``files`` is a list of (filename, data) tuples. All chapters are analyzed
    together so vocabulary progression spans files, but reports are grouped per
    file for the UI.
    """
    settings = settings or get_settings()
    prov = provider or BedrockClaudeProvider(settings)

    # Process files in natural filename order (Chapter 1, 2, … 10) so chapter
    # indexes — and every statistic keyed by them — plus the rendered order are
    # correct regardless of the order files were uploaded.
    ordered_files = sorted(files, key=lambda fd: _natural_key(fd[0]))

    placements: list[Placement] = []
    for filename, data in ordered_files:
        for chapter in _prepare_file_chapters(filename, data, len(placements)):
            placements.append((filename, chapter))

    cleaned = clean_chapters([chapter for _, chapter in placements])
    token_lists = _analyze_chapters(prov, cleaned, settings, progress)

    facts = _build_facts(placements, token_lists)
    index = CorpusIndex(facts, chapters=_chapter_refs(placements))

    run_id = save_run_safely(
        open_store(settings),
        facts,
        model_id=settings.bedrock_model_id,
        filenames=[name for name, _ in ordered_files],
    )

    return MultiFileReport(
        file_reports=build_file_reports(index),
        text_report=build_text_report(index),
        run_id=run_id,
    )


def analyze_docx_bytes(
    filename: str,
    data: bytes,
    provider: LemmatizeParseProvider | None = None,
    progress: ProgressCb | None = None,
    settings: Settings | None = None,
) -> DocumentReport:
    """Single-file convenience wrapper over :func:`analyze_docx_files`."""
    report = analyze_docx_files([(filename, data)], provider=provider, progress=progress, settings=settings)
    chapters = report.file_reports[0].chapters if report.file_reports else []
    return DocumentReport(filename=filename, chapters=chapters)


def load_run_index(
    run_id: str,
    store: TokenStore | None = None,
    settings: Settings | None = None,
) -> CorpusIndex | None:
    """A stored run's index, from :mod:`index_cache` when possible.

    ``None`` if the run is unknown. The cache is keyed on the database as well as
    the run, so an explicit ``store`` argument is honoured rather than silently
    answered from whatever was read last — see ``index_cache.key``. Resolving the
    path is deliberately cheaper than opening the store: on a hit nothing touches
    SQLite at all, which is the whole point of caching a lemma-at-a-time lens.
    """
    settings = settings or get_settings()
    cache_key = index_cache.key(store.path if store is not None else settings.db_path, run_id)
    cached = index_cache.get(cache_key)
    if cached is not None:
        return cached

    store = store or open_store(settings)
    if store is None or store.get_run(run_id) is None:
        return None
    index = store.load_index(run_id)
    index_cache.put(cache_key, index)
    return index


def reports_from_run(
    run_id: str,
    store: TokenStore | None = None,
    settings: Settings | None = None,
) -> MultiFileReport | None:
    """Rebuild reports from a stored run without calling the provider.

    The entry point for re-deriving statistics over an already-analyzed text —
    new columns and export grains can be developed against real data for free.
    """
    index = load_run_index(run_id, store=store, settings=settings)
    if index is None:
        return None
    return MultiFileReport(
        file_reports=build_file_reports(index),
        text_report=build_text_report(index),
        run_id=run_id,
    )


def lemma_report_from_run(
    run_id: str,
    lemma: str,
    store: TokenStore | None = None,
    settings: Settings | None = None,
    *,
    limit: int = LEMMA_OCCURRENCE_LIMIT,
    chapter_index: int | None = None,
    context: int = LEMMA_CONTEXT_TOKENS,
) -> LemmaReport | None:
    """One lemma's lens over a stored run (issue #16).

    ``None`` covers both "no such run" and "no such lemma in it" — the caller
    has nothing useful to say about either beyond "not found".
    """
    index = load_run_index(run_id, store=store, settings=settings)
    if index is None:
        return None
    return build_lemma_report(
        index, lemma, limit=limit, chapter_index=chapter_index, context=context
    )

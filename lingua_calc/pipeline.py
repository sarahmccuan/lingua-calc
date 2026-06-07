from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from lingua_calc.config import get_settings
from lingua_calc.docx_extract import extract_chapters_from_docx, TextChapter
from lingua_calc.models import DocumentReport, FileReport, MultiFileReport, ParsedToken
from lingua_calc.nlp.base import LemmatizeParseProvider
from lingua_calc.nlp.bedrock import BedrockClaudeProvider
from lingua_calc.stats import build_chapter_report
from lingua_calc.clean_extracted_files import clean_chapters


# progress(done, total, last_finished_title) — called once with done=0 when the
# total is known, then once per chapter as it completes (in completion order).
ProgressCb = Callable[[int, int, "str | None"], None]


def _natural_key(name: str) -> list:
    """Sort key that orders numbers numerically, so "Chapter 2" precedes
    "Chapter 10" instead of sorting lexicographically."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _analyze_chapters(
    prov: LemmatizeParseProvider,
    cleaned_chapters: list[TextChapter],
    progress: ProgressCb | None = None,
) -> tuple[
    list[tuple[str, str, list[ParsedToken]]],
    dict[str, int],
    dict[str, int],
    dict[tuple[str, str], int],
    dict[tuple[str, str], int],
]:
    """Run ``prov.analyze_chapter`` for every chapter concurrently, then build the
    cross-chapter first/last-occurrence indexes.

    Each chapter's Bedrock call is independent, so they run in parallel. Results
    are stored by original index and aggregated afterwards in chapter order, so the
    chapter index used for tracking is stable regardless of which call finishes
    first. If ``progress`` is given it is invoked as each chapter completes.
    """
    total = len(cleaned_chapters)
    token_lists: list[list[ParsedToken]] = [[] for _ in range(total)]

    if progress:
        progress(0, total, None)

    if cleaned_chapters:
        workers = max(1, min(get_settings().max_workers, total))
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

    chapter_tokens: list[tuple[str, str, list[ParsedToken]]] = []
    lemma_first_chapter: dict[str, int] = {}
    lemma_last_chapter: dict[str, int] = {}
    parse_first_chapter: dict[tuple[str, str], int] = {}
    parse_last_chapter: dict[tuple[str, str], int] = {}

    for index, (ch, tokens) in enumerate(zip(cleaned_chapters, token_lists)):
        chapter_tokens.append((ch.id, ch.title, tokens))
        for t in tokens:
            lemma_first_chapter.setdefault(t.lemma, index)
            lemma_last_chapter[t.lemma] = index
            parse_first_chapter.setdefault((t.lemma, t.parse), index)
            parse_last_chapter[(t.lemma, t.parse)] = index

    return (
        chapter_tokens,
        lemma_first_chapter,
        lemma_last_chapter,
        parse_first_chapter,
        parse_last_chapter,
    )


def analyze_docx_bytes(
    filename: str,
    data: bytes,
    provider: LemmatizeParseProvider | None = None,
    progress: ProgressCb | None = None,
) -> DocumentReport:
    prov = provider or BedrockClaudeProvider()
    chapters_in = extract_chapters_from_docx(data)
    # If the uploaded file is a single .docx (e.g. "Chapter 1.docx") and
    # the extractor returned the default title "Document", prefer the
    # filename stem as the chapter title and create a matching id.
    if len(chapters_in) == 1 and chapters_in[0].title == "Document":
        from pathlib import Path
        import re

        stem = Path(filename).stem
        title = stem
        slug = re.sub(r"\s+", "-", title.lower())
        slug = re.sub(r"[^a-z0-9-]", "", slug) or "ch-1"
        cid = f"1-{slug}"[:64]
        chapters_in = [TextChapter(id=cid, title=title, text=chapters_in[0].text)]
    cleaned_chapters = clean_chapters(chapters_in)

    (
        chapter_tokens,
        lemma_first_chapter,
        lemma_last_chapter,
        parse_first_chapter,
        parse_last_chapter,
    ) = _analyze_chapters(prov, cleaned_chapters, progress)

    reports = []
    for index, (chapter_id, chapter_title, tokens) in enumerate(chapter_tokens):
        reports.append(
            build_chapter_report(
                chapter_id,
                chapter_title,
                tokens,
                index,
                lemma_first_chapter,
                lemma_last_chapter,
                parse_first_chapter,
                parse_last_chapter,
            )
        )

    return DocumentReport(filename=filename, chapters=reports)


def analyze_docx_files(
    files: list[tuple[str, bytes]],
    provider: LemmatizeParseProvider | None = None,
    progress: ProgressCb | None = None,
) -> MultiFileReport | DocumentReport:
    """Analyze multiple .docx files; returns separate reports per file with shared vocabulary tracking.

    `files` is a list of (filename, data) tuples. All chapters are analyzed together so
    vocabulary progression (first/last occurrences) spans across files, but reports are
    grouped and returned per file for UI separation.
    """
    from pathlib import Path

    prov = provider or BedrockClaudeProvider()

    # Process files in natural filename order (Chapter 1, 2, … 10) so chapter
    # indexes — and the first/last-occurrence stats derived from them — plus the
    # rendered order are correct regardless of the order files were uploaded.
    files = sorted(files, key=lambda fd: _natural_key(fd[0]))

    # Collect chapters with file origin tracking
    file_chapter_map: list[tuple[str, list[TextChapter]]] = []  # (filename, chapters)
    all_chapters: list[TextChapter] = []

    for filename, data in files:
        chapters = extract_chapters_from_docx(data)
        # if extractor returned a single unnamed chapter, prefer filename stem
        if len(chapters) == 1 and chapters[0].title == "Document":
            stem = Path(filename).stem
            title = stem
            slug = re.sub(r"\s+", "-", title.lower())
            slug = re.sub(r"[^a-z0-9-]", "", slug) or "ch-1"
            cid = f"{len(all_chapters)+1}-{slug}"[:64]
            chapters = [TextChapter(id=cid, title=title, text=chapters[0].text)]
        else:
            # renumber chapter ids to keep them unique and ordered across files
            renumbered: list[TextChapter] = []
            for t in chapters:
                idx = len(all_chapters) + len(renumbered) + 1
                slug = re.sub(r"\s+", "-", t.title.lower())
                slug = re.sub(r"[^a-z0-9-]", "", slug) or f"ch-{idx}"
                cid = f"{idx}-{slug}"[:64]
                renumbered.append(TextChapter(id=cid, title=t.title, text=t.text))
            chapters = renumbered

        file_chapter_map.append((filename, chapters))
        all_chapters.extend(chapters)

    cleaned_chapters = clean_chapters(all_chapters)

    # Analyze all chapters together (cross-file vocab tracking), in parallel.
    (
        chapter_tokens,
        lemma_first_chapter,
        lemma_last_chapter,
        parse_first_chapter,
        parse_last_chapter,
    ) = _analyze_chapters(prov, cleaned_chapters, progress)

    # Group reports by file
    file_reports: list[FileReport] = []
    chapter_idx = 0
    for filename, original_chapters in file_chapter_map:
        file_chapter_reports = []
        for _ in original_chapters:
            index, (chapter_id, chapter_title, tokens) = chapter_idx, chapter_tokens[chapter_idx]
            file_chapter_reports.append(
                build_chapter_report(
                    chapter_id,
                    chapter_title,
                    tokens,
                    index,
                    lemma_first_chapter,
                    lemma_last_chapter,
                    parse_first_chapter,
                    parse_last_chapter,
                )
            )
            chapter_idx += 1
        file_reports.append(FileReport(filename=filename, chapters=file_chapter_reports))

    # Always return a MultiFileReport (UI can render single-file case too)
    return MultiFileReport(file_reports=file_reports)

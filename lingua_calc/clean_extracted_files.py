from __future__ import annotations

import re
from typing import Iterable, List
import unicodedata

from lingua_calc.docx_extract import TextChapter


def _clean_text(text: str) -> str:
    if not text:
        return text
    # remove bracketed sections like [note]
    cleaned = re.sub(r"\[[^\]]*\]", "", text)
    out_lines: list[str] = []
    for line in cleaned.splitlines():
        s = line.strip()
        if not s:
            out_lines.append("")
            continue
        if unicodedata.normalize("NFC", s).startswith(unicodedata.normalize("NFC", "μέρος")):
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def clean_chapters(chapters: Iterable[TextChapter]) -> List[TextChapter]:
    """Return a new list of TextChapter with cleaned text.

    This is a preprocessing step separate from the LLM provider.
    """
    out: list[TextChapter] = []
    for ch in chapters:
        cleaned = _clean_text(ch.text)
        out.append(TextChapter(id=ch.id, title=ch.title, text=cleaned))
    return out


__all__ = ["clean_chapters", "_clean_text"]

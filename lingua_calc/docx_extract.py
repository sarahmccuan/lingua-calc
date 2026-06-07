from __future__ import annotations

import io
import re
from dataclasses import dataclass

from docx import Document


@dataclass(frozen=True)
class TextChapter:
    id: str
    title: str
    text: str


def _heading_level(style_name: str | None) -> int | None:
    if not style_name:
        return None
    m = re.match(r"Heading\s*(\d+)", style_name, re.I)
    if m:
        return int(m.group(1))
    return None


def extract_chapters_from_docx(data: bytes) -> list[TextChapter]:
    """Split .docx body into chapters using Word 'Heading 1' paragraphs when present.

    If no Heading 1 exists, returns a single chapter with the full document text.
    """
    doc = Document(io.BytesIO(data))
    chapters_meta: list[tuple[str, str]] = []
    buffer: list[str] = []
    current_title = "Document"

    def body_text() -> str:
        return "\n".join(p.strip() for p in buffer if p.strip()).strip()

    for para in doc.paragraphs:
        style = para.style.name if para.style is not None else None
        level = _heading_level(style)
        raw = para.text or ""
        if level == 1 and raw.strip():
            prev = body_text()
            buffer.clear()
            if prev:
                chapters_meta.append((current_title, prev))
            current_title = raw.strip()
            continue
        if raw.strip():
            buffer.append(raw)

    tail = body_text()
    buffer.clear()
    if tail:
        chapters_meta.append((current_title, tail))

    if not chapters_meta:
        return [TextChapter(id="ch-1", title="Document", text="")]

    out: list[TextChapter] = []
    for i, (title, text) in enumerate(chapters_meta, start=1):
        slug = re.sub(r"\s+", "-", title.lower())
        slug = re.sub(r"[^a-z0-9-]", "", slug) or f"ch-{i}"
        cid = f"{i}-{slug}"[:64]
        out.append(TextChapter(id=cid, title=title, text=text))

    return out

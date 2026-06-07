from __future__ import annotations

from typing import Protocol, runtime_checkable

from lingua_calc.models import ParsedToken


@runtime_checkable
class LemmatizeParseProvider(Protocol):
    def analyze_chapter(self, text: str, chapter_title: str) -> list[ParsedToken]:
        """Return token analyses in linear order matching the source text."""
        ...

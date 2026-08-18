from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TranslationMessage:
    language: int
    source_language: int
    context: str
    source: str
    translation: str
    comment: str
    extra_comment: str
    location: str


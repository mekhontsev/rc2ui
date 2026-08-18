from __future__ import annotations

from enum import StrEnum


class LayoutMode(StrEnum):
    """Layout planning policy for generated forms."""

    FAITHFUL = "faithful"
    SIMPLIFIED = "simplified"

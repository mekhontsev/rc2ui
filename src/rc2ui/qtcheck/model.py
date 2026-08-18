from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from rc2ui.domain.diagnostics import Diagnostic


class QtCheckMode(StrEnum):
    AUTO = "auto"
    REQUIRED = "required"
    OFF = "off"


@dataclass(frozen=True, slots=True)
class ControlGeometryReference:
    """Raw RC topology plus the geometry intentionally emitted by layout."""

    object_name: str
    rect_dlu: tuple[int, int, int, int]
    layout_rect_dlu: tuple[int, int, int, int] | None = None
    separator_orientation: str | None = None
    qt_class: str | None = None
    horizontal_anchor: tuple[str, int] | None = None
    vertical_anchor: tuple[str, int] | None = None
    alternative_states: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class FormGeometryReference:
    rect_dlu: tuple[int, int, int, int]
    controls: tuple[ControlGeometryReference, ...]
    layout_rect_dlu: tuple[int, int, int, int] | None = None


@dataclass(frozen=True, slots=True)
class QtCheckRun:
    available: bool
    checked_forms: int
    diagnostics: tuple[Diagnostic, ...]
    report_path: Path | None = None
    preview_index: Path | None = None
    qt_version: str | None = None
    binding: str | None = None
    binding_version: str | None = None

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from rc2ui.domain.diagnostics import Diagnostic


class QtCheckMode(StrEnum):
    AUTO = "auto"
    REQUIRED = "required"
    OFF = "off"


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    """Runtime sizes and dynamic-font factors exercised by Qt checks."""

    font_scales: tuple[float, ...] = (2.0,)
    resize_scales: tuple[float, ...] = (0.75, 1.0, 1.5)

    def __post_init__(self) -> None:
        _positive_factors(self.font_scales, "font_scales")
        _positive_factors(self.resize_scales, "resize_scales")


def _positive_factors(values: tuple[float, ...], name: str) -> None:
    if not values:
        raise ValueError(f"{name} must be a non-empty array")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        for value in values
    ):
        raise ValueError(f"{name} must contain positive finite numbers")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must not contain duplicates")


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

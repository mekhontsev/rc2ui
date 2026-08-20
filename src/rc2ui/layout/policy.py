from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Iterable

from rc2ui.layout.mode import LayoutMode


class GapGrowth(StrEnum):
    PROPORTIONAL = "proportional"
    MINIMUM = "minimum"
    OUTER_MINIMUM = "outer-minimum"


class RuntimeAlternativesPolicy(StrEnum):
    AUTO = "auto"
    SOURCE_ORDER = "source-order"
    OFF = "off"


class SimplifiedProfile(StrEnum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


def _factor(value: float, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be a positive finite number")


@dataclass(frozen=True, slots=True)
class SimplifiedPolicy:
    profile: SimplifiedProfile = SimplifiedProfile.BALANCED
    max_serialized_tracks: int = 5

    def __post_init__(self) -> None:
        if not isinstance(self.profile, SimplifiedProfile):
            raise ValueError("simplified profile must be a SimplifiedProfile")
        if (
            isinstance(self.max_serialized_tracks, bool)
            or not isinstance(self.max_serialized_tracks, int)
            or self.max_serialized_tracks < 2
        ):
            raise ValueError("max_serialized_tracks must be an integer >= 2")


@dataclass(frozen=True, slots=True)
class LayoutPolicy:
    mode: LayoutMode = LayoutMode.FAITHFUL
    alignment_tolerance_dlu: int = 3
    text_width_safety_factor: float = 1.1
    max_designer_width_factor: float = 1.5
    gap_growth: GapGrowth = GapGrowth.PROPORTIONAL
    runtime_alternatives: RuntimeAlternativesPolicy = (
        RuntimeAlternativesPolicy.AUTO
    )
    simplified: SimplifiedPolicy = SimplifiedPolicy()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, LayoutMode):
            raise ValueError("layout mode must be a LayoutMode")
        if (
            isinstance(self.alignment_tolerance_dlu, bool)
            or not isinstance(self.alignment_tolerance_dlu, int)
            or self.alignment_tolerance_dlu < 0
        ):
            raise ValueError(
                "alignment_tolerance_dlu must be a non-negative integer"
            )
        _factor(
            self.text_width_safety_factor,
            "text_width_safety_factor",
        )
        _factor(
            self.max_designer_width_factor,
            "max_designer_width_factor",
        )
        if self.text_width_safety_factor < 1:
            raise ValueError("text_width_safety_factor must be >= 1")
        if self.max_designer_width_factor < 1:
            raise ValueError("max_designer_width_factor must be >= 1")
        if not isinstance(self.gap_growth, GapGrowth):
            raise ValueError("gap_growth must be a GapGrowth")
        if not isinstance(
            self.runtime_alternatives,
            RuntimeAlternativesPolicy,
        ):
            raise ValueError(
                "runtime_alternatives must be a RuntimeAlternativesPolicy"
            )


@dataclass(frozen=True, slots=True)
class LayoutOverride:
    name: str
    dialog: str | None = None
    dialog_regex: str | None = None
    priority: int = 0
    mode: LayoutMode | None = None
    alignment_tolerance_dlu: int | None = None
    text_width_safety_factor: float | None = None
    max_designer_width_factor: float | None = None
    gap_growth: GapGrowth | None = None
    runtime_alternatives: RuntimeAlternativesPolicy | None = None
    simplified_profile: SimplifiedProfile | None = None
    max_serialized_tracks: int | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("layout override name cannot be empty")
        if (self.dialog is None) == (self.dialog_regex is None):
            raise ValueError(
                f"layout override {self.name!r} requires exactly one of "
                "dialog or dialog_regex"
            )
        if self.dialog == "":
            raise ValueError("layout override dialog cannot be empty")
        if self.dialog_regex is not None:
            if not self.dialog_regex:
                raise ValueError("layout override dialog_regex cannot be empty")
            try:
                re.compile(self.dialog_regex)
            except re.error as error:
                raise ValueError(
                    f"layout override {self.name!r} has invalid dialog_regex: "
                    f"{error}"
                ) from error
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError("layout override priority must be an integer")
        # Reuse the complete policy validation for all supplied values.
        base = LayoutPolicy()
        self.apply(base)

    @property
    def exact(self) -> bool:
        return self.dialog is not None

    def matches(self, candidates: Iterable[str]) -> bool:
        if self.dialog is not None:
            return self.dialog in candidates
        assert self.dialog_regex is not None
        return any(
            re.fullmatch(self.dialog_regex, candidate) is not None
            for candidate in candidates
        )

    def apply(self, base: LayoutPolicy) -> LayoutPolicy:
        simplified = replace(
            base.simplified,
            **{
                key: value
                for key, value in {
                    "profile": self.simplified_profile,
                    "max_serialized_tracks": self.max_serialized_tracks,
                }.items()
                if value is not None
            },
        )
        return replace(
            base,
            simplified=simplified,
            **{
                key: value
                for key, value in {
                    "mode": self.mode,
                    "alignment_tolerance_dlu": self.alignment_tolerance_dlu,
                    "text_width_safety_factor": self.text_width_safety_factor,
                    "max_designer_width_factor": self.max_designer_width_factor,
                    "gap_growth": self.gap_growth,
                    "runtime_alternatives": self.runtime_alternatives,
                }.items()
                if value is not None
            },
        )


@dataclass(frozen=True, slots=True)
class LayoutPolicySet:
    default: LayoutPolicy = LayoutPolicy()
    overrides: tuple[LayoutOverride, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.default, LayoutPolicy):
            raise ValueError("default layout policy must be a LayoutPolicy")
        if not isinstance(self.overrides, tuple) or not all(
            isinstance(item, LayoutOverride) for item in self.overrides
        ):
            raise ValueError("layout overrides must be a tuple of LayoutOverride")

    def resolve(
        self,
        candidates: Iterable[str],
        *,
        mode: LayoutMode | None = None,
    ) -> LayoutPolicy:
        base = (
            replace(self.default, mode=mode)
            if mode is not None
            else self.default
        )
        values = tuple(dict.fromkeys(candidates))
        matches = [item for item in self.overrides if item.matches(values)]
        if not matches:
            return base
        precedence = max((item.priority, item.exact) for item in matches)
        winners = [
            item
            for item in matches
            if (item.priority, item.exact) == precedence
        ]
        if len(winners) > 1:
            names = ", ".join(repr(item.name) for item in winners)
            raise ValueError(
                "ambiguous layout overrides for "
                f"{', '.join(values) or '<unknown dialog>'}: {names}"
            )
        return winners[0].apply(base)

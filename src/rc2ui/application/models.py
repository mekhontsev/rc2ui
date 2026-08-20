from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rc2ui.domain.diagnostics import Diagnostic
from rc2ui.layout.mode import LayoutMode
from rc2ui.layout.policy import LayoutPolicySet
from rc2ui.mapping.overrides import ControlMap
from rc2ui.naming.map import NamingMap
from rc2ui.qtcheck.model import QtCheckMode, ValidationPolicy
from rc2ui.semantics.config import SemanticMap


@dataclass(frozen=True, slots=True)
class DialogSelection:
    """Optional allowlist for dialogs in one resource namespace."""

    exact: tuple[str, ...] = ()
    regex: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for selector in (*self.exact, *self.regex):
            if not isinstance(selector, str) or not selector:
                raise ValueError("dialog selectors must be non-empty strings")
        for expression in self.regex:
            try:
                re.compile(expression)
            except re.error as error:
                raise ValueError(
                    f"invalid dialog regex {expression!r}: {error}"
                ) from error

    @property
    def enabled(self) -> bool:
        return bool(self.exact or self.regex)

    def matches(self, candidates: Iterable[str]) -> bool:
        if not self.enabled:
            return True
        values = tuple(candidates)
        return any(value in self.exact for value in values) or any(
            re.fullmatch(expression, value) is not None
            for expression in self.regex
            for value in values
        )


@dataclass(frozen=True, slots=True)
class InputGroup:
    """RC sources and compiled containers belonging to one resource namespace."""

    rc_files: tuple[Path, ...]
    resource_files: tuple[Path, ...]
    dialog_selection: DialogSelection = DialogSelection()

    def __post_init__(self) -> None:
        if not self.rc_files:
            raise ValueError("an input group requires at least one RC source")
        if not self.resource_files:
            raise ValueError(
                "an input group requires at least one compiled resource"
            )


@dataclass(frozen=True, slots=True)
class ProjectRules:
    """Validated customization sections from the project configuration."""

    naming: NamingMap = NamingMap(())
    controls: ControlMap = ControlMap((), ())
    semantics: SemanticMap = SemanticMap(())


@dataclass(frozen=True, slots=True)
class ConversionRequest:
    project_root: Path
    output_dir: Path
    input_groups: tuple[InputGroup, ...]
    rules: ProjectRules = ProjectRules()
    config_path: Path | None = None
    include_paths: tuple[Path, ...] = ()
    defines: tuple[tuple[str, int], ...] = ()
    rc_encoding: str = "cp1251"
    default_language: int = 1033
    strict: bool = False
    layout_mode: LayoutMode = LayoutMode.FAITHFUL
    layout_policies: LayoutPolicySet = LayoutPolicySet()
    ui_comments: bool = True
    qt_check: QtCheckMode = QtCheckMode.AUTO
    qt_preview_dir: Path | None = None
    qt_font_scale: float = 1.0
    validation: ValidationPolicy = ValidationPolicy()

    def __post_init__(self) -> None:
        if not self.input_groups:
            raise ValueError("a conversion request requires an input group")
        if not isinstance(self.layout_mode, LayoutMode):
            raise ValueError("layout_mode must be a LayoutMode value")
        if not isinstance(self.layout_policies, LayoutPolicySet):
            raise ValueError("layout_policies must be a LayoutPolicySet")
        if not isinstance(self.ui_comments, bool):
            raise ValueError("ui_comments must be a boolean")
        if not math.isfinite(self.qt_font_scale) or self.qt_font_scale <= 0:
            raise ValueError("qt_font_scale must be a positive finite number")
        if not isinstance(self.validation, ValidationPolicy):
            raise ValueError("validation must be a ValidationPolicy")


@dataclass(frozen=True, slots=True)
class ControlArtifact:
    rc_id: str
    occurrence: int
    win_class: str
    object_name: str
    qt_class: str
    style: str
    extended_style: str
    rect_dlu: tuple[int, int, int, int]
    layout_rect_dlu: tuple[int, int, int, int]
    name_source: str
    confidence: float
    evidence: tuple[str, ...]
    separator_orientation: str | None = None
    horizontal_anchor: tuple[str, int] | None = None
    vertical_anchor: tuple[str, int] | None = None
    alternative_states: tuple[tuple[int, int], ...] = ()
    emitted: bool = True
    compound_kind: str | None = None
    mapping_rule: str | None = None
    button_group: str | None = None
    runtime_configured: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompoundArtifact:
    kind: str
    action: str
    primary_order: int
    orders: tuple[int, ...]
    source_ids: tuple[str, ...]
    object_names: tuple[str, ...]
    confidence: float
    evidence: tuple[str, ...]
    supporting_languages: tuple[int, ...]
    eligible_languages: tuple[int, ...]
    geometry: str = "union"
    rule_name: str | None = None
    result_class: str | None = None
    conflict: str | None = None


@dataclass(frozen=True, slots=True)
class LanguageAlignmentArtifact:
    language: int
    matched_controls: int
    default_controls: int
    variant_controls: int
    confidence: float


@dataclass(frozen=True, slots=True)
class LayoutEvidenceArtifact:
    group_memberships: int
    rejected_group_memberships: int
    row_alignments: int
    column_alignments: int
    overlaps: int
    runtime_alternatives: int
    rejected_runtime_alternatives: int


@dataclass(frozen=True, slots=True)
class RelationEvidenceArtifact:
    relation: str
    orders: tuple[int, ...]
    object_names: tuple[str, ...]
    confidence: float
    supporting_languages: tuple[int, ...]
    eligible_languages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class LayoutPolicyArtifact:
    alignment_tolerance_dlu: int
    text_width_safety_factor: float
    max_designer_width_factor: float
    gap_growth: str
    runtime_alternatives: str
    simplified_profile: str
    max_serialized_tracks: int


@dataclass(frozen=True, slots=True)
class SpacerArtifact:
    total: int
    explicit_gaps: int
    extent_markers: int
    hidden_extents: int
    font_floors: int
    trailing_tracks: int
    other: int


@dataclass(frozen=True, slots=True)
class FormArtifact:
    source: str
    rc_id: str
    language: int
    object_name: str
    output: Path
    controls: tuple[ControlArtifact, ...]
    available_languages: tuple[int, ...]
    default_rect_dlu: tuple[int, int, int, int] = (0, 0, 0, 0)
    layout_rect_dlu: tuple[int, int, int, int] = (0, 0, 0, 0)
    geometry_languages: tuple[int, ...] = ()
    translation_languages: tuple[int, ...] = ()
    language_alignments: tuple[LanguageAlignmentArtifact, ...] = ()
    layout_evidence: LayoutEvidenceArtifact | None = None
    layout_relations: tuple[RelationEvidenceArtifact, ...] = ()
    compounds: tuple[CompoundArtifact, ...] = ()
    layout_mode_requested: str = LayoutMode.FAITHFUL.value
    layout_mode_used: str = LayoutMode.FAITHFUL.value
    layout_policy: LayoutPolicyArtifact | None = None
    editability_score: float = 0.0
    simplified_regions: int = 0
    faithful_fallback_regions: int = 0
    layout_transformations: tuple[str, ...] = ()
    spacer_transformations: tuple[str, ...] = ()
    spacers_removed: int = 0
    spacers: SpacerArtifact | None = None


@dataclass(frozen=True, slots=True)
class BatchResult:
    forms: tuple[FormArtifact, ...]
    diagnostics: tuple[Diagnostic, ...]
    report_path: Path
    suggestions_path: Path
    translation_paths: tuple[Path, ...] = ()
    qt_report_path: Path | None = None
    qt_preview_index: Path | None = None

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.diagnostics)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.diagnostics)

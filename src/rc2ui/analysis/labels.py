from __future__ import annotations

from dataclasses import dataclass

from rc2ui.analysis.visual_geometry import control_visual_rect
from rc2ui.domain.geometry import RectDlu
from rc2ui.mapping.model import ControlRole, MappedControl


@dataclass(frozen=True, slots=True)
class LabelAssociation:
    label_order: int
    target_order: int
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _LabelCandidate:
    geometry_score: float
    order_hint: float
    label_order: int
    target_order: int
    evidence: tuple[str, ...]


def match_labels(controls: tuple[MappedControl, ...]) -> tuple[LabelAssociation, ...]:
    groups = _enclosing_groups(controls)
    candidates: list[_LabelCandidate] = []
    for label in controls:
        if label.role is not ControlRole.LABEL or not label.control.text:
            continue
        for target in controls:
            if not _can_be_buddy(target):
                continue
            score, order_hint, evidence = _score_pair(
                label,
                target,
                controls,
                groups,
            )
            if score >= 45:
                candidates.append(
                    _LabelCandidate(
                        geometry_score=score,
                        order_hint=order_hint,
                        label_order=label.control.order,
                        target_order=target.control.order,
                        evidence=evidence,
                    )
                )

    used_labels: set[int] = set()
    used_targets: set[int] = set()
    result: list[LabelAssociation] = []
    for candidate in sorted(
        candidates,
        # Geometry is the primary key. Resource order is deliberately only a
        # tie-breaker and cannot make a more distant field beat a closer one.
        key=lambda item: (
            -item.geometry_score,
            -item.order_hint,
            item.label_order,
            item.target_order,
        ),
    ):
        if (
            candidate.label_order in used_labels
            or candidate.target_order in used_targets
        ):
            continue
        used_labels.add(candidate.label_order)
        used_targets.add(candidate.target_order)
        result.append(
            LabelAssociation(
                label_order=candidate.label_order,
                target_order=candidate.target_order,
                confidence=min(0.99, candidate.geometry_score / 100),
                evidence=candidate.evidence,
            )
        )
    return tuple(sorted(result, key=lambda item: item.label_order))


def _can_be_buddy(control: MappedControl) -> bool:
    if control.role is not ControlRole.INPUT:
        return False
    return control.qt_class not in {"QCheckBox", "QRadioButton", "QScrollBar"}


def _score_pair(
    label: MappedControl,
    target: MappedControl,
    controls: tuple[MappedControl, ...],
    groups: dict[int, int | None],
) -> tuple[float, float, tuple[str, ...]]:
    label_rect = control_visual_rect(label.control)
    target_rect = control_visual_rect(target.control)
    score = 0.0
    evidence: list[str] = []

    vertical_delta = min(
        abs(left - right)
        for left, right in (
            (label_rect.top, target_rect.top),
            (label_rect.center_y, target_rect.center_y),
            (label_rect.bottom, target_rect.bottom),
        )
    )
    row_tolerance = max(3.0, min(label_rect.height, target_rect.height) * 0.55)
    horizontal_gap = target_rect.left - label_rect.right
    same_row = horizontal_gap >= -2 and vertical_delta <= row_tolerance
    if same_row:
        score += 70
        score -= min(max(horizontal_gap, 0), 30) * 0.8
        evidence.append("target is directly to the right on the same row")
        evidence.append(f"horizontal gap: {horizontal_gap} DLU")
    else:
        vertical_gap = target_rect.top - label_rect.bottom
        left_delta = abs(target_rect.left - label_rect.left)
        if vertical_gap >= -1 and vertical_gap <= 12 and left_delta <= 4:
            score += 52
            score -= vertical_gap
            evidence.append("target is directly below the label")

    order_hint = 0.0
    order_delta = target.control.order - label.control.order
    if order_delta == 1:
        order_hint = 1.0
        evidence.append("resource order supports an otherwise geometric match")
    elif 1 < order_delta <= 3:
        order_hint = 0.5

    if groups[label.control.order] == groups[target.control.order]:
        score += 12
        evidence.append("label and target share a visual group")
    else:
        score -= 60

    if same_row and _has_horizontal_blocker(label, target, controls):
        score -= 35
    if "&" in (label.control.text or ""):
        score += 5
        evidence.append("label contains a keyboard mnemonic")
    return score, order_hint, tuple(evidence)


def _has_horizontal_blocker(
    label: MappedControl,
    target: MappedControl,
    controls: tuple[MappedControl, ...],
) -> bool:
    label_rect = control_visual_rect(label.control)
    target_rect = control_visual_rect(target.control)
    left = label_rect.right
    right = target_rect.left
    center_y = (label_rect.center_y + target_rect.center_y) / 2
    for other in controls:
        if other.control.order in {label.control.order, target.control.order}:
            continue
        rect = control_visual_rect(other.control)
        if left < rect.center_x < right and rect.top - 2 <= center_y <= rect.bottom + 2:
            return True
    return False


def _enclosing_groups(controls: tuple[MappedControl, ...]) -> dict[int, int | None]:
    groups = [control for control in controls if control.role is ControlRole.GROUP]
    result: dict[int, int | None] = {}
    for control in controls:
        control_rect = control_visual_rect(control.control)
        containing = [
            group
            for group in groups
            if group.control.order != control.control.order
            and _contains(
                group.control.rect,
                control_rect.center_x,
                control_rect.center_y,
            )
        ]
        containing.sort(
            key=lambda group: (
                group.control.rect.width * group.control.rect.height,
                group.control.rect.top,
                group.control.rect.left,
                group.control.rect.bottom,
                group.control.rect.right,
                group.control.order,
            )
        )
        result[control.control.order] = (
            containing[0].control.order if containing else None
        )
    return result


def _contains(rect: RectDlu, x: float, y: float) -> bool:
    return rect.left <= x <= rect.right and rect.top <= y <= rect.bottom

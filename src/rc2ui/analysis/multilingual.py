from __future__ import annotations

from dataclasses import dataclass, replace
from math import inf
from statistics import median
from typing import Callable

from rc2ui.analysis.topology import (
    TopologyItem,
    TopologyRejection,
    select_topology_preserving_rects,
)
from rc2ui.analysis.visual_geometry import control_visual_rect
from rc2ui.domain.diagnostics import Diagnostic, Severity
from rc2ui.domain.dialog import Control, Dialog
from rc2ui.domain.geometry import RectDlu
from rc2ui.domain.resource_id import ResourceId


_UNMATCHED_COST = 105.0
_ORDER_TIE_BREAK_COST = 1e-9


class DefaultLanguageUnavailable(ValueError):
    def __init__(self, requested: int, available: tuple[int, ...]) -> None:
        self.requested = requested
        self.available = available
        super().__init__(
            f"default LANGID {requested} is unavailable; available: "
            + ", ".join(str(item) for item in available)
        )


@dataclass(frozen=True, slots=True)
class AlignedLanguageVariant:
    language: int
    dialog: Dialog
    controls: tuple[Control | None, ...]
    match_costs: tuple[float | None, ...]

    @property
    def matched_controls(self) -> int:
        return sum(control is not None for control in self.controls)

    @property
    def match_confidence(self) -> float:
        if not self.controls:
            return 1.0
        scores = (
            0.0
            if cost is None
            else 1.0 - min(1.0, max(0.0, cost) / _UNMATCHED_COST)
            for cost in self.match_costs
        )
        return sum(scores) / len(self.controls)

    def for_order(self, order: int) -> Control | None:
        return self.controls[order]


@dataclass(frozen=True, slots=True)
class PairRelationHint:
    orders: tuple[int, int]
    confidence: float
    supporting_languages: tuple[int, ...]
    eligible_languages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ParentRelationHint:
    order: int
    parent_order: int | None
    confidence: float
    supporting_languages: tuple[int, ...]
    eligible_languages: tuple[int, ...]
    tested_parent_order: int | None = None


@dataclass(frozen=True, slots=True)
class MultilingualLayoutHints:
    parents: tuple[ParentRelationHint, ...] = ()
    same_rows: tuple[PairRelationHint, ...] = ()
    same_columns: tuple[PairRelationHint, ...] = ()
    overlaps: tuple[PairRelationHint, ...] = ()
    alternatives: tuple[PairRelationHint, ...] = ()
    rejected_alternatives: tuple[PairRelationHint, ...] = ()

    @property
    def same_row_pairs(self) -> frozenset[frozenset[int]]:
        return frozenset(frozenset(item.orders) for item in self.same_rows)

    @property
    def same_column_pairs(self) -> frozenset[frozenset[int]]:
        return frozenset(frozenset(item.orders) for item in self.same_columns)

    @property
    def alternative_pairs(self) -> frozenset[frozenset[int]]:
        return frozenset(frozenset(item.orders) for item in self.alternatives)

    @property
    def rejected_alternative_pairs(self) -> frozenset[frozenset[int]]:
        return frozenset(
            frozenset(item.orders) for item in self.rejected_alternatives
        )


@dataclass(frozen=True, slots=True)
class _ParentCandidate:
    area: float
    negative_confidence: float
    visual_position: tuple[int, int, int, int]
    group_order: int
    supporting_languages: tuple[int, ...]
    eligible_languages: tuple[int, ...]

    @property
    def sort_key(self) -> tuple[float, float, int, int, int, int, int]:
        return (
            self.area,
            self.negative_confidence,
            *self.visual_position,
            self.group_order,
        )


@dataclass(frozen=True, slots=True)
class MultilingualDialog:
    """One default-language dialog enriched by aligned language variants."""

    dialog: Dialog
    default_dialog: Dialog
    layout_dialog: Dialog
    variants: tuple[AlignedLanguageVariant, ...]
    available_languages: tuple[int, ...]
    geometry_languages: tuple[int, ...]
    layout_hints: MultilingualLayoutHints
    diagnostics: tuple[Diagnostic, ...]

    @property
    def default_language(self) -> int:
        return self.default_dialog.key.language or 0


def fuse_dialog_languages(
    dialogs: tuple[Dialog, ...],
    requested_language: int | None,
) -> MultilingualDialog:
    if not dialogs:
        raise ValueError("at least one dialog language is required")
    ordered = tuple(sorted(dialogs, key=lambda item: item.key.language or 0))
    default = _choose_default(ordered, requested_language)
    diagnostics: list[Diagnostic] = []

    aligned: list[AlignedLanguageVariant] = []
    for variant in ordered:
        if variant is default:
            continue
        result = _align_variant(default, variant)
        aligned.append(result)
        diagnostics.extend(_alignment_diagnostics(default, result))

    layout_hints = _build_layout_hints(default, tuple(aligned))
    consensus, topology_rejections = _consensus_geometry(
        default,
        tuple(aligned),
    )
    diagnostics.extend(
        _topology_diagnostics(default, topology_rejections)
    )
    languages = tuple(item.key.language or 0 for item in ordered)
    geometry_languages = (default.key.language or 0,) + tuple(
        item.language for item in aligned if item.matched_controls
    )
    return MultilingualDialog(
        dialog=default,
        default_dialog=default,
        layout_dialog=consensus,
        variants=tuple(aligned),
        available_languages=languages,
        geometry_languages=tuple(sorted(set(geometry_languages))),
        layout_hints=layout_hints,
        diagnostics=tuple(diagnostics),
    )


def _build_layout_hints(
    default: Dialog,
    variants: tuple[AlignedLanguageVariant, ...],
) -> MultilingualLayoutHints:
    if not variants:
        return MultilingualLayoutHints()
    samples = (
        (
            default.key.language or 0,
            default,
            tuple(default.controls),
        ),
    ) + tuple(
        (variant.language, variant.dialog, variant.controls)
        for variant in variants
    )
    same_rows: list[PairRelationHint] = []
    same_columns: list[PairRelationHint] = []
    overlaps: list[PairRelationHint] = []
    alternatives: list[PairRelationHint] = []
    rejected_alternatives: list[PairRelationHint] = []
    for left_order in range(len(default.controls)):
        for right_order in range(left_order + 1, len(default.controls)):
            eligible = tuple(
                (language, dialog, controls[left_order], controls[right_order])
                for language, dialog, controls in samples
                if controls[left_order] is not None
                and controls[right_order] is not None
            )
            if len(eligible) < 2:
                continue
            for collection, predicate in (
                (same_rows, _same_row),
                (same_columns, _same_column),
                (overlaps, _significant_overlap),
            ):
                hint = _voted_pair_hint(
                    left_order,
                    right_order,
                    eligible,
                    predicate,
                )
                if hint is not None:
                    collection.append(hint)

            alternative_hint = _voted_pair_hint(
                left_order,
                right_order,
                eligible,
                _runtime_alternative_score,
            )
            if alternative_hint is not None:
                alternatives.append(alternative_hint)
            else:
                supporting = tuple(
                    language
                    for language, dialog, left, right in eligible
                    if _runtime_alternative_score(left, right, dialog) >= 0.5
                )
                confidence = sum(
                    _runtime_alternative_score(left, right, dialog)
                    for _, dialog, left, right in eligible
                ) / len(eligible)
                if confidence <= 1 / 3:
                    rejected_alternatives.append(
                        PairRelationHint(
                            (left_order, right_order),
                            1.0 - confidence,
                            tuple(
                                language
                                for language, _, _, _ in eligible
                                if language not in supporting
                            ),
                            tuple(language for language, _, _, _ in eligible),
                        )
                    )

    return MultilingualLayoutHints(
        parents=_parent_hints(default, samples),
        same_rows=tuple(same_rows),
        same_columns=tuple(same_columns),
        overlaps=tuple(overlaps),
        alternatives=tuple(alternatives),
        rejected_alternatives=tuple(rejected_alternatives),
    )


def _voted_pair_hint(
    left_order: int,
    right_order: int,
    eligible: tuple[tuple[int, Dialog, Control, Control], ...],
    predicate: Callable[[Control, Control, Dialog], float],
) -> PairRelationHint | None:
    scores = tuple(
        predicate(left, right, dialog)
        for _, dialog, left, right in eligible
    )
    supporting = tuple(
        language
        for (language, _, _, _), score in zip(eligible, scores)
        if score >= 0.5
    )
    confidence = sum(scores) / len(scores)
    if confidence + 1e-9 < 2 / 3:
        return None
    return PairRelationHint(
        orders=(left_order, right_order),
        confidence=confidence,
        supporting_languages=supporting,
        eligible_languages=tuple(language for language, _, _, _ in eligible),
    )


def _parent_hints(
    default: Dialog,
    samples: tuple[
        tuple[int, Dialog, tuple[Control | None, ...]],
        ...,
    ],
) -> tuple[ParentRelationHint, ...]:
    group_orders = tuple(
        control.order for control in default.controls if _is_group_box(control)
    )
    hints: list[ParentRelationHint] = []
    for order in range(len(default.controls)):
        if order in group_orders:
            candidate_groups = tuple(
                group_order for group_order in group_orders if group_order != order
            )
        else:
            candidate_groups = group_orders
        candidates: list[_ParentCandidate] = []
        for group_order in candidate_groups:
            evidence = []
            for language, dialog, controls in samples:
                control = controls[order]
                group = controls[group_order]
                if control is None or group is None:
                    continue
                evidence.append(
                    (
                        language,
                        _group_membership_score(control, group, dialog),
                        group.rect.width * group.rect.height,
                    )
                )
            if len(evidence) < 2:
                continue
            confidence = sum(score for _, score, _ in evidence) / len(evidence)
            if confidence + 1e-9 < 2 / 3:
                continue
            # Nested group boxes all contain the child. Prefer the smallest
            # consistently observed container, then the strongest relation.
            area = median(item[2] for item in evidence)
            default_group_rect = control_visual_rect(
                default.controls[group_order]
            )
            candidates.append(
                _ParentCandidate(
                    area=area,
                    negative_confidence=-confidence,
                    visual_position=(
                        default_group_rect.top,
                        default_group_rect.left,
                        default_group_rect.bottom,
                        default_group_rect.right,
                    ),
                    group_order=group_order,
                    supporting_languages=tuple(
                        language
                        for language, score, _ in evidence
                        if score >= 0.5
                    ),
                    eligible_languages=tuple(
                        language for language, _, _ in evidence
                    ),
                )
            )
        if candidates:
            choice = min(candidates, key=lambda item: item.sort_key)
            hints.append(
                ParentRelationHint(
                    order=order,
                    parent_order=choice.group_order,
                    confidence=-choice.negative_confidence,
                    supporting_languages=choice.supporting_languages,
                    eligible_languages=choice.eligible_languages,
                    tested_parent_order=choice.group_order,
                )
            )
            continue

        default_control = default.controls[order]
        default_groups = [
            (group_order, default.controls[group_order])
            for group_order in candidate_groups
        ]
        default_parent = _containing_group(default_control, default_groups)
        if default_parent is None:
            continue
        rejection_evidence = []
        for language, dialog, controls in samples:
            control = controls[order]
            group = controls[default_parent]
            if control is None or group is None:
                continue
            rejection_evidence.append(
                (
                    language,
                    1.0 - _group_membership_score(control, group, dialog),
                )
            )
        if len(rejection_evidence) < 2:
            continue
        confidence = sum(score for _, score in rejection_evidence) / len(
            rejection_evidence
        )
        if confidence + 1e-9 < 2 / 3:
            continue
        hints.append(
            ParentRelationHint(
                order=order,
                parent_order=None,
                confidence=confidence,
                supporting_languages=tuple(
                    language
                    for language, score in rejection_evidence
                    if score >= 0.5
                ),
                eligible_languages=tuple(
                    language for language, _ in rejection_evidence
                ),
                tested_parent_order=default_parent,
            )
        )
    return tuple(hints)


def _same_row(left: Control, right: Control, dialog: Dialog) -> float:
    # A group box is a container boundary.  Its large rectangle commonly
    # overlaps controls from several rows (and sometimes from another pane),
    # so treating that overlap as a peer-row vote creates false transitive
    # alignment evidence.
    if _is_group_box(left) != _is_group_box(right):
        return 0.0
    left_rect = control_visual_rect(left)
    right_rect = control_visual_rect(right)
    tolerance = max(3 / max(dialog.rect.height, 1), 0.018)
    scale = max(dialog.rect.height, 1)
    delta = min(
        abs(left_value - right_value) / scale
        for left_value, right_value in (
            (left_rect.top, right_rect.top),
            (left_rect.center_y, right_rect.center_y),
            (left_rect.bottom, right_rect.bottom),
        )
    )
    return max(0.0, 1.0 - delta / (tolerance * 2))


def _same_column(left: Control, right: Control, dialog: Dialog) -> float:
    left_rect = control_visual_rect(left)
    right_rect = control_visual_rect(right)
    tolerance = max(3 / max(dialog.rect.width, 1), 0.014)
    scale = max(dialog.rect.width, 1)
    delta = min(
        abs(left_value - right_value) / scale
        for left_value, right_value in (
            (left_rect.left, right_rect.left),
            (left_rect.center_x, right_rect.center_x),
            (left_rect.right, right_rect.right),
        )
    )
    return max(0.0, 1.0 - delta / (tolerance * 2))


def _significant_overlap(left: Control, right: Control, dialog: Dialog) -> float:
    del dialog
    left_rect = control_visual_rect(left)
    right_rect = control_visual_rect(right)
    intersection = _intersection_area(left_rect, right_rect)
    smaller = min(
        left_rect.width * left_rect.height,
        right_rect.width * right_rect.height,
    )
    return min(1.0, intersection / smaller / 0.2) if smaller else 0.0


def _runtime_alternative_score(
    left: Control,
    right: Control,
    dialog: Dialog,
) -> float:
    del dialog
    if _is_group_box(left) or _is_group_box(right):
        return 0.0
    left_rect = control_visual_rect(left)
    right_rect = control_visual_rect(right)
    left_area = left_rect.width * left_rect.height
    right_area = right_rect.width * right_rect.height
    if left_area <= 0 or right_area <= 0:
        return 0.0
    intersection = _intersection_area(left_rect, right_rect)
    union = left_area + right_area - intersection
    coverage = intersection / min(left_area, right_area)
    iou = intersection / union if union else 0.0
    return min(1.0, coverage / 0.82, iou / 0.6)


def _containing_group(
    control: Control,
    groups: list[tuple[int, Control | None]],
) -> int | None:
    control_rect = control_visual_rect(control)
    containing = [
        (order, group)
        for order, group in groups
        if group is not None
        and group.rect.left <= control_rect.center_x <= group.rect.right
        and group.rect.top <= control_rect.center_y <= group.rect.bottom
        and group.rect.width * group.rect.height
        > control_rect.width * control_rect.height
    ]
    if not containing:
        return None
    return min(
        containing,
        key=lambda item: (
            item[1].rect.width * item[1].rect.height,
            item[1].rect.top,
            item[1].rect.left,
            item[1].rect.bottom,
            item[1].rect.right,
            item[0],
        ),
    )[0]


def _group_membership_score(
    control: Control,
    group: Control,
    dialog: Dialog,
) -> float:
    control_rect = control_visual_rect(control)
    if (
        group.rect.width * group.rect.height
        <= control_rect.width * control_rect.height
    ):
        return 0.0
    horizontal_distance = max(
        group.rect.left - control_rect.center_x,
        control_rect.center_x - group.rect.right,
        0.0,
    )
    vertical_distance = max(
        group.rect.top - control_rect.center_y,
        control_rect.center_y - group.rect.bottom,
        0.0,
    )
    horizontal_tolerance = max(3.0, dialog.rect.width * 0.018)
    vertical_tolerance = max(3.0, dialog.rect.height * 0.025)
    normalized_distance = (
        horizontal_distance / horizontal_tolerance
        + vertical_distance / vertical_tolerance
    )
    return max(0.0, 1.0 - normalized_distance)


def _is_group_box(control: Control) -> bool:
    return (
        control.class_name.casefold() == "button"
        and control.style & 0x0F == 0x07
    )


def _intersection_area(left: RectDlu, right: RectDlu) -> int:
    width = min(left.right, right.right) - max(left.left, right.left)
    height = min(left.bottom, right.bottom) - max(left.top, right.top)
    return max(0, width) * max(0, height)


def _choose_default(
    dialogs: tuple[Dialog, ...],
    requested: int | None,
) -> Dialog:
    if requested is not None:
        for dialog in dialogs:
            if dialog.key.language == requested:
                return dialog
        if len(dialogs) > 1:
            raise DefaultLanguageUnavailable(
                requested,
                tuple(item.key.language or 0 for item in dialogs),
            )
        return dialogs[0]
    preference = {0: 0, 1033: 1}
    return min(
        dialogs,
        key=lambda dialog: (
            preference.get(dialog.key.language or 0, 2),
            dialog.key.language or 0,
        ),
    )


def _align_variant(
    reference: Dialog,
    variant: Dialog,
) -> AlignedLanguageVariant:
    matched: dict[int, tuple[Control, float]] = {}
    used_target_orders: set[int] = set()
    reference_ids = _controls_by_id(reference.controls)
    target_ids = _controls_by_id(variant.controls)

    # A unique, non-static Win32 ID is stronger evidence than geometry or
    # z-order and remains stable when translators resize or reorder controls.
    for identity in sorted(
        reference_ids.keys() & target_ids.keys(),
        key=_sortable_identity,
    ):
        reference_candidates = reference_ids[identity]
        target_candidates = target_ids[identity]
        if (
            len(reference_candidates) != 1
            or len(target_candidates) != 1
            or identity == (-1, None)
        ):
            continue
        source_control = reference_candidates[0]
        target_control = target_candidates[0]
        if (
            source_control.class_name.casefold()
            != target_control.class_name.casefold()
        ):
            continue
        matched[source_control.order] = (target_control, 0.0)
        used_target_orders.add(target_control.order)

    remaining_reference = tuple(
        control for control in reference.controls if control.order not in matched
    )
    remaining_target = tuple(
        control
        for control in variant.controls
        if control.order not in used_target_orders
    )
    for source_index, target_index, cost in _minimum_cost_matching(
        remaining_reference,
        remaining_target,
        reference,
        variant,
    ):
        source_control = remaining_reference[source_index]
        target_control = remaining_target[target_index]
        matched[source_control.order] = (target_control, cost)

    controls: list[Control | None] = []
    costs: list[float | None] = []
    for control in sorted(reference.controls, key=lambda item: item.order):
        match = matched.get(control.order)
        controls.append(match[0] if match else None)
        costs.append(match[1] if match else None)
    return AlignedLanguageVariant(
        language=variant.key.language or 0,
        dialog=variant,
        controls=tuple(controls),
        match_costs=tuple(costs),
    )


def _minimum_cost_matching(
    reference: tuple[Control, ...],
    target: tuple[Control, ...],
    reference_dialog: Dialog,
    target_dialog: Dialog,
) -> tuple[tuple[int, int, float], ...]:
    if not reference or not target:
        return ()
    # Real rows/columns plus one dummy slot per control allow the global
    # assignment to leave genuinely unrelated controls unmatched.
    size = len(reference) + len(target)
    matrix: list[list[float]] = []
    for row in range(size):
        values: list[float] = []
        for column in range(size):
            if row < len(reference) and column < len(target):
                value = _control_cost(
                    reference[row],
                    target[column],
                    reference_dialog,
                    target_dialog,
                )
            elif row < len(reference) or column < len(target):
                value = _UNMATCHED_COST
            else:
                value = 0.0
            values.append(value)
        matrix.append(values)

    assignment = _hungarian(matrix)
    result = []
    for row, column in enumerate(assignment[: len(reference)]):
        if column >= len(target):
            continue
        cost = matrix[row][column]
        if cost < _UNMATCHED_COST:
            result.append((row, column, cost))
    return tuple(result)


def _control_cost(
    reference: Control,
    target: Control,
    reference_dialog: Dialog,
    target_dialog: Dialog,
) -> float:
    if reference.class_name.casefold() != target.class_name.casefold():
        return 10_000.0
    reference_identity = _resource_identity(reference.key.resource_id)
    target_identity = _resource_identity(target.key.resource_id)
    identity_cost = 0.0
    if (
        reference_identity == target_identity
        and reference_identity != (-1, None)
    ):
        identity_cost = -28.0
    elif reference_identity != (-1, None) and target_identity != (-1, None):
        identity_cost = 125.0

    reference_rect = _normalized_rect(
        control_visual_rect(reference),
        reference_dialog.rect,
    )
    target_rect = _normalized_rect(
        control_visual_rect(target),
        target_dialog.rect,
    )
    center_cost = 52.0 * (
        abs(reference_rect[0] - target_rect[0])
        + abs(reference_rect[1] - target_rect[1])
    )
    size_cost = 26.0 * (
        abs(reference_rect[2] - target_rect[2])
        + abs(reference_rect[3] - target_rect[3])
    )
    maximum_order = max(
        len(reference_dialog.controls),
        len(target_dialog.controls),
        1,
    )
    # RC order is z-order, not semantic identity. Keep it only as a tiny,
    # deterministic tie-break for controls whose class and geometry agree.
    order_cost = (
        _ORDER_TIE_BREAK_COST
        * abs(reference.order - target.order)
        / maximum_order
    )
    style_cost = 12.0 if (reference.style ^ target.style) & 0x1F else 0.0
    text_cost = 4.0 if bool(reference.text) != bool(target.text) else 0.0
    return (
        identity_cost
        + center_cost
        + size_cost
        + order_cost
        + style_cost
        + text_cost
    )


def _hungarian(costs: list[list[float]]) -> tuple[int, ...]:
    """Return a deterministic minimum-cost assignment for a square matrix."""

    size = len(costs)
    potentials_rows = [0.0] * (size + 1)
    potentials_columns = [0.0] * (size + 1)
    matched_row = [0] * (size + 1)
    previous_column = [0] * (size + 1)
    for row in range(1, size + 1):
        matched_row[0] = row
        minimum = [inf] * (size + 1)
        used = [False] * (size + 1)
        column = 0
        while True:
            used[column] = True
            current_row = matched_row[column]
            delta = inf
            next_column = 0
            for candidate in range(1, size + 1):
                if used[candidate]:
                    continue
                reduced = (
                    costs[current_row - 1][candidate - 1]
                    - potentials_rows[current_row]
                    - potentials_columns[candidate]
                )
                if reduced < minimum[candidate]:
                    minimum[candidate] = reduced
                    previous_column[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(size + 1):
                if used[candidate]:
                    potentials_rows[matched_row[candidate]] += delta
                    potentials_columns[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if matched_row[column] == 0:
                break
        while True:
            previous = previous_column[column]
            matched_row[column] = matched_row[previous]
            column = previous
            if column == 0:
                break

    assignment = [-1] * size
    for column in range(1, size + 1):
        if matched_row[column]:
            assignment[matched_row[column] - 1] = column - 1
    return tuple(assignment)


def _consensus_geometry(
    default: Dialog,
    variants: tuple[AlignedLanguageVariant, ...],
) -> tuple[Dialog, tuple[TopologyRejection, ...]]:
    # Keep the entire default-language dialog authoritative. Other languages
    # are projected into its coordinate system and only refine the temporary
    # geometry used for relationship/layout inference.
    target_rect = default.rect
    proposals: dict[int, RectDlu] = {}
    for control in sorted(default.controls, key=lambda item: item.order):
        samples = [
            _project_rect(
                control_visual_rect(control),
                default.rect,
                target_rect,
            )
        ]
        for variant in variants:
            matched = variant.for_order(control.order)
            if matched is not None:
                samples.append(
                    _project_rect(
                        control_visual_rect(matched),
                        variant.dialog.rect,
                        target_rect,
                    )
                )
        left = _consensus_edge(
            samples[0].left,
            tuple(sample.left for sample in samples),
        )
        top = _consensus_edge(
            samples[0].top,
            tuple(sample.top for sample in samples),
        )
        proposals[control.order] = RectDlu(
            left,
            top,
            samples[0].width,
            samples[0].height,
        )
    selection = select_topology_preserving_rects(
        tuple(
            TopologyItem(
                order=control.order,
                rect=control_visual_rect(control),
                is_container=_is_group_box(control),
            )
            for control in default.controls
        ),
        proposals,
        bounds=target_rect,
    )
    fused_controls = tuple(
        replace(control, rect=selection.rect_for(control.order))
        for control in default.controls
    )
    return (
        replace(default, rect=target_rect, controls=fused_controls),
        selection.rejections,
    )


def _consensus_edge(
    default_value: int,
    values: tuple[int, ...],
) -> int:
    if len(values) == 1:
        return default_value
    return round(median(values))


def _topology_diagnostics(
    default: Dialog,
    rejections: tuple[TopologyRejection, ...],
) -> tuple[Diagnostic, ...]:
    if not rejections:
        return ()
    controls = {control.order: control for control in default.controls}
    descriptions = []
    for rejection in rejections:
        control = controls[rejection.order]
        identity = control.key.resource_id.display_name
        reasons = "/".join(rejection.reasons)
        peer_text = (
            " against orders " + ",".join(str(order) for order in rejection.peers)
            if rejection.peers
            else ""
        )
        descriptions.append(
            f"{identity} (order {rejection.order}: {reasons}{peer_text})"
        )
    return (
        Diagnostic(
            code="language.topology-correction-rejected",
            severity=Severity.INFO,
            message=(
                "rejected multilingual geometry correction for "
                f"{len(rejections)} control(s) to preserve default topology: "
                + "; ".join(descriptions)
            ),
            location=_dialog_location(default),
        ),
    )


def _project_rect(
    rect: RectDlu,
    source: RectDlu,
    target: RectDlu,
) -> RectDlu:
    width = source.width or 1
    height = source.height or 1
    left = target.left + (rect.left - source.left) * target.width / width
    right = target.left + (rect.right - source.left) * target.width / width
    top = target.top + (rect.top - source.top) * target.height / height
    bottom = target.top + (rect.bottom - source.top) * target.height / height
    return RectDlu(
        round(left),
        round(top),
        max(0, round(right) - round(left)),
        max(0, round(bottom) - round(top)),
    )


def _normalized_rect(rect: RectDlu, dialog_rect: RectDlu) -> tuple[float, ...]:
    width = dialog_rect.width or 1
    height = dialog_rect.height or 1
    return (
        (rect.center_x - dialog_rect.left) / width,
        (rect.center_y - dialog_rect.top) / height,
        rect.width / width,
        rect.height / height,
    )


def _alignment_diagnostics(
    default: Dialog,
    variant: AlignedLanguageVariant,
) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    unmatched_default = [
        default.controls[order]
        for order, control in enumerate(variant.controls)
        if control is None
    ]
    used_orders = {
        control.order for control in variant.controls if control is not None
    }
    unmatched_variant = [
        control
        for control in variant.dialog.controls
        if control.order not in used_orders
    ]
    if unmatched_default or unmatched_variant:
        diagnostics.append(
            Diagnostic(
                code="language.structure-mismatch",
                severity=Severity.WARNING,
                message=(
                    f"LANGID {variant.language}: matched "
                    f"{variant.matched_controls}/{len(default.controls)} default "
                    f"controls; default-only {len(unmatched_default)}, "
                    f"variant-only {len(unmatched_variant)}"
                ),
                location=_dialog_location(default),
            )
        )

    incompatible = []
    for order, target in enumerate(variant.controls):
        if target is None:
            continue
        source = default.controls[order]
        if (
            source.class_name.casefold() != target.class_name.casefold()
            or (source.style ^ target.style) & 0x1F
        ):
            incompatible.append(order)
    if incompatible:
        diagnostics.append(
            Diagnostic(
                code="language.control-mismatch",
                severity=Severity.WARNING,
                message=(
                    f"LANGID {variant.language}: {len(incompatible)} matched "
                    "controls differ in Win32 class or type style"
                ),
                location=_dialog_location(default),
            )
        )
    return tuple(diagnostics)


def _controls_by_id(
    controls: tuple[Control, ...],
) -> dict[tuple[int | None, str | None], list[Control]]:
    result: dict[tuple[int | None, str | None], list[Control]] = {}
    for control in controls:
        result.setdefault(_resource_identity(control.key.resource_id), []).append(
            control
        )
    return result


def _resource_identity(resource_id: ResourceId) -> tuple[int | None, str | None]:
    return resource_id.ordinal, resource_id.name


def _sortable_identity(
    identity: tuple[int | None, str | None],
) -> tuple[bool, int, str]:
    return (
        identity[0] is None,
        identity[0] if identity[0] is not None else 0,
        identity[1] or "",
    )


def _dialog_location(dialog: Dialog) -> str:
    return f"{dialog.key.source}:{dialog.key.resource_id.display_name}"

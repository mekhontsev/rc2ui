from __future__ import annotations

import re
from dataclasses import replace
from typing import Callable

from rc2ui.analysis.labels import LabelAssociation, match_labels
from rc2ui.analysis.multilingual import MultilingualDialog
from rc2ui.analysis.visual_geometry import control_visual_rect
from rc2ui.domain.dialog import Control
from rc2ui.domain.geometry import RectDlu
from rc2ui.domain.resource_id import ResourceId
from rc2ui.mapping.model import ControlRole, MappedControl
from rc2ui.semantics.model import (
    CompoundAction,
    CompoundCandidate,
    CompoundGeometry,
    CompoundKind,
)


UDS_ALIGNRIGHT = 0x0004
UDS_ALIGNLEFT = 0x0008
UDS_AUTOBUDDY = 0x0010
UDS_HORZ = 0x0040
TBS_VERT = 0x0002

_BROWSE_TEXT = re.compile(
    r"(?:\.{3}|…|browse(?:\.{3})?|select(?:\.{3})?|choose(?:\.{3})?|"
    r"open(?:\.{3})?|обзор(?:\.{3})?|выбрать(?:\.{3})?)",
    re.IGNORECASE,
)
_BROWSE_ID = re.compile(r"(?:BROWSE|SELECT|CHOOSE|PICK|FOLDER|FILE|PATH)")
_VALUE_ID = re.compile(r"(?:VALUE|POSITION|POS|PERCENT|PCT|CURRENT|NUMBER)")
_ACTION_ID = re.compile(
    r"(?:ADD|NEW|INSERT|REMOVE|DELETE|DEL|UP|DOWN|EDIT|RENAME|CLEAR)"
)
_ACTION_TEXT = re.compile(
    r"(?:add|new|insert|remove|delete|up|down|edit|rename|clear|"
    r"добавить|новый|вставить|удалить|вверх|вниз|изменить|очистить)"
    r"(?:\.{3})?",
    re.IGNORECASE,
)
_NUMERIC_TEXT = re.compile(
    r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:\s*[%°A-Za-zА-Яа-я]+)?"
)


def detect_compounds(
    multilingual: MultilingualDialog,
    mapped_controls: tuple[MappedControl, ...],
) -> tuple[CompoundCandidate, ...]:
    """Find common compound controls without deciding destructive changes."""

    layout_by_order = {
        control.order: control
        for control in multilingual.layout_dialog.controls
    }
    layout_mapped = tuple(
        replace(mapped, control=layout_by_order[mapped.control.order])
        for mapped in mapped_controls
    )
    labels = match_labels(layout_mapped)
    candidates = (
        _detect_edit_updown(multilingual, layout_mapped, labels)
        + _detect_edit_browse(multilingual, layout_mapped, labels)
        + _detect_slider_value(multilingual, layout_mapped, labels)
        + _detect_list_actions(multilingual, layout_mapped, labels)
    )
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.primary_order,
                item.kind.value,
                item.orders,
            ),
        )
    )


def resource_selectors(resource_id: ResourceId) -> tuple[str, ...]:
    values = list(resource_id.symbols)
    values.append(
        f"#{resource_id.ordinal}"
        if resource_id.ordinal is not None
        else resource_id.name or ""
    )
    return tuple(dict.fromkeys(value for value in values if value))


def _detect_edit_updown(
    multilingual: MultilingualDialog,
    controls: tuple[MappedControl, ...],
    labels: tuple[LabelAssociation, ...],
) -> tuple[CompoundCandidate, ...]:
    edits = [
        mapped
        for mapped in controls
        if mapped.control.class_name.casefold() == "edit"
        and mapped.qt_class == "QLineEdit"
        and mapped.mapping_rule is None
    ]
    spins = [
        mapped
        for mapped in controls
        if mapped.control.class_name.casefold() == "msctls_updown32"
        and not mapped.control.style & UDS_HORZ
        and mapped.mapping_rule is None
    ]
    scored: list[tuple[float, MappedControl, MappedControl, tuple[str, ...]]] = []
    for edit in edits:
        for spin in spins:
            autobuddy = _is_autobuddy_pair(edit.control, spin.control)
            if spin.control.style & UDS_AUTOBUDDY and not autobuddy:
                continue
            if not autobuddy and not _same_visual_group(edit, spin, controls):
                continue
            score, evidence = _edit_updown_score(edit.control, spin.control)
            if score >= 0.68:
                scored.append((score, edit, spin, evidence))
    result: list[CompoundCandidate] = []
    for score, edit, spin, evidence in _select_disjoint_pairs(scored):
        result.append(
            _candidate(
                multilingual,
                CompoundKind.EDIT_UPDOWN,
                edit.control,
                (spin.control,),
                score,
                evidence,
                labels,
                CompoundAction.SUGGEST,
                score_fn=lambda left, right: _edit_updown_score(left, right)[0],
                geometry=_autobuddy_geometry(edit.control, spin.control),
            )
        )
    return tuple(result)


def _edit_updown_score(edit: Control, spin: Control) -> tuple[float, tuple[str, ...]]:
    if _is_autobuddy_pair(edit, spin):
        evidence = [
            "UDS_AUTOBUDDY binds the immediately preceding EDIT in z-order"
        ]
        geometry = _autobuddy_geometry(edit, spin)
        if geometry is CompoundGeometry.AUTOBUDDY_LEFT:
            evidence.append("UDS_ALIGNLEFT supplies runtime geometry")
        elif geometry is CompoundGeometry.AUTOBUDDY_RIGHT:
            evidence.append("UDS_ALIGNRIGHT supplies runtime geometry")
        return 0.99, tuple(evidence)
    if spin.style & UDS_AUTOBUDDY:
        return 0.0, ()

    edit_rect = control_visual_rect(edit)
    spin_rect = control_visual_rect(spin)
    overlap = _vertical_overlap_ratio(edit_rect, spin_rect)
    edge_delta = min(
        abs(spin_rect.left - edit_rect.right),
        abs(spin_rect.right - edit_rect.left),
        abs(spin_rect.right - edit_rect.right),
        abs(spin_rect.left - edit_rect.left),
    )
    narrow = spin_rect.width <= max(18, edit_rect.width * 0.35)
    if overlap < 0.5 or edge_delta > max(8, spin_rect.width):
        return 0.0, ()
    score = 0.38 + min(0.22, overlap * 0.22)
    score += max(0.0, 0.2 - edge_delta * 0.025)
    evidence = [
        "standard EDIT and msctls_updown32 classes",
        f"vertical overlap {overlap:.0%}",
        f"nearest edge delta {edge_delta} DLU",
    ]
    if narrow:
        score += 0.08
        evidence.append("up-down control is narrow relative to the edit")
    style_flags = spin.style & (UDS_AUTOBUDDY | UDS_ALIGNRIGHT | UDS_ALIGNLEFT)
    if style_flags:
        score += 0.12
        evidence.append("up-down buddy/alignment style is present")
    return min(score, 0.99), tuple(evidence)


def _is_autobuddy_pair(edit: Control, spin: Control) -> bool:
    return bool(spin.style & UDS_AUTOBUDDY) and spin.order == edit.order + 1


def _autobuddy_geometry(
    edit: Control,
    spin: Control,
) -> CompoundGeometry:
    if not _is_autobuddy_pair(edit, spin):
        return CompoundGeometry.UNION
    alignment = spin.style & (UDS_ALIGNLEFT | UDS_ALIGNRIGHT)
    if alignment == UDS_ALIGNLEFT:
        return CompoundGeometry.AUTOBUDDY_LEFT
    if alignment == UDS_ALIGNRIGHT:
        return CompoundGeometry.AUTOBUDDY_RIGHT
    return CompoundGeometry.UNION


def _detect_edit_browse(
    multilingual: MultilingualDialog,
    controls: tuple[MappedControl, ...],
    labels: tuple[LabelAssociation, ...],
) -> tuple[CompoundCandidate, ...]:
    edits = [
        mapped
        for mapped in controls
        if mapped.qt_class == "QLineEdit" and mapped.mapping_rule is None
    ]
    buttons = [
        mapped
        for mapped in controls
        if mapped.qt_class in {"QPushButton", "QToolButton"}
        and mapped.mapping_rule is None
        and _is_browse_button(multilingual, mapped.control.order)
    ]
    scored: list[tuple[float, MappedControl, MappedControl, tuple[str, ...]]] = []
    for edit in edits:
        for button in buttons:
            if not _same_visual_group(edit, button, controls):
                continue
            score, evidence = _horizontal_pair_score(
                edit.control,
                button.control,
                maximum_gap=14,
            )
            if score >= 0.7:
                scored.append(
                    (
                        min(0.99, score + 0.12),
                        edit,
                        button,
                        evidence + ("button text or ID denotes a browse action",),
                    )
                )
    return tuple(
        _candidate(
            multilingual,
            CompoundKind.EDIT_BROWSE,
            edit.control,
            (button.control,),
            score,
            evidence,
            labels,
            CompoundAction.BUNDLE,
            score_fn=lambda left, right: _horizontal_pair_score(
                left,
                right,
                maximum_gap=14,
            )[0],
        )
        for score, edit, button, evidence in _select_disjoint_pairs(scored)
    )


def _detect_slider_value(
    multilingual: MultilingualDialog,
    controls: tuple[MappedControl, ...],
    labels: tuple[LabelAssociation, ...],
) -> tuple[CompoundCandidate, ...]:
    sliders = [
        mapped
        for mapped in controls
        if mapped.control.class_name.casefold() == "msctls_trackbar32"
        and mapped.mapping_rule is None
    ]
    values = [mapped for mapped in controls if _is_value_display(mapped)]
    scored: list[tuple[float, MappedControl, MappedControl, tuple[str, ...]]] = []
    for slider in sliders:
        vertical = bool(slider.control.style & TBS_VERT)
        for value in values:
            if not _same_visual_group(slider, value, controls):
                continue
            score, evidence = _slider_value_score(
                slider.control,
                value.control,
                vertical=vertical,
            )
            if score >= 0.7:
                scored.append((score, slider, value, evidence))
    result: list[CompoundCandidate] = []
    for score, slider, value, evidence in _select_disjoint_pairs(scored):
        result.append(
            _candidate(
                multilingual,
                CompoundKind.SLIDER_VALUE,
                slider.control,
                (value.control,),
                score,
                evidence,
                labels,
                (
                    CompoundAction.BUNDLE
                    if score >= 0.84
                    else CompoundAction.SUGGEST
                ),
                score_fn=lambda left, right: _slider_value_score(
                    left,
                    right,
                    vertical=bool(left.style & TBS_VERT),
                )[0],
            )
        )
    return tuple(result)


def _detect_list_actions(
    multilingual: MultilingualDialog,
    controls: tuple[MappedControl, ...],
    labels: tuple[LabelAssociation, ...],
) -> tuple[CompoundCandidate, ...]:
    lists = [
        mapped
        for mapped in controls
        if mapped.qt_class in {"QListWidget", "QTreeWidget", "QTableWidget"}
        and mapped.mapping_rule is None
    ]
    buttons = [
        mapped
        for mapped in controls
        if mapped.qt_class in {"QPushButton", "QToolButton"}
        and mapped.mapping_rule is None
        and _is_list_action(multilingual, mapped.control.order)
    ]
    candidates: list[CompoundCandidate] = []
    for list_control in lists:
        by_side: dict[str, list[tuple[int, MappedControl]]] = {
            "left": [],
            "right": [],
        }
        list_rect = control_visual_rect(list_control.control)
        for button in buttons:
            if not _same_visual_group(list_control, button, controls):
                continue
            button_rect = control_visual_rect(button.control)
            if not (
                list_rect.top - 10
                <= button_rect.center_y
                <= list_rect.bottom + 10
            ):
                continue
            right_gap = button_rect.left - list_rect.right
            left_gap = list_rect.left - button_rect.right
            if -2 <= right_gap <= 50:
                by_side["right"].append((right_gap, button))
            elif -2 <= left_gap <= 50:
                by_side["left"].append((left_gap, button))
        side, entries = max(
            by_side.items(),
            key=lambda item: (len(item[1]), -sum(max(0, gap) for gap, _ in item[1])),
        )
        if len(entries) < 2:
            continue
        entries.sort(key=lambda item: control_visual_rect(item[1].control).top)
        button_controls = tuple(item.control for _, item in entries)
        left_edges = [control_visual_rect(item).left for item in button_controls]
        edge_spread = max(left_edges) - min(left_edges)
        if edge_spread > 6:
            continue
        mean_gap = sum(max(0, gap) for gap, _ in entries) / len(entries)
        score = min(
            0.97,
            0.68
            + min(0.12, len(entries) * 0.03)
            + max(0.0, 0.12 - mean_gap * 0.004)
            + max(0.0, 0.06 - edge_spread * 0.01),
        )
        evidence = (
            f"{len(entries)} recognized action buttons form a {side} side panel",
            f"mean list-to-button gap {mean_gap:.1f} DLU",
            f"button edge spread {edge_spread} DLU",
        )
        candidates.append(
            _candidate(
                multilingual,
                CompoundKind.LIST_ACTIONS,
                list_control.control,
                button_controls,
                score,
                evidence,
                labels,
                (
                    CompoundAction.BUNDLE
                    if score >= 0.85
                    else CompoundAction.SUGGEST
                ),
                score_fn=None,
            )
        )
    return tuple(candidates)


def _candidate(
    multilingual: MultilingualDialog,
    kind: CompoundKind,
    primary: Control,
    members: tuple[Control, ...],
    score: float,
    evidence: tuple[str, ...],
    labels: tuple[LabelAssociation, ...],
    default_action: CompoundAction,
    *,
    score_fn: Callable[[Control, Control], float] | None,
    geometry: CompoundGeometry = CompoundGeometry.UNION,
) -> CompoundCandidate:
    orders = (primary.order,) + tuple(member.order for member in members)
    eligible = [multilingual.default_language]
    supporting = [multilingual.default_language]
    if score_fn is not None and len(members) == 1:
        for variant in multilingual.variants:
            variant_primary = variant.for_order(primary.order)
            variant_member = variant.for_order(members[0].order)
            if variant_primary is None or variant_member is None:
                continue
            eligible.append(variant.language)
            variant_score = score_fn(variant_primary, variant_member)
            if variant_score >= 0.65:
                supporting.append(variant.language)
    elif score_fn is None:
        for variant in multilingual.variants:
            if all(variant.for_order(order) is not None for order in orders):
                eligible.append(variant.language)
                supporting.append(variant.language)
    consensus = len(supporting) / len(eligible) if eligible else 1.0
    confidence = min(0.99, score * 0.9 + consensus * 0.1)
    full_evidence = list(evidence)
    if len(eligible) > 1:
        full_evidence.append(
            "geometry supported by LANGIDs "
            + ", ".join(str(language) for language in supporting)
            + " of "
            + ", ".join(str(language) for language in eligible)
        )
    return CompoundCandidate(
        kind=kind,
        primary_order=primary.order,
        orders=orders,
        primary_ids=resource_selectors(primary.key.resource_id),
        member_ids=tuple(
            resource_selectors(member.key.resource_id) for member in members
        ),
        label_texts=_label_texts(multilingual, orders, labels),
        confidence=confidence,
        evidence=tuple(full_evidence),
        supporting_languages=tuple(sorted(set(supporting))),
        eligible_languages=tuple(sorted(set(eligible))),
        default_action=default_action,
        geometry=geometry,
    )


def _label_texts(
    multilingual: MultilingualDialog,
    orders: tuple[int, ...],
    associations: tuple[LabelAssociation, ...],
) -> tuple[str, ...]:
    label_orders = {
        association.label_order
        for association in associations
        if association.target_order in orders
    }
    texts: list[str] = []
    for label_order in sorted(label_orders):
        text = multilingual.default_dialog.controls[label_order].text
        if text:
            texts.append(text)
        for variant in multilingual.variants:
            control = variant.for_order(label_order)
            if control is not None and control.text:
                texts.append(control.text)
    return tuple(dict.fromkeys(texts))


def _select_disjoint_pairs(
    scored: list[tuple[float, MappedControl, MappedControl, tuple[str, ...]]],
) -> tuple[tuple[float, MappedControl, MappedControl, tuple[str, ...]], ...]:
    used: set[int] = set()
    result = []
    for item in sorted(
        scored,
        key=lambda value: (
            -value[0],
            value[1].control.order,
            value[2].control.order,
        ),
    ):
        orders = {item[1].control.order, item[2].control.order}
        if used & orders:
            continue
        used.update(orders)
        result.append(item)
    return tuple(result)


def _horizontal_pair_score(
    left: Control,
    right: Control,
    *,
    maximum_gap: int,
) -> tuple[float, tuple[str, ...]]:
    left_rect = control_visual_rect(left)
    right_rect = control_visual_rect(right)
    overlap = _vertical_overlap_ratio(left_rect, right_rect)
    gap = min(
        abs(right_rect.left - left_rect.right),
        abs(left_rect.left - right_rect.right),
    )
    if overlap < 0.55 or gap > maximum_gap:
        return 0.0, ()
    score = min(0.9, 0.5 + overlap * 0.25 + (maximum_gap - gap) * 0.01)
    return score, (
        f"vertical overlap {overlap:.0%}",
        f"horizontal gap {gap} DLU",
    )


def _slider_value_score(
    slider: Control,
    value: Control,
    *,
    vertical: bool,
) -> tuple[float, tuple[str, ...]]:
    slider_rect = control_visual_rect(slider)
    value_rect = control_visual_rect(value)
    if vertical:
        cross_overlap = _horizontal_overlap_ratio(slider_rect, value_rect)
        gap = min(
            abs(value_rect.top - slider_rect.bottom),
            abs(slider_rect.top - value_rect.bottom),
        )
        compact = value_rect.height <= max(24, slider_rect.height * 0.35)
    else:
        cross_overlap = _vertical_overlap_ratio(slider_rect, value_rect)
        gap = min(
            abs(value_rect.left - slider_rect.right),
            abs(slider_rect.left - value_rect.right),
        )
        compact = value_rect.width <= max(50, slider_rect.width * 0.45)
    if cross_overlap < 0.35 or gap > 20 or not compact:
        return 0.0, ()
    score = min(0.94, 0.55 + cross_overlap * 0.2 + (20 - gap) * 0.008)
    if _value_evidence(value):
        score += 0.12
    return min(score, 0.98), (
        f"value control is adjacent to the slider by {gap} DLU",
        f"cross-axis overlap {cross_overlap:.0%}",
        "value control has numeric text, editable class, or value-like ID",
    )


def _same_visual_group(
    left: MappedControl,
    right: MappedControl,
    controls: tuple[MappedControl, ...],
) -> bool:
    return _group_for(left, controls) == _group_for(right, controls)


def _group_for(
    target: MappedControl,
    controls: tuple[MappedControl, ...],
) -> int | None:
    rect = control_visual_rect(target.control)
    groups = [
        control
        for control in controls
        if control.role is ControlRole.GROUP
        and control.control.order != target.control.order
        and _contains(control.control.rect, rect.center_x, rect.center_y)
    ]
    if not groups:
        return None
    return min(
        groups,
        key=lambda item: item.control.rect.width * item.control.rect.height,
    ).control.order


def _is_browse_button(multilingual: MultilingualDialog, order: int) -> bool:
    controls = [multilingual.default_dialog.controls[order]] + [
        control
        for variant in multilingual.variants
        if (control := variant.for_order(order)) is not None
    ]
    return any(
        _BROWSE_TEXT.fullmatch(_normalized_text(control.text))
        or any(
            _BROWSE_ID.search(selector)
            for selector in resource_selectors(control.key.resource_id)
        )
        for control in controls
    )


def _is_list_action(multilingual: MultilingualDialog, order: int) -> bool:
    controls = [multilingual.default_dialog.controls[order]] + [
        control
        for variant in multilingual.variants
        if (control := variant.for_order(order)) is not None
    ]
    return any(
        _ACTION_TEXT.fullmatch(_normalized_text(control.text))
        or any(
            _ACTION_ID.search(selector)
            for selector in resource_selectors(control.key.resource_id)
        )
        for control in controls
    )


def _is_value_display(mapped: MappedControl) -> bool:
    if mapped.mapping_rule is not None:
        return False
    if mapped.qt_class == "QLineEdit":
        return True
    if mapped.qt_class != "QLabel":
        return False
    return _value_evidence(mapped.control)


def _value_evidence(control: Control) -> bool:
    text = _normalized_text(control.text)
    return (
        bool(text and _NUMERIC_TEXT.fullmatch(text))
        or any(
            _VALUE_ID.search(selector)
            for selector in resource_selectors(control.key.resource_id)
        )
        or (not text and control.class_name.casefold() == "static")
    )


def _normalized_text(text: str | None) -> str:
    return (text or "").replace("&", "").strip()


def _vertical_overlap_ratio(left: RectDlu, right: RectDlu) -> float:
    overlap = max(0, min(left.bottom, right.bottom) - max(left.top, right.top))
    denominator = min(left.height, right.height)
    return overlap / denominator if denominator else 0.0


def _horizontal_overlap_ratio(left: RectDlu, right: RectDlu) -> float:
    overlap = max(0, min(left.right, right.right) - max(left.left, right.left))
    denominator = min(left.width, right.width)
    return overlap / denominator if denominator else 0.0


def _contains(rect: RectDlu, x: float, y: float) -> bool:
    return rect.left <= x <= rect.right and rect.top <= y <= rect.bottom

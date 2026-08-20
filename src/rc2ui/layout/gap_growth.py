from __future__ import annotations

from dataclasses import replace

from rc2ui.layout.policy import GapGrowth
from rc2ui.qt.model import QtLayout, QtLayoutItem, QtWidget


def apply_gap_growth(widget: QtWidget, policy: GapGrowth) -> QtWidget:
    """Apply surplus-space policy without changing source minimum distances."""

    children = tuple(apply_gap_growth(child, policy) for child in widget.children)
    layout = _layout(widget.layout, policy) if widget.layout is not None else None
    return replace(widget, children=children, layout=layout)


def _layout(layout: QtLayout, policy: GapGrowth) -> QtLayout:
    items = tuple(_item(item, policy) for item in layout.items)
    result = replace(layout, items=items)
    if policy is GapGrowth.PROPORTIONAL:
        return result
    if layout.class_name == "QGridLayout":
        return replace(
            result,
            stretch=_minimum_gap_tracks(
                result.stretch,
                items,
                axis="column",
                outer_only=policy is GapGrowth.OUTER_MINIMUM,
            ),
            row_stretch=_minimum_gap_tracks(
                result.row_stretch,
                items,
                axis="row",
                outer_only=policy is GapGrowth.OUTER_MINIMUM,
            ),
        )
    return replace(
        result,
        stretch=_minimum_box_gaps(
            result.stretch,
            items,
            outer_only=policy is GapGrowth.OUTER_MINIMUM,
        ),
    )


def _item(item: QtLayoutItem, policy: GapGrowth) -> QtLayoutItem:
    return replace(
        item,
        widget=(
            apply_gap_growth(item.widget, policy)
            if item.widget is not None
            else None
        ),
        layout=(
            _layout(item.layout, policy) if item.layout is not None else None
        ),
    )


def _minimum_gap_tracks(
    weights: tuple[int, ...],
    items: tuple[QtLayoutItem, ...],
    *,
    axis: str,
    outer_only: bool,
) -> tuple[int, ...]:
    if not weights:
        return weights
    occupied: set[int] = set()
    for item in items:
        if item.spacer is not None or (
            item.widget is not None and _is_internal_widget(item.widget)
        ):
            continue
        start = item.column if axis == "column" else item.row
        if start is None:
            continue
        span = item.column_span if axis == "column" else item.row_span
        occupied.update(range(start, start + span))
    gaps = set(range(len(weights))) - occupied
    if outer_only:
        gaps &= _outer_indices(len(weights), occupied)
    return tuple(
        0 if index in gaps else value
        for index, value in enumerate(weights)
    )


def _minimum_box_gaps(
    weights: tuple[int, ...],
    items: tuple[QtLayoutItem, ...],
    *,
    outer_only: bool,
) -> tuple[int, ...]:
    if not weights:
        return weights
    gaps = {
        index
        for index, item in enumerate(items[: len(weights)])
        if item.spacer is not None
    }
    if outer_only:
        occupied = set(range(min(len(items), len(weights)))) - gaps
        gaps &= _outer_indices(len(weights), occupied)
    return tuple(
        0 if index in gaps else value
        for index, value in enumerate(weights)
    )


def _outer_indices(length: int, occupied: set[int]) -> set[int]:
    if not occupied:
        return set(range(length))
    first = min(occupied)
    last = max(occupied)
    return set(range(first)) | set(range(last + 1, length))


def _is_internal_widget(widget: QtWidget) -> bool:
    return any(
        property_.name == "rc2uiInternal" and property_.value is True
        for property_ in widget.properties
    )

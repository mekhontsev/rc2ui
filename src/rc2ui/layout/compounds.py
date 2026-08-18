from __future__ import annotations

from dataclasses import replace

from rc2ui.domain.geometry import RectDlu
from rc2ui.layout.alternatives import VisualNode
from rc2ui.semantics.model import (
    CompoundAction,
    CompoundGeometry,
    SemanticPlan,
)


def apply_compound_layout(
    nodes: dict[int, VisualNode],
    plan: SemanticPlan,
    *,
    resolved_rects: dict[int, RectDlu],
) -> dict[int, VisualNode]:
    """Materialize compound replacement and explicit WinAPI runtime geometry.

    A logical ``bundle`` intentionally leaves its controls in the common
    coordinate grid. A nested container would hide their distant row/column
    anchors from layout inference and could reverse qualitative relationships
    during resize. Aligned auto-buddy controls are the exception: WinAPI
    explicitly overrides their template coordinates, so the effective runtime
    rectangles must be established before inference.
    """

    result = dict(nodes)
    for decision in plan.decisions:
        candidate = decision.candidate
        members = [result.get(order) for order in candidate.orders]
        if any(node is None for node in members):
            continue
        concrete = [node for node in members if node is not None]
        primary = result[candidate.primary_order]
        if decision.action is not CompoundAction.REPLACE:
            if candidate.geometry is not CompoundGeometry.UNION:
                _position_autobuddy(
                    result,
                    candidate.primary_order,
                    candidate.secondary_orders[0],
                    candidate.geometry,
                    resolved_rects,
                )
            continue
        footprint = (
            primary.rect
            if candidate.geometry is not CompoundGeometry.UNION
            else _union_rect(tuple(node.rect for node in concrete))
        )
        for order in candidate.orders:
            result.pop(order)
        mapped = replace(
            primary.mapped,
            control=replace(primary.mapped.control, rect=footprint),
        )
        result[candidate.primary_order] = VisualNode(
            order=candidate.primary_order,
            orders=candidate.orders,
            rect=footprint,
            mapped=mapped,
            widget=primary.widget,
            children=[],
        )
        # Secondary rectangles remain in provenance but are excluded from
        # runtime geometry validation.
        resolved_rects[candidate.primary_order] = footprint
    return result


def _position_autobuddy(
    nodes: dict[int, VisualNode],
    primary_order: int,
    secondary_order: int,
    geometry: CompoundGeometry,
    resolved_rects: dict[int, RectDlu],
) -> None:
    buddy = nodes[primary_order]
    spin = nodes[secondary_order]
    if buddy.rect.width < 2:
        return
    requested_width = spin.rect.width or max(1, round(buddy.rect.height * 0.75))
    spin_width = max(1, min(requested_width, buddy.rect.width - 1))
    if geometry is CompoundGeometry.AUTOBUDDY_LEFT:
        spin_rect = RectDlu(
            buddy.rect.left,
            buddy.rect.top,
            spin_width,
            buddy.rect.height,
        )
        buddy_rect = RectDlu(
            buddy.rect.left + spin_width,
            buddy.rect.top,
            buddy.rect.width - spin_width,
            buddy.rect.height,
        )
    else:
        buddy_rect = RectDlu(
            buddy.rect.left,
            buddy.rect.top,
            buddy.rect.width - spin_width,
            buddy.rect.height,
        )
        spin_rect = RectDlu(
            buddy_rect.right,
            buddy.rect.top,
            spin_width,
            buddy.rect.height,
        )
    nodes[primary_order] = _with_rect(buddy, buddy_rect)
    nodes[secondary_order] = _with_rect(spin, spin_rect)
    resolved_rects[primary_order] = buddy_rect
    resolved_rects[secondary_order] = spin_rect


def _with_rect(node: VisualNode, rect: RectDlu) -> VisualNode:
    return replace(
        node,
        rect=rect,
        mapped=replace(
            node.mapped,
            control=replace(node.mapped.control, rect=rect),
        ),
    )


def _union_rect(rects: tuple[RectDlu, ...]) -> RectDlu:
    left = min(rect.left for rect in rects)
    top = min(rect.top for rect in rects)
    right = max(rect.right for rect in rects)
    bottom = max(rect.bottom for rect in rects)
    return RectDlu(left, top, right - left, bottom - top)

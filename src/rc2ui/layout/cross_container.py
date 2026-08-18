from __future__ import annotations

from dataclasses import dataclass

from rc2ui.domain.geometry import RectDlu
from rc2ui.layout.alternatives import VisualNode
from rc2ui.layout.anchors import AnchorKind, AxisAnchorGroup
from rc2ui.layout.row_anchors import coherent_vertical_anchor_groups
from rc2ui.mapping.model import ControlRole


@dataclass(frozen=True, slots=True)
class CrossContainerAnchor:
    order: int
    kind: AnchorKind
    coordinate2: int


def align_peer_group_rows(
    nodes: list[VisualNode],
    *,
    tolerance: int,
    same_row_pairs: frozenset[frozenset[int]],
) -> tuple[CrossContainerAnchor, ...]:
    """Snap rows shared by children of sibling group boxes.

    Each QGroupBox necessarily owns an independent Qt layout, so ordinary
    per-container inference cannot see that fields in two neighbouring group
    boxes occupied the same source row.  Normalize that source relation before
    the nested layouts are built.  The layouts can then reproduce the same
    global coordinate using their respective fixed top padding.

    Only coherent rows containing controls from at least two sibling groups
    are changed.  Stacked/unrelated groups and relations within one group keep
    using the ordinary container inference.
    """

    anchors: list[CrossContainerAnchor] = []
    _align_sibling_group_children(
        nodes,
        tolerance=tolerance,
        same_row_pairs=same_row_pairs,
        anchors=anchors,
    )
    return tuple(sorted(anchors, key=lambda item: item.order))


def _align_sibling_group_children(
    siblings: list[VisualNode],
    *,
    tolerance: int,
    same_row_pairs: frozenset[frozenset[int]],
    anchors: list[CrossContainerAnchor],
) -> None:
    groups = [
        node
        for node in siblings
        if node.mapped.role is ControlRole.GROUP and node.children
    ]
    candidates: list[VisualNode] = []
    parent_for_order: dict[int, int] = {}
    for group in groups:
        for child in group.children:
            if (
                child.mapped.role is ControlRole.GROUP
                or child.mapped.separator_orientation is not None
            ):
                continue
            candidates.append(child)
            parent_for_order[child.order] = group.order

    selected = coherent_vertical_anchor_groups(
        candidates,
        same_row_pairs=same_row_pairs,
        tolerance=tolerance,
    )
    handled: set[tuple[AnchorKind, int, frozenset[int]]] = set()
    for candidate in candidates:
        group = selected.get(candidate.order)
        if group is None:
            continue
        key = (group.kind, group.coordinate2, group.node_orders)
        if key in handled:
            continue
        handled.add(key)
        members = [
            item for item in candidates if item.order in group.node_orders
        ]
        if len({parent_for_order[item.order] for item in members}) < 2:
            continue
        for member in members:
            member.rect = _vertically_aligned(member.rect, group)
            anchors.extend(
                CrossContainerAnchor(order, group.kind, group.coordinate2)
                for order in member.orders
            )

    for group in groups:
        _align_sibling_group_children(
            group.children,
            tolerance=tolerance,
            same_row_pairs=same_row_pairs,
            anchors=anchors,
        )


def _vertically_aligned(
    rect: RectDlu,
    group: AxisAnchorGroup,
) -> RectDlu:
    if group.kind is AnchorKind.START:
        top = round(group.coordinate2 / 2)
    elif group.kind is AnchorKind.CENTER:
        top = round((group.coordinate2 - rect.height) / 2)
    else:
        top = round(group.coordinate2 / 2 - rect.height)
    return RectDlu(rect.x, top, rect.width, rect.height)

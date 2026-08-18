from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from rc2ui.domain.geometry import RectDlu
from rc2ui.mapping.model import ControlRole, MappedControl
from rc2ui.qt.model import QtLayout, QtLayoutItem, QtProperty, QtWidget


@dataclass(slots=True)
class VisualNode:
    order: int
    orders: tuple[int, ...]
    rect: RectDlu
    mapped: MappedControl
    widget: QtWidget
    children: list[VisualNode]


@dataclass(frozen=True, slots=True)
class AlternativeDetection:
    orders: tuple[int, ...]
    layers: tuple[tuple[int, ...], ...]
    object_names: tuple[str, ...]
    geometry_match: float
    z_order_span: int
    topmost_object_name: str
    order_evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _AlternativeComponent:
    orders: tuple[int, ...]
    ranks: tuple[int, ...]
    order_evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _GeometryThresholds:
    minimum_coverage: float
    minimum_iou: float
    edge_tolerance_multiplier: int


_STRICT_GEOMETRY = _GeometryThresholds(0.9, 0.72, 2)
_ORDER_SUPPORTED_GEOMETRY = _GeometryThresholds(0.82, 0.6, 3)


def collapse_runtime_alternatives(
    nodes: list[VisualNode],
    *,
    tolerance: int,
    next_name: Callable[[str], str],
    forced_pairs: frozenset[frozenset[int]] = frozenset(),
    rejected_pairs: frozenset[frozenset[int]] = frozenset(),
) -> tuple[list[VisualNode], tuple[AlternativeDetection, ...]]:
    """Collapse near-identical sibling rectangles into one visual slot."""

    detections: list[AlternativeDetection] = []
    for node in nodes:
        if not node.children:
            continue
        node.children, child_detections = collapse_runtime_alternatives(
            node.children,
            tolerance=tolerance,
            next_name=next_name,
            forced_pairs=forced_pairs,
            rejected_pairs=rejected_pairs,
        )
        detections.extend(child_detections)

    components = _alternative_components(
        nodes,
        tolerance=tolerance,
        forced_pairs=forced_pairs,
        rejected_pairs=rejected_pairs,
    )
    by_order = {node.order: node for node in nodes}
    grouped_orders = {
        order for component in components for order in component.orders
    }
    result = [node for node in nodes if node.order not in grouped_orders]

    for component in components:
        alternatives = sorted(
            (by_order[order] for order in component.orders),
            key=lambda node: node.order,
        )
        all_orders = tuple(order for node in alternatives for order in node.orders)
        slot_rect = _union_rect(tuple(node.rect for node in alternatives))
        slot_layout = _alternative_layout(
            alternatives,
            slot_rect,
            tolerance=tolerance,
            name=next_name("runtimeAlternativesLayout"),
        )
        representative = alternatives[0].mapped
        result.append(
            VisualNode(
                order=min(all_orders),
                orders=all_orders,
                rect=slot_rect,
                mapped=replace(
                    representative,
                    qt_class="QWidget",
                    role=_combined_role(alternatives),
                    properties=(),
                    expands_horizontally=any(
                        node.mapped.expands_horizontally for node in alternatives
                    ),
                    expands_vertically=any(
                        node.mapped.expands_vertically for node in alternatives
                    ),
                    warning=None,
                    custom_widget=None,
                    button_group=None,
                    mapping_rule=None,
                    mapping_rule_key=None,
                    runtime_configured=(),
                ),
                widget=QtWidget(
                    class_name="QWidget",
                    object_name=next_name("runtimeAlternatives"),
                    layout=slot_layout,
                ),
                children=[],
            )
        )
        detections.append(
            AlternativeDetection(
                orders=all_orders,
                layers=tuple(node.orders for node in alternatives),
                object_names=tuple(node.widget.object_name for node in alternatives),
                geometry_match=min(
                    intersection_over_union(left.rect, right.rect)
                    for index, left in enumerate(alternatives)
                    for right in alternatives[index + 1 :]
                ),
                z_order_span=max(component.ranks) - min(component.ranks),
                topmost_object_name=alternatives[-1].widget.object_name,
                order_evidence=component.order_evidence,
            )
        )

    # The collapsed nodes return to the ordinary coordinate-driven layout.
    # Resource order remains meaningful only inside the overlapping slot,
    # where it preserves Win32 z-order.
    result.sort(
        key=lambda node: (
            node.rect.top,
            node.rect.left,
            node.rect.bottom,
            node.rect.right,
            node.order,
        )
    )
    return result, tuple(detections)


def _alternative_layout(
    alternatives: list[VisualNode],
    slot: RectDlu,
    *,
    tolerance: int,
    name: str,
) -> QtLayout:
    """Overlay alternatives while retaining meaningful internal offsets."""

    preserve_subrectangles = any(
        max(
            abs(node.rect.left - slot.left),
            abs(node.rect.top - slot.top),
            abs(node.rect.right - slot.right),
            abs(node.rect.bottom - slot.bottom),
        )
        > tolerance * 2
        for node in alternatives
    )
    if preserve_subrectangles:
        columns = tuple(
            sorted(
                {slot.left, slot.right}
                | {edge for node in alternatives for edge in (node.rect.left, node.rect.right)}
            )
        )
        rows = tuple(
            sorted(
                {slot.top, slot.bottom}
                | {edge for node in alternatives for edge in (node.rect.top, node.rect.bottom)}
            )
        )
        items = tuple(
            QtLayoutItem(
                widget=node.widget,
                row=rows.index(node.rect.top),
                column=columns.index(node.rect.left),
                row_span=rows.index(node.rect.bottom) - rows.index(node.rect.top),
                column_span=(
                    columns.index(node.rect.right) - columns.index(node.rect.left)
                ),
                alignment=_slot_alignment(node, node.rect),
            )
            for node in alternatives
        )
        column_stretch = tuple(
            right - left for left, right in zip(columns, columns[1:])
        )
        row_stretch = tuple(
            bottom - top for top, bottom in zip(rows, rows[1:])
        )
    else:
        items = tuple(
            QtLayoutItem(
                widget=node.widget,
                row=0,
                column=0,
                alignment=_slot_alignment(node, slot),
            )
            for node in alternatives
        )
        column_stretch = (
            int(any(node.mapped.expands_horizontally for node in alternatives)),
        )
        row_stretch = (
            int(any(node.mapped.expands_vertically for node in alternatives)),
        )
    return QtLayout(
        "QGridLayout",
        name,
        items,
        properties=(
            QtProperty("leftMargin", 0),
            QtProperty("topMargin", 0),
            QtProperty("rightMargin", 0),
            QtProperty("bottomMargin", 0),
            QtProperty("spacing", 0),
        ),
        stretch=column_stretch,
        row_stretch=row_stretch,
        minimum_widths=column_stretch,
        minimum_heights=row_stretch,
    )


def _slot_alignment(node: VisualNode, slot: RectDlu) -> str | None:
    """Keep fixed alternatives on the source edge of their union slot."""

    flags: list[str] = []
    if not node.mapped.expands_horizontally:
        left_gap = abs(node.rect.left - slot.left)
        right_gap = abs(slot.right - node.rect.right)
        if right_gap < left_gap:
            flags.append("Qt::AlignRight")
        elif left_gap < right_gap:
            flags.append("Qt::AlignLeft")
        else:
            flags.append("Qt::AlignHCenter")
    if not node.mapped.expands_vertically:
        top_gap = abs(node.rect.top - slot.top)
        bottom_gap = abs(slot.bottom - node.rect.bottom)
        if bottom_gap < top_gap:
            flags.append("Qt::AlignBottom")
        elif top_gap < bottom_gap:
            flags.append("Qt::AlignTop")
        else:
            flags.append("Qt::AlignVCenter")
    return "|".join(flags) if flags else None


def intersection_area(left: RectDlu, right: RectDlu) -> int:
    width = min(left.right, right.right) - max(left.left, right.left)
    height = min(left.bottom, right.bottom) - max(left.top, right.top)
    return max(0, width) * max(0, height)


def intersection_over_union(left: RectDlu, right: RectDlu) -> float:
    intersection = intersection_area(left, right)
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union if union else 0.0


def _alternative_components(
    nodes: list[VisualNode],
    *,
    tolerance: int,
    forced_pairs: frozenset[frozenset[int]],
    rejected_pairs: frozenset[frozenset[int]],
) -> tuple[_AlternativeComponent, ...]:
    candidates = [
        node
        for node in nodes
        if node.mapped.role
        not in {
            ControlRole.GROUP,
            ControlRole.CONTAINER,
            ControlRole.DECORATION,
        }
    ]
    ranks = {
        node.order: rank
        for rank, node in enumerate(sorted(nodes, key=lambda item: item.order))
    }
    layer_offsets = _supported_layer_offsets(
        candidates,
        ranks=ranks,
        tolerance=tolerance,
    )
    adjacency: dict[int, set[int]] = {node.order: set() for node in candidates}
    pair_evidence: dict[frozenset[int], tuple[str, ...]] = {}
    for index, left in enumerate(candidates):
        for right in candidates[index + 1 :]:
            expanded_pairs = tuple(
                frozenset((left_order, right_order))
                for left_order in left.orders
                for right_order in right.orders
            )
            if any(pair in rejected_pairs for pair in expanded_pairs):
                evidence = ()
            elif any(pair in forced_pairs for pair in expanded_pairs):
                evidence = ("geometry", "multilingual-consensus")
            else:
                evidence = _runtime_alternative_evidence(
                    left.rect,
                    right.rect,
                    tolerance=tolerance,
                    order_distance=abs(ranks[left.order] - ranks[right.order]),
                    layer_offsets=layer_offsets,
                )
            if evidence:
                adjacency[left.order].add(right.order)
                adjacency[right.order].add(left.order)
                pair_evidence[frozenset((left.order, right.order))] = evidence

    components: list[_AlternativeComponent] = []
    remaining = set(adjacency)
    while remaining:
        seed = min(remaining)
        stack = [seed]
        component: set[int] = set()
        while stack:
            order = stack.pop()
            if order in component:
                continue
            component.add(order)
            stack.extend(adjacency[order] - component)
        remaining -= component
        if len(component) < 2:
            continue

        # A transitive chain can connect rectangles whose endpoints are not
        # equivalent. Collapse only components where every pair agrees.
        ordered = tuple(sorted(component))
        pairs = tuple(
            frozenset((left, right))
            for index, left in enumerate(ordered)
            for right in ordered[index + 1 :]
        )
        if all(pair in pair_evidence for pair in pairs):
            evidence = {
                item
                for pair in pairs
                for item in pair_evidence[pair]
                if item != "geometry"
            }
            components.append(
                _AlternativeComponent(
                    orders=ordered,
                    ranks=tuple(ranks[order] for order in ordered),
                    order_evidence=tuple(sorted(evidence)),
                )
            )
    return tuple(components)


def _runtime_alternative_evidence(
    left: RectDlu,
    right: RectDlu,
    *,
    tolerance: int,
    order_distance: int,
    layer_offsets: set[int],
) -> tuple[str, ...]:
    evidence = ["geometry"]
    if order_distance <= 2:
        evidence.append("near-z-order")
    if order_distance in layer_offsets:
        evidence.append(f"layer-offset:{order_distance}")

    if _matches_geometry(
        left,
        right,
        tolerance=tolerance,
        thresholds=_STRICT_GEOMETRY,
    ):
        return tuple(evidence)
    if len(evidence) == 1:
        return ()
    if _matches_geometry(
        left,
        right,
        tolerance=tolerance,
        thresholds=_ORDER_SUPPORTED_GEOMETRY,
    ):
        return tuple(evidence)
    return ()


def _matches_geometry(
    left: RectDlu,
    right: RectDlu,
    *,
    tolerance: int,
    thresholds: _GeometryThresholds,
) -> bool:
    left_area = left.width * left.height
    right_area = right.width * right.height
    if left_area <= 0 or right_area <= 0:
        return False
    intersection = intersection_area(left, right)
    if intersection / min(left_area, right_area) < thresholds.minimum_coverage:
        return False
    if intersection_over_union(left, right) < thresholds.minimum_iou:
        return False
    edge_tolerance = max(
        tolerance * thresholds.edge_tolerance_multiplier,
        thresholds.edge_tolerance_multiplier,
    )
    return (
        abs(left.left - right.left) <= edge_tolerance
        and abs(left.top - right.top) <= edge_tolerance
        and abs(left.right - right.right) <= edge_tolerance
        and abs(left.bottom - right.bottom) <= edge_tolerance
    )


def _supported_layer_offsets(
    nodes: list[VisualNode],
    *,
    ranks: dict[int, int],
    tolerance: int,
) -> set[int]:
    pairs_by_offset: dict[int, list[frozenset[int]]] = {}
    for index, left in enumerate(nodes):
        for right in nodes[index + 1 :]:
            if not _matches_geometry(
                left.rect,
                right.rect,
                tolerance=tolerance,
                thresholds=_ORDER_SUPPORTED_GEOMETRY,
            ):
                continue
            offset = abs(ranks[left.order] - ranks[right.order])
            pairs_by_offset.setdefault(offset, []).append(
                frozenset((left.order, right.order))
            )

    return {
        offset
        for offset, pairs in pairs_by_offset.items()
        if offset > 2 and _has_two_disjoint_pairs(pairs)
    }


def _has_two_disjoint_pairs(pairs: list[frozenset[int]]) -> bool:
    return any(
        first.isdisjoint(second)
        for index, first in enumerate(pairs)
        for second in pairs[index + 1 :]
    )


def _union_rect(rectangles: tuple[RectDlu, ...]) -> RectDlu:
    left = min(rect.left for rect in rectangles)
    top = min(rect.top for rect in rectangles)
    right = max(rect.right for rect in rectangles)
    bottom = max(rect.bottom for rect in rectangles)
    return RectDlu(left, top, right - left, bottom - top)


def _combined_role(alternatives: list[VisualNode]) -> ControlRole:
    roles = {node.mapped.role for node in alternatives}
    return roles.pop() if len(roles) == 1 else ControlRole.INPUT

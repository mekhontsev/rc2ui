from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from statistics import median

from rc2ui.domain.geometry import RectDlu
from rc2ui.layout.alternatives import VisualNode


class Axis(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class AnchorKind(StrEnum):
    START = "start"
    CENTER = "center"
    END = "end"


@dataclass(frozen=True, slots=True)
class AxisAnchorGroup:
    kind: AnchorKind
    coordinate2: int
    node_orders: frozenset[int]


@dataclass(frozen=True, slots=True)
class AxisAnchorAnalysis:
    axis: Axis
    groups: tuple[AxisAnchorGroup, ...]

    def shared_kinds(
        self,
        left: VisualNode,
        right: VisualNode,
    ) -> tuple[AnchorKind, ...]:
        return tuple(
            group.kind
            for group in self.groups
            if left.order in group.node_orders
            and right.order in group.node_orders
        )

    def aligned(self, left: VisualNode, right: VisualNode) -> bool:
        return bool(self.shared_kinds(left, right))

    def dominant_kind(
        self,
        nodes: list[VisualNode],
        *,
        preference: tuple[AnchorKind, ...],
    ) -> AnchorKind:
        orders = {node.order for node in nodes}
        priority = {kind: index for index, kind in enumerate(preference)}
        candidates = [
            (
                -len(orders & group.node_orders),
                _anchor_spread(
                    [
                        node
                        for node in nodes
                        if node.order in group.node_orders
                    ],
                    self.axis,
                    group.kind,
                ),
                priority[group.kind],
                group.coordinate2,
                group.kind,
            )
            for group in self.groups
            if len(orders & group.node_orders) >= 2
        ]
        if not candidates:
            return preference[0]
        return min(candidates)[-1]


class _DisjointSet:
    def __init__(self, orders: tuple[int, ...]) -> None:
        self._parent = {order: order for order in orders}

    def find(self, order: int) -> int:
        parent = self._parent[order]
        if parent != order:
            self._parent[order] = self.find(parent)
        return self._parent[order]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        self._parent[max(left_root, right_root)] = min(left_root, right_root)


def analyze_axis_anchors(
    nodes: list[VisualNode],
    *,
    axis: Axis,
    tolerance: int,
    hinted_pairs: frozenset[frozenset[int]] = frozenset(),
) -> AxisAnchorAnalysis:
    """Find coordinate relations without depending on RC declaration order."""

    if tolerance < 0:
        raise ValueError("coordinate tolerance cannot be negative")
    if not nodes:
        return AxisAnchorAnalysis(axis, ())

    orders = tuple(node.order for node in nodes)
    sets = {kind: _DisjointSet(orders) for kind in AnchorKind}
    tolerance2 = tolerance * 2
    for kind in AnchorKind:
        entries = sorted(
            (
                (_anchor_coordinate2(node.rect, axis, kind), node.order)
                for node in nodes
            ),
            key=lambda item: (item[0], item[1]),
        )
        clusters: list[list[tuple[int, int]]] = []
        for coordinate2, order in entries:
            if (
                not clusters
                or coordinate2
                - round(median(value for value, _ in clusters[-1]))
                > tolerance2
            ):
                clusters.append([(coordinate2, order)])
            else:
                clusters[-1].append((coordinate2, order))
        for cluster in clusters:
            first_order = cluster[0][1]
            for _, order in cluster[1:]:
                sets[kind].union(first_order, order)

    # Multilingual hints carry the relation, but not its anchor type. Choose
    # the closest same-kind edge in authoritative geometry and join that graph.
    for left_index, left in enumerate(nodes):
        for right in nodes[left_index + 1 :]:
            if not _nodes_related(left, right, hinted_pairs):
                continue
            kind = min(
                AnchorKind,
                key=lambda candidate: (
                    abs(
                        _anchor_coordinate2(left.rect, axis, candidate)
                        - _anchor_coordinate2(right.rect, axis, candidate)
                    ),
                    _kind_priority(axis, candidate),
                ),
            )
            sets[kind].union(left.order, right.order)

    groups: list[AxisAnchorGroup] = []
    for kind in AnchorKind:
        components: dict[int, list[VisualNode]] = {}
        for node in nodes:
            components.setdefault(sets[kind].find(node.order), []).append(node)
        for members in components.values():
            groups.append(
                AxisAnchorGroup(
                    kind=kind,
                    coordinate2=round(
                        median(
                            _anchor_coordinate2(node.rect, axis, kind)
                            for node in members
                        )
                    ),
                    node_orders=frozenset(node.order for node in members),
                )
            )
    groups.sort(
        key=lambda group: (
            _kind_priority(axis, group.kind),
            group.coordinate2,
            min(group.node_orders),
        )
    )
    return AxisAnchorAnalysis(axis, tuple(groups))


def anchor_coordinate2(
    rect: RectDlu,
    axis: Axis,
    kind: AnchorKind,
) -> int:
    return _anchor_coordinate2(rect, axis, kind)


def _anchor_coordinate2(
    rect: RectDlu,
    axis: Axis,
    kind: AnchorKind,
) -> int:
    if axis is Axis.HORIZONTAL:
        start, end = rect.left, rect.right
    else:
        start, end = rect.top, rect.bottom
    if kind is AnchorKind.START:
        return start * 2
    if kind is AnchorKind.END:
        return end * 2
    return start + end


def _nodes_related(
    left: VisualNode,
    right: VisualNode,
    pairs: frozenset[frozenset[int]],
) -> bool:
    return any(
        frozenset((left_order, right_order)) in pairs
        for left_order in left.orders
        for right_order in right.orders
    )


def _kind_priority(axis: Axis, kind: AnchorKind) -> int:
    if axis is Axis.HORIZONTAL:
        return {
            AnchorKind.START: 0,
            AnchorKind.END: 1,
            AnchorKind.CENTER: 2,
        }[kind]
    return {
        AnchorKind.CENTER: 0,
        AnchorKind.START: 1,
        AnchorKind.END: 2,
    }[kind]


def _anchor_spread(
    nodes: list[VisualNode],
    axis: Axis,
    kind: AnchorKind,
) -> int:
    values = [_anchor_coordinate2(node.rect, axis, kind) for node in nodes]
    return max(values) - min(values) if values else 0

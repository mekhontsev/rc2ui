from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from rc2ui.domain.geometry import RectDlu


_ORDER_TOLERANCE_DLU = 1
_CONTAINMENT_TOLERANCE_DLU = 1
_UNANCHORED_CORRECTION_TOLERANCE_DLU = 3
_GAP_AFFINITY_EVIDENCE_DLU = 2


@dataclass(frozen=True, slots=True)
class TopologyItem:
    """A default-language rectangle participating in topology checks."""

    order: int
    rect: RectDlu
    is_container: bool = False


@dataclass(frozen=True, slots=True)
class TopologyRejection:
    """One proposed rectangle reverted to preserve the default topology."""

    order: int
    reasons: tuple[str, ...]
    peers: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TopologySelection:
    """Accepted rectangles and the local corrections which were rejected."""

    rects: tuple[tuple[int, RectDlu], ...]
    rejections: tuple[TopologyRejection, ...]

    def rect_for(self, order: int) -> RectDlu:
        for candidate_order, rect in self.rects:
            if candidate_order == order:
                return rect
        raise KeyError(order)


@dataclass(frozen=True, slots=True)
class _OrderConstraint:
    axis: Literal["horizontal", "vertical"]
    before: int
    after: int
    allowed_overlap: int

    @property
    def orders(self) -> tuple[int, int]:
        return self.before, self.after

    @property
    def reason(self) -> str:
        return f"{self.axis}-order"

    def is_violated(self, rects: dict[int, RectDlu]) -> bool:
        before = rects[self.before]
        after = rects[self.after]
        if self.axis == "horizontal":
            return before.right > after.left + self.allowed_overlap
        return before.bottom > after.top + self.allowed_overlap


@dataclass(frozen=True, slots=True)
class _AlignmentConstraint:
    anchor: Literal["left", "right", "top", "bottom", "center-x", "center-y"]
    first: int
    second: int
    allowed_delta: float

    @property
    def orders(self) -> tuple[int, int]:
        return self.first, self.second

    @property
    def reason(self) -> str:
        return f"{self.anchor}-alignment"

    def is_violated(self, rects: dict[int, RectDlu]) -> bool:
        first = _anchor_value(rects[self.first], self.anchor)
        second = _anchor_value(rects[self.second], self.anchor)
        return abs(first - second) > self.allowed_delta


@dataclass(frozen=True, slots=True)
class _ContainmentConstraint:
    child: int
    container: int | None
    fixed_container: RectDlu | None = None

    @property
    def orders(self) -> tuple[int, ...]:
        if self.container is None:
            return (self.child,)
        return self.child, self.container

    @property
    def reason(self) -> str:
        return "dialog-containment" if self.container is None else "containment"

    def is_violated(self, rects: dict[int, RectDlu]) -> bool:
        container = (
            self.fixed_container
            if self.container is None
            else rects[self.container]
        )
        if container is None:
            raise AssertionError("fixed container is required for dialog bounds")
        return not _contains(
            container,
            rects[self.child],
            _CONTAINMENT_TOLERANCE_DLU,
        )


@dataclass(frozen=True, slots=True)
class _NeighbourGapConstraint:
    before: int
    after: int
    source_gap: int
    allowed_delta: int

    @property
    def orders(self) -> tuple[int, int]:
        return self.before, self.after

    @property
    def reason(self) -> str:
        return "horizontal-gap"

    def is_violated(self, rects: dict[int, RectDlu]) -> bool:
        gap = rects[self.after].left - rects[self.before].right
        return abs(gap - self.source_gap) > self.allowed_delta


@dataclass(frozen=True, slots=True)
class _GapAffinityConstraint:
    left: int
    middle: int
    right: int
    closer_side: Literal["left", "right"]

    @property
    def orders(self) -> tuple[int, int, int]:
        return self.left, self.middle, self.right

    @property
    def reason(self) -> str:
        return "horizontal-gap-affinity"

    def is_violated(self, rects: dict[int, RectDlu]) -> bool:
        left_gap = rects[self.middle].left - rects[self.left].right
        right_gap = rects[self.right].left - rects[self.middle].right
        if self.closer_side == "left":
            return left_gap >= right_gap
        return right_gap >= left_gap


_Constraint = (
    _OrderConstraint
    | _AlignmentConstraint
    | _ContainmentConstraint
    | _NeighbourGapConstraint
    | _GapAffinityConstraint
)


def select_topology_preserving_rects(
    items: tuple[TopologyItem, ...],
    proposals: Mapping[int, RectDlu],
    *,
    bounds: RectDlu | None = None,
    order_axes: tuple[Literal["horizontal", "vertical"], ...] = (
        "horizontal",
        "vertical",
    ),
    preserve_alignments: bool = True,
    preserve_containment: bool = True,
    reject_unanchored: bool = True,
    order_requires_orthogonal_overlap: bool = False,
    preserve_neighbour_gaps: bool = False,
    neighbour_gap_tolerance: int = 3,
) -> TopologySelection:
    """Accept a local subset that preserves default relationships.

    Corrections are not limited by distance. When proposals conflict with a
    clear default order or containment relation, only controls involved in a
    conflict are reverted. The most disruptive proposal is reverted first.
    """

    defaults = {item.order: item.rect for item in items}
    if len(defaults) != len(items):
        raise ValueError("topology item orders must be unique")
    unknown = set(proposals) - defaults.keys()
    if unknown:
        raise ValueError(f"proposals contain unknown orders: {sorted(unknown)}")
    if neighbour_gap_tolerance < 0:
        raise ValueError("neighbour gap tolerance cannot be negative")

    accepted = {
        order: proposals.get(order, default_rect)
        for order, default_rect in defaults.items()
    }
    constraints = _build_constraints(
        items,
        bounds,
        order_axes=order_axes,
        preserve_alignments=preserve_alignments,
        preserve_containment=preserve_containment,
        order_requires_orthogonal_overlap=(
            order_requires_orthogonal_overlap
        ),
        preserve_neighbour_gaps=preserve_neighbour_gaps,
        neighbour_gap_tolerance=neighbour_gap_tolerance,
    )
    rejected: dict[int, tuple[set[str], set[int]]] = {}

    peer_orders = {
        order
        for constraint in constraints
        if len(constraint.orders) > 1
        for order in constraint.orders
    }
    for order, candidate in tuple(accepted.items()):
        if (
            reject_unanchored
            and order not in peer_orders
            and _maximum_edge_distance(defaults[order], candidate)
            > _UNANCHORED_CORRECTION_TOLERANCE_DLU
        ):
            accepted[order] = defaults[order]
            rejected[order] = ({"unanchored"}, set())

    while True:
        violations = tuple(
            constraint
            for constraint in constraints
            if constraint.is_violated(accepted)
        )
        if not violations:
            break
        changed = {
            order
            for constraint in violations
            for order in constraint.orders
            if accepted[order] != defaults[order]
        }
        if not changed:
            raise AssertionError("default topology must satisfy its constraints")

        violation_counts = {
            order: sum(order in constraint.orders for constraint in violations)
            for order in changed
        }
        order = max(
            changed,
            key=lambda candidate: (
                violation_counts[candidate],
                _rect_distance(defaults[candidate], accepted[candidate]),
                candidate,
            ),
        )
        relevant = tuple(
            constraint for constraint in violations if order in constraint.orders
        )
        reasons, peers = rejected.setdefault(order, (set(), set()))
        for constraint in relevant:
            reasons.add(constraint.reason)
            peers.update(
                candidate
                for candidate in constraint.orders
                if candidate != order
            )
        accepted[order] = defaults[order]

    return TopologySelection(
        rects=tuple(sorted(accepted.items())),
        rejections=tuple(
            TopologyRejection(
                order=order,
                reasons=tuple(sorted(reasons)),
                peers=tuple(sorted(peers)),
            )
            for order, (reasons, peers) in sorted(rejected.items())
        ),
    )


def _build_constraints(
    items: tuple[TopologyItem, ...],
    bounds: RectDlu | None,
    *,
    order_axes: tuple[Literal["horizontal", "vertical"], ...],
    preserve_alignments: bool,
    preserve_containment: bool,
    order_requires_orthogonal_overlap: bool,
    preserve_neighbour_gaps: bool,
    neighbour_gap_tolerance: int,
) -> tuple[_Constraint, ...]:
    by_order = {item.order: item for item in items}
    parents = {
        item.order: _smallest_container(item, items)
        for item in items
    }
    constraints: list[_Constraint] = []

    if preserve_neighbour_gaps:
        constraints.extend(
            _horizontal_neighbour_constraints(
                items,
                tolerance=neighbour_gap_tolerance,
            )
        )

    if preserve_containment and bounds is not None:
        for item in items:
            if _contains(bounds, item.rect, _CONTAINMENT_TOLERANCE_DLU):
                constraints.append(
                    _ContainmentConstraint(
                        child=item.order,
                        container=None,
                        fixed_container=bounds,
                    )
                )

    for child, parent in parents.items():
        if preserve_containment and parent is not None:
            constraints.append(
                _ContainmentConstraint(child=child, container=parent)
            )

    orders = sorted(by_order)
    for index, left_order in enumerate(orders):
        for right_order in orders[index + 1 :]:
            if parents[left_order] != parents[right_order]:
                continue
            left = by_order[left_order].rect
            right = by_order[right_order].rect
            if preserve_alignments:
                constraints.extend(
                    _alignment_constraints(
                        left_order,
                        left,
                        right_order,
                        right,
                    )
                )
            for axis in order_axes:
                if (
                    order_requires_orthogonal_overlap
                    and not _orthogonal_projection_overlaps(
                        left,
                        right,
                        axis,
                    )
                ):
                    continue
                order = _ordered_pair(
                    left_order,
                    left,
                    right_order,
                    right,
                    axis,
                )
                if order is not None:
                    constraints.append(order)
    return tuple(constraints)


def _horizontal_neighbour_constraints(
    items: tuple[TopologyItem, ...],
    *,
    tolerance: int,
) -> tuple[_NeighbourGapConstraint | _GapAffinityConstraint, ...]:
    """Protect local row spacing from unrelated global anchor votes."""

    controls = tuple(item for item in items if not item.is_container)
    maximum_gap = max(4, tolerance * 4)
    pair_constraints: dict[tuple[int, int], _NeighbourGapConstraint] = {}
    affinity_constraints: list[_GapAffinityConstraint] = []
    for middle in controls:
        left = _nearest_horizontal_neighbour(
            middle,
            controls,
            side="left",
            row_tolerance=tolerance,
        )
        right = _nearest_horizontal_neighbour(
            middle,
            controls,
            side="right",
            row_tolerance=tolerance,
        )
        left_gap = (
            middle.rect.left - left.rect.right if left is not None else None
        )
        right_gap = (
            right.rect.left - middle.rect.right if right is not None else None
        )
        if left is not None and left_gap is not None and left_gap <= maximum_gap:
            pair_constraints[(left.order, middle.order)] = (
                _NeighbourGapConstraint(
                    before=left.order,
                    after=middle.order,
                    source_gap=left_gap,
                    allowed_delta=tolerance,
                )
            )
        if (
            right is not None
            and right_gap is not None
            and right_gap <= maximum_gap
        ):
            pair_constraints[(middle.order, right.order)] = (
                _NeighbourGapConstraint(
                    before=middle.order,
                    after=right.order,
                    source_gap=right_gap,
                    allowed_delta=tolerance,
                )
            )
        if (
            left is None
            or right is None
            or left_gap is None
            or right_gap is None
            or left_gap > maximum_gap
            or right_gap > maximum_gap
            or abs(left_gap - right_gap) < _GAP_AFFINITY_EVIDENCE_DLU
        ):
            continue
        affinity_constraints.append(
            _GapAffinityConstraint(
                left=left.order,
                middle=middle.order,
                right=right.order,
                closer_side="left" if left_gap < right_gap else "right",
            )
        )
    return tuple(pair_constraints.values()) + tuple(affinity_constraints)


def _nearest_horizontal_neighbour(
    middle: TopologyItem,
    items: tuple[TopologyItem, ...],
    *,
    side: Literal["left", "right"],
    row_tolerance: int,
) -> TopologyItem | None:
    candidates = [
        item
        for item in items
        if item.order != middle.order
        and _same_visual_row(item.rect, middle.rect, row_tolerance)
        and (
            item.rect.right <= middle.rect.left
            if side == "left"
            else item.rect.left >= middle.rect.right
        )
    ]
    if not candidates:
        return None
    if side == "left":
        return min(
            candidates,
            key=lambda item: (
                middle.rect.left - item.rect.right,
                abs(item.rect.center_y - middle.rect.center_y),
                -item.rect.left,
                item.order,
            ),
        )
    return min(
        candidates,
        key=lambda item: (
            item.rect.left - middle.rect.right,
            abs(item.rect.center_y - middle.rect.center_y),
            item.rect.left,
            item.order,
        ),
    )


def _same_visual_row(
    left: RectDlu,
    right: RectDlu,
    tolerance: int,
) -> bool:
    return (
        min(left.bottom, right.bottom) > max(left.top, right.top)
        and abs(left.center_y - right.center_y) <= tolerance
    )


def _orthogonal_projection_overlaps(
    left: RectDlu,
    right: RectDlu,
    axis: Literal["horizontal", "vertical"],
) -> bool:
    if axis == "horizontal":
        return min(left.bottom, right.bottom) > max(left.top, right.top)
    return min(left.right, right.right) > max(left.left, right.left)


def _smallest_container(
    item: TopologyItem,
    items: tuple[TopologyItem, ...],
) -> int | None:
    candidates = [
        candidate
        for candidate in items
        if candidate.order != item.order
        and candidate.is_container
        and candidate.rect.width * candidate.rect.height
        > item.rect.width * item.rect.height
        and _contains(
            candidate.rect,
            item.rect,
            _CONTAINMENT_TOLERANCE_DLU,
        )
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda candidate: (
            candidate.rect.width * candidate.rect.height,
            candidate.order,
        ),
    ).order


def _ordered_pair(
    left_order: int,
    left: RectDlu,
    right_order: int,
    right: RectDlu,
    axis: Literal["horizontal", "vertical"],
) -> _OrderConstraint | None:
    if axis == "horizontal":
        if left.center_x < right.center_x and (
            left.right <= right.left + _ORDER_TOLERANCE_DLU
        ):
            return _OrderConstraint(
                axis,
                left_order,
                right_order,
                max(0, left.right - right.left),
            )
        if right.center_x < left.center_x and (
            right.right <= left.left + _ORDER_TOLERANCE_DLU
        ):
            return _OrderConstraint(
                axis,
                right_order,
                left_order,
                max(0, right.right - left.left),
            )
        return None
    if left.center_y < right.center_y and (
        left.bottom <= right.top + _ORDER_TOLERANCE_DLU
    ):
        return _OrderConstraint(
            axis,
            left_order,
            right_order,
            max(0, left.bottom - right.top),
        )
    if right.center_y < left.center_y and (
        right.bottom <= left.top + _ORDER_TOLERANCE_DLU
    ):
        return _OrderConstraint(
            axis,
            right_order,
            left_order,
            max(0, right.bottom - left.top),
        )
    return None


def _alignment_constraints(
    first_order: int,
    first: RectDlu,
    second_order: int,
    second: RectDlu,
) -> tuple[_AlignmentConstraint, ...]:
    anchors: tuple[
        Literal["left", "right", "top", "bottom", "center-x", "center-y"],
        ...,
    ] = ("left", "right", "top", "bottom", "center-x", "center-y")
    result = []
    for anchor in anchors:
        delta = abs(_anchor_value(first, anchor) - _anchor_value(second, anchor))
        if delta <= _ORDER_TOLERANCE_DLU:
            result.append(
                _AlignmentConstraint(
                    anchor=anchor,
                    first=first_order,
                    second=second_order,
                    allowed_delta=max(float(_ORDER_TOLERANCE_DLU), delta),
                )
            )
    return tuple(result)


def _anchor_value(
    rect: RectDlu,
    anchor: Literal["left", "right", "top", "bottom", "center-x", "center-y"],
) -> float:
    if anchor == "left":
        return rect.left
    if anchor == "right":
        return rect.right
    if anchor == "top":
        return rect.top
    if anchor == "bottom":
        return rect.bottom
    if anchor == "center-x":
        return rect.center_x
    return rect.center_y


def _contains(container: RectDlu, child: RectDlu, tolerance: int) -> bool:
    return (
        child.left >= container.left - tolerance
        and child.top >= container.top - tolerance
        and child.right <= container.right + tolerance
        and child.bottom <= container.bottom + tolerance
    )


def _rect_distance(left: RectDlu, right: RectDlu) -> int:
    return (
        abs(left.left - right.left)
        + abs(left.top - right.top)
        + abs(left.right - right.right)
        + abs(left.bottom - right.bottom)
    )


def _maximum_edge_distance(left: RectDlu, right: RectDlu) -> int:
    return max(
        abs(left.left - right.left),
        abs(left.top - right.top),
        abs(left.right - right.right),
        abs(left.bottom - right.bottom),
    )

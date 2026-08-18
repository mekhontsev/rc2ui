from __future__ import annotations

from statistics import median

from rc2ui.domain.geometry import RectDlu
from rc2ui.layout.alternatives import VisualNode
from rc2ui.layout.anchors import (
    AnchorKind,
    Axis,
    AxisAnchorGroup,
    anchor_coordinate2,
)


def coherent_vertical_anchor_groups(
    nodes: list[VisualNode],
    *,
    same_row_pairs: frozenset[frozenset[int]],
    tolerance: int,
    vertical_separators: list[VisualNode] | None = None,
    prefer_near_center: bool = True,
) -> dict[int, AxisAnchorGroup | None]:
    """Choose one topology-safe anchor for each visual row.

    Pairwise overlap is not a transitive row relation: one tall control can
    overlap two adjacent rows without making those rows peers.  Explicit
    vertical separators also partition otherwise coincident rows into panes.
    """

    result: dict[int, AxisAnchorGroup | None] = {
        node.order: None for node in nodes
    }
    regions = _vertical_alignment_regions(
        nodes,
        separators=vertical_separators or [],
    )
    components = _compatible_row_components(
        nodes,
        same_row_pairs=same_row_pairs,
        regions=regions,
        tolerance=tolerance,
    )
    priority = {
        AnchorKind.CENTER: 0,
        AnchorKind.START: 1,
        AnchorKind.END: 2,
    }
    for component in components:
        members = [nodes[index] for index in sorted(component)]
        if len(members) < 2:
            continue
        hinted = any(
            frozenset((left_order, right_order)) in same_row_pairs
            for left_index, left in enumerate(members)
            for right in members[left_index + 1 :]
            for left_order in left.orders
            for right_order in right.orders
        )
        candidates: list[tuple[int, int, AnchorKind, int]] = []
        for kind in AnchorKind:
            values = [
                anchor_coordinate2(member.rect, Axis.VERTICAL, kind)
                for member in members
            ]
            spread = max(values) - min(values)
            coordinate2 = round(median(values))
            if (
                hinted or spread <= (tolerance + 1) * 2
            ) and _vertical_anchor_preserves_order(
                members,
                nodes,
                kind=kind,
                coordinate2=coordinate2,
            ):
                candidates.append(
                    (spread, priority[kind], kind, coordinate2)
                )
        if not candidates:
            continue
        if prefer_near_center and all(
            not member.mapped.expands_vertically for member in members
        ):
            # QLabel, QLineEdit, QComboBox and similar controls have different
            # native heights. Hand-authored RC rows often make a neighbouring
            # edge look fractionally tighter than the intended centre. When
            # the evidence differs by no more than one DLU, prefer the centre
            # so Qt can place every fixed-height peer in one shared row cell.
            best_spread = min(candidate[0] for candidate in candidates)
            near_best = [
                candidate
                for candidate in candidates
                if candidate[0] <= best_spread + 2
            ]
            _, _, kind, coordinate2 = min(
                near_best,
                key=lambda candidate: (
                    candidate[1],
                    candidate[0],
                    candidate[2],
                    candidate[3],
                ),
            )
        else:
            _, _, kind, coordinate2 = min(candidates)
        group = AxisAnchorGroup(
            kind=kind,
            coordinate2=coordinate2,
            node_orders=frozenset(member.order for member in members),
        )
        for member in members:
            result[member.order] = group
    return result


def _vertical_anchor_preserves_order(
    members: list[VisualNode],
    nodes: list[VisualNode],
    *,
    kind: AnchorKind,
    coordinate2: int,
) -> bool:
    """Do not let row snapping cross a distinct neighbouring source row."""

    member_orders = {member.order for member in members}
    group = AxisAnchorGroup(
        kind=kind,
        coordinate2=coordinate2,
        node_orders=frozenset(member_orders),
    )
    proposed = {
        member.order: RectDlu(
            member.rect.x,
            _aligned_start(member.rect.y, member.rect.height, group),
            member.rect.width,
            member.rect.height,
        )
        for member in members
    }
    checked: set[frozenset[int]] = set()
    for left in members:
        for right in nodes:
            pair = frozenset((left.order, right.order))
            if left.order == right.order or pair in checked:
                continue
            checked.add(pair)
            left_rect = left.rect
            right_rect = right.rect
            proposed_left = proposed.get(left.order, left_rect)
            proposed_right = proposed.get(right.order, right_rect)
            if left_rect.bottom <= right_rect.top + 1:
                allowed_overlap = max(0, left_rect.bottom - right_rect.top)
                if proposed_left.bottom > proposed_right.top + allowed_overlap:
                    return False
            elif right_rect.bottom <= left_rect.top + 1:
                allowed_overlap = max(0, right_rect.bottom - left_rect.top)
                if proposed_right.bottom > proposed_left.top + allowed_overlap:
                    return False
    return True


def _compatible_row_components(
    nodes: list[VisualNode],
    *,
    same_row_pairs: frozenset[frozenset[int]],
    regions: dict[int, tuple[int, ...]],
    tolerance: int,
) -> tuple[frozenset[int], ...]:
    """Build anchor-coherent rows without transitive overlap bridges."""

    components = {index: {index} for index in range(len(nodes))}
    owner = {index: index for index in range(len(nodes))}
    edges: list[tuple[int, int, int, int, int, int]] = []
    for left_index, left in enumerate(nodes):
        for right_index in range(left_index + 1, len(nodes)):
            right = nodes[right_index]
            if not _row_pair_is_compatible(
                left,
                right,
                same_row_pairs=same_row_pairs,
                regions=regions,
            ):
                continue
            hinted = _has_pair_hint(left, right, same_row_pairs)
            anchor_delta2 = min(
                abs(
                    anchor_coordinate2(left.rect, Axis.VERTICAL, kind)
                    - anchor_coordinate2(
                        right.rect,
                        Axis.VERTICAL,
                        kind,
                    )
                )
                for kind in AnchorKind
            )
            edges.append(
                (
                    0 if hinted else 1,
                    anchor_delta2,
                    left.order,
                    right.order,
                    left_index,
                    right_index,
                )
            )

    for _, _, _, _, left_index, right_index in sorted(edges):
        left_owner = owner[left_index]
        right_owner = owner[right_index]
        if left_owner == right_owner:
            continue
        left_component = components[left_owner]
        right_component = components[right_owner]
        if not _row_components_can_merge(
            nodes,
            left_component,
            right_component,
            same_row_pairs=same_row_pairs,
            regions=regions,
            tolerance=tolerance,
        ):
            continue
        target = min(left_owner, right_owner)
        removed = max(left_owner, right_owner)
        merged = left_component | right_component
        components[target] = merged
        del components[removed]
        for member in merged:
            owner[member] = target

    return tuple(
        frozenset(component)
        for _, component in sorted(components.items())
    )


def _row_components_can_merge(
    nodes: list[VisualNode],
    left_component: set[int],
    right_component: set[int],
    *,
    same_row_pairs: frozenset[frozenset[int]],
    regions: dict[int, tuple[int, ...]],
    tolerance: int,
) -> bool:
    merged = left_component | right_component
    if len({regions[nodes[index].order] for index in merged}) > 1:
        return False
    bound2 = (tolerance + 1) * 2
    for kind in AnchorKind:
        values = [
            anchor_coordinate2(nodes[index].rect, Axis.VERTICAL, kind)
            for index in merged
        ]
        if max(values) - min(values) <= bound2:
            return True
    return all(
        _has_pair_hint(
            nodes[left_member],
            nodes[right_member],
            same_row_pairs,
        )
        and _same_visual_row(
            nodes[left_member],
            nodes[right_member],
            same_row_pairs=same_row_pairs,
        )
        for left_member in left_component
        for right_member in right_component
    )


def _row_pair_is_compatible(
    left: VisualNode,
    right: VisualNode,
    *,
    same_row_pairs: frozenset[frozenset[int]],
    regions: dict[int, tuple[int, ...]],
) -> bool:
    return (
        regions[left.order] == regions[right.order]
        and _same_visual_row(
            left,
            right,
            same_row_pairs=same_row_pairs,
        )
    )


def _has_pair_hint(
    left: VisualNode,
    right: VisualNode,
    same_row_pairs: frozenset[frozenset[int]],
) -> bool:
    return any(
        frozenset((left_order, right_order)) in same_row_pairs
        for left_order in left.orders
        for right_order in right.orders
    )


def _vertical_alignment_regions(
    nodes: list[VisualNode],
    *,
    separators: list[VisualNode],
) -> dict[int, tuple[int, ...]]:
    """Partition row inference at explicit vertical RC separators."""

    ordered = sorted(
        separators,
        key=lambda node: (node.rect.left, node.rect.top, node.order),
    )
    return {
        node.order: tuple(
            _separator_side(node.rect, separator.rect)
            for separator in ordered
        )
        for node in nodes
    }


def _separator_side(rect: RectDlu, separator: RectDlu) -> int:
    if rect.bottom <= separator.top or rect.top >= separator.bottom:
        return 0
    if rect.right <= separator.left:
        return -1
    if rect.left >= separator.right:
        return 1
    return 0


def _same_visual_row(
    left: VisualNode,
    right: VisualNode,
    *,
    same_row_pairs: frozenset[frozenset[int]],
) -> bool:
    hinted = _has_pair_hint(left, right, same_row_pairs)
    minimum_height = min(left.rect.height, right.rect.height)
    maximum_height = max(left.rect.height, right.rect.height)
    overlap = max(
        0,
        min(left.rect.bottom, right.rect.bottom)
        - max(left.rect.top, right.rect.top),
    )
    return (
        minimum_height > 0
        and maximum_height <= minimum_height * 2
        and (
            overlap >= minimum_height * 0.5
            or (hinted and overlap >= minimum_height * 0.25)
        )
    )


def _aligned_start(
    start: int,
    size: int,
    group: AxisAnchorGroup,
) -> int:
    if group.kind is AnchorKind.START:
        return round(group.coordinate2 / 2)
    if group.kind is AnchorKind.END:
        return round((group.coordinate2 - size * 2) / 2)
    return round((group.coordinate2 - size) / 2)

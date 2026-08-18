from __future__ import annotations

from dataclasses import dataclass

from rc2ui.domain.geometry import RectDlu
from rc2ui.layout.alternatives import VisualNode
from rc2ui.layout.anchors import Axis


@dataclass(frozen=True, slots=True)
class CoordinateTracks:
    boundaries: tuple[int, ...]
    stretch: tuple[int, ...]

    def span(self, start: int, end: int) -> tuple[int, int]:
        first = min(
            range(len(self.boundaries)),
            key=lambda index: (abs(self.boundaries[index] - start), index),
        )
        last = min(
            range(len(self.boundaries)),
            key=lambda index: (abs(self.boundaries[index] - end), index),
        )
        if last <= first:
            last = min(first + 1, len(self.boundaries) - 1)
            first = max(0, last - 1)
        return first, last - first


def build_coordinate_tracks(
    nodes: list[VisualNode],
    *,
    bounds: RectDlu,
    axis: Axis,
) -> CoordinateTracks:
    """Turn exact rectangle edges into proportional scalable intervals."""

    if axis is Axis.HORIZONTAL:
        bounds_start, bounds_end = bounds.left, bounds.right
        starts = [node.rect.left for node in nodes]
        ends = [node.rect.right for node in nodes]
    else:
        bounds_start, bounds_end = bounds.top, bounds.bottom
        starts = [node.rect.top for node in nodes]
        ends = [node.rect.bottom for node in nodes]

    # Anchor inference has already snapped probable human alignment errors.
    # Keep every remaining edge exact: even a one-DLU interval may be an
    # intentional gap, and losing it would violate resize/order invariants.
    boundaries = tuple(sorted({bounds_start, bounds_end, *starts, *ends}))
    if len(boundaries) < 2:
        boundaries = (bounds_start, max(bounds_start + 1, bounds_end))
    stretch = tuple(
        max(1, right - left)
        for left, right in zip(boundaries, boundaries[1:])
    )
    return CoordinateTracks(boundaries, stretch)

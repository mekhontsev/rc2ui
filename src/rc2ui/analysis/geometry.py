from __future__ import annotations

from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True, slots=True)
class CoordinateCluster:
    anchor: int
    values: tuple[int, ...]


def cluster_coordinates(
    values: list[int] | tuple[int, ...], *, tolerance: int = 3
) -> tuple[CoordinateCluster, ...]:
    """Cluster nearby DLU coordinates without depending on input order."""

    if tolerance < 0:
        raise ValueError("coordinate tolerance cannot be negative")
    clusters: list[list[int]] = []
    for value in sorted(values):
        if not clusters or value - round(median(clusters[-1])) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return tuple(
        CoordinateCluster(anchor=round(median(cluster)), values=tuple(cluster))
        for cluster in clusters
    )


def normalized_anchor(value: int, clusters: tuple[CoordinateCluster, ...]) -> int:
    if not clusters:
        return value
    return min(clusters, key=lambda cluster: (abs(cluster.anchor - value), cluster.anchor)).anchor

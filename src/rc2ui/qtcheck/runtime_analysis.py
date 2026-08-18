from __future__ import annotations

from pathlib import Path

from rc2ui.qtcheck.protocol import diagnostic


def analyze_stretch(
    snapshots: list[dict[str, object]],
    *,
    path: Path,
) -> tuple[dict[str, str], ...]:
    """Compare smallest and largest runtime geometries without importing Qt."""

    if len(snapshots) < 2:
        return ()
    first = min(snapshots, key=_snapshot_area)
    last = max(snapshots, key=_snapshot_area)
    first_widgets = first.get("widgets")
    last_widgets = last.get("widgets")
    if not isinstance(first_widgets, dict) or not isinstance(last_widgets, dict):
        return ()

    diagnostics: list[dict[str, str]] = []
    strong_growth = {"Expanding", "MinimumExpanding", "Ignored"}
    for name, first_item in first_widgets.items():
        last_item = last_widgets.get(name)
        if not isinstance(first_item, dict) or not isinstance(last_item, dict):
            continue
        if not first_item.get("visible") or not last_item.get("visible"):
            continue
        first_geometry = first_item.get("geometry")
        last_geometry = last_item.get("geometry")
        first_parent = first_item.get("parent_size")
        last_parent = last_item.get("parent_size")
        if not all(
            _is_number_list(value, minimum_length=4 if position < 2 else 2)
            for position, value in enumerate(
                (first_geometry, last_geometry, first_parent, last_parent)
            )
        ):
            continue
        parent_width_growth = last_parent[0] - first_parent[0]
        parent_height_growth = last_parent[1] - first_parent[1]
        width_growth = last_geometry[2] - first_geometry[2]
        height_growth = last_geometry[3] - first_geometry[3]
        if (
            first_item.get("horizontal_policy") in strong_growth
            and parent_width_growth >= 24
            and width_growth < 4
        ):
            diagnostics.append(
                diagnostic(
                    "qt.no-horizontal-growth",
                    "warning",
                    f"expanding widget {name!r} did not grow horizontally",
                    path,
                )
            )
        if (
            first_item.get("vertical_policy") in strong_growth
            and parent_height_growth >= 24
            and height_growth < 4
        ):
            diagnostics.append(
                diagnostic(
                    "qt.no-vertical-growth",
                    "warning",
                    f"expanding widget {name!r} did not grow vertically",
                    path,
                )
            )
    return tuple(diagnostics)


def _snapshot_area(snapshot: dict[str, object]) -> int:
    size = snapshot.get("form_size")
    if not _is_number_list(size, minimum_length=2):
        return 0
    return int(size[0]) * int(size[1])


def _is_number_list(value: object, *, minimum_length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= minimum_length
        and all(isinstance(item, (int, float)) for item in value)
    )

from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from pathlib import Path
from statistics import median

from rc2ui.qtcheck.protocol import diagnostic


_MODERATE_AXIS_DRIFT = 0.12
_MODERATE_VECTOR_DRIFT = 0.16
_RADICAL_AXIS_DRIFT = 0.24
_RADICAL_VECTOR_DRIFT = 0.30
_SOURCE_ANCHOR_TOLERANCE_DLU = 3
_MINIMUM_ANCHOR_GROUP = 2
_RUNTIME_ANCHOR_TOLERANCE = 0.015
_LOCAL_GAP_MAXIMUM_DLU = 12
_LOCAL_GAP_AFFINITY_EVIDENCE_DLU = 2


@dataclass(frozen=True, slots=True)
class _Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def left(self) -> float:
        return self.x

    @property
    def top(self) -> float:
        return self.y

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


@dataclass(frozen=True, slots=True)
class _GeometryPair:
    name: str
    source: _Rect
    layout: _Rect
    runtime: _Rect
    separator_orientation: str | None
    qt_class: str | None
    source_parent_name: str | None
    runtime_parent_name: str | None
    horizontal_anchor: tuple[str, int] | None
    vertical_anchor: tuple[str, int] | None
    anchor_metadata: bool
    alternative_states: tuple[tuple[int, int], ...]


def analyze_source_geometry(
    snapshots: list[dict[str, object]],
    reference: object,
    *,
    path: Path,
) -> tuple[dict[str, str], ...]:
    """Compare runtime Qt geometry with normalized source DLU geometry."""

    if not isinstance(reference, dict) or not snapshots:
        return ()
    source_form = _rect(reference.get("rect_dlu"))
    layout_form = _rect(reference.get("layout_rect_dlu")) or source_form
    raw_controls = reference.get("controls")
    if (
        source_form is None
        or source_form.width <= 0
        or source_form.height <= 0
        or not isinstance(raw_controls, list)
    ):
        return ()

    baseline = _snapshot_geometry(
        _baseline_snapshot(snapshots),
        raw_controls,
    )
    if baseline is None:
        return ()
    runtime_form, pairs = baseline

    diagnostics: list[dict[str, str]] = []
    diagnostics.extend(
        _position_diagnostics(pairs, layout_form, runtime_form, path)
    )
    diagnostics.extend(
        _size_diagnostics(pairs, layout_form, runtime_form, path)
    )
    for snapshot in snapshots:
        geometry = _snapshot_geometry(snapshot, raw_controls)
        if geometry is None:
            continue
        current_form, current_pairs = geometry
        diagnostics.extend(_order_diagnostics(current_pairs, current_form, path))
        diagnostics.extend(
            _anchor_diagnostics(
                current_pairs,
                current_form,
                path,
            )
        )
        diagnostics.extend(
            _local_gap_affinity_diagnostics(
                current_pairs,
                current_form,
                path,
            )
        )
        diagnostics.extend(
            _separator_diagnostics(current_pairs, current_form, path)
        )
        diagnostics.extend(_parent_diagnostics(current_pairs, current_form, path))
    diagnostics.extend(
        _resize_gap_diagnostics(
            snapshots,
            raw_controls,
            path,
        )
    )
    return tuple(_deduplicate_diagnostics(diagnostics))


def _snapshot_geometry(
    snapshot: dict[str, object],
    raw_controls: list[object],
) -> tuple[_Rect, list[_GeometryPair]] | None:
    runtime_form = _runtime_form(snapshot)
    runtime_widgets = snapshot.get("widgets")
    if runtime_form is None or not isinstance(runtime_widgets, dict):
        return None
    source_entries = _source_entries(raw_controls)
    source_parents = _source_parent_names(source_entries)
    pairs: list[_GeometryPair] = []
    for raw in raw_controls:
        if not isinstance(raw, dict):
            continue
        name = raw.get("object_name")
        source = _rect(raw.get("rect_dlu"))
        layout = _rect(raw.get("layout_rect_dlu")) or source
        runtime_item = runtime_widgets.get(name) if isinstance(name, str) else None
        if (
            not isinstance(name, str)
            or source is None
            or not isinstance(runtime_item, dict)
            or not runtime_item.get("visible")
        ):
            continue
        runtime = _rect(
            runtime_item.get("root_geometry")
            or runtime_item.get("geometry")
        )
        if runtime is None:
            continue
        orientation = raw.get("separator_orientation")
        anchor_metadata = (
            raw.get("horizontal_anchor") is not None
            or raw.get("vertical_anchor") is not None
        )
        pairs.append(
            _GeometryPair(
                name=name,
                source=source,
                layout=layout,
                runtime=runtime,
                separator_orientation=(
                    orientation
                    if orientation in {"horizontal", "vertical"}
                    else None
                ),
                qt_class=(
                    raw.get("qt_class")
                    if isinstance(raw.get("qt_class"), str)
                    else None
                ),
                source_parent_name=source_parents.get(name),
                runtime_parent_name=(
                    runtime_item.get("parent_name")
                    if isinstance(runtime_item.get("parent_name"), str)
                    else None
                ),
                horizontal_anchor=_anchor_reference(
                    raw.get("horizontal_anchor")
                ),
                vertical_anchor=_anchor_reference(
                    raw.get("vertical_anchor")
                ),
                anchor_metadata=anchor_metadata,
                alternative_states=_alternative_states(
                    raw.get("alternative_states")
                ),
            )
        )
    return runtime_form, pairs


def _source_entries(
    raw_controls: list[object],
) -> list[tuple[str, _Rect, str | None]]:
    entries: list[tuple[str, _Rect, str | None]] = []
    for raw in raw_controls:
        if not isinstance(raw, dict):
            continue
        name = raw.get("object_name")
        rect = _rect(raw.get("rect_dlu"))
        if not isinstance(name, str) or rect is None:
            continue
        qt_class = raw.get("qt_class")
        entries.append(
            (
                name,
                rect,
                qt_class if isinstance(qt_class, str) else None,
            )
        )
    return entries


def _source_parent_names(
    entries: list[tuple[str, _Rect, str | None]],
) -> dict[str, str | None]:
    groups = [entry for entry in entries if entry[2] == "QGroupBox"]
    result: dict[str, str | None] = {}
    for name, rect, _ in entries:
        containing = [
            group
            for group in groups
            if group[0] != name
            and _contains_source_rect(group[1], rect)
            and group[1].width * group[1].height > rect.width * rect.height
        ]
        containing.sort(
            key=lambda group: (
                group[1].width * group[1].height,
                group[1].top,
                group[1].left,
                group[0],
            )
        )
        result[name] = containing[0][0] if containing else None
    return result


def _parent_diagnostics(
    pairs: list[_GeometryPair],
    runtime_form: _Rect,
    path: Path,
) -> list[dict[str, str]]:
    changed = [
        f"{pair.name!r}: expected {pair.source_parent_name!r}, got "
        f"{pair.runtime_parent_name!r}"
        for pair in pairs
        if pair.source_parent_name is not None
        and pair.runtime_parent_name != pair.source_parent_name
        and not (
            pair.runtime_parent_name
            and pair.runtime_parent_name.startswith("runtimeAlternatives")
        )
    ]
    if not changed:
        return []
    return [
        diagnostic(
            "qt.source-parent-changed",
            "error",
            (
                f"{len(changed)} control(s) left their RC group: "
                + "; ".join(changed[:6])
                + f" at {_runtime_size(runtime_form)}"
            ),
            path,
        )
    ]


def _position_diagnostics(
    pairs: list[_GeometryPair],
    layout_form: _Rect,
    runtime_form: _Rect,
    path: Path,
) -> list[dict[str, str]]:
    warning: list[tuple[float, _GeometryPair, float, float, float, float]] = []
    severe: list[tuple[float, _GeometryPair, float, float, float, float]] = []
    for pair in pairs:
        if pair.source.width <= 0 or pair.source.height <= 0:
            continue
        if not _contains_point(
            layout_form,
            pair.layout.center_x,
            pair.layout.center_y,
        ):
            continue
        fixed_content = pair.qt_class in {
            "QLabel",
            "QPushButton",
            "QToolButton",
            "QCheckBox",
            "QRadioButton",
        }
        expected_x = (
            pair.layout.left if fixed_content else pair.layout.center_x
        ) - layout_form.left
        expected_y = (
            pair.layout.top if fixed_content else pair.layout.center_y
        ) - layout_form.top
        expected_x /= layout_form.width
        expected_y /= layout_form.height
        actual_x = (
            pair.runtime.left if fixed_content else pair.runtime.center_x
        ) / runtime_form.width
        actual_y = (
            pair.runtime.top if fixed_content else pair.runtime.center_y
        ) / runtime_form.height
        delta_x = abs(expected_x - actual_x)
        delta_y = abs(expected_y - actual_y)
        distance = hypot(delta_x, delta_y)
        item = (
            distance,
            pair,
            expected_x,
            expected_y,
            actual_x,
            actual_y,
        )
        if (
            max(delta_x, delta_y) >= _RADICAL_AXIS_DRIFT
            or distance >= _RADICAL_VECTOR_DRIFT
        ):
            severe.append(item)
        elif (
            max(delta_x, delta_y) >= _MODERATE_AXIS_DRIFT
            or distance >= _MODERATE_VECTOR_DRIFT
        ):
            warning.append(item)

    result: list[dict[str, str]] = []
    if severe:
        result.append(
            diagnostic(
                "qt.source-geometry-drift",
                "error",
                _drift_message(
                    severe,
                    "radically moved",
                    runtime_form,
                ),
                path,
            )
        )
    if warning:
        result.append(
            diagnostic(
                "qt.source-geometry-drift",
                "warning",
                _drift_message(
                    warning,
                    "moved substantially",
                    runtime_form,
                ),
                path,
            )
        )
    return result


def _drift_message(
    items: list[tuple[float, _GeometryPair, float, float, float, float]],
    description: str,
    runtime_form: _Rect,
) -> str:
    samples = "; ".join(
        (
            f"{pair.name!r} expected ({expected_x:.0%}, {expected_y:.0%}), "
            f"got ({actual_x:.0%}, {actual_y:.0%})"
        )
        for _, pair, expected_x, expected_y, actual_x, actual_y in sorted(
            items,
            key=lambda item: (-item[0], item[1].name),
        )[:4]
    )
    return (
        f"{len(items)} widget(s) {description} relative to RC: {samples} "
        f"at {_runtime_size(runtime_form)}"
    )


def _size_diagnostics(
    pairs: list[_GeometryPair],
    layout_form: _Rect,
    runtime_form: _Rect,
    path: Path,
) -> list[dict[str, str]]:
    collapsed: list[str] = []
    for pair in pairs:
        if pair.source.width <= 0 or pair.source.height <= 0:
            continue
        if pair.qt_class in {
            "QLabel",
            "QPushButton",
            "QToolButton",
            "QCheckBox",
            "QRadioButton",
        }:
            continue
        source_width = pair.layout.width / layout_form.width
        source_height = pair.layout.height / layout_form.height
        runtime_width = pair.runtime.width / runtime_form.width
        runtime_height = pair.runtime.height / runtime_form.height
        width_collapsed = (
            source_width >= 0.08 and runtime_width < source_width * 0.25
        )
        height_collapsed = (
            source_height >= 0.06 and runtime_height < source_height * 0.25
        )
        if width_collapsed or height_collapsed:
            collapsed.append(pair.name)
    if not collapsed:
        return []
    return [
        diagnostic(
            "qt.source-size-collapse",
            "error",
            (
                f"{len(collapsed)} widget(s) collapsed relative to RC: "
                + ", ".join(repr(name) for name in sorted(collapsed)[:8])
                + f" at {_runtime_size(runtime_form)}"
            ),
            path,
        )
    ]


def _order_diagnostics(
    pairs: list[_GeometryPair],
    runtime_form: _Rect,
    path: Path,
) -> list[dict[str, str]]:
    changed: list[str] = []
    runtime_x_tolerance = max(4.0, runtime_form.width * 0.015)
    runtime_y_tolerance = max(4.0, runtime_form.height * 0.015)
    ordinary = [
        pair
        for pair in pairs
        if pair.separator_orientation is None
        and pair.source.width > 0
        and pair.source.height > 0
    ]
    for index, left in enumerate(ordinary):
        for right in ordinary[index + 1 :]:
            if left.source_parent_name != right.source_parent_name:
                continue
            if not _runtime_layers_comparable(left, right):
                continue
            if _nested_source_pair(left, right):
                continue
            if left.source.right <= right.source.left:
                if left.runtime.right > right.runtime.left + runtime_x_tolerance:
                    changed.append(f"{left.name!r}/{right.name!r} left-to-right")
            elif right.source.right <= left.source.left:
                if right.runtime.right > left.runtime.left + runtime_x_tolerance:
                    changed.append(f"{right.name!r}/{left.name!r} left-to-right")
            if left.source.bottom <= right.source.top:
                if left.runtime.bottom > right.runtime.top + runtime_y_tolerance:
                    changed.append(f"{left.name!r}/{right.name!r} top-to-bottom")
            elif right.source.bottom <= left.source.top:
                if right.runtime.bottom > left.runtime.top + runtime_y_tolerance:
                    changed.append(f"{right.name!r}/{left.name!r} top-to-bottom")
    if not changed:
        return []
    return [
        diagnostic(
            "qt.source-order-changed",
            "error",
            (
                f"runtime reversed {len(changed)} clear RC ordering relation(s): "
                + "; ".join(changed[:6])
                + f" at {_runtime_size(runtime_form)}"
            ),
            path,
        )
    ]


def _anchor_diagnostics(
    pairs: list[_GeometryPair],
    runtime_form: _Rect,
    path: Path,
) -> list[dict[str, str]]:
    broken: list[str] = []
    ordinary = [
        pair
        for pair in pairs
        if pair.separator_orientation is None
        and pair.source.width > 0
        and pair.source.height > 0
    ]
    for axis, runtime_size in (
        ("horizontal", runtime_form.width),
        ("vertical", runtime_form.height),
    ):
        clusters = (
            _declared_anchor_clusters(ordinary, axis=axis)
            if any(pair.anchor_metadata for pair in ordinary)
            else (
                _vertical_row_anchor_clusters(ordinary)
                if axis == "vertical"
                else _selected_anchor_clusters(ordinary, axis=axis)
            )
        )
        for kind, cluster in clusters:
            allowed = max(
                4.0,
                runtime_size * _RUNTIME_ANCHOR_TOLERANCE,
            )
            broken_pairs = [
                (left, right)
                for index, left in enumerate(cluster)
                for right in cluster[index + 1 :]
                if _runtime_layers_comparable(left, right)
                and abs(
                    _anchor_value(left.runtime, axis=axis, kind=kind)
                    - _anchor_value(right.runtime, axis=axis, kind=kind)
                )
                > allowed
                and not _stronger_source_anchor_is_preserved(
                    [left, right],
                    axis=axis,
                    broken_kind=kind,
                    allowed_runtime_spread=allowed,
                )
            ]
            if not broken_pairs:
                continue
            affected = sorted(
                {pair.name for pair_pair in broken_pairs for pair in pair_pair}
            )
            names = ", ".join(repr(name) for name in affected[:5])
            broken.append(f"{axis} {kind}: {names}")
    if not broken:
        return []
    return [
        diagnostic(
            "qt.source-anchor-drift",
            "error",
            (
                f"{len(broken)} RC anchor group(s) did not remain aligned: "
                + "; ".join(broken[:5])
                + f" at {_runtime_size(runtime_form)}"
            ),
            path,
        )
    ]


def _stronger_source_anchor_is_preserved(
    cluster: list[_GeometryPair],
    *,
    axis: str,
    broken_kind: str,
    allowed_runtime_spread: float,
) -> bool:
    """Do not prefer a fuzzy edge over an exact preserved edge.

    Similar-size controls can be within the source tolerance for start,
    centre and end at once.  If the broken relation was only approximate but
    the same controls have a strictly tighter source anchor that still holds,
    the qualitative alignment was preserved rather than lost.
    """

    broken_values = [
        _anchor_value(pair.source, axis=axis, kind=broken_kind)
        for pair in cluster
    ]
    broken_spread = max(broken_values) - min(broken_values)
    for candidate in ("start", "center", "end"):
        if candidate == broken_kind:
            continue
        source_values = [
            _anchor_value(pair.source, axis=axis, kind=candidate)
            for pair in cluster
        ]
        if max(source_values) - min(source_values) + 1e-9 >= broken_spread:
            continue
        runtime_values = [
            _anchor_value(pair.runtime, axis=axis, kind=candidate)
            for pair in cluster
        ]
        if max(runtime_values) - min(runtime_values) <= allowed_runtime_spread:
            return True
    return False


def _vertical_row_anchor_clusters(
    pairs: list[_GeometryPair],
) -> list[tuple[str, list[_GeometryPair]]]:
    """Infer vertical anchors only among controls sharing a visual row."""

    containers: dict[str | None, list[_GeometryPair]] = {}
    for pair in pairs:
        containers.setdefault(pair.source_parent_name, []).append(pair)
    result: list[tuple[str, list[_GeometryPair]]] = []
    priority = {"center": 0, "start": 1, "end": 2}
    for container_pairs in containers.values():
        remaining = set(range(len(container_pairs)))
        while remaining:
            component = {remaining.pop()}
            changed = True
            while changed:
                changed = False
                for index in tuple(remaining):
                    if any(
                        _same_source_row(
                            container_pairs[index].source,
                            container_pairs[member].source,
                        )
                        for member in component
                    ):
                        component.add(index)
                        remaining.remove(index)
                        changed = True
            cluster = [container_pairs[index] for index in sorted(component)]
            if len(cluster) < _MINIMUM_ANCHOR_GROUP:
                continue
            candidates: list[tuple[float, int, str]] = []
            for kind in ("start", "center", "end"):
                values = [
                    _anchor_value(pair.source, axis="vertical", kind=kind)
                    for pair in cluster
                ]
                spread = max(values) - min(values)
                if spread <= _SOURCE_ANCHOR_TOLERANCE_DLU:
                    candidates.append((spread, priority[kind], kind))
            if candidates:
                result.append((min(candidates)[-1], cluster))
    return result


def _same_source_row(left: _Rect, right: _Rect) -> bool:
    minimum_height = min(left.height, right.height)
    maximum_height = max(left.height, right.height)
    return (
        minimum_height > 0
        and maximum_height <= minimum_height * 2
        and _overlap(left.top, left.bottom, right.top, right.bottom)
        >= minimum_height * 0.5
    )


def _selected_anchor_clusters(
    pairs: list[_GeometryPair],
    *,
    axis: str,
) -> list[tuple[str, list[_GeometryPair]]]:
    """Resolve ambiguous near-alignments exactly as one layout decision.

    A hand-authored rectangle can be within tolerance of several unrelated
    edges at once.  Reporting every possible cluster makes contradictory
    invariants.  Prefer the largest, tightest group for each control; left/top
    and right/bottom win ties over a coincidental centre on the same axis.
    """

    if any(pair.anchor_metadata for pair in pairs):
        return _declared_anchor_clusters(pairs, axis=axis)

    candidates: list[tuple[str, list[_GeometryPair], float]] = []
    containers: dict[str | None, list[_GeometryPair]] = {}
    for pair in pairs:
        container = (
            pair.runtime_parent_name
            if pair.runtime_parent_name
            and pair.runtime_parent_name.startswith("runtimeAlternatives")
            else pair.source_parent_name
        )
        containers.setdefault(container, []).append(pair)
    for container_pairs in containers.values():
        for kind in ("start", "center", "end"):
            for raw_cluster in _anchor_clusters(
                container_pairs,
                axis=axis,
                kind=kind,
                tolerance=_SOURCE_ANCHOR_TOLERANCE_DLU,
            ):
                cluster = [
                    pair
                    for pair in raw_cluster
                    if not any(
                        other is not pair
                        and _contains_rect(pair.source, other.source)
                        for other in raw_cluster
                    )
                ]
                if len(cluster) < _MINIMUM_ANCHOR_GROUP:
                    continue
                values = [
                    _anchor_value(pair.source, axis=axis, kind=kind)
                    for pair in cluster
                ]
                spread = max(values) - min(values)
                if (
                    axis == "vertical"
                    and spread > 0
                    and max(pair.source.height for pair in cluster)
                    > min(pair.source.height for pair in cluster) * 3
                ):
                    continue
                candidates.append((kind, cluster, spread))

    priority = (
        {"start": 0, "end": 1, "center": 2}
        if axis == "horizontal"
        else {"center": 0, "start": 1, "end": 2}
    )
    selected: dict[str, tuple[str, tuple[str, ...]]] = {}
    keyed: dict[tuple[str, tuple[str, ...]], list[_GeometryPair]] = {}
    for kind, cluster, spread in candidates:
        names = tuple(sorted(pair.name for pair in cluster))
        key = (kind, names)
        keyed[key] = cluster
        rank = (-len(cluster), spread, priority[kind], names)
        for pair in cluster:
            current = selected.get(pair.name)
            if current is None:
                selected[pair.name] = key
                continue
            current_cluster = keyed[current]
            current_values = [
                _anchor_value(item.source, axis=axis, kind=current[0])
                for item in current_cluster
            ]
            current_rank = (
                -len(current_cluster),
                max(current_values) - min(current_values),
                priority[current[0]],
                current[1],
            )
            if rank < current_rank:
                selected[pair.name] = key

    result: list[tuple[str, list[_GeometryPair]]] = []
    for key, cluster in keyed.items():
        if all(selected.get(pair.name) == key for pair in cluster):
            result.append((key[0], cluster))
    return result


def _declared_anchor_clusters(
    pairs: list[_GeometryPair],
    *,
    axis: str,
) -> list[tuple[str, list[_GeometryPair]]]:
    """Use the exact peer anchor selected while constructing the layout.

    The source can support several near-alignments at once. Re-inferring the
    choice from snapped report rectangles can select a different edge than
    the layout and create contradictory post-generation requirements.
    """

    groups: dict[tuple[str, int], list[_GeometryPair]] = {}
    for pair in pairs:
        anchor = (
            pair.horizontal_anchor
            if axis == "horizontal"
            else pair.vertical_anchor
        )
        if anchor is None:
            continue
        kind, coordinate2 = anchor
        groups.setdefault(
            (kind, coordinate2),
            [],
        ).append(pair)
    return [
        (kind, sorted(cluster, key=lambda pair: pair.name))
        for (kind, _), cluster in sorted(
            groups.items(),
            key=lambda item: (
                item[0][0],
                item[0][1],
            ),
        )
        if len(cluster) >= _MINIMUM_ANCHOR_GROUP
    ]


def _resize_gap_diagnostics(
    snapshots: list[dict[str, object]],
    raw_controls: list[object],
    path: Path,
) -> list[dict[str, str]]:
    """Require source whitespace to remain elastic without changing order."""

    if len(snapshots) < 2:
        return []
    first_geometry = _snapshot_geometry(
        min(snapshots, key=_snapshot_area),
        raw_controls,
    )
    last_geometry = _snapshot_geometry(
        max(snapshots, key=_snapshot_area),
        raw_controls,
    )
    if first_geometry is None or last_geometry is None:
        return []
    first_form, first_pairs = first_geometry
    last_form, last_pairs = last_geometry
    first_by_name = {pair.name: pair for pair in first_pairs}
    last_by_name = {pair.name: pair for pair in last_pairs}
    pairs = [
        pair
        for pair in first_pairs
        if pair.name in last_by_name
        and pair.separator_orientation is None
        and pair.source.width > 0
        and pair.source.height > 0
    ]
    shrunk: list[str] = []
    static: list[str] = []
    for index, left in enumerate(pairs):
        for right in pairs[index + 1 :]:
            if left.source_parent_name != right.source_parent_name:
                continue
            if not _runtime_layers_comparable(left, right):
                continue
            if _nested_source_pair(left, right):
                continue
            if _same_horizontal_lane(left.source, right.source):
                relation = _ordered_gap(left, right, axis="horizontal")
                if relation is not None:
                    before, after, source_gap = relation
                    _classify_gap_growth(
                        first_by_name[before.name].runtime,
                        first_by_name[after.name].runtime,
                        last_by_name[before.name].runtime,
                        last_by_name[after.name].runtime,
                        axis="horizontal",
                        source_gap=source_gap,
                        form_growth=last_form.width - first_form.width,
                        description=f"{before.name!r}/{after.name!r} horizontally",
                        shrunk=shrunk,
                        static=static,
                    )
            if _same_vertical_lane(left.source, right.source):
                relation = _ordered_gap(left, right, axis="vertical")
                if relation is not None:
                    before, after, source_gap = relation
                    _classify_gap_growth(
                        first_by_name[before.name].runtime,
                        first_by_name[after.name].runtime,
                        last_by_name[before.name].runtime,
                        last_by_name[after.name].runtime,
                        axis="vertical",
                        source_gap=source_gap,
                        form_growth=last_form.height - first_form.height,
                        description=f"{before.name!r}/{after.name!r} vertically",
                        shrunk=shrunk,
                        static=static,
                    )

    diagnostics: list[dict[str, str]] = []
    if shrunk:
        diagnostics.append(
            diagnostic(
                "qt.source-gap-shrunk",
                "error",
                (
                    f"{len(shrunk)} RC gap(s) shrank while the form grew: "
                    + "; ".join(shrunk[:6])
                ),
                path,
            )
        )
    if static:
        diagnostics.append(
            diagnostic(
                "qt.source-gap-static",
                "error",
                (
                    f"{len(static)} RC gap(s) did not participate in resize: "
                    + "; ".join(static[:6])
                ),
                path,
            )
        )
    return diagnostics


def _classify_gap_growth(
    first_before: _Rect,
    first_after: _Rect,
    last_before: _Rect,
    last_after: _Rect,
    *,
    axis: str,
    source_gap: float,
    form_growth: float,
    description: str,
    shrunk: list[str],
    static: list[str],
) -> None:
    if form_growth < 24 or source_gap < _SOURCE_ANCHOR_TOLERANCE_DLU:
        return
    first_gap = _runtime_gap(first_before, first_after, axis=axis)
    last_gap = _runtime_gap(last_before, last_after, axis=axis)
    growth = last_gap - first_gap
    if growth < -2:
        shrunk.append(description)
        return
    # Coordinate tracks need the gap to participate, but not to receive a
    # fixed fraction of all surplus: spanning widgets and nested containers
    # legitimately redistribute that surplus. A clearly positive change is
    # enough to preserve the requested qualitative resize behaviour.
    if growth <= 0:
        static.append(description)


def _ordered_gap(
    left: _GeometryPair,
    right: _GeometryPair,
    *,
    axis: str,
) -> tuple[_GeometryPair, _GeometryPair, float] | None:
    if axis == "horizontal":
        if left.source.right <= right.source.left:
            return left, right, right.source.left - left.source.right
        if right.source.right <= left.source.left:
            return right, left, left.source.left - right.source.right
    else:
        if left.source.bottom <= right.source.top:
            return left, right, right.source.top - left.source.bottom
        if right.source.bottom <= left.source.top:
            return right, left, left.source.top - right.source.bottom
    return None


def _runtime_gap(before: _Rect, after: _Rect, *, axis: str) -> float:
    return (
        after.left - before.right
        if axis == "horizontal"
        else after.top - before.bottom
    )


def _local_gap_affinity_diagnostics(
    pairs: list[_GeometryPair],
    runtime_form: _Rect,
    path: Path,
) -> list[dict[str, str]]:
    """Detect a local caption becoming attached to the wrong neighbour."""

    ordinary = [
        pair
        for pair in pairs
        if pair.separator_orientation is None
        and pair.qt_class != "QGroupBox"
        and pair.source.width > 0
        and pair.source.height > 0
    ]
    changed: list[str] = []
    for middle in ordinary:
        peers = [
            pair
            for pair in ordinary
            if pair is not middle
            and pair.source_parent_name == middle.source_parent_name
            and _runtime_layers_comparable(pair, middle)
            and _same_source_row(pair.source, middle.source)
        ]
        left = _nearest_source_neighbour(middle, peers, side="left")
        right = _nearest_source_neighbour(middle, peers, side="right")
        if left is None or right is None:
            continue
        source_left_gap = middle.source.left - left.source.right
        source_right_gap = right.source.left - middle.source.right
        if (
            max(source_left_gap, source_right_gap) > _LOCAL_GAP_MAXIMUM_DLU
            or abs(source_left_gap - source_right_gap)
            < _LOCAL_GAP_AFFINITY_EVIDENCE_DLU
        ):
            continue
        runtime_left_gap = middle.runtime.left - left.runtime.right
        runtime_right_gap = right.runtime.left - middle.runtime.right
        source_closer_left = source_left_gap < source_right_gap
        runtime_closer_left = runtime_left_gap < runtime_right_gap
        if source_closer_left == runtime_closer_left and (
            runtime_left_gap != runtime_right_gap
        ):
            continue
        changed.append(
            (
                f"{left.name!r}/{middle.name!r}/{right.name!r} "
                f"RC gaps {source_left_gap:g}/{source_right_gap:g}, "
                f"runtime gaps {runtime_left_gap:g}/{runtime_right_gap:g}"
            )
        )
    if not changed:
        return []
    return [
        diagnostic(
            "qt.source-gap-affinity-changed",
            "error",
            (
                f"{len(changed)} local RC neighbour relation(s) changed: "
                + "; ".join(changed[:6])
                + f" at {_runtime_size(runtime_form)}"
            ),
            path,
        )
    ]


def _nearest_source_neighbour(
    middle: _GeometryPair,
    peers: list[_GeometryPair],
    *,
    side: str,
) -> _GeometryPair | None:
    candidates = [
        pair
        for pair in peers
        if (
            pair.source.right <= middle.source.left
            if side == "left"
            else pair.source.left >= middle.source.right
        )
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda pair: (
            (
                middle.source.left - pair.source.right
                if side == "left"
                else pair.source.left - middle.source.right
            ),
            abs(pair.source.center_y - middle.source.center_y),
            pair.name,
        ),
    )


def _same_horizontal_lane(left: _Rect, right: _Rect) -> bool:
    return _overlap(left.top, left.bottom, right.top, right.bottom) >= (
        min(left.height, right.height) * 0.25
    )


def _same_vertical_lane(left: _Rect, right: _Rect) -> bool:
    return _overlap(left.left, left.right, right.left, right.right) >= (
        min(left.width, right.width) * 0.25
    )


def _nested_source_pair(left: _GeometryPair, right: _GeometryPair) -> bool:
    return _contains_rect(left.source, right.source) or _contains_rect(
        right.source,
        left.source,
    )


def _runtime_layers_comparable(
    left: _GeometryPair,
    right: _GeometryPair,
) -> bool:
    """Compare relations only within one simultaneously visible layer."""

    left_states = dict(left.alternative_states)
    right_states = dict(right.alternative_states)
    return all(
        left_states[group] == right_states[group]
        for group in left_states.keys() & right_states.keys()
    )


def _contains_rect(outer: _Rect, inner: _Rect) -> bool:
    return (
        outer.left <= inner.left
        and outer.top <= inner.top
        and outer.right >= inner.right
        and outer.bottom >= inner.bottom
        and (outer.width * outer.height) > (inner.width * inner.height)
    )


def _contains_source_rect(outer: _Rect, inner: _Rect) -> bool:
    tolerance = _SOURCE_ANCHOR_TOLERANCE_DLU
    return (
        inner.left >= outer.left - tolerance
        and inner.top >= outer.top - tolerance
        and inner.right <= outer.right + tolerance
        and inner.bottom <= outer.bottom + tolerance
    )


def _contains_point(rect: _Rect, x: float, y: float) -> bool:
    return rect.left <= x <= rect.right and rect.top <= y <= rect.bottom


def _separator_diagnostics(
    pairs: list[_GeometryPair],
    runtime_form: _Rect,
    path: Path,
) -> list[dict[str, str]]:
    violations: list[str] = []
    for separator in pairs:
        orientation = separator.separator_orientation
        if orientation is None:
            continue
        if orientation == "vertical":
            malformed = separator.runtime.height < separator.runtime.width * 3
            source_cross_start = separator.source.top
            source_cross_end = separator.source.bottom
            separator_source_start = separator.source.left
            separator_source_end = separator.source.right
            separator_runtime_center = separator.runtime.center_x
        else:
            malformed = separator.runtime.width < separator.runtime.height * 3
            source_cross_start = separator.source.left
            source_cross_end = separator.source.right
            separator_source_start = separator.source.top
            separator_source_end = separator.source.bottom
            separator_runtime_center = separator.runtime.center_y
        if malformed:
            violations.append(f"{separator.name!r} lost {orientation} orientation")

        before: list[_GeometryPair] = []
        after: list[_GeometryPair] = []
        for pair in pairs:
            if pair is separator:
                continue
            source_cross_center = (
                pair.source.center_y
                if orientation == "vertical"
                else pair.source.center_x
            )
            if not (
                source_cross_start - 3
                <= source_cross_center
                <= source_cross_end + 3
            ):
                continue
            source_start = (
                pair.source.left
                if orientation == "vertical"
                else pair.source.top
            )
            source_end = (
                pair.source.right
                if orientation == "vertical"
                else pair.source.bottom
            )
            if source_end <= separator_source_start + 3:
                before.append(pair)
            elif source_start >= separator_source_end - 3:
                after.append(pair)
        if not before or not after:
            continue
        runtime_tolerance = max(
            4.0,
            (
                runtime_form.width
                if orientation == "vertical"
                else runtime_form.height
            )
            * 0.015,
        )
        for pair in before:
            center = (
                pair.runtime.center_x
                if orientation == "vertical"
                else pair.runtime.center_y
            )
            if center >= separator_runtime_center + runtime_tolerance:
                violations.append(
                    f"{pair.name!r} crossed {separator.name!r}"
                )
        for pair in after:
            center = (
                pair.runtime.center_x
                if orientation == "vertical"
                else pair.runtime.center_y
            )
            if center <= separator_runtime_center - runtime_tolerance:
                violations.append(
                    f"{pair.name!r} crossed {separator.name!r}"
                )
    if not violations:
        return []
    return [
        diagnostic(
            "qt.separator-region-violation",
            "error",
            (
                f"{len(violations)} separator invariant violation(s): "
                + "; ".join(violations[:8])
                + f" at {_runtime_size(runtime_form)}"
            ),
            path,
        )
    ]


def _anchor_clusters(
    pairs: list[_GeometryPair],
    *,
    axis: str,
    kind: str,
    tolerance: float,
) -> list[list[_GeometryPair]]:
    entries = sorted(
        (
            (_anchor_value(pair.source, axis=axis, kind=kind), pair)
            for pair in pairs
        ),
        key=lambda item: (item[0], item[1].name),
    )
    clusters: list[list[_GeometryPair]] = []
    values: list[list[float]] = []
    for value, pair in entries:
        if not values or value - median(values[-1]) > tolerance:
            values.append([value])
            clusters.append([pair])
        else:
            values[-1].append(value)
            clusters[-1].append(pair)
    return clusters


def _anchor_value(rect: _Rect, *, axis: str, kind: str) -> float:
    start, end = (
        (rect.left, rect.right)
        if axis == "horizontal"
        else (rect.top, rect.bottom)
    )
    if kind == "start":
        return start
    if kind == "end":
        return end
    return (start + end) / 2


def _baseline_snapshot(
    snapshots: list[dict[str, object]],
) -> dict[str, object]:
    return next(
        (snapshot for snapshot in snapshots if snapshot.get("baseline")),
        sorted(snapshots, key=_snapshot_area)[len(snapshots) // 2],
    )


def _runtime_form(snapshot: dict[str, object]) -> _Rect | None:
    size = snapshot.get("form_size")
    if not _is_number_sequence(size, 2):
        return None
    return _Rect(0, 0, float(size[0]), float(size[1]))


def _rect(value: object) -> _Rect | None:
    if not _is_number_sequence(value, 4):
        return None
    return _Rect(*(float(item) for item in value[:4]))


def _anchor_reference(value: object) -> tuple[str, int] | None:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or value[0] not in {"start", "center", "end"}
        or not isinstance(value[1], int)
    ):
        return None
    return value[0], value[1]


def _alternative_states(value: object) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        (state[0], state[1])
        for state in value
        if (
            isinstance(state, (list, tuple))
            and len(state) == 2
            and all(isinstance(item, int) for item in state)
        )
    )


def _snapshot_area(snapshot: dict[str, object]) -> float:
    form = _runtime_form(snapshot)
    return form.width * form.height if form is not None else 0


def _runtime_size(form: _Rect) -> str:
    return f"{round(form.width)}x{round(form.height)}"


def _overlap(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
) -> float:
    return max(0.0, min(left_end, right_end) - max(left_start, right_start))


def _is_number_sequence(value: object, length: int) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) >= length
        and all(isinstance(item, (int, float)) for item in value[:length])
    )


def _deduplicate_diagnostics(
    diagnostics: list[dict[str, str]],
) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, str]] = []
    for item in diagnostics:
        key = (item["code"], item["severity"], item["message"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result

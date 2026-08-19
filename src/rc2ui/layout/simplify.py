from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace

from rc2ui.qt.model import (
    QtEnum,
    QtLayout,
    QtLayoutItem,
    QtProperty,
    QtSizePolicy,
    QtSpacer,
    QtWidget,
)


@dataclass(frozen=True, slots=True)
class SimplificationResult:
    root_widget: QtWidget
    editability_score: float
    simplified_regions: int
    faithful_fallback_regions: int
    transformations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Rect:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def horizontal_center(self) -> float:
        return (self.left + self.right) / 2

    @property
    def vertical_center(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass(frozen=True, slots=True)
class _Entry:
    key: str
    item: QtLayoutItem
    rect: _Rect


@dataclass(frozen=True, slots=True)
class _Candidate:
    layout: QtLayout
    placements: dict[str, _Rect]
    transformation: str
    faithful_fallback: bool = False
    source_margins_preserved: bool = False


class _ObjectNameAllocator:
    def __init__(self, used: Iterable[str]) -> None:
        self._used = set(used)

    def next(self, base: str) -> str:
        candidate = base
        suffix = 2
        while candidate in self._used:
            candidate = f"{base}{suffix}"
            suffix += 1
        self._used.add(candidate)
        return candidate


class _Simplifier:
    def __init__(self, used_names: Iterable[str]) -> None:
        self.simplified_regions = 0
        self.faithful_fallback_regions = 0
        self.transformations: Counter[str] = Counter()
        self.names = _ObjectNameAllocator(used_names)

    def widget(self, widget: QtWidget) -> QtWidget:
        children = tuple(self.widget(child) for child in widget.children)
        layout = (
            self.layout(widget.layout)
            if widget.layout is not None
            else None
        )
        return _designer_responsive_widget(
            replace(widget, children=children, layout=layout)
        )

    def layout(self, layout: QtLayout) -> QtLayout:
        items = tuple(self._nested_item(item) for item in layout.items)
        faithful = replace(layout, items=items)
        if layout.class_name != "QGridLayout":
            return faithful

        entries = _grid_entries(faithful)
        if not entries:
            return faithful
        reference = {entry.key: entry.rect for entry in entries}
        semantic_candidates = (
            (
                _separator_panels_candidate(
                    faithful,
                    entries,
                    names=self.names,
                )
                if len(entries) >= 3
                else None
            ),
            _form_candidate(faithful, entries) if len(entries) >= 2 else None,
            _form_grid_candidate(faithful, entries) if len(entries) >= 2 else None,
            (
                _axis_candidate(
                    faithful,
                    entries,
                    horizontal=True,
                    names=self.names,
                )
                if len(entries) >= 2
                else None
            ),
            (
                _axis_candidate(
                    faithful,
                    entries,
                    horizontal=False,
                    names=self.names,
                )
                if len(entries) >= 2
                else None
            ),
            (
                _compact_grid_candidate(faithful, entries)
                if len(entries) >= 2
                else None
            ),
            (
                _vertical_bands_candidate(
                    faithful,
                    entries,
                    names=self.names,
                )
                if len(entries) >= 3
                else None
            ),
        )
        candidates = tuple(
            (
                candidate
                if candidate is None or candidate.source_margins_preserved
                else _wrap_in_source_margins(
                    faithful,
                    entries,
                    candidate,
                    names=self.names,
                )
            )
            for candidate in semantic_candidates
        ) + (_clean_faithful_grid(faithful, entries),)
        faithful_cost = _layout_cost(faithful)
        for candidate in candidates:
            if candidate is None:
                continue
            if not _preserves_topology(reference, candidate.placements):
                continue
            if _layout_cost(candidate.layout) >= faithful_cost:
                continue
            if (
                candidate.transformation == "grid-to-separator-panels"
                and any(
                    alternative is not None
                    and alternative is not candidate
                    and _preserves_topology(
                        reference,
                        alternative.placements,
                    )
                    and _layout_cost(alternative.layout) < faithful_cost
                    and _designer_friction(alternative.layout)
                    < _designer_friction(candidate.layout)
                    for alternative in candidates
                )
            ):
                continue
            self.simplified_regions += 1
            if candidate.faithful_fallback:
                self.faithful_fallback_regions += 1
            self.transformations[candidate.transformation] += 1
            return candidate.layout

        self.faithful_fallback_regions += 1
        return faithful

    def _nested_item(self, item: QtLayoutItem) -> QtLayoutItem:
        return replace(
            item,
            widget=self.widget(item.widget) if item.widget is not None else None,
            layout=self.layout(item.layout) if item.layout is not None else None,
        )


def _separator_panels_candidate(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    *,
    names: _ObjectNameAllocator,
) -> _Candidate | None:
    """Turn long RC separator lines into explicit nested panel boundaries.

    A separator is much stronger structural evidence than coincident control
    edges.  Keeping it in the global coordinate mesh makes unrelated left,
    right, top, and bottom panels share dozens of tiny tracks.  This transform
    only fires when the line substantially spans a region and ordinary widgets
    can be assigned to both sides without crossing it.
    """

    # A horizontal separator already agrees with the normal vertical-band
    # decomposition, which is both simpler and better at preserving dense
    # stacked forms.  This candidate is needed when a vertical boundary makes
    # those global bands semantically wrong; horizontal lines are then still
    # used recursively to subdivide either pane.
    if not any(_separator_orientation(entry) == "vertical" for entry in entries):
        return None

    row_count, column_count = _grid_shape(source)
    row_weights = _weights(
        source.minimum_heights or source.row_stretch,
        row_count,
    )
    column_weights = _weights(
        source.minimum_widths or source.stretch,
        column_count,
    )
    bounds = _Rect(0, 0, sum(column_weights), sum(row_weights))
    built = _separator_region_layout(
        source,
        entries,
        bounds,
        names=names,
        depth=0,
    )
    if built is None:
        return None
    layout, split_count = built
    if split_count == 0:
        return None
    return _Candidate(
        layout,
        {entry.key: entry.rect for entry in entries},
        "grid-to-separator-panels",
        source_margins_preserved=True,
    )


def _separator_region_layout(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    bounds: _Rect,
    *,
    names: _ObjectNameAllocator,
    depth: int,
) -> tuple[QtLayout, int] | None:
    split = _best_separator_split(entries, bounds)
    if split is None:
        if depth == 0:
            return None
        return (
            _terminal_region_layout(
                source,
                entries,
                bounds,
                names=names,
            ),
            0,
        )

    separator, orientation, before, after = split
    if orientation == "horizontal":
        before_bounds = _Rect(
            bounds.left,
            bounds.top,
            bounds.right,
            max(bounds.top, separator.rect.top),
        )
        after_bounds = _Rect(
            bounds.left,
            min(bounds.bottom, separator.rect.bottom),
            bounds.right,
            bounds.bottom,
        )
        class_name = "QVBoxLayout"
        before_name = "TopPanel"
        after_name = "BottomPanel"
        stretch = (
            max(1, round(before_bounds.height)),
            max(1, round(separator.rect.height)),
            max(1, round(after_bounds.height)),
        )
    else:
        before_bounds = _Rect(
            bounds.left,
            bounds.top,
            max(bounds.left, separator.rect.left),
            bounds.bottom,
        )
        after_bounds = _Rect(
            min(bounds.right, separator.rect.right),
            bounds.top,
            bounds.right,
            bounds.bottom,
        )
        class_name = "QHBoxLayout"
        before_name = "LeftPanel"
        after_name = "RightPanel"
        stretch = (
            max(1, round(before_bounds.width)),
            max(1, round(separator.rect.width)),
            max(1, round(after_bounds.width)),
        )

    if (
        orientation == "vertical"
        and _best_separator_split(before, before_bounds) is None
        and _best_separator_split(after, after_bounds) is None
    ):
        return (
            _shared_vertical_panel_grid(
                source,
                separator,
                before,
                after,
                bounds,
                before_bounds,
                after_bounds,
                names=names,
                depth=depth,
            ),
            1,
        )

    before_result = _separator_region_layout(
        source,
        before,
        before_bounds,
        names=names,
        depth=depth + 1,
    )
    after_result = _separator_region_layout(
        source,
        after,
        after_bounds,
        names=names,
        depth=depth + 1,
    )
    assert before_result is not None
    assert after_result is not None
    before_layout, before_splits = before_result
    after_layout, after_splits = after_result
    before_layout = replace(
        before_layout,
        object_name=names.next(f"{source.object_name}{before_name}"),
    )
    after_layout = replace(
        after_layout,
        object_name=names.next(f"{source.object_name}{after_name}"),
    )
    separator_item = replace(
        separator.item,
        row=None,
        column=None,
        row_span=1,
        column_span=1,
    )
    return (
        QtLayout(
            class_name,
            source.object_name if depth == 0 else names.next(
                f"{source.object_name}SeparatorRegion"
            ),
            (
                QtLayoutItem(layout=before_layout),
                separator_item,
                QtLayoutItem(layout=after_layout),
            ),
            properties=_portable_properties(source, zero_spacing=True),
            stretch=stretch,
        ),
        1 + before_splits + after_splits,
    )


def _shared_vertical_panel_grid(
    source: QtLayout,
    separator: _Entry,
    left_entries: tuple[_Entry, ...],
    right_entries: tuple[_Entry, ...],
    bounds: _Rect,
    left_bounds: _Rect,
    right_bounds: _Rect,
    *,
    names: _ObjectNameAllocator,
    depth: int,
) -> QtLayout:
    """Keep both sides on common coarse rows while isolating their columns."""

    entry_side = {
        entry.key: 0 for entry in left_entries
    } | {
        entry.key: 2 for entry in right_entries
    }
    bands = _vertical_overlap_bands(left_entries + right_entries)
    items: list[QtLayoutItem] = []
    row_weights: list[int] = []
    previous_bottom = bounds.top
    for band_index, band in enumerate(bands):
        band_top = min(entry.rect.top for entry in band)
        band_bottom = max(entry.rect.bottom for entry in band)
        gap = band_top - previous_bottom
        if gap > 0:
            row_weights.append(max(1, round(gap)))
        band_row = len(row_weights)
        row_weights.append(max(1, round(band_bottom - band_top)))
        for column, side_bounds in ((0, left_bounds), (2, right_bounds)):
            side_entries = tuple(
                entry for entry in band if entry_side[entry.key] == column
            )
            if not side_entries:
                continue
            band_bounds = _Rect(
                side_bounds.left,
                band_top,
                side_bounds.right,
                band_bottom,
            )
            side_layout = _terminal_region_layout(
                source,
                side_entries,
                band_bounds,
                names=names,
            )
            side_name = "Left" if column == 0 else "Right"
            side_layout = replace(
                side_layout,
                object_name=names.next(
                    f"{source.object_name}{side_name}Band{band_index + 1}"
                ),
            )
            items.append(
                QtLayoutItem(
                    layout=side_layout,
                    row=band_row,
                    column=column,
                )
            )
        previous_bottom = band_bottom
    trailing_gap = bounds.bottom - previous_bottom
    if trailing_gap > 0:
        row_weights.append(max(1, round(trailing_gap)))
    if not row_weights:
        row_weights.append(max(1, round(bounds.height)))
    items.append(
        replace(
            separator.item,
            row=0,
            column=1,
            row_span=len(row_weights),
            column_span=1,
        )
    )
    column_weights = (
        max(1, round(left_bounds.width)),
        max(1, round(separator.rect.width)),
        max(1, round(right_bounds.width)),
    )
    return QtLayout(
        "QGridLayout",
        source.object_name if depth == 0 else names.next(
            f"{source.object_name}VerticalPanels"
        ),
        tuple(items),
        properties=_portable_properties(source, zero_spacing=True),
        stretch=column_weights,
        row_stretch=tuple(row_weights),
        minimum_widths=column_weights,
        minimum_heights=tuple(row_weights),
    )


def _best_separator_split(
    entries: tuple[_Entry, ...],
    bounds: _Rect,
) -> tuple[
    _Entry,
    str,
    tuple[_Entry, ...],
    tuple[_Entry, ...],
] | None:
    candidates: list[
        tuple[
            float,
            float,
            _Entry,
            str,
            tuple[_Entry, ...],
            tuple[_Entry, ...],
        ]
    ] = []
    for separator in entries:
        orientation = _separator_orientation(separator)
        if orientation is None:
            continue
        long_extent = (
            separator.rect.width
            if orientation == "horizontal"
            else separator.rect.height
        )
        region_extent = (
            bounds.width if orientation == "horizontal" else bounds.height
        )
        coverage = long_extent / max(1.0, region_extent)
        if coverage < 0.65:
            continue
        partition = _partition_around_separator(
            entries,
            separator,
            orientation,
        )
        if partition is None:
            continue
        before, after = partition
        # Prefer the strongest visual boundary.  For equal coverage, a split
        # with meaningful content on both sides is more useful.
        balance = min(len(before), len(after)) / max(len(before), len(after))
        candidates.append(
            (
                coverage,
                balance,
                separator,
                orientation,
                before,
                after,
            )
        )
    if not candidates:
        return None
    _, _, separator, orientation, before, after = max(
        candidates,
        # Horizontal boundaries establish common top/bottom regions first.
        # A vertical split inside either region can then retain shared rows
        # across its two side panels.
        key=lambda item: (
            item[3] == "horizontal",
            item[0],
            item[1],
            item[2].key,
        ),
    )
    return separator, orientation, before, after


def _partition_around_separator(
    entries: tuple[_Entry, ...],
    separator: _Entry,
    orientation: str,
) -> tuple[tuple[_Entry, ...], tuple[_Entry, ...]] | None:
    before: list[_Entry] = []
    after: list[_Entry] = []
    split_start = (
        separator.rect.top
        if orientation == "horizontal"
        else separator.rect.left
    )
    split_end = (
        separator.rect.bottom
        if orientation == "horizontal"
        else separator.rect.right
    )
    for entry in entries:
        if entry is separator:
            continue
        start = entry.rect.top if orientation == "horizontal" else entry.rect.left
        end = entry.rect.bottom if orientation == "horizontal" else entry.rect.right
        if end <= split_start + 1:
            before.append(entry)
            continue
        if start >= split_end - 1:
            after.append(entry)
            continue
        if _separator_orientation(entry) is not None:
            center = (
                entry.rect.vertical_center
                if orientation == "horizontal"
                else entry.rect.horizontal_center
            )
            split_center = (split_start + split_end) / 2
            (before if center < split_center else after).append(entry)
            continue
        # Small hand-authored overshoots are harmless because the native
        # parent clips them.  A real widget crossing the boundary is evidence
        # that the line is decorative rather than a panel separator.
        overlap = min(end, split_end) - max(start, split_start)
        if overlap <= 2:
            center = (
                entry.rect.vertical_center
                if orientation == "horizontal"
                else entry.rect.horizontal_center
            )
            split_center = (split_start + split_end) / 2
            (before if center < split_center else after).append(entry)
            continue
        return None
    if not before or not after:
        return None
    return tuple(before), tuple(after)


def _separator_orientation(entry: _Entry) -> str | None:
    widget = entry.item.widget
    if widget is None or widget.class_name != "QFrame":
        return None
    shape = next(
        (
            property_.value.value
            for property_ in widget.properties
            if property_.name == "frameShape"
            and isinstance(property_.value, QtEnum)
        ),
        None,
    )
    if shape == "QFrame::HLine":
        return "horizontal"
    if shape == "QFrame::VLine":
        return "vertical"
    return None


def _terminal_region_layout(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    bounds: _Rect,
    *,
    names: _ObjectNameAllocator,
) -> QtLayout:
    """Use only strong local patterns inside a separator-defined panel."""

    faithful = _coordinate_region_layout(
        source,
        entries,
        bounds,
        names=names,
    )
    if len(entries) < 2:
        return faithful
    reference = {entry.key: entry.rect for entry in entries}
    # Horizontal rows (most often a bottom button strip) are independent of
    # the vertical scale shared by adjacent panels.  Do not turn a whole side
    # pane into a private VBox/FormLayout: its font-driven row heights could
    # then drift relative to the other side of the separator.
    candidates = (
        _axis_candidate(source, entries, horizontal=True, names=names),
    )
    faithful_cost = _layout_cost(faithful)
    faithful_friction = _designer_friction(faithful)
    for candidate in candidates:
        if candidate is None or not _preserves_topology(
            reference,
            candidate.placements,
        ):
            continue
        wrapped = _wrap_in_region_margins(
            source,
            entries,
            candidate,
            bounds,
            names=names,
        )
        if (
            _layout_cost(wrapped.layout) < faithful_cost
            or _designer_friction(wrapped.layout) < faithful_friction
        ):
            return wrapped.layout
    return faithful


def _wrap_in_region_margins(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    candidate: _Candidate,
    bounds: _Rect,
    *,
    names: _ObjectNameAllocator,
) -> _Candidate:
    content_left = min(entry.rect.left for entry in entries)
    content_top = min(entry.rect.top for entry in entries)
    content_right = max(entry.rect.right for entry in entries)
    content_bottom = max(entry.rect.bottom for entry in entries)
    horizontal = _three_zone_weights(
        content_left - bounds.left,
        content_right - bounds.left,
        bounds.width,
    )
    vertical = _three_zone_weights(
        content_top - bounds.top,
        content_bottom - bounds.top,
        bounds.height,
    )
    inner = replace(
        candidate.layout,
        object_name=names.next(f"{source.object_name}PanelContent"),
    )
    wrapper = QtLayout(
        "QGridLayout",
        names.next(f"{source.object_name}Panel"),
        (
            QtLayoutItem(layout=inner, row=1, column=1),
            QtLayoutItem(
                spacer=QtSpacer(
                    names.next(f"{source.object_name}PanelExtentMarker"),
                    "horizontal",
                    size_type="Minimum",
                    size_hint=0,
                ),
                row=2,
                column=2,
            ),
        ),
        properties=_zero_spacing_properties(source.properties),
        stretch=horizontal,
        row_stretch=vertical,
        minimum_widths=horizontal,
        minimum_heights=vertical,
    )
    return replace(candidate, layout=wrapper)


def _coordinate_region_layout(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    bounds: _Rect,
    *,
    names: _ObjectNameAllocator,
) -> QtLayout:
    horizontal_edges = {bounds.left, bounds.right}
    vertical_edges = {bounds.top, bounds.bottom}
    clipped: dict[str, _Rect] = {}
    for entry in entries:
        rect = _Rect(
            max(bounds.left, min(bounds.right, entry.rect.left)),
            max(bounds.top, min(bounds.bottom, entry.rect.top)),
            max(bounds.left, min(bounds.right, entry.rect.right)),
            max(bounds.top, min(bounds.bottom, entry.rect.bottom)),
        )
        if rect.width <= 0 or rect.height <= 0:
            # This should only be reachable for a malformed line at the exact
            # region boundary.  Keeping a one-unit track preserves the item.
            rect = _Rect(
                rect.left,
                rect.top,
                max(rect.left + 1, rect.right),
                max(rect.top + 1, rect.bottom),
            )
        clipped[entry.key] = rect
        horizontal_edges.update((rect.left, rect.right))
        vertical_edges.update((rect.top, rect.bottom))
    columns = sorted(horizontal_edges)
    rows = sorted(vertical_edges)
    column_for = {edge: index for index, edge in enumerate(columns)}
    row_for = {edge: index for index, edge in enumerate(rows)}
    column_weights = tuple(
        max(1, round(right - left))
        for left, right in zip(columns, columns[1:])
    )
    row_weights = tuple(
        max(1, round(bottom - top))
        for top, bottom in zip(rows, rows[1:])
    )
    items = [
        replace(
            entry.item,
            row=row_for[clipped[entry.key].top],
            column=column_for[clipped[entry.key].left],
            row_span=(
                row_for[clipped[entry.key].bottom]
                - row_for[clipped[entry.key].top]
            ),
            column_span=(
                column_for[clipped[entry.key].right]
                - column_for[clipped[entry.key].left]
            ),
        )
        for entry in entries
    ]
    content_right = max(rect.right for rect in clipped.values())
    content_bottom = max(rect.bottom for rect in clipped.values())
    if content_right < bounds.right or content_bottom < bounds.bottom:
        # QGridLayout does not distribute surplus proportionally into a wholly
        # empty trailing track on every Qt style.  A zero-sized marker makes
        # that source margin a real participant without covering the canvas.
        items.append(
            QtLayoutItem(
                spacer=QtSpacer(
                    names.next(f"{source.object_name}PanelExtentMarker"),
                    "horizontal",
                    size_type="Minimum",
                    size_hint=0,
                ),
                row=len(row_weights) - 1,
                column=len(column_weights) - 1,
            )
        )
    return QtLayout(
        "QGridLayout",
        names.next(f"{source.object_name}PanelGrid"),
        tuple(items),
        properties=_portable_properties(source, zero_spacing=True),
        stretch=column_weights,
        row_stretch=row_weights,
        minimum_widths=column_weights,
        minimum_heights=row_weights,
    )


def simplify_form(root_widget: QtWidget) -> SimplificationResult:
    """Create a Designer-oriented form without mutating faithful planning."""

    simplifier = _Simplifier(_object_names(root_widget))
    simplified = _retain_root_width_ruler(
        root_widget,
        simplifier.widget(root_widget),
        names=simplifier.names,
    )
    return SimplificationResult(
        root_widget=simplified,
        editability_score=_editability_score(simplified),
        simplified_regions=simplifier.simplified_regions,
        faithful_fallback_regions=simplifier.faithful_fallback_regions,
        transformations=tuple(
            f"{name}:{count}"
            for name, count in sorted(simplifier.transformations.items())
        ),
    )


def _retain_root_width_ruler(
    faithful: QtWidget,
    simplified: QtWidget,
    *,
    names: _ObjectNameAllocator,
) -> QtWidget:
    """Keep a zero-height, font-relative width floor in simplified forms.

    Semantic layouts naturally grow vertically when their font changes.  A
    top-level dialog does not, however, reliably grow to its new horizontal
    sizeHint on every Qt style.  The faithful width ruler is therefore kept as
    a zero-height item.  It cannot cover controls on the Designer canvas, but
    it makes the dialog minimum width follow FontChange without runtime code.
    """

    if faithful.layout is None or simplified.layout is None:
        return simplified
    ruler = next(
        (
            item
            for item in faithful.layout.items
            if item.widget is not None
            and item.widget.object_name.startswith("rc2uiFontWidthRuler")
        ),
        None,
    )
    if ruler is None:
        return simplified
    ruler = replace(
        ruler,
        row=None,
        column=None,
        row_span=1,
        column_span=1,
    )
    layout = simplified.layout
    if layout.class_name == "QVBoxLayout":
        return replace(
            simplified,
            layout=replace(layout, items=layout.items + (ruler,)),
        )
    if layout.class_name == "QGridLayout":
        rows, columns = _grid_shape(layout)
        return replace(
            simplified,
            layout=replace(
                layout,
                items=layout.items
                + (
                    replace(
                        ruler,
                        row=0,
                        column=0,
                        row_span=rows,
                        column_span=columns,
                    ),
                ),
            ),
        )
    wrapper = QtLayout(
        "QVBoxLayout",
        names.next(f"{layout.object_name}SimplifiedRoot"),
        (QtLayoutItem(layout=layout), ruler),
        properties=_portable_properties(layout, zero_spacing=True),
    )
    return replace(simplified, layout=wrapper)


def _designer_responsive_widget(widget: QtWidget) -> QtWidget:
    """Let text controls publish font-dependent width in simplified forms."""

    if widget.class_name not in {
        "QCheckBox",
        "QGroupBox",
        "QPushButton",
        "QRadioButton",
        "QToolButton",
    }:
        return widget
    properties: list[QtProperty] = []
    changed = False
    for property_ in widget.properties:
        if property_.name != "sizePolicy" or not isinstance(
            property_.value,
            QtSizePolicy,
        ):
            properties.append(property_)
            continue
        if property_.value.horizontal == "Minimum":
            properties.append(property_)
            continue
        properties.append(
            replace(
                property_,
                value=replace(property_.value, horizontal="Minimum"),
            )
        )
        changed = True
    return replace(widget, properties=tuple(properties)) if changed else widget


def editability_score(root_widget: QtWidget) -> float:
    return _editability_score(root_widget)


def _grid_entries(layout: QtLayout) -> tuple[_Entry, ...]:
    row_count, column_count = _grid_shape(layout)
    row_weights = _weights(layout.minimum_heights or layout.row_stretch, row_count)
    column_weights = _weights(
        layout.minimum_widths or layout.stretch,
        column_count,
    )
    row_offsets = _offsets(row_weights)
    column_offsets = _offsets(column_weights)
    entries: list[_Entry] = []
    for item in layout.items:
        if _is_technical_item(item):
            continue
        key = _item_key(item)
        if key is None:
            continue
        row = item.row or 0
        column = item.column or 0
        entries.append(
            _Entry(
                key,
                item,
                _Rect(
                    column_offsets[column],
                    row_offsets[row],
                    column_offsets[min(column_count, column + item.column_span)],
                    row_offsets[min(row_count, row + item.row_span)],
                ),
            )
        )
    return tuple(entries)


def _form_candidate(
    source: QtLayout,
    entries: tuple[_Entry, ...],
) -> _Candidate | None:
    rows = _form_rows(entries)
    if rows is None:
        return None
    if any(right.rect.left > left.rect.right for left, right in rows):
        # A QFormLayout has fixed inter-column spacing. Positive RC gaps need
        # an explicit expanding track and are handled by _form_grid_candidate.
        return None

    items: list[QtLayoutItem] = []
    placements: dict[str, _Rect] = {}
    for row_index, row in enumerate(rows):
        left, right = row
        items.extend(
            (
                _positioned_item(left.item, row=row_index, column=0),
                _positioned_item(right.item, row=row_index, column=1),
            )
        )
        placements[left.key] = _Rect(0, row_index, 1, row_index + 1)
        placements[right.key] = _Rect(1, row_index, 2, row_index + 1)

    properties = _portable_properties(source) + (
        QtProperty(
            "fieldGrowthPolicy",
            QtEnum("QFormLayout::AllNonFixedFieldsGrow"),
        ),
    )
    return _Candidate(
        QtLayout(
            "QFormLayout",
            source.object_name,
            tuple(items),
            properties=properties,
        ),
        placements,
        "grid-to-form",
    )


def _form_grid_candidate(
    source: QtLayout,
    entries: tuple[_Entry, ...],
) -> _Candidate | None:
    rows = _form_rows(entries)
    if rows is None:
        return None
    gaps = tuple(
        max(0, round(right.rect.left - left.rect.right))
        for left, right in rows
    )
    if not any(gaps):
        return None

    items: list[QtLayoutItem] = []
    placements: dict[str, _Rect] = {}
    row_stretch: list[int] = []
    for row_index, (left, right) in enumerate(rows):
        logical_row = row_index * 2
        items.extend(
            (
                _positioned_item(left.item, row=logical_row, column=0),
                _positioned_item(right.item, row=logical_row, column=2),
            )
        )
        placements[left.key] = _Rect(0, logical_row, 1, logical_row + 1)
        placements[right.key] = _Rect(2, logical_row, 3, logical_row + 1)
        row_height = max(left.rect.height, right.rect.height)
        row_stretch.append(max(1, round(row_height)))
        if row_index + 1 < len(rows):
            next_top = min(entry.rect.top for entry in rows[row_index + 1])
            current_bottom = max(left.rect.bottom, right.rect.bottom)
            row_stretch.append(max(1, round(next_top - current_bottom)))

    label_width = max(left.rect.width for left, _ in rows)
    field_width = max(right.rect.width for _, right in rows)
    column_stretch = (
        max(1, round(label_width)),
        max(1, round(sum(gaps) / len(gaps))),
        max(1, round(field_width)),
    )
    return _Candidate(
        QtLayout(
            "QGridLayout",
            source.object_name,
            tuple(items),
            properties=_portable_properties(source, zero_spacing=True),
            stretch=column_stretch,
            row_stretch=tuple(row_stretch),
            minimum_widths=column_stretch,
            minimum_heights=tuple(row_stretch),
        ),
        placements,
        "grid-to-form-grid",
    )


def _form_rows(
    entries: tuple[_Entry, ...],
) -> tuple[tuple[_Entry, _Entry], ...] | None:
    if len(entries) < 4 or any(entry.item.widget is None for entry in entries):
        return None
    grouped = _overlap_groups(entries, horizontal=False)
    if len(grouped) < 2 or any(len(row) != 2 for row in grouped):
        return None
    rows: list[tuple[_Entry, _Entry]] = []
    for row in grouped:
        left, right = sorted(row, key=lambda entry: entry.rect.left)
        assert left.item.widget is not None
        assert right.item.widget is not None
        if left.item.widget.class_name != "QLabel":
            return None
        if right.item.widget.class_name in {
            "QLabel",
            "QGroupBox",
            "QFrame",
            "QPushButton",
            "QToolButton",
        }:
            return None
        rows.append((left, right))
    return tuple(rows)


def _axis_candidate(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    *,
    horizontal: bool,
    names: _ObjectNameAllocator,
) -> _Candidate | None:
    cross_groups = _overlap_groups(entries, horizontal=not horizontal)
    if len(cross_groups) != 1:
        return None
    ordered = sorted(
        entries,
        key=(
            (lambda entry: entry.rect.left)
            if horizontal
            else (lambda entry: entry.rect.top)
        ),
    )
    for left, right in zip(ordered, ordered[1:]):
        left_end = left.rect.right if horizontal else left.rect.bottom
        right_start = right.rect.left if horizontal else right.rect.top
        if left_end > right_start:
            return None

    orientation = "horizontal" if horizontal else "vertical"
    items: list[QtLayoutItem] = []
    stretch: list[int] = []
    for index, entry in enumerate(ordered):
        items.append(
            replace(
                entry.item,
                row=None,
                column=None,
                row_span=1,
                column_span=1,
            )
        )
        stretch.append(
            max(
                1,
                round(entry.rect.width if horizontal else entry.rect.height),
            )
        )
        if index + 1 == len(ordered):
            continue
        next_entry = ordered[index + 1]
        gap = (
            next_entry.rect.left - entry.rect.right
            if horizontal
            else next_entry.rect.top - entry.rect.bottom
        )
        if gap <= 0:
            continue
        items.append(
            QtLayoutItem(
                spacer=QtSpacer(
                    names.next(
                        f"{source.object_name}"
                        f"{orientation.title()}Gap{index + 1}"
                    ),
                    orientation,
                    size_type="Minimum",
                    size_hint=max(1, round(gap)),
                )
            )
        )
        stretch.append(max(1, round(gap)))
    placements = {
        entry.key: (
            _Rect(index, 0, index + 1, 1)
            if horizontal
            else _Rect(0, index, 1, index + 1)
        )
        for index, entry in enumerate(ordered)
    }
    class_name = "QHBoxLayout" if horizontal else "QVBoxLayout"
    return _Candidate(
        QtLayout(
            class_name,
            source.object_name,
            tuple(items),
            properties=_portable_properties(source, zero_spacing=True),
            stretch=tuple(stretch),
        ),
        placements,
        "grid-to-hbox" if horizontal else "grid-to-vbox",
    )


def _vertical_bands_candidate(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    *,
    names: _ObjectNameAllocator,
) -> _Candidate | None:
    """Replace a fine global grid with editable horizontal row layouts."""

    bands = _vertical_overlap_bands(entries)
    if len(bands) < 2 or max(map(len, bands)) < 2:
        return None

    row_count, _ = _grid_shape(source)
    row_weights = _weights(
        source.minimum_heights or source.row_stretch,
        row_count,
    )
    total_height = float(sum(row_weights))
    items: list[QtLayoutItem] = []
    stretch: list[int] = []
    previous_bottom = 0.0
    placements: dict[str, _Rect] = {}
    for band_index, band in enumerate(bands):
        band_top = min(entry.rect.top for entry in band)
        band_bottom = max(entry.rect.bottom for entry in band)
        gap = band_top - previous_bottom
        if gap > 0:
            items.append(
                QtLayoutItem(
                    spacer=QtSpacer(
                        names.next(
                            f"{source.object_name}VerticalGap{band_index + 1}"
                        ),
                        "vertical",
                        size_type="Minimum",
                        size_hint=max(1, round(gap)),
                    )
                )
            )
            stretch.append(max(1, round(gap)))

        row_candidate = _horizontal_band_candidate(
            source,
            band,
            names=names,
        )
        if row_candidate is None:
            return None
        items.append(QtLayoutItem(layout=row_candidate.layout))
        stretch.append(max(1, round(band_bottom - band_top)))
        for entry in band:
            placements[entry.key] = entry.rect
        previous_bottom = band_bottom

    trailing_gap = total_height - previous_bottom
    if trailing_gap > 0:
        items.append(
            QtLayoutItem(
                spacer=QtSpacer(
                    names.next(f"{source.object_name}VerticalGapEnd"),
                    "vertical",
                    size_type="Minimum",
                    size_hint=max(1, round(trailing_gap)),
                )
            )
        )
        stretch.append(max(1, round(trailing_gap)))

    return _Candidate(
        QtLayout(
            "QVBoxLayout",
            source.object_name,
            tuple(items),
            properties=_portable_properties(source, zero_spacing=True),
            stretch=tuple(stretch),
        ),
        placements,
        "grid-to-vertical-bands",
        source_margins_preserved=True,
    )


def _vertical_overlap_bands(
    entries: tuple[_Entry, ...],
) -> tuple[tuple[_Entry, ...], ...]:
    ordered = sorted(
        entries,
        key=lambda entry: (entry.rect.top, entry.rect.bottom, entry.key),
    )
    bands: list[list[_Entry]] = []
    current_bottom = float("-inf")
    for entry in ordered:
        if not bands or entry.rect.top >= current_bottom:
            bands.append([entry])
            current_bottom = entry.rect.bottom
            continue
        bands[-1].append(entry)
        current_bottom = max(current_bottom, entry.rect.bottom)
    return tuple(
        tuple(
            sorted(
                band,
                key=lambda entry: (entry.rect.left, entry.rect.right, entry.key),
            )
        )
        for band in bands
    )


def _horizontal_band_candidate(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    *,
    names: _ObjectNameAllocator,
) -> _Candidate | None:
    if len(entries) == 1:
        [entry] = entries
        candidate = _Candidate(
            QtLayout(
                "QHBoxLayout",
                source.object_name,
                (
                    replace(
                        entry.item,
                        row=None,
                        column=None,
                        row_span=1,
                        column_span=1,
                    ),
                ),
                properties=_portable_properties(source, zero_spacing=True),
                stretch=(max(1, round(entry.rect.width)),),
            ),
            {entry.key: _Rect(0, 0, 1, 1)},
            "grid-band-single",
        )
    else:
        candidate = _axis_candidate(
            source,
            entries,
            horizontal=True,
            names=names,
        )
        if candidate is None:
            candidate = _compact_grid_candidate(source, entries)
        if candidate is None or not _preserves_topology(
            {entry.key: entry.rect for entry in entries},
            candidate.placements,
        ):
            candidate = _coordinate_subgrid_candidate(source, entries)
        if candidate is None or not _preserves_topology(
            {entry.key: entry.rect for entry in entries},
            candidate.placements,
        ):
            return None
    return _wrap_in_horizontal_source_margins(
        source,
        entries,
        candidate,
        names=names,
    )


def _coordinate_subgrid_candidate(
    source: QtLayout,
    entries: tuple[_Entry, ...],
) -> _Candidate:
    """Keep exact topology inside one complex band, not the whole form."""

    horizontal_edges = sorted(
        {edge for entry in entries for edge in (entry.rect.left, entry.rect.right)}
    )
    vertical_edges = sorted(
        {edge for entry in entries for edge in (entry.rect.top, entry.rect.bottom)}
    )
    column_for = {edge: index for index, edge in enumerate(horizontal_edges)}
    row_for = {edge: index for index, edge in enumerate(vertical_edges)}
    items = tuple(
        replace(
            entry.item,
            row=row_for[entry.rect.top],
            column=column_for[entry.rect.left],
            row_span=row_for[entry.rect.bottom] - row_for[entry.rect.top],
            column_span=(
                column_for[entry.rect.right] - column_for[entry.rect.left]
            ),
        )
        for entry in entries
    )
    columns = tuple(
        max(1, round(right - left))
        for left, right in zip(horizontal_edges, horizontal_edges[1:])
    )
    rows = tuple(
        max(1, round(bottom - top))
        for top, bottom in zip(vertical_edges, vertical_edges[1:])
    )
    return _Candidate(
        QtLayout(
            "QGridLayout",
            source.object_name,
            items,
            properties=_portable_properties(source, zero_spacing=True),
            stretch=columns,
            row_stretch=rows,
            minimum_widths=columns,
            minimum_heights=rows,
        ),
        {entry.key: entry.rect for entry in entries},
        "grid-band-faithful",
    )


def _compact_grid_candidate(
    source: QtLayout,
    entries: tuple[_Entry, ...],
) -> _Candidate | None:
    guide_entries = tuple(
        entry
        for entry in entries
        if entry.item.widget is None
        or entry.item.widget.class_name not in {"QGroupBox", "QFrame"}
    ) or entries
    row_guides = _cluster_values(
        entry.rect.vertical_center for entry in guide_entries
    )
    column_guides = _cluster_values(
        entry.rect.horizontal_center for entry in guide_entries
    )
    if not row_guides or not column_guides:
        return None
    row_weights = _guide_track_weights(
        row_guides,
        entries,
        horizontal=False,
    )
    column_weights = _guide_track_weights(
        column_guides,
        entries,
        horizontal=True,
    )

    items: list[QtLayoutItem] = []
    placements: dict[str, _Rect] = {}
    for entry in entries:
        rows = _covered_guides(
            row_guides,
            entry.rect.top,
            entry.rect.bottom,
            entry.rect.vertical_center,
        )
        columns = _covered_guides(
            column_guides,
            entry.rect.left,
            entry.rect.right,
            entry.rect.horizontal_center,
        )
        row = min(rows) * 2
        column = min(columns) * 2
        row_span = (max(rows) - min(rows)) * 2 + 1
        column_span = (max(columns) - min(columns)) * 2 + 1
        items.append(
            _positioned_item(
                entry.item,
                row=row,
                column=column,
                row_span=row_span,
                column_span=column_span,
            )
        )
        placements[entry.key] = _Rect(
            column,
            row,
            column + column_span,
            row + row_span,
        )

    return _Candidate(
        QtLayout(
            "QGridLayout",
            source.object_name,
            tuple(items),
            properties=_portable_properties(source, zero_spacing=True),
            stretch=column_weights,
            row_stretch=row_weights,
            minimum_widths=column_weights,
            minimum_heights=row_weights,
        ),
        placements,
        "coordinate-to-compact-grid",
    )


def _wrap_in_source_margins(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    candidate: _Candidate,
    *,
    names: _ObjectNameAllocator,
) -> _Candidate:
    row_count, column_count = _grid_shape(source)
    row_weights = _weights(source.minimum_heights or source.row_stretch, row_count)
    column_weights = _weights(
        source.minimum_widths or source.stretch,
        column_count,
    )
    total_width = float(sum(column_weights))
    total_height = float(sum(row_weights))
    content_left = min(entry.rect.left for entry in entries)
    content_top = min(entry.rect.top for entry in entries)
    content_right = max(entry.rect.right for entry in entries)
    content_bottom = max(entry.rect.bottom for entry in entries)
    horizontal = _three_zone_weights(
        content_left,
        content_right,
        total_width,
    )
    vertical = _three_zone_weights(
        content_top,
        content_bottom,
        total_height,
    )
    inner = replace(
        candidate.layout,
        object_name=names.next(f"{source.object_name}Content"),
    )
    wrapper = QtLayout(
        "QGridLayout",
        source.object_name,
        (
            QtLayoutItem(layout=inner, row=1, column=1),
            QtLayoutItem(
                spacer=QtSpacer(
                    names.next(f"{source.object_name}ExtentMarker"),
                    "horizontal",
                    size_type="Minimum",
                    size_hint=0,
                ),
                row=2,
                column=2,
            ),
        ),
        properties=_zero_spacing_properties(source.properties),
        stretch=horizontal,
        row_stretch=vertical,
        minimum_widths=horizontal,
        minimum_heights=vertical,
    )
    return replace(candidate, layout=wrapper)


def _wrap_in_horizontal_source_margins(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    candidate: _Candidate,
    *,
    names: _ObjectNameAllocator,
) -> _Candidate:
    _, column_count = _grid_shape(source)
    column_weights = _weights(
        source.minimum_widths or source.stretch,
        column_count,
    )
    content_left = min(entry.rect.left for entry in entries)
    content_right = max(entry.rect.right for entry in entries)
    horizontal = _three_zone_weights(
        content_left,
        content_right,
        float(sum(column_weights)),
    )
    inner = replace(
        candidate.layout,
        object_name=names.next(f"{source.object_name}BandContent"),
    )
    wrapper = QtLayout(
        "QGridLayout",
        names.next(f"{source.object_name}Band"),
        (
            QtLayoutItem(layout=inner, row=0, column=1),
            QtLayoutItem(
                spacer=QtSpacer(
                    names.next(f"{source.object_name}BandExtentMarker"),
                    "horizontal",
                    size_type="Minimum",
                    size_hint=0,
                ),
                row=0,
                column=2,
            ),
        ),
        properties=_zero_spacing_properties(source.properties),
        stretch=horizontal,
        minimum_widths=horizontal,
    )
    return replace(candidate, layout=wrapper)


def _clean_faithful_grid(
    source: QtLayout,
    entries: tuple[_Entry, ...],
) -> _Candidate:
    """Retain exact tracks without Designer-blocking technical overlays."""

    return _Candidate(
        replace(
            source,
            items=tuple(
                item
                for item in source.items
                if not _is_technical_item(item)
            ),
        ),
        {entry.key: entry.rect for entry in entries},
        "faithful-grid-cleanup",
        faithful_fallback=True,
    )


def _three_zone_weights(
    content_start: float,
    content_end: float,
    total: float,
) -> tuple[int, int, int]:
    return (
        max(0, round(max(0.0, content_start))),
        max(1, round(max(1.0, content_end - content_start))),
        max(0, round(max(0.0, total - content_end))),
    )


def _positioned_item(
    item: QtLayoutItem,
    *,
    row: int,
    column: int,
    row_span: int = 1,
    column_span: int = 1,
) -> QtLayoutItem:
    return replace(
        item,
        row=row,
        column=column,
        row_span=row_span,
        column_span=column_span,
    )


def _overlap_groups(
    entries: tuple[_Entry, ...],
    *,
    horizontal: bool,
) -> tuple[tuple[_Entry, ...], ...]:
    ordered = sorted(
        entries,
        key=(
            (lambda entry: entry.rect.left)
            if horizontal
            else (lambda entry: entry.rect.top)
        ),
    )
    groups: list[list[_Entry]] = []
    ends: list[float] = []
    for entry in ordered:
        start = entry.rect.left if horizontal else entry.rect.top
        end = entry.rect.right if horizontal else entry.rect.bottom
        best: int | None = None
        for index, group_end in enumerate(ends):
            if start < group_end:
                best = index
                break
        if best is None:
            groups.append([entry])
            ends.append(end)
        else:
            groups[best].append(entry)
            ends[best] = max(ends[best], end)
    return tuple(tuple(group) for group in groups)


def _cluster_values(
    values: Iterable[float],
    *,
    tolerance: float = 3.0,
) -> tuple[float, ...]:
    ordered = sorted(float(value) for value in values)
    clusters: list[list[float]] = []
    for value in ordered:
        if not clusters or value - clusters[-1][-1] > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return tuple(sum(cluster) / len(cluster) for cluster in clusters)


def _covered_guides(
    guides: tuple[float, ...],
    start: float,
    end: float,
    center: float,
) -> tuple[int, ...]:
    covered = tuple(
        index
        for index, guide in enumerate(guides)
        if start <= guide <= end
    )
    if covered:
        return covered
    return (
        min(
            range(len(guides)),
            key=lambda index: (abs(guides[index] - center), index),
        ),
    )


def _guide_track_weights(
    guides: tuple[float, ...],
    entries: tuple[_Entry, ...],
    *,
    horizontal: bool,
) -> tuple[int, ...]:
    members: list[list[_Entry]] = [[] for _ in guides]
    for entry in entries:
        covered = _covered_guides(
            guides,
            entry.rect.left if horizontal else entry.rect.top,
            entry.rect.right if horizontal else entry.rect.bottom,
            (
                entry.rect.horizontal_center
                if horizontal
                else entry.rect.vertical_center
            ),
        )
        if len(covered) == 1:
            members[covered[0]].append(entry)

    sizes: list[int] = []
    for index, group in enumerate(members):
        if group:
            extent = max(
                entry.rect.width if horizontal else entry.rect.height
                for entry in group
            )
        else:
            distances = tuple(
                abs(guides[index] - guides[neighbor])
                for neighbor in (index - 1, index + 1)
                if 0 <= neighbor < len(guides)
            )
            extent = min(distances, default=1.0)
        sizes.append(max(1, round(extent)))

    result: list[int] = []
    for index, size in enumerate(sizes):
        result.append(size)
        if index + 1 == len(sizes):
            continue
        current = members[index]
        following = members[index + 1]
        if current and following:
            current_end = max(
                entry.rect.right if horizontal else entry.rect.bottom
                for entry in current
            )
            following_start = min(
                entry.rect.left if horizontal else entry.rect.top
                for entry in following
            )
            gap = following_start - current_end
        else:
            gap = (
                guides[index + 1]
                - guides[index]
                - (sizes[index] + sizes[index + 1]) / 2
            )
        result.append(max(1, round(gap)))
    return tuple(result)


def _preserves_topology(
    reference: dict[str, _Rect],
    candidate: dict[str, _Rect],
) -> bool:
    if set(reference) != set(candidate):
        return False
    keys = sorted(reference)
    for index, left_key in enumerate(keys):
        for right_key in keys[index + 1 :]:
            left = reference[left_key]
            right = reference[right_key]
            replacement_left = candidate[left_key]
            replacement_right = candidate[right_key]
            if not _axis_topology(
                left.left,
                left.right,
                right.left,
                right.right,
                replacement_left.left,
                replacement_left.right,
                replacement_right.left,
                replacement_right.right,
            ):
                return False
            if not _axis_topology(
                left.top,
                left.bottom,
                right.top,
                right.bottom,
                replacement_left.top,
                replacement_left.bottom,
                replacement_right.top,
                replacement_right.bottom,
            ):
                return False
    return True


def _axis_topology(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
    new_left_start: float,
    new_left_end: float,
    new_right_start: float,
    new_right_end: float,
) -> bool:
    if left_end <= right_start:
        return new_left_end <= new_right_start
    if right_end <= left_start:
        return new_right_end <= new_left_start
    if min(new_left_end, new_right_end) <= max(new_left_start, new_right_start):
        return False
    equalities = (
        (left_start == right_start, new_left_start == new_right_start),
        (left_end == right_end, new_left_end == new_right_end),
    )
    return all(not required or preserved for required, preserved in equalities)


def _portable_properties(
    layout: QtLayout,
    *,
    zero_spacing: bool = False,
) -> tuple[QtProperty, ...]:
    properties = tuple(
        property_
        for property_ in layout.properties
        if property_.name == "sizeConstraint"
    )
    return _zero_spacing_properties(properties) if zero_spacing else properties


def _zero_spacing_properties(
    properties: tuple[QtProperty, ...],
) -> tuple[QtProperty, ...]:
    return tuple(
        property_
        for property_ in properties
        if property_.name
        not in {"spacing", "horizontalSpacing", "verticalSpacing"}
    ) + (QtProperty("spacing", 0),)


def _item_key(item: QtLayoutItem) -> str | None:
    if item.widget is not None:
        return f"widget:{item.widget.object_name}"
    if item.layout is not None:
        return f"layout:{item.layout.object_name}"
    if item.spacer is not None:
        return f"spacer:{item.spacer.object_name}"
    return None


def _is_technical_item(item: QtLayoutItem) -> bool:
    if item.widget is not None and any(
        property_.name == "rc2uiInternal" and property_.value is True
        for property_ in item.widget.properties
    ):
        return True
    if item.spacer is None:
        return False
    return item.spacer.object_name.startswith(
        (
            "fontMinimumWidthSpacer",
            "fontMinimumHeightSpacer",
            "trailingHorizontalSpacer",
            "trailingVerticalSpacer",
        )
    )


def _grid_shape(layout: QtLayout) -> tuple[int, int]:
    row_count = max(
        ((item.row or 0) + item.row_span for item in layout.items),
        default=1,
    )
    column_count = max(
        ((item.column or 0) + item.column_span for item in layout.items),
        default=1,
    )
    return max(1, row_count), max(1, column_count)


def _weights(values: tuple[int, ...], count: int) -> tuple[int, ...]:
    return tuple(
        max(1, values[index] if index < len(values) else 1)
        for index in range(count)
    )


def _offsets(weights: tuple[int, ...]) -> tuple[float, ...]:
    result = [0.0]
    for value in weights:
        result.append(result[-1] + value)
    return tuple(result)


def _layout_cost(layout: QtLayout) -> int:
    cost = 2 + len(layout.items)
    if layout.class_name == "QGridLayout":
        rows, columns = _grid_shape(layout)
        cost += rows + columns
        cost += sum(
            max(0, item.row_span - 1) + max(0, item.column_span - 1)
            for item in layout.items
        )
    elif layout.class_name == "QFormLayout":
        cost += 1
    for item in layout.items:
        if _is_technical_item(item):
            cost += 8
        if item.widget is not None and item.widget.layout is not None:
            cost += _layout_cost(item.widget.layout)
        elif item.layout is not None:
            cost += _layout_cost(item.layout)
    return cost


def _editability_score(root_widget: QtWidget) -> float:
    widget_count = _widget_count(root_widget)
    layout_cost = (
        _designer_friction(root_widget.layout) if root_widget.layout else 0
    )
    denominator = max(1, widget_count * 8 + layout_cost)
    return round(widget_count * 8 / denominator, 4)


def _designer_friction(layout: QtLayout) -> int:
    """Estimate mouse-editing difficulty, not serialization complexity."""

    cost = 1
    if layout.class_name == "QGridLayout":
        rows, columns = _grid_shape(layout)
        if rows > 3 and columns > 3:
            # A fine two-dimensional coordinate mesh is the structure that
            # makes dropping a control in Designer impractical.  Small 1x3
            # margin wrappers and compact semantic grids are intentionally
            # cheap even when there are many of them.
            cost += (rows - 1) * (columns - 1)
        else:
            cost += max(0, rows * columns - len(layout.items) * 2)
    elif layout.class_name == "QFormLayout":
        cost += 1
    for item in layout.items:
        if _is_technical_item(item):
            cost += 4
        if item.widget is not None and item.widget.layout is not None:
            cost += _designer_friction(item.widget.layout)
        elif item.layout is not None:
            cost += _designer_friction(item.layout)
    return cost


def _widget_count(widget: QtWidget) -> int:
    result = 0 if _is_internal_widget(widget) else 1
    result += sum(_widget_count(child) for child in widget.children)
    if widget.layout is not None:
        result += _layout_widget_count(widget.layout)
    return result


def _layout_widget_count(layout: QtLayout) -> int:
    result = 0
    for item in layout.items:
        if item.widget is not None:
            result += _widget_count(item.widget)
        elif item.layout is not None:
            result += _layout_widget_count(item.layout)
    return result


def _object_names(root_widget: QtWidget) -> tuple[str, ...]:
    names: list[str] = []

    def visit_widget(widget: QtWidget) -> None:
        names.append(widget.object_name)
        if widget.layout is not None:
            visit_layout(widget.layout)
        for child in widget.children:
            visit_widget(child)

    def visit_layout(layout: QtLayout) -> None:
        names.append(layout.object_name)
        for item in layout.items:
            if item.widget is not None:
                visit_widget(item.widget)
            elif item.layout is not None:
                visit_layout(item.layout)
            elif item.spacer is not None:
                names.append(item.spacer.object_name)

    visit_widget(root_widget)
    return tuple(names)


def _is_internal_widget(widget: QtWidget) -> bool:
    return any(
        property_.name == "rc2uiInternal" and property_.value is True
        for property_ in widget.properties
    )

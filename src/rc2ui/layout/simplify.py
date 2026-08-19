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


_ROW_BOUNDARY_TOLERANCE = 1.0
_MAX_LAYOUT_STRETCH_ITEMS = 5


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
            _form_candidate(faithful, entries) if len(entries) >= 2 else None,
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
                _vertical_bands_candidate(
                    faithful,
                    entries,
                    names=self.names,
                )
                if len(entries) >= 3
                else None
            ),
            (
                _slicing_candidate(
                    faithful,
                    entries,
                    names=self.names,
                )
                if len(entries) >= 3
                else None
            ),
            _form_grid_candidate(faithful, entries) if len(entries) >= 2 else None,
            (
                _compact_grid_candidate(faithful, entries)
                if len(entries) >= 2
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


def simplify_form(root_widget: QtWidget) -> SimplificationResult:
    """Create a Designer-oriented form without mutating faithful planning."""

    simplifier = _Simplifier(_object_names(root_widget))
    simplified = simplifier.widget(root_widget)
    simplified = _retain_root_width_ruler(
        root_widget,
        simplified,
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
    item_stretches: list[int] = []
    for index, entry in enumerate(ordered):
        extent = max(
            1,
            round(entry.rect.width if horizontal else entry.rect.height),
        )
        items.append(
            replace(
                _with_item_axis_stretch(
                    entry.item,
                    horizontal=horizontal,
                    value=extent,
                ),
                row=None,
                column=None,
                row_span=1,
                column_span=1,
            )
        )
        item_stretches.append(extent)
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
        item_stretches.append(max(1, round(gap)))
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
        _bounded_axis_layout(
            class_name,
            source.object_name,
            tuple(items),
            tuple(item_stretches),
            properties=_portable_properties(source, zero_spacing=True),
            names=names,
        ),
        placements,
        "grid-to-hbox" if horizontal else "grid-to-vbox",
    )


def _bounded_axis_layout(
    class_name: str,
    object_name: str,
    items: tuple[QtLayoutItem, ...],
    stretches: tuple[int, ...],
    *,
    properties: tuple[QtProperty, ...],
    names: _ObjectNameAllocator,
) -> QtLayout:
    """Build a proportional box-layout tree with short Designer vectors."""

    if len(items) <= _MAX_LAYOUT_STRETCH_ITEMS:
        return QtLayout(
            class_name,
            object_name,
            items,
            properties=properties,
            stretch=stretches,
        )

    gap_indexes = tuple(
        index
        for index, item in enumerate(items)
        if item.spacer is not None
        and (
            (class_name == "QHBoxLayout" and item.spacer.orientation == "horizontal")
            or (
                class_name == "QVBoxLayout"
                and item.spacer.orientation == "vertical"
            )
        )
        and 0 < index < len(items) - 1
    )
    if gap_indexes:
        split = min(
            gap_indexes,
            key=lambda index: (
                abs(sum(stretches[:index]) - sum(stretches[index + 1 :])),
                index,
            ),
        )
        left_items = items[:split]
        right_items = items[split + 1 :]
        left_stretches = stretches[:split]
        right_stretches = stretches[split + 1 :]
        middle = (items[split],)
        root_stretches = (
            max(1, sum(left_stretches)),
            stretches[split],
            max(1, sum(right_stretches)),
        )
    else:
        split = len(items) // 2
        left_items = items[:split]
        right_items = items[split:]
        left_stretches = stretches[:split]
        right_stretches = stretches[split:]
        middle = ()
        root_stretches = (
            max(1, sum(left_stretches)),
            max(1, sum(right_stretches)),
        )

    left = _bounded_axis_layout(
        class_name,
        names.next(f"{object_name}Segment"),
        left_items,
        left_stretches,
        properties=properties,
        names=names,
    )
    right = _bounded_axis_layout(
        class_name,
        names.next(f"{object_name}Segment"),
        right_items,
        right_stretches,
        properties=properties,
        names=names,
    )
    return QtLayout(
        class_name,
        object_name,
        (QtLayoutItem(layout=left), *middle, QtLayoutItem(layout=right)),
        properties=properties,
        stretch=root_stretches,
    )


def _vertical_bands_candidate(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    *,
    names: _ObjectNameAllocator,
) -> _Candidate | None:
    """Replace a fine global grid with editable horizontal row layouts."""

    bands = _vertical_overlap_bands(entries)
    if len(bands) < 2:
        return None

    row_count, _ = _grid_shape(source)
    row_weights = _weights(
        source.minimum_heights or source.row_stretch,
        row_count,
    )
    total_height = float(sum(row_weights))
    items: list[QtLayoutItem] = []
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
        row_candidate = _horizontal_band_candidate(
            source,
            band,
            names=names,
        )
        if row_candidate is None:
            return None
        items.append(QtLayoutItem(layout=row_candidate.layout))
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
    return _Candidate(
        QtLayout(
            "QVBoxLayout",
            source.object_name,
            tuple(items),
            properties=_portable_properties(source, zero_spacing=True),
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
        if (
            not bands
            or entry.rect.top
            >= current_bottom - _ROW_BOUNDARY_TOLERANCE
        ):
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


def _slicing_candidate(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    *,
    names: _ObjectNameAllocator,
) -> _Candidate | None:
    """Recursively split panes along source-empty horizontal/vertical cuts."""

    partition = _best_slice_partition(entries)
    if partition is None:
        return None
    horizontal, first, second, gap = partition
    first_item = _sliced_group_item(source, first, names=names)
    second_item = _sliced_group_item(source, second, names=names)
    if first_item is None or second_item is None:
        return None
    class_name = "QHBoxLayout" if horizontal else "QVBoxLayout"
    orientation = "horizontal" if horizontal else "vertical"
    middle: tuple[QtLayoutItem, ...] = ()
    stretches = (
        max(1, round(_group_extent(first, horizontal=horizontal))),
        max(1, round(_group_extent(second, horizontal=horizontal))),
    )
    if gap > 0:
        middle = (
            QtLayoutItem(
                spacer=QtSpacer(
                    names.next(f"{source.object_name}SliceGap"),
                    orientation,
                    size_type="Minimum",
                    size_hint=max(1, round(gap)),
                )
            ),
        )
        stretches = (stretches[0], max(1, round(gap)), stretches[1])
    return _Candidate(
        QtLayout(
            class_name,
            source.object_name,
            (first_item, *middle, second_item),
            properties=_portable_properties(source, zero_spacing=True),
            stretch=stretches,
        ),
        {entry.key: entry.rect for entry in entries},
        "grid-to-slicing-layout",
    )


def _sliced_group_item(
    source: QtLayout,
    entries: tuple[_Entry, ...],
    *,
    names: _ObjectNameAllocator,
) -> QtLayoutItem | None:
    if len(entries) == 1:
        return replace(
            entries[0].item,
            row=None,
            column=None,
            row_span=1,
            column_span=1,
        )
    for horizontal in (True, False):
        candidate = _axis_candidate(
            source,
            entries,
            horizontal=horizontal,
            names=names,
        )
        if candidate is not None:
            return QtLayoutItem(
                layout=replace(
                    candidate.layout,
                    object_name=names.next(f"{source.object_name}Slice"),
                )
            )
    candidate = _slicing_candidate(source, entries, names=names)
    if candidate is None:
        return None
    return QtLayoutItem(
        layout=replace(
            candidate.layout,
            object_name=names.next(f"{source.object_name}Slice"),
        )
    )


def _best_slice_partition(
    entries: tuple[_Entry, ...],
) -> tuple[bool, tuple[_Entry, ...], tuple[_Entry, ...], float] | None:
    candidates: list[
        tuple[float, int, bool, tuple[_Entry, ...], tuple[_Entry, ...]]
    ] = []
    for horizontal in (True, False):
        ordered = sorted(
            entries,
            key=(
                (lambda entry: (entry.rect.left, entry.rect.right, entry.key))
                if horizontal
                else (
                    lambda entry: (entry.rect.top, entry.rect.bottom, entry.key)
                )
            ),
        )
        for index in range(1, len(ordered)):
            first = tuple(ordered[:index])
            second = tuple(ordered[index:])
            first_end = max(
                entry.rect.right if horizontal else entry.rect.bottom
                for entry in first
            )
            second_start = min(
                entry.rect.left if horizontal else entry.rect.top
                for entry in second
            )
            gap = second_start - first_end
            if gap < -_ROW_BOUNDARY_TOLERANCE:
                continue
            candidates.append(
                (
                    max(0.0, gap),
                    min(len(first), len(second)),
                    horizontal,
                    first,
                    second,
                )
            )
    if not candidates:
        return None
    gap, _balance, horizontal, first, second = max(
        candidates,
        key=lambda candidate: (candidate[0], candidate[1], candidate[2]),
    )
    return horizontal, first, second, gap


def _group_extent(
    entries: tuple[_Entry, ...],
    *,
    horizontal: bool,
) -> float:
    starts = tuple(
        entry.rect.left if horizontal else entry.rect.top for entry in entries
    )
    ends = tuple(
        entry.rect.right if horizontal else entry.rect.bottom for entry in entries
    )
    return max(ends) - min(starts)


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
                        _with_item_axis_stretch(
                            entry.item,
                            horizontal=True,
                            value=max(1, round(entry.rect.width)),
                        ),
                        row=None,
                        column=None,
                        row_span=1,
                        column_span=1,
                    ),
                ),
                properties=_portable_properties(source, zero_spacing=True),
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


def _with_item_axis_stretch(
    item: QtLayoutItem,
    *,
    horizontal: bool,
    value: int,
) -> QtLayoutItem:
    """Keep row proportions without a Designer-hostile layout list."""

    if item.widget is None:
        return item
    properties: list[QtProperty] = []
    changed = False
    for property_ in item.widget.properties:
        if property_.name != "sizePolicy" or not isinstance(
            property_.value,
            QtSizePolicy,
        ):
            properties.append(property_)
            continue
        properties.append(
            replace(
                property_,
                value=replace(
                    property_.value,
                    horizontal_stretch=(
                        value
                        if horizontal
                        else property_.value.horizontal_stretch
                    ),
                    vertical_stretch=(
                        property_.value.vertical_stretch
                        if horizontal
                        else value
                    ),
                ),
            )
        )
        changed = True
    if not changed:
        return item
    return replace(
        item,
        widget=replace(item.widget, properties=tuple(properties)),
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

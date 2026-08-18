from __future__ import annotations

from dataclasses import replace

from rc2ui.qt.model import (
    QtEnum,
    QtLayout,
    QtLayoutItem,
    QtProperty,
    QtSize,
    QtSizePolicy,
    QtSpacer,
    QtString,
    QtWidget,
)


_DYNAMIC_MINIMUM = QtProperty(
    "sizeConstraint",
    QtEnum("QLayout::SetMinimumSize"),
)
_RULER_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_UNBOUNDED = 16_777_215


def make_font_responsive(
    layout: QtLayout,
    *,
    baseline_width: int,
    baseline_height: int,
    source_width_dlu: int,
    source_height_dlu: int,
    width_spacer_name: str,
    height_spacer_name: str,
    width_ruler_name: str,
    height_ruler_name: str,
) -> QtLayout:
    """Propagate dynamic font size hints without losing the RC size floor.

    SetMinimumSize makes every nested layout invalidate and publish its new
    minimum after QEvent::FontChange.  On the root it would otherwise replace
    the explicit dialog minimum with a much smaller aggregate size hint, so
    two invisible full-span Minimum spacers retain the source-sized baseline.

    Win32 DLU are font-relative (roughly one quarter of an average character
    horizontally and one eighth of a character height vertically).  Zero-
    thickness QLabel rulers encode those relationships in plain .ui XML.  As
    Qt handles FontChange, their size hints grow and the coordinate grid grows
    with them.  Unlike a fixed pixel reserve this also scales gaps and distant
    alignments.  The fixed spacers remain the floor because glyph metrics vary
    slightly by platform and font substitution.
    """

    constrained = _constrained_layout(layout)
    width_item = QtLayoutItem(
        spacer=QtSpacer(
            width_spacer_name,
            "horizontal",
            size_type="Minimum",
            size_hint=max(1, baseline_width),
        )
    )
    height_item = QtLayoutItem(
        spacer=QtSpacer(
            height_spacer_name,
            "vertical",
            size_type="Minimum",
            size_hint=max(1, baseline_height),
        )
    )
    if constrained.class_name == "QGridLayout":
        row_count, column_count = _grid_shape(constrained)
        width_item = replace(
            width_item,
            row=0,
            column=0,
            row_span=row_count,
            column_span=column_count,
        )
        height_item = replace(
            height_item,
            row=0,
            column=0,
            row_span=row_count,
            column_span=column_count,
        )
        ruler_items = (
            _font_ruler_item(
                object_name=width_ruler_name,
                text=_horizontal_ruler_text(source_width_dlu),
                horizontal=True,
                row_count=row_count,
                column_count=column_count,
            ),
            _font_ruler_item(
                object_name=height_ruler_name,
                text=_vertical_ruler_text(source_height_dlu),
                horizontal=False,
                row_count=row_count,
                column_count=column_count,
            ),
        )
    else:
        # Empty forms use a QVBoxLayout.  Rulers cannot overlap content there,
        # so retain only their fixed source-size floor.
        ruler_items = ()
    return replace(
        constrained,
        items=constrained.items + (width_item, height_item) + ruler_items,
    )


def _font_ruler_item(
    *,
    object_name: str,
    text: str,
    horizontal: bool,
    row_count: int,
    column_count: int,
) -> QtLayoutItem:
    maximum_size = (
        QtSize(_UNBOUNDED, 0) if horizontal else QtSize(0, _UNBOUNDED)
    )
    size_policy = (
        QtSizePolicy("Minimum", "Fixed")
        if horizontal
        else QtSizePolicy("Fixed", "Minimum")
    )
    return QtLayoutItem(
        widget=QtWidget(
            class_name="QLabel",
            object_name=object_name,
            properties=(
                QtProperty("text", QtString(text, translatable=False)),
                QtProperty("sizePolicy", size_policy),
                QtProperty("maximumSize", maximum_size),
                QtProperty("rc2uiInternal", True, dynamic=True),
            ),
        ),
        row=0,
        column=0,
        row_span=row_count,
        column_span=column_count,
    )


def _horizontal_ruler_text(width_dlu: int) -> str:
    character_count = max(1, width_dlu // 4)
    repeats = (character_count + len(_RULER_ALPHABET) - 1) // len(
        _RULER_ALPHABET
    )
    return (_RULER_ALPHABET * repeats)[:character_count]


def _vertical_ruler_text(height_dlu: int) -> str:
    line_count = max(1, height_dlu // 8)
    return "\n".join("M" for _ in range(line_count))


def _constrained_widget(widget: QtWidget) -> QtWidget:
    return replace(
        widget,
        layout=(
            _constrained_layout(widget.layout)
            if widget.layout is not None
            else None
        ),
        children=tuple(_constrained_widget(child) for child in widget.children),
    )


def _constrained_layout(layout: QtLayout) -> QtLayout:
    properties = tuple(
        property_
        for property_ in layout.properties
        if property_.name != "sizeConstraint"
    ) + (_DYNAMIC_MINIMUM,)
    return replace(
        layout,
        properties=properties,
        items=tuple(_constrained_item(item) for item in layout.items),
    )


def _constrained_item(item: QtLayoutItem) -> QtLayoutItem:
    return replace(
        item,
        widget=(
            _constrained_widget(item.widget)
            if item.widget is not None
            else None
        ),
        layout=(
            _constrained_layout(item.layout)
            if item.layout is not None
            else None
        ),
    )


def _grid_shape(layout: QtLayout) -> tuple[int, int]:
    rows = (
        max((item.row or 0) + item.row_span for item in layout.items)
        if layout.items
        else 1
    )
    columns = (
        max((item.column or 0) + item.column_span for item in layout.items)
        if layout.items
        else 1
    )
    return max(1, rows), max(1, columns)

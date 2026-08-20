from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace

from rc2ui.layout.policy import GapGrowth, SimplifiedProfile
from rc2ui.qt.model import (
    QtLayout,
    QtLayoutItem,
    QtProperty,
    QtWidget,
)


_MARGIN_PROPERTIES = {
    "leftMargin",
    "topMargin",
    "rightMargin",
    "bottomMargin",
}


@dataclass(frozen=True, slots=True)
class SpacerSummary:
    total: int = 0
    explicit_gaps: int = 0
    extent_markers: int = 0
    hidden_extents: int = 0
    font_floors: int = 0
    trailing_tracks: int = 0
    other: int = 0


@dataclass(frozen=True, slots=True)
class SpacerCompactionResult:
    root_widget: QtWidget
    removed_spacers: int
    transformations: tuple[str, ...]


class _Compactor:
    def __init__(
        self,
        *,
        profile: SimplifiedProfile,
        gap_growth: GapGrowth,
    ) -> None:
        self.profile = profile
        self.gap_growth = gap_growth
        self.transformations: Counter[str] = Counter()

    def widget(self, widget: QtWidget) -> QtWidget:
        children = tuple(self.widget(child) for child in widget.children)
        layout = self.layout(widget.layout) if widget.layout is not None else None
        return replace(widget, children=children, layout=layout)

    def layout(self, layout: QtLayout) -> QtLayout:
        nested = replace(
            layout,
            items=tuple(self._item(item) for item in layout.items),
        )
        if self.profile is SimplifiedProfile.CONSERVATIVE:
            return nested
        margin_compacted = self._margin_wrapper(nested)
        return self._uniform_box_gaps(margin_compacted)

    def _item(self, item: QtLayoutItem) -> QtLayoutItem:
        return replace(
            item,
            widget=self.widget(item.widget) if item.widget is not None else None,
            layout=self.layout(item.layout) if item.layout is not None else None,
        )

    def _margin_wrapper(self, layout: QtLayout) -> QtLayout:
        if (
            layout.class_name != "QGridLayout"
            or len(layout.items) != 2
            or not _has_zero_grid_chrome(layout.properties)
        ):
            return layout
        inner_item = next(
            (item for item in layout.items if item.layout is not None),
            None,
        )
        marker_item = next(
            (
                item
                for item in layout.items
                if item.spacer is not None
                and "ExtentMarker" in item.spacer.object_name
                and (
                    "PanelExtentMarker" not in item.spacer.object_name
                    or self.gap_growth is not GapGrowth.PROPORTIONAL
                )
                and item.spacer.size_type == "Minimum"
                and item.spacer.size_hint == 0
            ),
            None,
        )
        if inner_item is None or marker_item is None:
            return layout
        if inner_item.column != 1 or marker_item.column != 2:
            return layout

        horizontal = layout.minimum_widths
        horizontal_growth = layout.stretch
        if len(horizontal) != 3 or len(horizontal_growth) != 3:
            return layout
        marker_name = marker_item.spacer.object_name
        if inner_item.row == 0 and marker_item.row == 0:
            vertical = (0, 1, 0)
            vertical_growth = vertical
        elif inner_item.row == 1 and marker_item.row == 2:
            vertical = layout.minimum_heights
            vertical_growth = layout.row_stretch
            if len(vertical) != 3 or len(vertical_growth) != 3:
                return layout
        else:
            return layout

        # Under proportional growth the outer tracks are elastic source
        # geometry, not ordinary fixed margins.  Replacing them with layout
        # margins changes resize behaviour even when the initial pixels happen
        # to match.  Only the all-zero case is structurally redundant.
        if self.gap_growth is GapGrowth.PROPORTIONAL and any(
            (
                horizontal[0],
                horizontal[2],
                horizontal_growth[0],
                horizontal_growth[2],
                vertical[0],
                vertical[2],
                vertical_growth[0],
                vertical_growth[2],
            )
        ):
            return layout

        assert inner_item.layout is not None
        inner = inner_item.layout
        margins = {
            "leftMargin": horizontal[0],
            "topMargin": vertical[0],
            "rightMargin": horizontal[2],
            "bottomMargin": vertical[2],
        }
        transformation = (
            "band-marker-to-margins"
            if "BandExtentMarker" in marker_name
            else "extent-marker-to-margins"
        )
        self.transformations[transformation] += 1
        return replace(
            inner,
            object_name=layout.object_name,
            properties=_replace_margins(inner.properties, margins),
        )

    def _uniform_box_gaps(self, layout: QtLayout) -> QtLayout:
        if self.gap_growth is not GapGrowth.MINIMUM:
            return layout
        if layout.class_name not in {"QHBoxLayout", "QVBoxLayout"}:
            return layout
        if not _has_zero_box_spacing(layout):
            return layout
        if len(layout.items) < 5 or len(layout.items) % 2 == 0:
            return layout
        gap_indices = tuple(range(1, len(layout.items), 2))
        if len(gap_indices) < 2:
            return layout
        orientation = (
            "horizontal" if layout.class_name == "QHBoxLayout" else "vertical"
        )
        gaps = []
        for index, item in enumerate(layout.items):
            if index in gap_indices:
                spacer = item.spacer
                if (
                    spacer is None
                    or "Gap" not in spacer.object_name
                    or spacer.orientation != orientation
                    or spacer.size_type != "Minimum"
                ):
                    return layout
                gaps.append(spacer.size_hint)
            elif item.spacer is not None:
                return layout
        if len(set(gaps)) != 1 or gaps[0] <= 0:
            return layout
        if layout.stretch and len(layout.stretch) != len(layout.items):
            return layout

        kept_indices = tuple(range(0, len(layout.items), 2))
        self.transformations["uniform-gaps-to-spacing"] += 1
        return replace(
            layout,
            items=tuple(layout.items[index] for index in kept_indices),
            properties=_replace_spacing(layout.properties, gaps[0]),
            stretch=(
                tuple(layout.stretch[index] for index in kept_indices)
                if layout.stretch
                else ()
            ),
        )


def compact_simplified_spacers(
    simplified: QtWidget,
    *,
    profile: SimplifiedProfile,
    gap_growth: GapGrowth = GapGrowth.PROPORTIONAL,
) -> SpacerCompactionResult:
    """Reduce Designer-only spacer noise without touching faithful output."""

    before = summarize_spacers(simplified)
    compactor = _Compactor(
        profile=profile,
        gap_growth=gap_growth,
    )
    root_widget = compactor.widget(simplified)
    after = summarize_spacers(root_widget)
    return SpacerCompactionResult(
        root_widget=root_widget,
        removed_spacers=max(0, before.total - after.total),
        transformations=tuple(
            f"{name}:{count}"
            for name, count in sorted(compactor.transformations.items())
        ),
    )


def summarize_spacers(root_widget: QtWidget) -> SpacerSummary:
    counts: Counter[str] = Counter()

    def visit_widget(widget: QtWidget) -> None:
        if widget.layout is not None:
            visit_layout(widget.layout)
        for child in widget.children:
            visit_widget(child)

    def visit_layout(layout: QtLayout) -> None:
        for item in layout.items:
            if item.spacer is not None:
                name = item.spacer.object_name
                if name.startswith("fontMinimum"):
                    counts["font_floors"] += 1
                elif name.startswith("trailing"):
                    counts["trailing_tracks"] += 1
                elif "HiddenExtent" in name:
                    counts["hidden_extents"] += 1
                elif "ExtentMarker" in name:
                    counts["extent_markers"] += 1
                elif "Gap" in name:
                    counts["explicit_gaps"] += 1
                else:
                    counts["other"] += 1
            elif item.layout is not None:
                visit_layout(item.layout)
            elif item.widget is not None:
                visit_widget(item.widget)

    visit_widget(root_widget)
    return SpacerSummary(
        total=sum(counts.values()),
        explicit_gaps=counts["explicit_gaps"],
        extent_markers=counts["extent_markers"],
        hidden_extents=counts["hidden_extents"],
        font_floors=counts["font_floors"],
        trailing_tracks=counts["trailing_tracks"],
        other=counts["other"],
    )


def _replace_margins(
    properties: tuple[QtProperty, ...],
    margins: dict[str, int],
) -> tuple[QtProperty, ...]:
    retained = tuple(
        property_
        for property_ in properties
        if property_.name not in _MARGIN_PROPERTIES
    )
    return retained + tuple(
        QtProperty(name, margins[name])
        for name in (
            "leftMargin",
            "topMargin",
            "rightMargin",
            "bottomMargin",
        )
    )


def _replace_spacing(
    properties: tuple[QtProperty, ...],
    spacing: int,
) -> tuple[QtProperty, ...]:
    retained = tuple(
        property_
        for property_ in properties
        if property_.name
        not in {"spacing", "horizontalSpacing", "verticalSpacing"}
    )
    return retained + (QtProperty("spacing", spacing),)


def _has_zero_grid_chrome(properties: tuple[QtProperty, ...]) -> bool:
    values = {property_.name: property_.value for property_ in properties}
    return all(values.get(name) == 0 for name in _MARGIN_PROPERTIES) and (
        values.get("spacing") == 0
        or (
            values.get("horizontalSpacing") == 0
            and values.get("verticalSpacing") == 0
        )
    )


def _has_zero_box_spacing(layout: QtLayout) -> bool:
    values = {property_.name: property_.value for property_ in layout.properties}
    if values.get("spacing") == 0:
        return True
    axis_property = (
        "horizontalSpacing"
        if layout.class_name == "QHBoxLayout"
        else "verticalSpacing"
    )
    return values.get(axis_property) == 0

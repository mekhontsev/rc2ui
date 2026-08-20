from __future__ import annotations

import unittest
from dataclasses import replace

from rc2ui.layout.policy import GapGrowth, SimplifiedProfile
from rc2ui.layout.spacer_compaction import (
    compact_simplified_spacers,
    summarize_spacers,
)
from rc2ui.qt.model import (
    QtLayout,
    QtLayoutItem,
    QtProperty,
    QtSpacer,
    QtWidget,
)


def zero_grid_chrome() -> tuple[QtProperty, ...]:
    return tuple(
        QtProperty(name, 0)
        for name in (
            "leftMargin",
            "topMargin",
            "rightMargin",
            "bottomMargin",
            "spacing",
        )
    )


class SpacerCompactionTests(unittest.TestCase):
    def test_exact_uniform_gaps_compact_only_when_growth_can_be_fixed(self) -> None:
        source = QtWidget(
            "QDialog",
            "dialog",
            layout=QtLayout(
                "QHBoxLayout",
                "row",
                (
                    QtLayoutItem(widget=QtWidget("QPushButton", "one")),
                    QtLayoutItem(
                        spacer=QtSpacer(
                            "rowHorizontalGap1",
                            "horizontal",
                            size_type="Minimum",
                            size_hint=5,
                        )
                    ),
                    QtLayoutItem(widget=QtWidget("QPushButton", "two")),
                    QtLayoutItem(
                        spacer=QtSpacer(
                            "rowHorizontalGap2",
                            "horizontal",
                            size_type="Minimum",
                            size_hint=5,
                        )
                    ),
                    QtLayoutItem(widget=QtWidget("QPushButton", "three")),
                ),
                properties=(QtProperty("spacing", 0),),
                stretch=(40, 5, 40, 5, 40),
            ),
        )

        proportional = compact_simplified_spacers(
            source,
            profile=SimplifiedProfile.BALANCED,
            gap_growth=GapGrowth.PROPORTIONAL,
        )
        self.assertEqual(proportional.root_widget, source)
        aggressive = compact_simplified_spacers(
            source,
            profile=SimplifiedProfile.AGGRESSIVE,
            gap_growth=GapGrowth.PROPORTIONAL,
        )
        self.assertEqual(aggressive.root_widget, source)

        minimum = compact_simplified_spacers(
            source,
            profile=SimplifiedProfile.BALANCED,
            gap_growth=GapGrowth.MINIMUM,
        )
        self.assertEqual(minimum.removed_spacers, 2)
        self.assertEqual(
            minimum.transformations,
            ("uniform-gaps-to-spacing:1",),
        )
        self.assertEqual(len(minimum.root_widget.layout.items), 3)
        self.assertEqual(minimum.root_widget.layout.stretch, (40, 40, 40))
        self.assertEqual(
            next(
                property_.value
                for property_ in minimum.root_widget.layout.properties
                if property_.name == "spacing"
            ),
            5,
        )

    def test_source_margin_wrapper_becomes_direct_layout(self) -> None:
        inner = QtLayout(
            "QVBoxLayout",
            "content",
            (QtLayoutItem(widget=QtWidget("QPushButton", "button")),),
            properties=(QtProperty("spacing", 0),),
        )
        source = QtWidget(
            "QDialog",
            "dialog",
            layout=QtLayout(
                "QGridLayout",
                "wrapper",
                (
                    QtLayoutItem(layout=inner, row=1, column=1),
                    QtLayoutItem(
                        spacer=QtSpacer(
                            "wrapperExtentMarker",
                            "horizontal",
                            size_type="Minimum",
                            size_hint=0,
                        ),
                        row=2,
                        column=2,
                    ),
                ),
                stretch=(10, 80, 10),
                row_stretch=(5, 40, 5),
                minimum_widths=(10, 80, 10),
                minimum_heights=(5, 40, 5),
                properties=zero_grid_chrome(),
            ),
        )

        result = compact_simplified_spacers(
            source,
            profile=SimplifiedProfile.BALANCED,
            gap_growth=GapGrowth.MINIMUM,
        )

        self.assertEqual(result.removed_spacers, 1)
        self.assertEqual(result.root_widget.layout.class_name, "QVBoxLayout")
        self.assertEqual(result.root_widget.layout.object_name, "wrapper")
        margins = {
            property_.name: property_.value
            for property_ in result.root_widget.layout.properties
            if property_.name.endswith("Margin")
        }
        self.assertEqual(
            margins,
            {
                "leftMargin": 10,
                "topMargin": 5,
                "rightMargin": 10,
                "bottomMargin": 5,
            },
        )

        proportional = compact_simplified_spacers(
            source,
            profile=SimplifiedProfile.BALANCED,
            gap_growth=GapGrowth.PROPORTIONAL,
        )
        self.assertEqual(proportional.root_widget, source)
        self.assertEqual(proportional.removed_spacers, 0)

        implicit_chrome = replace(
            source,
            layout=replace(source.layout, properties=()),
        )
        implicit = compact_simplified_spacers(
            implicit_chrome,
            profile=SimplifiedProfile.BALANCED,
            gap_growth=GapGrowth.MINIMUM,
        )
        self.assertEqual(implicit.root_widget, implicit_chrome)

    def test_conservative_profile_retains_source_wrapper(self) -> None:
        marker = QtSpacer(
            "wrapperExtentMarker",
            "horizontal",
            size_type="Minimum",
            size_hint=0,
        )
        source = QtWidget(
            "QDialog",
            "dialog",
            layout=QtLayout(
                "QGridLayout",
                "wrapper",
                (
                    QtLayoutItem(
                        layout=QtLayout("QVBoxLayout", "content", ()),
                        row=1,
                        column=1,
                    ),
                    QtLayoutItem(spacer=marker, row=2, column=2),
                ),
                minimum_widths=(5, 90, 5),
                minimum_heights=(5, 40, 5),
            ),
        )

        result = compact_simplified_spacers(
            source,
            profile=SimplifiedProfile.CONSERVATIVE,
        )

        self.assertEqual(result.root_widget, source)
        self.assertEqual(result.removed_spacers, 0)

    def test_zero_band_wrapper_is_redundant_under_proportional_growth(self) -> None:
        inner = QtLayout(
            "QHBoxLayout",
            "content",
            (QtLayoutItem(widget=QtWidget("QPushButton", "button")),),
        )
        source = QtWidget(
            "QDialog",
            "dialog",
            layout=QtLayout(
                "QGridLayout",
                "band",
                (
                    QtLayoutItem(layout=inner, row=0, column=1),
                    QtLayoutItem(
                        spacer=QtSpacer(
                            "BandExtentMarker",
                            "horizontal",
                            size_type="Minimum",
                            size_hint=0,
                        ),
                        row=0,
                        column=2,
                    ),
                ),
                stretch=(0, 100, 0),
                minimum_widths=(0, 100, 0),
                properties=zero_grid_chrome(),
            ),
        )

        result = compact_simplified_spacers(
            source,
            profile=SimplifiedProfile.BALANCED,
            gap_growth=GapGrowth.PROPORTIONAL,
        )

        self.assertEqual(result.removed_spacers, 1)
        self.assertEqual(
            result.transformations,
            ("band-marker-to-margins:1",),
        )
        self.assertEqual(result.root_widget.layout.class_name, "QHBoxLayout")
        self.assertEqual(result.root_widget.layout.object_name, "band")

    def test_spacer_summary_distinguishes_semantic_roles(self) -> None:
        names = (
            "rowHorizontalGap1",
            "panelExtentMarker",
            "rowHiddenExtent",
            "fontMinimumWidthSpacer",
            "trailingVerticalSpacer",
            "customSpacer",
        )
        root = QtWidget(
            "QDialog",
            "dialog",
            layout=QtLayout(
                "QVBoxLayout",
                "layout",
                tuple(
                    QtLayoutItem(spacer=QtSpacer(name, "vertical"))
                    for name in names
                ),
            ),
        )

        summary = summarize_spacers(root)

        self.assertEqual(summary.total, 6)
        self.assertEqual(summary.explicit_gaps, 1)
        self.assertEqual(summary.extent_markers, 1)
        self.assertEqual(summary.hidden_extents, 1)
        self.assertEqual(summary.font_floors, 1)
        self.assertEqual(summary.trailing_tracks, 1)
        self.assertEqual(summary.other, 1)


if __name__ == "__main__":
    unittest.main()

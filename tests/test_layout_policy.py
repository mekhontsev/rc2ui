from __future__ import annotations

import unittest

from rc2ui.layout.gap_growth import apply_gap_growth
from rc2ui.layout.mode import LayoutMode
from rc2ui.layout.policy import (
    GapGrowth,
    LayoutOverride,
    LayoutPolicy,
    LayoutPolicySet,
)
from rc2ui.qt.model import (
    QtLayout,
    QtLayoutItem,
    QtProperty,
    QtSpacer,
    QtWidget,
)


class LayoutPolicyTests(unittest.TestCase):
    def test_priority_precedes_exactness_and_equal_winners_are_ambiguous(self) -> None:
        policies = LayoutPolicySet(
            overrides=(
                LayoutOverride(
                    "exact",
                    dialog="IDD_ONE",
                    alignment_tolerance_dlu=1,
                ),
                LayoutOverride(
                    "high-priority-family",
                    dialog_regex="IDD_.*",
                    priority=1,
                    alignment_tolerance_dlu=5,
                ),
            )
        )
        self.assertEqual(
            policies.resolve(("IDD_ONE",)).alignment_tolerance_dlu,
            5,
        )

        ambiguous = LayoutPolicySet(
            overrides=(
                LayoutOverride("left", dialog="IDD_ONE"),
                LayoutOverride("right", dialog="IDD_ONE"),
            )
        )
        with self.assertRaisesRegex(ValueError, "ambiguous layout overrides"):
            ambiguous.resolve(("IDD_ONE",))

    def test_command_mode_changes_default_before_dialog_override(self) -> None:
        policies = LayoutPolicySet(
            LayoutPolicy(mode=LayoutMode.FAITHFUL),
            (
                LayoutOverride(
                    "special",
                    dialog="IDD_SPECIAL",
                    mode=LayoutMode.FAITHFUL,
                ),
            ),
        )
        self.assertEqual(
            policies.resolve(
                ("IDD_REGULAR",),
                mode=LayoutMode.SIMPLIFIED,
            ).mode,
            LayoutMode.SIMPLIFIED,
        )
        self.assertEqual(
            policies.resolve(
                ("IDD_SPECIAL",),
                mode=LayoutMode.SIMPLIFIED,
            ).mode,
            LayoutMode.FAITHFUL,
        )


class GapGrowthTests(unittest.TestCase):
    def test_grid_minimum_zeroes_only_empty_track_stretch(self) -> None:
        root = QtWidget(
            "QDialog",
            "dialog",
            layout=QtLayout(
                "QGridLayout",
                "grid",
                (
                    QtLayoutItem(
                        widget=QtWidget("QLabel", "left"),
                        row=0,
                        column=0,
                    ),
                    QtLayoutItem(
                        spacer=QtSpacer("gap", "horizontal"),
                        row=0,
                        column=1,
                    ),
                    QtLayoutItem(
                        widget=QtWidget("QLineEdit", "right"),
                        row=0,
                        column=2,
                    ),
                    QtLayoutItem(
                        widget=QtWidget(
                            "QLabel",
                            "fontRuler",
                            properties=(
                                QtProperty("rc2uiInternal", True),
                            ),
                        ),
                        row=0,
                        column=0,
                        column_span=3,
                    ),
                ),
                stretch=(10, 20, 30),
                row_stretch=(12,),
                minimum_widths=(10, 20, 30),
                minimum_heights=(12,),
            ),
        )

        result = apply_gap_growth(root, GapGrowth.MINIMUM)

        assert result.layout is not None
        self.assertEqual(result.layout.stretch, (10, 0, 30))
        self.assertEqual(result.layout.minimum_widths, (10, 20, 30))
        self.assertEqual(result.layout.row_stretch, (12,))

    def test_outer_minimum_keeps_internal_gap_proportional(self) -> None:
        root = QtWidget(
            "QDialog",
            "dialog",
            layout=QtLayout(
                "QGridLayout",
                "grid",
                (
                    QtLayoutItem(
                        widget=QtWidget("QLabel", "left"),
                        row=0,
                        column=1,
                    ),
                    QtLayoutItem(
                        widget=QtWidget("QLineEdit", "right"),
                        row=0,
                        column=3,
                    ),
                ),
                stretch=(5, 10, 20, 10, 5),
            ),
        )

        result = apply_gap_growth(root, GapGrowth.OUTER_MINIMUM)

        assert result.layout is not None
        self.assertEqual(result.layout.stretch, (0, 10, 20, 10, 0))


if __name__ == "__main__":
    unittest.main()

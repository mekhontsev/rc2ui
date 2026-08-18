from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from rc2ui.domain.diagnostics import Severity
from rc2ui.domain.geometry import RectDlu
from rc2ui.layout.simplify import simplify_form
from rc2ui.qt.emitter import emit_ui
from rc2ui.qt.model import QtWidget
from rc2ui.qtcheck.discovery import discover_qt_binding
from rc2ui.qtcheck.runner import run_qt_checks
from rc2ui.validation.ui_xml import validate_ui_xml
from tests.test_layout_and_emitter import build, make_dialog


def content_layout(widget):
    return next(
        item.layout
        for item in widget.layout.items
        if item.layout is not None
    )


class SimplifiedLayoutTests(unittest.TestCase):
    def test_repeated_label_editor_rows_become_form_layout(self) -> None:
        faithful = build(
            make_dialog(
                [
                    ("Static", "&One:", 0, RectDlu(5, 5, 35, 8)),
                    ("Edit", "", 0, RectDlu(40, 3, 90, 14)),
                    ("Static", "&Two:", 0, RectDlu(5, 25, 35, 8)),
                    ("Edit", "", 0, RectDlu(40, 23, 90, 14)),
                    ("Static", "&Three:", 0, RectDlu(5, 45, 35, 8)),
                    ("Edit", "", 0, RectDlu(40, 43, 90, 14)),
                ]
            )
        )

        simplified = simplify_form(faithful.root_widget)
        text = emit_ui(simplified.root_widget)
        validate_ui_xml(text)
        xml = ET.fromstring(text)

        self.assertEqual(content_layout(simplified.root_widget).class_name, "QFormLayout")
        self.assertEqual(simplified.transformations, ("grid-to-form:1",))
        self.assertEqual(simplified.simplified_regions, 1)
        self.assertEqual(simplified.faithful_fallback_regions, 0)
        self.assertEqual(
            len(xml.findall("./widget/layout/item/layout/item")),
            6,
        )
        self.assertFalse(
            any(
                name.startswith("rc2uiFont")
                for name in (
                    widget.get("name", "")
                    for widget in xml.findall(".//widget")
                )
            )
        )
        spacer_names = [
            spacer.get("name", "") for spacer in xml.findall(".//spacer")
        ]
        self.assertEqual(len(spacer_names), 1)
        self.assertTrue(spacer_names[0].endswith("ExtentMarker"))
        self.assertFalse(
            any(name.startswith("fontMinimum") for name in spacer_names)
        )

    def test_gapped_label_editor_rows_use_scalable_form_grid(self) -> None:
        faithful = build(
            make_dialog(
                [
                    ("Static", "&One:", 0, RectDlu(5, 5, 35, 8)),
                    ("Edit", "", 0, RectDlu(50, 3, 80, 14)),
                    ("Static", "&Two:", 0, RectDlu(5, 25, 35, 8)),
                    ("Edit", "", 0, RectDlu(50, 23, 80, 14)),
                    ("Static", "&Three:", 0, RectDlu(5, 45, 35, 8)),
                    ("Edit", "", 0, RectDlu(50, 43, 80, 14)),
                ]
            )
        )

        simplified = simplify_form(faithful.root_widget)
        form_grid = content_layout(simplified.root_widget)

        self.assertEqual(form_grid.class_name, "QGridLayout")
        self.assertEqual(len(form_grid.stretch), 3)
        self.assertEqual(
            simplified.transformations,
            ("grid-to-form-grid:1",),
        )

    def test_horizontal_button_row_becomes_hbox(self) -> None:
        faithful = build(
            make_dialog(
                [
                    ("Button", "One", 0, RectDlu(5, 5, 40, 14)),
                    ("Button", "Two", 0, RectDlu(55, 5, 40, 14)),
                    ("Button", "Three", 0, RectDlu(105, 5, 40, 14)),
                ]
            )
        )

        simplified = simplify_form(faithful.root_widget)

        self.assertEqual(
            content_layout(simplified.root_widget).class_name,
            "QHBoxLayout",
        )
        self.assertEqual(simplified.transformations, ("grid-to-hbox:1",))

    def test_vertical_control_list_becomes_vbox(self) -> None:
        faithful = build(
            make_dialog(
                [
                    ("Button", "One", 0, RectDlu(5, 5, 40, 14)),
                    ("Button", "Two", 0, RectDlu(5, 25, 40, 14)),
                    ("Button", "Three", 0, RectDlu(5, 45, 40, 14)),
                ]
            )
        )

        simplified = simplify_form(faithful.root_widget)

        self.assertEqual(
            content_layout(simplified.root_widget).class_name,
            "QVBoxLayout",
        )
        self.assertEqual(simplified.transformations, ("grid-to-vbox:1",))

    def test_coordinate_matrix_becomes_compact_grid(self) -> None:
        faithful = build(
            make_dialog(
                [
                    ("Button", "A", 0, RectDlu(5, 5, 40, 14)),
                    ("Button", "B", 0, RectDlu(55, 5, 40, 14)),
                    ("Button", "C", 0, RectDlu(5, 25, 40, 14)),
                    ("Button", "D", 0, RectDlu(55, 25, 40, 14)),
                ]
            )
        )

        simplified = simplify_form(faithful.root_widget)

        self.assertEqual(simplified.root_widget.layout.class_name, "QGridLayout")
        compact = content_layout(simplified.root_widget)
        self.assertEqual(len(compact.row_stretch), 3)
        self.assertEqual(len(compact.stretch), 3)
        self.assertLess(compact.row_stretch[1], compact.row_stretch[0])
        self.assertLess(compact.stretch[1], compact.stretch[0])
        self.assertEqual(
            simplified.transformations,
            ("coordinate-to-compact-grid:1",),
        )

    def test_partial_overlap_keeps_faithful_region(self) -> None:
        faithful = build(
            make_dialog(
                [
                    ("Edit", "", 0, RectDlu(7, 8, 100, 14)),
                    ("Edit", "", 0, RectDlu(82, 8, 80, 14)),
                ]
            )
        )

        simplified = simplify_form(faithful.root_widget)

        self.assertEqual(simplified.simplified_regions, 1)
        self.assertEqual(simplified.faithful_fallback_regions, 1)
        self.assertEqual(
            simplified.transformations,
            ("faithful-grid-cleanup:1",),
        )
        self.assertEqual(
            simplified.root_widget.layout.stretch,
            faithful.root_widget.layout.stretch,
        )

    def test_nested_group_is_simplified_independently(self) -> None:
        faithful = build(
            make_dialog(
                [
                    ("Button", "Settings", 7, RectDlu(5, 5, 160, 68)),
                    ("Static", "&One:", 0, RectDlu(15, 20, 35, 8)),
                    ("Edit", "", 0, RectDlu(60, 18, 85, 14)),
                    ("Static", "&Two:", 0, RectDlu(15, 42, 35, 8)),
                    ("Edit", "", 0, RectDlu(60, 40, 85, 14)),
                ]
            )
        )

        simplified = simplify_form(faithful.root_widget)
        group = next(
            item.widget
            for item in simplified.root_widget.layout.items
            if item.widget is not None and item.widget.class_name == "QGroupBox"
        )

        self.assertEqual(content_layout(group).class_name, "QGridLayout")
        self.assertEqual(simplified.simplified_regions, 2)
        self.assertEqual(simplified.faithful_fallback_regions, 1)

    def test_generated_layout_names_avoid_existing_object_names(self) -> None:
        faithful = build(
            make_dialog(
                [
                    ("Button", "One", 0, RectDlu(5, 5, 40, 14)),
                    ("Button", "Two", 0, RectDlu(55, 5, 40, 14)),
                ]
            )
        )
        root = replace(
            faithful.root_widget,
            children=(
                QtWidget("QWidget", "geometryGridLayoutContent"),
            ),
        )

        simplified = simplify_form(root)
        text = emit_ui(simplified.root_widget)

        validate_ui_xml(text)
        self.assertEqual(
            content_layout(simplified.root_widget).object_name,
            "geometryGridLayoutContent2",
        )

    @unittest.skipUnless(
        discover_qt_binding().available,
        "Qt 6 binding is not installed",
    )
    def test_simplified_form_survives_resize_and_dynamic_font_change(self) -> None:
        faithful = build(
            make_dialog(
                [
                    ("Static", "&First value:", 0, RectDlu(5, 5, 35, 8)),
                    ("Edit", "", 0, RectDlu(50, 3, 80, 14)),
                    ("Static", "&Second value:", 0, RectDlu(5, 25, 35, 8)),
                    ("Edit", "", 0, RectDlu(50, 23, 80, 14)),
                    ("Static", "&Third value:", 0, RectDlu(5, 45, 35, 8)),
                    ("Edit", "", 0, RectDlu(50, 43, 80, 14)),
                ]
            )
        )
        simplified = simplify_form(faithful.root_widget)

        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            ui = root / "simplified.ui"
            report = root / "qt-report.json"
            ui.write_text(emit_ui(simplified.root_widget), encoding="utf-8")
            result = run_qt_checks(
                (ui,),
                report_path=report,
                required=True,
                font_scale=1.5,
            )
            payload = json.loads(report.read_text(encoding="utf-8"))

        self.assertFalse(
            any(
                diagnostic.severity is Severity.ERROR
                for diagnostic in result.diagnostics
            )
        )
        font_test = payload["forms"][0]["font_test"]
        self.assertTrue(font_test["passed"])
        self.assertGreater(
            font_test["font_point_size_after"],
            font_test["font_point_size_before"],
        )
        self.assertGreaterEqual(
            font_test["form_size_after"][0],
            font_test["form_size_before"][0],
        )
        self.assertGreater(
            font_test["form_size_after"][1],
            font_test["form_size_before"][1],
        )


if __name__ == "__main__":
    unittest.main()

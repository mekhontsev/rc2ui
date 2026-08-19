from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from rc2ui.domain.diagnostics import Severity
from rc2ui.domain.geometry import RectDlu
from rc2ui.layout.infer import LayoutBuilder
from rc2ui.layout.simplify import editability_score, simplify_form
from rc2ui.mapping.controls import ControlMapper
from rc2ui.naming.resolver import NameResolver
from rc2ui.qt.emitter import emit_ui
from rc2ui.qt.model import QtWidget
from rc2ui.qtcheck.discovery import discover_qt_binding
from rc2ui.qtcheck.model import (
    ControlGeometryReference,
    FormGeometryReference,
)
from rc2ui.qtcheck.runner import run_qt_checks
from rc2ui.validation.ui_xml import validate_ui_xml
from tests.test_layout_and_emitter import build, dense_multiline_dialog, make_dialog


def content_layout(widget):
    return next(
        item.layout
        for item in widget.layout.items
        if item.layout is not None
    )


def simplified_case(dialog):
    mapped = tuple(ControlMapper().map(control) for control in dialog.controls)
    naming = NameResolver().resolve(dialog, mapped)
    faithful = LayoutBuilder().build(dialog, mapped, naming)
    simplified = simplify_form(faithful.root_widget)
    reference = FormGeometryReference(
        rect_dlu=(
            dialog.rect.x,
            dialog.rect.y,
            dialog.rect.width,
            dialog.rect.height,
        ),
        layout_rect_dlu=(
            faithful.layout_bounds.x,
            faithful.layout_bounds.y,
            faithful.layout_bounds.width,
            faithful.layout_bounds.height,
        ),
        controls=tuple(
            ControlGeometryReference(
                object_name=naming.for_order(control.order).object_name,
                rect_dlu=(
                    control.rect.x,
                    control.rect.y,
                    control.rect.width,
                    control.rect.height,
                ),
                layout_rect_dlu=(
                    faithful.rect_for(control.order).x,
                    faithful.rect_for(control.order).y,
                    faithful.rect_for(control.order).width,
                    faithful.rect_for(control.order).height,
                ),
                qt_class=mapped[control.order].qt_class,
                horizontal_anchor=faithful.anchors_for(control.order)[0],
                vertical_anchor=faithful.anchors_for(control.order)[1],
            )
            for control in dialog.controls
        ),
    )
    return simplified, reference


def separator_panel_dialog():
    """Obfuscated regression for aligned side panels and a button footer."""

    hidden_checkbox = 0x40000000 | 0x00002003
    specs = [
        ("ComboBox", "", 3, RectDlu(6, 11, 108, 20)),
        ("ComboBox", "", 3, RectDlu(123, 11, 74, 20)),
        ("Edit", "", 0, RectDlu(6, 66, 84, 12)),
        ("Button", "Use current value", 3, RectDlu(6, 82, 93, 11)),
        ("Edit", "", 0, RectDlu(110, 66, 75, 12)),
        ("Button", "Fill", 0, RectDlu(110, 81, 86, 12)),
        ("ComboBox", "", 3, RectDlu(6, 107, 126, 20)),
        ("Edit", "", 0, RectDlu(6, 135, 126, 12)),
        ("ComboBox", "", 3, RectDlu(209, 11, 108, 20)),
        ("Button", "Use schedule", 3, RectDlu(212, 29, 82, 10)),
        ("Edit", "", 0, RectDlu(209, 40, 108, 12)),
        ("Edit", "", 0, RectDlu(209, 66, 61, 12)),
        ("Edit", "", 0, RectDlu(272, 66, 45, 12)),
        ("Button", "Set value", 0, RectDlu(209, 81, 108, 12)),
        ("Edit", "", 0, RectDlu(209, 107, 108, 12)),
        (
            "Button",
            "Optional panel setting",
            hidden_checkbox,
            RectDlu(209, 127, 91, 20),
        ),
        ("Button", "OK", 1, RectDlu(38, 159, 36, 14)),
        ("Button", "Back", 0, RectDlu(78, 159, 37, 14)),
        ("Button", "Cancel", 0, RectDlu(119, 159, 36, 14)),
        ("Button", "More", 0, RectDlu(185, 159, 24, 14)),
        # Container declarations deliberately follow their children, as they
        # often do in hand-authored RC files.
        ("Button", "Source", 7, RectDlu(3, 1, 114, 27)),
        ("Button", "Account", 7, RectDlu(119, 1, 81, 27)),
        ("Button", "Price", 7, RectDlu(3, 56, 102, 41)),
        ("Button", "Quantity", 7, RectDlu(106, 56, 94, 41)),
        ("Button", "Client", 7, RectDlu(3, 97, 132, 27)),
        ("Button", "Comment", 7, RectDlu(3, 125, 132, 27)),
        ("Button", "Execution", 7, RectDlu(205, 1, 115, 27)),
        ("Button", "", 7, RectDlu(205, 28, 115, 27)),
        ("Button", "Value", 7, RectDlu(205, 56, 115, 41)),
        ("Button", "Limit", 7, RectDlu(205, 97, 115, 27)),
        ("Static", "", 0x10, RectDlu(0, 155, 361, 1)),
        ("Static", "", 0x11, RectDlu(202, 0, 1, 156)),
    ]
    return replace(
        make_dialog(specs),
        rect=RectDlu(0, 0, 323, 177),
        caption="Panel sample",
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
        self.assertEqual(
            [
                widget.get("name", "")
                for widget in xml.findall(".//widget")
                if widget.get("name", "").startswith("rc2uiFont")
            ],
            ["rc2uiFontWidthRuler"],
        )
        spacer_names = [
            spacer.get("name", "") for spacer in xml.findall(".//spacer")
        ]
        self.assertEqual(len(spacer_names), 1)
        self.assertTrue(spacer_names[0].endswith("ExtentMarker"))
        self.assertFalse(
            any(name.startswith("fontMinimum") for name in spacer_names)
        )

    def test_gapped_label_editor_rows_use_coarse_form_grid(self) -> None:
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
        self.assertEqual(
            content_layout(simplified.root_widget).class_name,
            "QGridLayout",
        )
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
        xml = ET.fromstring(emit_ui(simplified.root_widget))

        self.assertEqual(
            content_layout(simplified.root_widget).class_name,
            "QGridLayout",
        )
        self.assertEqual(
            max(
                len(layout.get("columnstretch", "").split(","))
                for layout in xml.findall(
                    ".//layout[@class='QGridLayout'][@columnstretch]"
                )
            ),
            3,
        )
        self.assertEqual(
            simplified.transformations,
            ("coordinate-to-compact-grid:1",),
        )

    def test_diagonal_controls_keep_proportional_grid_tracks(self) -> None:
        faithful = build(
            make_dialog(
                [
                    (
                        "Button",
                        f"Action {index}",
                        0,
                        RectDlu(5 + index * 8, 5 + index * 20, 40, 14),
                    )
                    for index in range(8)
                ]
            )
        )

        simplified = simplify_form(faithful.root_widget)
        xml = ET.fromstring(emit_ui(simplified.root_widget))

        self.assertEqual(
            content_layout(simplified.root_widget).class_name,
            "QGridLayout",
        )
        self.assertEqual(
            simplified.transformations,
            ("coordinate-to-compact-grid:1",),
        )
        content = xml.find(
            ".//layout[@name='geometryGridLayoutContent2']"
        )
        self.assertIsNotNone(content)
        self.assertEqual(len(content.get("columnstretch", "").split(",")), 15)
        self.assertEqual(len(content.get("rowstretch", "").split(",")), 15)

    def test_wide_row_keeps_proportional_layout_stretch(self) -> None:
        faithful = build(
            make_dialog(
                [
                    (
                        "Button",
                        f"Action {index}",
                        0,
                        RectDlu(5 + index * 45, 5, 40, 14),
                    )
                    for index in range(6)
                ]
            )
        )

        simplified = simplify_form(faithful.root_widget)
        xml = ET.fromstring(emit_ui(simplified.root_widget))
        rows = xml.findall(".//layout[@class='QHBoxLayout']")

        self.assertTrue(rows)
        self.assertEqual(
            rows[0].get("stretch"),
            "40,5,40,5,40,5,40,5,40,5,40",
        )

    def test_tall_pane_keeps_compact_grid_when_slicing_is_unsafe(self) -> None:
        dialog = replace(
            make_dialog(
                [("ListBox", "", 0, RectDlu(5, 6, 80, 77))]
                + [
                    (
                        "Static",
                        f"Field {row}:",
                        0,
                        RectDlu(98, 8 + row * 21, 45, 8),
                    )
                    for row in range(4)
                ]
                + [
                    (
                        "Edit",
                        "",
                        0,
                        RectDlu(148, 5 + row * 21, 90, 14),
                    )
                    for row in range(4)
                ]
            ),
            rect=RectDlu(0, 0, 245, 95),
        )

        simplified = simplify_form(build(dialog).root_widget)
        xml = ET.fromstring(emit_ui(simplified.root_widget))

        self.assertEqual(
            simplified.transformations,
            ("coordinate-to-compact-grid:1",),
        )
        content = content_layout(simplified.root_widget)
        self.assertEqual(content.class_name, "QGridLayout")
        self.assertLessEqual(len(content.stretch), 5)
        self.assertLessEqual(len(content.row_stretch), 9)
        self.assertEqual(
            len(xml.findall(".//widget[@class='QLineEdit']")),
            4,
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
        text = emit_ui(simplified.root_widget)
        self.assertIn("rc2uiFontWidthRuler", text)
        self.assertNotIn("rc2uiFontHeightRuler", text)
        self.assertNotIn("fontMinimum", text)
        self.assertNotIn("trailingHorizontalSpacer", text)
        self.assertNotIn("trailingVerticalSpacer", text)

    def test_complex_form_root_becomes_editable_vertical_bands(self) -> None:
        faithful = build(dense_multiline_dialog())

        simplified = simplify_form(faithful.root_widget)
        xml = ET.fromstring(emit_ui(simplified.root_widget))

        self.assertEqual(simplified.root_widget.layout.class_name, "QVBoxLayout")
        self.assertTrue(simplified.root_widget.layout.stretch)
        self.assertEqual(
            len(simplified.root_widget.layout.stretch),
            len(simplified.root_widget.layout.items) - 1,
        )
        self.assertIn("grid-to-vertical-bands:1", simplified.transformations)
        self.assertFalse(
            any(
                attribute in item.attrib
                for item in xml.findall("./widget/layout/item")
                for attribute in ("row", "column", "rowspan", "colspan")
            )
        )
        internal_widgets = [
            widget.get("name")
            for widget in xml.findall(".//widget")
            if widget.find("./property[@name='rc2uiInternal']") is not None
        ]
        self.assertEqual(internal_widgets, ["rc2uiFontWidthRuler"])
        root_layout = xml.find("./widget/layout")
        self.assertIsNotNone(root_layout)
        self.assertEqual(
            len(root_layout.get("stretch", "").split(",")),
            len(simplified.root_widget.layout.items) - 1,
        )
        self.assertGreater(
            simplified.editability_score,
            editability_score(faithful.root_widget),
        )
        checkbox_policy = xml.find(
            ".//widget[@name="
            "'preserveItemsDuringProcessingOperationCheckBox']"
            "/property[@name='sizePolicy']/sizepolicy"
        )
        self.assertIsNotNone(checkbox_policy)
        self.assertEqual(checkbox_policy.get("hsizetype"), "Minimum")

    def test_perpendicular_separators_create_coarse_shared_panel_rows(
        self,
    ) -> None:
        dialog = separator_panel_dialog()
        simplified, reference = simplified_case(dialog)
        xml = ET.fromstring(emit_ui(simplified.root_widget))

        self.assertIn(
            "grid-to-separator-panels:1",
            simplified.transformations,
        )
        self.assertEqual(simplified.root_widget.layout.class_name, "QVBoxLayout")
        top = simplified.root_widget.layout.items[0].layout
        self.assertIsNotNone(top)
        self.assertEqual(top.class_name, "QGridLayout")
        self.assertEqual(len(top.stretch), 3)
        self.assertLessEqual(len(top.row_stretch), 9)
        self.assertGreater(
            simplified.editability_score,
            editability_score(build(dialog).root_widget),
        )

        emitted_names = {
            widget.get("name") for widget in xml.findall(".//widget")
        }
        self.assertTrue(
            {
                control.object_name for control in reference.controls
            }.issubset(emitted_names)
        )
        checkboxes = xml.findall(".//widget[@class='QCheckBox']")
        self.assertEqual(len(checkboxes), 3)
        hidden = next(
            checkbox
            for checkbox in checkboxes
            if checkbox.findtext("./property[@name='visible']/bool") == "false"
        )
        self.assertEqual(
            " ".join(
                hidden.findtext("./property[@name='text']/string").split()
            ),
            "Optional panel setting",
        )
        self.assertEqual(
            hidden.findtext("./property[@name='visible']/bool"),
            "false",
        )

        stretch_lengths = [
            len(layout.get(attribute, "").split(","))
            for layout in xml.findall(".//layout")
            for attribute in ("columnstretch", "rowstretch")
            if layout.get(attribute)
        ]
        self.assertLessEqual(max(stretch_lengths), 9)

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

    @unittest.skipUnless(
        discover_qt_binding().available,
        "Qt 6 binding is not installed",
    )
    def test_dense_simplified_form_grows_with_dynamic_font(self) -> None:
        simplified, reference = simplified_case(dense_multiline_dialog())

        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            ui = root / "dense.ui"
            report = root / "qt-report.json"
            ui.write_text(emit_ui(simplified.root_widget), encoding="utf-8")
            result = run_qt_checks(
                (ui,),
                report_path=report,
                required=True,
                geometry_references={ui: reference},
            )
            payload = json.loads(report.read_text(encoding="utf-8"))

        font_test = payload["forms"][0]["font_test"]
        self.assertTrue(result.available)
        self.assertTrue(font_test["passed"])
        self.assertGreater(
            font_test["form_size_after"][0],
            font_test["form_size_before"][0] * 1.75,
        )
        self.assertFalse(
            any(
                diagnostic["code"]
                in {
                    "qt.font-height-clipped",
                    "qt.font-width-clipped",
                    "qt.source-gap-static",
                    "qt.source-order-changed",
                }
                for diagnostic in payload["forms"][0]["diagnostics"]
            )
        )

    @unittest.skipUnless(
        discover_qt_binding().available,
        "Qt 6 binding is not installed",
    )
    def test_simplified_degradation_corpus_keeps_gaps_and_order(self) -> None:
        cases = {
            "long_row": replace(
                make_dialog(
                    [
                        (
                            "Button",
                            f"Action {index}",
                            0,
                            RectDlu(5 + index * 45, 8, 40, 14),
                        )
                        for index in range(6)
                    ]
                ),
                rect=RectDlu(0, 0, 280, 45),
            ),
            "matrix": replace(
                make_dialog(
                    [
                        (
                            "Button",
                            f"Cell {row} {column}",
                            0,
                            RectDlu(
                                5 + column * 40,
                                6 + row * 21,
                                35,
                                14,
                            ),
                        )
                        for row in range(4)
                        for column in range(6)
                    ]
                ),
                rect=RectDlu(0, 0, 245, 92),
            ),
            "form": replace(
                make_dialog(
                    [
                        spec
                        for row in range(10)
                        for spec in (
                            (
                                "Static",
                                f"Value {row}:",
                                0,
                                RectDlu(5, 7 + row * 20, 52, 8),
                            ),
                            (
                                "Edit",
                                "",
                                0,
                                RectDlu(64, 4 + row * 20, 110, 14),
                            ),
                        )
                    ]
                ),
                rect=RectDlu(0, 0, 185, 210),
            ),
            "tall_pane": replace(
                make_dialog(
                    [("ListBox", "", 0, RectDlu(5, 6, 80, 77))]
                    + [
                        (
                            "Static",
                            f"Field {row}:",
                            0,
                            RectDlu(98, 8 + row * 21, 45, 8),
                        )
                        for row in range(4)
                    ]
                    + [
                        (
                            "Edit",
                            "",
                            0,
                            RectDlu(148, 5 + row * 21, 90, 14),
                        )
                        for row in range(4)
                    ]
                ),
                rect=RectDlu(0, 0, 245, 95),
            ),
        }
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            paths: list[Path] = []
            references: dict[Path, FormGeometryReference] = {}
            for name, dialog in cases.items():
                simplified, reference = simplified_case(dialog)
                path = root / f"{name}.ui"
                path.write_text(
                    emit_ui(simplified.root_widget),
                    encoding="utf-8",
                )
                paths.append(path)
                references[path] = reference
            report_path = root / "qt-report.json"
            run_qt_checks(
                tuple(paths),
                report_path=report_path,
                required=True,
                geometry_references=references,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        degradation_codes = {
            "qt.font-height-clipped",
            "qt.font-order-changed",
            "qt.font-width-clipped",
            "qt.source-gap-affinity-changed",
            "qt.source-gap-shrunk",
            "qt.source-gap-static",
            "qt.source-order-changed",
            "qt.unexpected-overlap",
        }
        self.assertFalse(
            [
                (Path(form["path"]).stem, diagnostic["code"])
                for form in report["forms"]
                for diagnostic in form["diagnostics"]
                if diagnostic["code"] in degradation_codes
            ]
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from math import ceil
from pathlib import PurePosixPath

from rc2ui.analysis.multilingual import (
    MultilingualLayoutHints,
    PairRelationHint,
    ParentRelationHint,
    fuse_dialog_languages,
)
from rc2ui.domain.dialog import (
    Control,
    ControlKey,
    Dialog,
    DialogFont,
    DialogKey,
)
from rc2ui.domain.geometry import RectDlu
from rc2ui.domain.resource_id import ResourceId
from rc2ui.layout.infer import LayoutBuilder, _layout_topology_diagnostics
from rc2ui.mapping.controls import ControlMapper
from rc2ui.naming.resolver import NameResolver
from rc2ui.qt.emitter import emit_ui
from rc2ui.qt.model import QtProperty, QtString, QtWidget
from rc2ui.translations.form import prepare_localized_form
from rc2ui.validation.ui_xml import validate_ui_xml
from tests.test_mapping_and_naming import sample_dialog


def make_dialog(specs: list[tuple[str, str, int, RectDlu]]) -> Dialog:
    key = DialogKey(
        PurePosixPath("main.rc"),
        ResourceId.from_ordinal(100, "IDD_SAMPLE"),
        1033,
    )
    controls = tuple(
        Control(
            key=ControlKey(key, ResourceId.from_ordinal(1000 + order), 1),
            class_name=class_name,
            text=text,
            rect=rect,
            style=style,
            extended_style=0,
            order=order,
        )
        for order, (class_name, text, style, rect) in enumerate(specs)
    )
    return Dialog(key, "Sample", RectDlu(0, 0, 180, 80), 0, 0, controls)


def dense_multiline_dialog() -> Dialog:
    """Obfuscated geometry regression derived from a real dense RC form."""

    group = 7
    combo = 3
    multiline = 0x2000
    specs = [
        ("Button", "Primary source", group, RectDlu(3, 2, 132, 26)),
        ("ComboBox", "", combo, RectDlu(7, 12, 124, 79)),
        ("Button", "Secondary source", group, RectDlu(137, 2, 100, 26)),
        ("ComboBox", "", combo, RectDlu(142, 12, 92, 79)),
        ("Button", "Mode", group, RectDlu(3, 31, 165, 25)),
        ("Button", "Choice alpha", 9, RectDlu(7, 41, 75, 12)),
        ("Button", "Choice beta", 9, RectDlu(89, 41, 75, 12)),
        ("Button", "Code", group, RectDlu(3, 59, 66, 25)),
        ("ComboBox", "", combo, RectDlu(7, 69, 58, 91)),
        ("Button", "Date", group, RectDlu(71, 59, 62, 25)),
        ("Static", "-", 1, RectDlu(75, 69, 54, 12)),
        (
            "Button",
            "Preserve items during processing operation",
            3 | multiline,
            RectDlu(138, 58, 99, 30),
        ),
        ("Button", "Amount", group, RectDlu(3, 87, 91, 25)),
        ("Edit", "", 0, RectDlu(6, 97, 86, 12)),
        ("Button", "Quantity", group, RectDlu(96, 87, 72, 25)),
        ("Edit", "", 0, RectDlu(98, 97, 58, 12)),
        (
            "msctls_updown32",
            "Spin",
            0x00B0,
            RectDlu(156, 97, 10, 12),
        ),
        ("Button", "Unit", group, RectDlu(172, 87, 66, 25)),
        ("Static", "100", 1, RectDlu(176, 97, 58, 12)),
        ("Button", "Three related values", group, RectDlu(3, 115, 235, 25)),
        ("Edit", "", 0, RectDlu(7, 125, 72, 12)),
        ("Edit", "", 0, RectDlu(85, 125, 72, 12)),
        ("Edit", "", 0, RectDlu(163, 125, 72, 12)),
        ("Button", "Rate", group, RectDlu(3, 143, 77, 25)),
        ("Edit", "", 0, RectDlu(7, 153, 70, 12)),
        ("Button", "Term", group, RectDlu(82, 143, 77, 25)),
        ("Edit", "", 0, RectDlu(86, 153, 70, 12)),
        ("Button", "Refund", group, RectDlu(161, 143, 77, 25)),
        ("Edit", "", 0, RectDlu(165, 153, 70, 12)),
        (
            "Button",
            "Use open schedule",
            3 | multiline,
            RectDlu(7, 171, 74, 11),
        ),
        ("Static", "Execution period", 0, RectDlu(24, 185, 88, 11)),
        ("Edit", "", 0, RectDlu(114, 183, 31, 12)),
        ("Button", "Benchmark", group, RectDlu(3, 198, 234, 26)),
        ("ComboBox", "", combo, RectDlu(7, 209, 227, 79)),
        ("Button", "Partner", group, RectDlu(3, 229, 235, 25)),
        ("ComboBox", "", combo, RectDlu(7, 239, 228, 90)),
        ("Button", "Client code", group, RectDlu(3, 257, 77, 25)),
        ("ComboBox", "", combo, RectDlu(6, 267, 70, 91)),
        ("Button", "Comment", group, RectDlu(82, 257, 77, 25)),
        ("Edit", "", 0, RectDlu(85, 267, 70, 12)),
        ("Button", "Adjustment", group, RectDlu(161, 257, 77, 25)),
        ("Edit", "", 0, RectDlu(165, 267, 70, 12)),
        ("Static", "", 0x10, RectDlu(1, 288, 238, 1)),
        ("Button", "Apply", 1, RectDlu(65, 294, 55, 14)),
        ("Button", "Cancel", 0, RectDlu(130, 294, 39, 14)),
    ]
    return replace(
        make_dialog(specs),
        rect=RectDlu(0, 0, 240, 314),
        caption="Sample operation",
        font=DialogFont(9, "Arial"),
    )


def build(dialog: Dialog, hints: MultilingualLayoutHints | None = None):
    mapper = ControlMapper()
    mapped = tuple(mapper.map(control) for control in dialog.controls)
    naming = NameResolver().resolve(dialog, mapped)
    return LayoutBuilder().build(dialog, mapped, naming, hints)


def source_widget_items(layout):
    return [
        item
        for item in layout.items
        if item.widget is not None
        and not any(
            property_.name == "rc2uiInternal" and property_.value is True
            for property_ in item.widget.properties
        )
    ]


class LayoutAndEmitterTests(unittest.TestCase):
    def test_ui_comments_can_be_omitted_without_changing_string_values(self) -> None:
        widget = QtWidget(
            "QDialog",
            "sampleDialog",
            properties=(
                QtProperty(
                    "windowTitle",
                    QtString(
                        "Sample",
                        comment="stable-key",
                        extra_comment="source note",
                    ),
                ),
            ),
        )

        annotated = ET.fromstring(emit_ui(widget))
        clean = ET.fromstring(emit_ui(widget, include_comments=False))
        annotated_string = annotated.find(".//string")
        clean_string = clean.find(".//string")

        self.assertEqual(annotated_string.get("comment"), "stable-key")
        self.assertEqual(annotated_string.get("extracomment"), "source note")
        self.assertEqual(clean_string.text, "Sample")
        self.assertNotIn("comment", clean_string.attrib)
        self.assertNotIn("extracomment", clean_string.attrib)
    def test_multilingual_hints_recover_rows_columns_and_group_parent(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Group", 7, RectDlu(5, 5, 95, 65)),
                ("Button", "One", 0, RectDlu(15, 20, 30, 14)),
                ("Button", "Two", 0, RectDlu(58, 29, 30, 14)),
                ("Button", "Three", 0, RectDlu(19, 48, 30, 14)),
                ("Button", "Four", 0, RectDlu(62, 48, 30, 14)),
            ]
        )
        languages = (1033, 1049, 1031)
        hints = MultilingualLayoutHints(
            parents=tuple(
                ParentRelationHint(order, 0, 1.0, languages, languages)
                for order in range(1, 5)
            ),
            same_rows=(
                PairRelationHint((1, 2), 2 / 3, (1049, 1031), languages),
                PairRelationHint((3, 4), 1.0, languages, languages),
            ),
            same_columns=(
                PairRelationHint((1, 3), 2 / 3, (1049, 1031), languages),
                PairRelationHint((2, 4), 2 / 3, (1049, 1031), languages),
            ),
        )

        result = build(dialog, hints)

        group = result.root_widget.layout.items[0].widget
        self.assertEqual(group.class_name, "QGroupBox")
        self.assertEqual(group.layout.class_name, "QGridLayout")
        widget_items = [
            item for item in group.layout.items if item.widget is not None
        ]
        self.assertEqual(widget_items[0].row, widget_items[1].row)
        self.assertEqual(widget_items[2].row, widget_items[3].row)
        self.assertLess(widget_items[0].row, widget_items[2].row)
        self.assertEqual(widget_items[0].column, widget_items[2].column)
        self.assertEqual(widget_items[1].column, widget_items[3].column)

    def test_multilingual_hint_can_force_or_reject_runtime_alternative(self) -> None:
        dialog = make_dialog(
            [
                ("Edit", "", 0, RectDlu(57, 8, 105, 14)),
                ("Button", "One", 0, RectDlu(7, 30, 35, 14)),
                ("Button", "Two", 0, RectDlu(50, 30, 35, 14)),
                ("Button", "Three", 0, RectDlu(93, 30, 35, 14)),
                ("ComboBox", "", 0, RectDlu(62, 11, 96, 13)),
            ]
        )
        languages = (1033, 1049, 1031)
        pair = PairRelationHint((0, 4), 2 / 3, (1049, 1031), languages)

        forced = build(
            dialog,
            MultilingualLayoutHints(alternatives=(pair,)),
        )
        rejected = build(
            dialog,
            MultilingualLayoutHints(rejected_alternatives=(pair,)),
        )

        self.assertIn(
            "layout.runtime-alternatives",
            [item.code for item in forced.diagnostics],
        )
        self.assertNotIn(
            "layout.runtime-alternatives",
            [item.code for item in rejected.diagnostics],
        )

    def test_emits_geometry_grid_and_label_buddy_without_child_geometry(self) -> None:
        result = build(sample_dialog())

        text = emit_ui(result.root_widget)
        validate_ui_xml(text)
        xml = ET.fromstring(text)

        self.assertEqual(xml.find("./widget/layout").get("class"), "QGridLayout")
        layout_element = xml.find("./widget/layout")
        self.assertEqual(
            layout_element.get("columnminimumwidth"),
            layout_element.get("columnstretch"),
        )
        self.assertEqual(
            layout_element.get("rowminimumheight"),
            layout_element.get("rowstretch"),
        )
        for emitted_layout in xml.findall(".//layout"):
            self.assertEqual(
                emitted_layout.findtext(
                    "./property[@name='sizeConstraint']/enum"
                ),
                "QLayout::SetMinimumSize",
            )
        buddy = xml.find(
            ".//widget[@name='userNameLabel']/property[@name='buddy']/cstring"
        )
        self.assertEqual(buddy.text, "userNameEdit")
        self.assertIsNone(
            xml.find(".//widget[@name='userNameEdit']/property[@name='geometry']")
        )
        minimum = xml.find(
            "./widget/property[@name='minimumSize']/size"
        )
        self.assertIsNotNone(minimum)
        geometry = xml.find("./widget/property[@name='geometry']/rect")
        self.assertEqual(minimum.findtext("width"), geometry.findtext("width"))
        self.assertEqual(minimum.findtext("height"), geometry.findtext("height"))
        width_spacer = next(
            spacer
            for spacer in xml.findall(".//spacer")
            if spacer.get("name", "").startswith("fontMinimumWidthSpacer")
        )
        height_spacer = next(
            spacer
            for spacer in xml.findall(".//spacer")
            if spacer.get("name", "").startswith("fontMinimumHeightSpacer")
        )
        self.assertIsNotNone(width_spacer)
        self.assertIsNotNone(height_spacer)
        self.assertEqual(
            width_spacer.findtext("./property[@name='sizeType']/enum"),
            "QSizePolicy::Policy::Minimum",
        )
        self.assertEqual(
            width_spacer.findtext("./property[@name='sizeHint']/size/width"),
            geometry.findtext("width"),
        )
        self.assertEqual(
            height_spacer.findtext("./property[@name='sizeHint']/size/height"),
            geometry.findtext("height"),
        )
        width_ruler = next(
            widget
            for widget in xml.findall(".//widget")
            if widget.get("name", "").startswith("rc2uiFontWidthRuler")
        )
        height_ruler = next(
            widget
            for widget in xml.findall(".//widget")
            if widget.get("name", "").startswith("rc2uiFontHeightRuler")
        )
        self.assertEqual(width_ruler.get("class"), "QLabel")
        self.assertEqual(height_ruler.get("class"), "QLabel")
        self.assertEqual(
            width_ruler.findtext("./property[@name='maximumSize']/size/height"),
            "0",
        )
        self.assertEqual(
            height_ruler.findtext("./property[@name='maximumSize']/size/width"),
            "0",
        )
        self.assertEqual(
            width_ruler.find("./property[@name='text']/string").get("notr"),
            "true",
        )
        self.assertEqual(
            width_ruler.findtext("./property[@name='rc2uiInternal']/bool"),
            "true",
        )
        self.assertEqual(
            width_ruler.find("./property[@name='rc2uiInternal']").get("stdset"),
            "0",
        )

    def test_keeps_an_intentional_one_dlu_gap_as_a_grid_track(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Left", 0, RectDlu(10, 8, 20, 14)),
                ("Button", "Right", 0, RectDlu(31, 8, 20, 14)),
                ("Button", "Unrelated", 0, RectDlu(1, 42, 30, 14)),
            ]
        )

        result = build(dialog)

        layout = result.root_widget.layout
        by_name = {
            item.widget.object_name: item
            for item in layout.items
            if item.widget is not None
        }
        left = by_name["leftButton"]
        right = by_name["rightButton"]
        left_edge = sum(layout.stretch[: left.column + left.column_span])
        right_edge = sum(layout.stretch[: right.column])
        self.assertEqual(right_edge - left_edge, 1)
        self.assertEqual(layout.minimum_widths, layout.stretch)
        self.assertEqual(layout.minimum_heights, layout.row_stretch)

    def test_uses_grid_for_repeated_columns_with_small_human_offsets(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "One", 0, RectDlu(7, 8, 42, 14)),
                ("Button", "Two", 0, RectDlu(60, 8, 42, 14)),
                ("Button", "Three", 0, RectDlu(8, 28, 42, 14)),
                ("Button", "Four", 0, RectDlu(62, 29, 42, 14)),
            ]
        )

        result = build(dialog)

        self.assertEqual(result.root_widget.layout.class_name, "QGridLayout")
        widget_items = [
            item
            for item in result.root_widget.layout.items
            if item.widget is not None
        ]
        self.assertEqual(widget_items[0].row, widget_items[1].row)
        self.assertEqual(widget_items[2].row, widget_items[3].row)
        self.assertEqual(widget_items[0].column, widget_items[2].column)
        self.assertEqual(widget_items[1].column, widget_items[3].column)
        self.assertEqual(result.rect_for(0).left, result.rect_for(2).left)
        self.assertEqual(result.rect_for(1).left, result.rect_for(3).left)

    def test_dialog_screen_position_is_not_a_client_coordinate_origin(self) -> None:
        dialog = replace(
            make_dialog(
                [("ListBox", "", 0, RectDlu(50, 20, 80, 40))]
            ),
            rect=RectDlu(115, 10, 180, 80),
        )

        result = build(dialog)

        layout = result.root_widget.layout
        self.assertEqual(sum(layout.stretch), dialog.rect.width)
        self.assertEqual(sum(layout.row_stretch), dialog.rect.height)
        item = next(item for item in layout.items if item.widget is not None)
        self.assertEqual(sum(layout.stretch[: item.column]), 50)
        self.assertEqual(sum(layout.row_stretch[: item.row]), 20)

    def test_tiny_child_dialog_keeps_its_dlu_aspect_ratio(self) -> None:
        dialog = replace(
            make_dialog(
                [("Static", "Encoding:", 0, RectDlu(1, 0, 18, 12))]
            ),
            rect=RectDlu(50, 50, 20, 15),
        )

        result = build(dialog)

        geometry = next(
            property_.value
            for property_ in result.root_widget.properties
            if property_.name == "geometry"
        )
        self.assertEqual((geometry.width, geometry.height), (35, 28))

    def test_extends_layout_bounds_for_controls_outside_declared_client(self) -> None:
        dialog = replace(
            make_dialog(
                [
                    ("Button", "Inside", 0, RectDlu(10, 10, 50, 14)),
                    ("Button", "Staged", 0, RectDlu(190, 10, 50, 14)),
                ]
            ),
            rect=RectDlu(100, 80, 216, 70),
        )

        result = build(dialog)

        self.assertEqual(result.layout_bounds, RectDlu(0, 0, 240, 70))
        self.assertEqual(sum(result.root_widget.layout.stretch), 240)
        self.assertIn(
            "layout.client-bounds-extended",
            {item.code for item in result.diagnostics},
        )

    def test_parks_a_far_offscreen_runtime_control_outside_the_layout(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Visible", 0, RectDlu(10, 10, 50, 14)),
                ("Button", "Runtime", 0, RectDlu(30093, 30049, 22, 20)),
            ]
        )

        result = build(dialog)

        self.assertEqual(result.layout_bounds, RectDlu(0, 0, 180, 80))
        self.assertEqual(len(result.root_widget.children), 1)
        self.assertEqual(result.root_widget.children[0].object_name, "runtimeButton")
        self.assertIn(
            "layout.offscreen-control-parked",
            {item.code for item in result.diagnostics},
        )
        xml = ET.fromstring(emit_ui(result.root_widget))
        validate_ui_xml(emit_ui(result.root_widget))
        parked = xml.find("./widget/widget[@name='runtimeButton']")
        self.assertIsNotNone(parked)
        self.assertEqual(
            parked.findtext("property[@name='visible']/bool"),
            "false",
        )
        self.assertIsNone(
            xml.find("./widget/layout/item/widget[@name='runtimeButton']")
        )

    def test_tall_single_line_edit_preserves_its_vertical_grid_span(self) -> None:
        dialog = make_dialog(
            [("Edit", "", 0, RectDlu(5, 5, 150, 50))]
        )

        result = build(dialog)

        [item] = source_widget_items(result.root_widget.layout)
        self.assertNotIn("Qt::AlignVCenter", item.alignment or "")
        xml = ET.fromstring(emit_ui(result.root_widget))
        policy = xml.find(
            ".//widget[@class='QLineEdit']"
            "/property[@name='sizePolicy']/sizepolicy"
        )
        self.assertEqual(policy.get("hsizetype"), "Ignored")
        self.assertEqual(policy.get("vsizetype"), "Preferred")

    def test_expanding_vertical_peers_keep_independent_source_spans(self) -> None:
        dialog = replace(
            make_dialog(
                [
                    (
                        "msctls_trackbar32",
                        "",
                        0x0002,
                        RectDlu(8, 30, 30, 50),
                    ),
                    ("Static", "", 0x0011, RectDlu(42, 1, 1, 100)),
                ]
            ),
            rect=RectDlu(0, 0, 50, 105),
        )

        result = build(dialog)

        slider = next(
            item
            for item in result.root_widget.layout.items
            if item.widget is not None and item.widget.class_name == "QSlider"
        )
        occupied_height = sum(
            result.root_widget.layout.row_stretch[
                slider.row : slider.row + slider.row_span
            ]
        )
        self.assertEqual(occupied_height, 50)

    def test_visual_row_uses_one_coherent_anchor_despite_small_offsets(self) -> None:
        dialog = replace(
            make_dialog(
                [
                    ("Static", "", 3, RectDlu(14, 9, 20, 20)),
                    ("Static", "Version", 0, RectDlu(49, 10, 119, 9)),
                    ("Static", "Copyright", 0, RectDlu(49, 20, 119, 8)),
                    ("Button", "OK", 1, RectDlu(195, 6, 30, 11)),
                ]
            ),
            rect=RectDlu(0, 0, 230, 75),
        )

        result = build(dialog)

        self.assertEqual(
            {result.rect_for(order).top for order in (0, 1, 3)},
            {9},
        )
        by_name = {
            item.widget.object_name: item
            for item in result.root_widget.layout.items
            if item.widget is not None
        }
        version_item = next(
            item
            for item in by_name.values()
            if any(
                getattr(property_.value, "value", None) == "Version"
                for property_ in item.widget.properties
            )
        )
        self.assertIn("Qt::AlignTop", version_item.alignment)
        self.assertIn("Qt::AlignTop", by_name["okButton"].alignment)

    def test_tall_control_does_not_merge_top_and_bottom_anchor_rows(self) -> None:
        dialog = replace(
            make_dialog(
                [
                    ("Button", "Tall", 7, RectDlu(130, 5, 100, 80)),
                    ("Button", "Top", 7, RectDlu(5, 5, 115, 30)),
                    ("Edit", "", 0, RectDlu(5, 73, 115, 12)),
                ]
            ),
            rect=RectDlu(0, 0, 240, 100),
        )
        languages = (1033, 1049, 1031)
        hints = MultilingualLayoutHints(
            same_rows=(
                PairRelationHint((0, 1), 1.0, languages, languages),
                PairRelationHint((0, 2), 1.0, languages, languages),
            )
        )

        result = build(dialog, hints)

        self.assertEqual(result.rect_for(0), RectDlu(130, 5, 100, 80))
        self.assertEqual(result.rect_for(1), RectDlu(5, 5, 115, 30))
        self.assertEqual(result.rect_for(2), RectDlu(5, 73, 115, 12))

    def test_multilingual_hint_cannot_merge_third_and_fourth_form_rows(
        self,
    ) -> None:
        specs: list[tuple[str, str, int, RectDlu]] = []
        for row in range(10):
            top = 8 + row * 13
            specs.extend(
                (
                    ("Static", f"Field {row + 1}", 0, RectDlu(8, top + 3, 42, 8)),
                    ("Edit", "", 0, RectDlu(55, top, 100, 14)),
                )
            )
        dialog = replace(
            make_dialog(specs),
            rect=RectDlu(0, 0, 165, 145),
        )
        languages = (1033, 1049, 1031)
        hints = MultilingualLayoutHints(
            same_rows=(
                PairRelationHint((4, 6), 1.0, languages, languages),
                PairRelationHint((5, 7), 1.0, languages, languages),
            )
        )

        result = build(dialog, hints)

        self.assertEqual(result.rect_for(4), specs[4][3])
        self.assertEqual(result.rect_for(5), specs[5][3])
        self.assertEqual(result.rect_for(6), specs[6][3])
        self.assertEqual(result.rect_for(7), specs[7][3])
        items = {
            item.widget.object_name: item
            for item in result.root_widget.layout.items
            if item.widget is not None
        }
        third_rows = {
            items["field3Label"].row,
            items["field3Edit"].row,
        }
        fourth_rows = {
            items["field4Label"].row,
            items["field4Edit"].row,
        }
        self.assertTrue(max(third_rows) < min(fourth_rows))

    def test_group_in_other_pane_cannot_merge_adjacent_form_rows(self) -> None:
        # This is an intentionally anonymised regression model.  The unusual
        # declaration order and the geometry are the relevant RC evidence;
        # no source identifier or user-facing text is retained.
        specs = [
            ("Static", "", 0x11, RectDlu(189, 0, 1, 290)),
            ("Static", "A", 0, RectDlu(193, 54, 46, 8)),
            ("Edit", "", 0, RectDlu(254, 51, 69, 14)),
            ("Static", "C", 0, RectDlu(193, 84, 46, 8)),
            ("Edit", "", 0, RectDlu(254, 81, 69, 14)),
            ("Static", "B", 0, RectDlu(193, 69, 59, 8)),
            ("Edit", "", 0, RectDlu(254, 67, 69, 14)),
            ("Button", "G", 7, RectDlu(4, 58, 183, 25)),
            ("Button", "X", 9, RectDlu(9, 70, 41, 10)),
        ]
        dialog = replace(
            make_dialog(specs),
            rect=RectDlu(0, 0, 326, 313),
        )
        languages = (1033, 1049)
        hints = MultilingualLayoutHints(
            same_rows=tuple(
                PairRelationHint(pair, 1.0, languages, languages)
                for pair in ((1, 2), (3, 4), (5, 6))
            )
        )

        result = build(dialog, hints)

        self.assertLess(
            max(result.rect_for(order).bottom for order in (1, 2)),
            min(result.rect_for(order).top for order in (5, 6)),
        )
        items = {
            item.widget.object_name: item
            for item in result.root_widget.layout.items
            if item.widget is not None
        }
        first = (items["aLabel"], items["aEdit"])
        second = (items["bLabel"], items["bEdit"])
        self.assertLessEqual(
            max(item.row + item.row_span for item in first),
            min(item.row for item in second),
        )

    def test_vertical_separator_partitions_row_alignment_evidence(self) -> None:
        specs = [
            ("Static", "", 0x11, RectDlu(189, 0, 1, 100)),
            ("Static", "A", 0, RectDlu(193, 54, 46, 8)),
            ("Edit", "", 0, RectDlu(254, 51, 69, 14)),
            ("Static", "B", 0, RectDlu(193, 69, 59, 8)),
            ("Edit", "", 0, RectDlu(254, 67, 69, 14)),
            ("Button", "X", 0, RectDlu(4, 58, 183, 25)),
        ]
        dialog = replace(
            make_dialog(specs),
            rect=RectDlu(0, 0, 326, 120),
        )
        languages = (1033, 1049)
        hints = MultilingualLayoutHints(
            same_rows=(
                PairRelationHint((1, 2), 1.0, languages, languages),
                PairRelationHint((3, 4), 1.0, languages, languages),
            )
        )

        result = build(dialog, hints)

        self.assertLess(
            max(result.rect_for(order).bottom for order in (1, 2)),
            min(result.rect_for(order).top for order in (3, 4)),
        )

    def test_tall_peer_cannot_bridge_two_adjacent_rows(self) -> None:
        specs = [
            ("Static", "A", 0, RectDlu(193, 54, 46, 8)),
            ("Edit", "", 0, RectDlu(254, 51, 69, 14)),
            ("Static", "B", 0, RectDlu(193, 69, 59, 8)),
            ("Edit", "", 0, RectDlu(254, 67, 69, 14)),
            ("Button", "X", 0, RectDlu(4, 58, 183, 25)),
        ]
        dialog = replace(
            make_dialog(specs),
            rect=RectDlu(0, 0, 326, 120),
        )
        languages = (1033, 1049)
        hints = MultilingualLayoutHints(
            same_rows=(
                PairRelationHint((0, 1), 1.0, languages, languages),
                PairRelationHint((2, 3), 1.0, languages, languages),
            )
        )

        result = build(dialog, hints)

        self.assertLess(
            max(result.rect_for(order).bottom for order in (0, 1)),
            min(result.rect_for(order).top for order in (2, 3)),
        )

    def test_pre_emission_guard_rejects_collapsed_source_rows(self) -> None:
        dialog = make_dialog(
            [
                ("Edit", "", 0, RectDlu(50, 10, 80, 14)),
                ("Edit", "", 0, RectDlu(50, 30, 80, 14)),
            ]
        )
        mapped = tuple(ControlMapper().map(item) for item in dialog.controls)
        naming = NameResolver().resolve(dialog, mapped)

        diagnostics = _layout_topology_diagnostics(
            dialog,
            mapped,
            naming,
            {
                0: RectDlu(50, 20, 80, 14),
                1: RectDlu(50, 20, 80, 14),
            },
        )

        self.assertEqual(diagnostics[0].code, "layout.topology-changed")

    def test_pre_emission_guard_ignores_order_across_disjoint_panes(self) -> None:
        dialog = make_dialog(
            [
                ("Edit", "", 0, RectDlu(10, 60, 70, 14)),
                ("Edit", "", 0, RectDlu(100, 20, 70, 14)),
            ]
        )
        mapped = tuple(ControlMapper().map(item) for item in dialog.controls)
        naming = NameResolver().resolve(dialog, mapped)

        diagnostics = _layout_topology_diagnostics(
            dialog,
            mapped,
            naming,
            {
                0: RectDlu(10, 20, 70, 14),
                1: RectDlu(100, 60, 70, 14),
            },
        )

        self.assertEqual(diagnostics, ())

    def test_row_anchor_cannot_cross_a_neighbouring_source_row(self) -> None:
        dialog = replace(
            make_dialog(
                [
                    ("Button", "A", 2, RectDlu(12, 30, 29, 10)),
                    ("Edit", "", 0, RectDlu(100, 32, 60, 14)),
                    ("Button", "B", 2, RectDlu(12, 41, 36, 10)),
                ]
            ),
            rect=RectDlu(0, 0, 180, 70),
        )

        result = build(dialog)

        self.assertLessEqual(result.rect_for(0).bottom, result.rect_for(2).top)
        self.assertLessEqual(
            abs(result.rect_for(0).top - dialog.controls[0].rect.top),
            1,
        )

    def test_places_controls_inside_group_box_layout(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Account", 7, RectDlu(5, 5, 168, 55)),
                ("Static", "Name:", 0, RectDlu(12, 20, 42, 8)),
                ("Edit", "", 0, RectDlu(60, 18, 100, 14)),
            ]
        )

        result = build(dialog)

        group = result.root_widget.layout.items[0].widget
        self.assertIsNotNone(group)
        self.assertEqual(group.class_name, "QGroupBox")
        self.assertIsNotNone(group.layout)
        self.assertEqual(group.layout.class_name, "QGridLayout")
        self.assertEqual(
            {property_.name for property_ in group.layout.properties},
            {
                "horizontalSpacing",
                "verticalSpacing",
                "leftMargin",
                "topMargin",
                "rightMargin",
                "bottomMargin",
                "sizeConstraint",
            },
        )

    def test_single_fields_in_peer_groups_keep_top_alignment(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Edit", 7, RectDlu(5, 5, 80, 50)),
                ("Edit", "", 0, RectDlu(12, 20, 65, 14)),
                ("Button", "Combo", 7, RectDlu(90, 5, 80, 50)),
                ("ComboBox", "", 0, RectDlu(97, 20, 65, 13)),
            ]
        )

        result = build(dialog)

        groups = [
            item.widget
            for item in source_widget_items(result.root_widget.layout)
            if item.widget.class_name == "QGroupBox"
        ]
        fields = [source_widget_items(group.layout)[0] for group in groups]
        self.assertEqual(
            [field.widget.class_name for field in fields],
            ["QLineEdit", "QComboBox"],
        )
        self.assertTrue(
            all("Qt::AlignTop" in (field.alignment or "") for field in fields)
        )
        self.assertEqual(
            [group.layout.minimum_heights[0] for group in groups],
            [7, 7],
        )

    def test_peer_group_fields_snap_a_small_cross_container_row_error(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Edit", 7, RectDlu(5, 5, 80, 50)),
                ("Edit", "", 0, RectDlu(12, 20, 65, 14)),
                ("Button", "Combo", 7, RectDlu(90, 5, 80, 50)),
                ("ComboBox", "", 0, RectDlu(97, 21, 65, 14)),
            ]
        )

        result = build(dialog)

        self.assertEqual(result.rect_for(1).top, result.rect_for(3).top)
        self.assertEqual(result.anchors_for(1)[1], result.anchors_for(3)[1])
        groups = [
            item.widget
            for item in source_widget_items(result.root_widget.layout)
            if item.widget.class_name == "QGroupBox"
        ]
        self.assertEqual(
            [group.layout.minimum_heights[0] for group in groups],
            [7, 7],
        )

    def test_bottom_touching_peer_fields_apply_recorded_top_guide(self) -> None:
        dialog = replace(
            make_dialog(
                [
                    ("Button", "Alpha", 7, RectDlu(10, 20, 94, 25)),
                    ("ComboBox", "", 2, RectDlu(12, 31, 90, 91)),
                    ("Button", "Beta", 7, RectDlu(112, 20, 99, 25)),
                    ("Edit", "", 0, RectDlu(114, 31, 95, 12)),
                ]
            ),
            rect=RectDlu(0, 0, 225, 90),
        )

        result = build(dialog)

        groups = [
            item.widget
            for item in source_widget_items(result.root_widget.layout)
            if item.widget.class_name == "QGroupBox"
        ]
        fields = [source_widget_items(group.layout)[0] for group in groups]
        self.assertTrue(
            all("Qt::AlignTop" in (field.alignment or "") for field in fields)
        )
        self.assertEqual(
            [group.layout.row_stretch[0] for group in groups],
            [0, 0],
        )
        self.assertEqual(result.rect_for(1).top, result.rect_for(3).top)

    def test_long_single_line_label_expands_serialized_designer_width(self) -> None:
        dialog = make_dialog(
            [
                (
                    "Static",
                    "A deliberately long single-line field description",
                    0,
                    RectDlu(10, 12, 45, 8),
                ),
                ("Edit", "", 0, RectDlu(65, 10, 100, 14)),
            ]
        )

        result = build(dialog)
        geometry = next(
            property_.value
            for property_ in result.root_widget.properties
            if property_.name == "geometry"
        )

        self.assertGreater(geometry.width, round(dialog.rect.width * 1.75))
        self.assertLessEqual(
            geometry.width,
            ceil(dialog.rect.width * 1.75 * 1.5),
        )

    def test_long_group_title_expands_serialized_designer_width(self) -> None:
        dialog = make_dialog(
            [
                (
                    "Button",
                    "A group title that is much wider than its frame",
                    7,
                    RectDlu(10, 10, 55, 30),
                ),
                ("Edit", "", 0, RectDlu(15, 22, 45, 12)),
            ]
        )

        result = build(dialog)
        geometry = next(
            property_.value
            for property_ in result.root_widget.properties
            if property_.name == "geometry"
        )

        self.assertGreater(geometry.width, round(dialog.rect.width * 1.75))

    def test_long_button_family_text_uses_common_width_reserve(self) -> None:
        for style, expected_class in ((3, "QCheckBox"), (9, "QRadioButton")):
            with self.subTest(expected_class=expected_class):
                dialog = make_dialog(
                    [
                        (
                            "Button",
                            "A long selectable option close to the dialog edge",
                            style,
                            RectDlu(100, 12, 70, 12),
                        ),
                    ]
                )

                result = build(dialog)
                geometry = next(
                    property_.value
                    for property_ in result.root_widget.properties
                    if property_.name == "geometry"
                )
                widget = source_widget_items(result.root_widget.layout)[0].widget

                self.assertEqual(widget.class_name, expected_class)
                self.assertGreater(
                    geometry.width,
                    round(dialog.rect.width * 1.75),
                )

    def test_dense_multiline_button_text_wraps_without_widening_form(self) -> None:
        dialog = dense_multiline_dialog()

        xml = ET.fromstring(emit_ui(build(dialog).root_widget))
        geometry_width = xml.findtext(
            "./widget/property[@name='geometry']/rect/width"
        )
        check_boxes = xml.findall(".//widget[@class='QCheckBox']")
        wrapped = check_boxes[0].findtext("./property[@name='text']/string")
        single_line = check_boxes[1].findtext(
            "./property[@name='text']/string"
        )

        self.assertEqual(geometry_width, "420")
        self.assertEqual(
            wrapped,
            "Preserve items\nduring processing\noperation",
        )
        self.assertEqual(single_line, "Use open schedule")

    def test_multiline_button_translation_is_wrapped_independently(self) -> None:
        def language_dialog(language: int, text: str) -> Dialog:
            dialog = replace(
                make_dialog(
                    [
                        (
                            "Button",
                            text,
                            3 | 0x2000,
                            RectDlu(78, 10, 99, 30),
                        ),
                    ]
                ),
                rect=RectDlu(0, 0, 180, 60),
            )
            key = replace(dialog.key, language=language)
            control = replace(
                dialog.controls[0],
                key=replace(dialog.controls[0].key, dialog=key),
            )
            return replace(dialog, key=key, controls=(control,))

        default = language_dialog(
            1033,
            "Preserve items during processing operation",
        )
        translated = language_dialog(
            1031,
            "Retain resources throughout final handling",
        )
        multilingual = fuse_dialog_languages((default, translated), 1033)
        mapper = ControlMapper()
        mapped = tuple(mapper.map(control) for control in default.controls)
        layout_mapped = tuple(
            mapper.map(control) for control in multilingual.layout_dialog.controls
        )
        naming = NameResolver().resolve(default, mapped)
        layout = LayoutBuilder().build(
            multilingual.layout_dialog,
            layout_mapped,
            naming,
            multilingual.layout_hints,
        )

        localized = prepare_localized_form(
            layout.root_widget,
            multilingual,
            mapped,
            naming,
            form_class="sampleDialog",
            control_map=None,
            ui_path=PurePosixPath("sample.ui"),
        )
        message = next(
            item
            for item in localized.messages
            if item.source.startswith("Preserve")
        )

        self.assertEqual(
            message.source,
            "Preserve items\nduring processing\noperation",
        )
        self.assertEqual(
            message.translation,
            "Retain resources\nthroughout final\nhandling",
        )

    def test_text_width_reserve_is_encoded_in_dynamic_font_ruler(self) -> None:
        dialog = make_dialog(
            [
                (
                    "Button",
                    "A long selectable option close to the dialog edge",
                    3,
                    RectDlu(100, 12, 70, 12),
                ),
            ]
        )

        xml = ET.fromstring(emit_ui(build(dialog).root_widget))
        width_ruler = next(
            widget
            for widget in xml.findall(".//widget")
            if widget.get("name", "").startswith("rc2uiFontWidthRuler")
        )
        ruler_text = width_ruler.findtext(
            "./property[@name='text']/string"
        )

        self.assertGreater(len(ruler_text), dialog.rect.width // 4)

    def test_near_capacity_source_label_gets_cross_toolkit_width_reserve(
        self,
    ) -> None:
        dialog = replace(
            make_dialog(
                [
                    (
                        "Static",
                        "Near-capacity field caption",
                        0,
                        RectDlu(10, 20, 93, 12),
                    ),
                ]
            ),
            rect=RectDlu(0, 0, 240, 100),
        )

        result = build(dialog)
        geometry = next(
            property_.value
            for property_ in result.root_widget.properties
            if property_.name == "geometry"
        )

        self.assertGreater(geometry.width, round(dialog.rect.width * 1.75))

    def test_single_centered_group_child_remains_centered(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Centered", 7, RectDlu(5, 5, 80, 50)),
                ("Edit", "", 0, RectDlu(12, 27, 65, 14)),
            ]
        )

        result = build(dialog)

        group = next(
            item.widget
            for item in source_widget_items(result.root_widget.layout)
            if item.widget.class_name == "QGroupBox"
        )
        field = source_widget_items(group.layout)[0]
        self.assertIn("Qt::AlignVCenter", field.alignment or "")

    def test_large_whitespace_inside_group_remains_elastic(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Outer", 7, RectDlu(0, 0, 170, 75)),
                ("Button", "Inner", 7, RectDlu(7, 30, 156, 20)),
            ]
        )

        result = build(dialog)

        outer = next(
            item.widget
            for item in result.root_widget.layout.items
            if item.widget is not None
        )
        self.assertGreater(outer.layout.row_stretch[0], 0)
        self.assertGreater(outer.layout.row_stretch[-1], 0)

    def test_control_crossing_group_frame_is_not_made_a_child(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Frame", 7, RectDlu(5, 5, 70, 40)),
                ("Button", "Crossing", 0, RectDlu(60, 18, 40, 14)),
            ]
        )

        result = build(dialog)

        root_widgets = [
            item.widget for item in source_widget_items(result.root_widget.layout)
        ]
        self.assertEqual(
            {widget.class_name for widget in root_widgets},
            {"QGroupBox", "QPushButton"},
        )
        group = next(
            widget
            for widget in root_widgets
            if widget.class_name == "QGroupBox"
        )
        self.assertIsNone(group.layout)

    def test_dropdown_combo_height_does_not_eject_it_from_top_group(self) -> None:
        dialog = make_dialog(
            [
                ("ComboBox", "", 3, RectDlu(12, 17, 148, 100)),
                ("Button", "Close", 0, RectDlu(125, 58, 48, 14)),
                # Resource order is z-order, not container order. Resource
                # editors commonly write the group after its child controls.
                ("Button", "Mode", 7, RectDlu(5, 3, 168, 42)),
            ]
        )

        result = build(dialog)

        root_layout = result.root_widget.layout
        top_group = root_layout.items[0].widget
        self.assertIsNotNone(top_group)
        self.assertEqual(top_group.class_name, "QGroupBox")
        combo_items = [
            item
            for item in top_group.layout.items
            if item.widget is not None
            and item.widget.class_name == "QComboBox"
        ]
        self.assertEqual(len(combo_items), 1)
        close_item = next(
            item
            for item in root_layout.items
            if item.widget is not None
            and item.widget.class_name == "QPushButton"
        )
        self.assertGreater(close_item.row, root_layout.items[0].row)
        self.assertEqual(result.anchors_for(1)[0][0], "end")

    def test_coordinates_define_layout_when_all_controls_are_reordered(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Close", 0, RectDlu(125, 62, 48, 14)),
                ("Edit", "", 0, RectDlu(60, 17, 100, 14)),
                ("Button", "Help", 0, RectDlu(70, 62, 48, 14)),
                ("Static", "Account:", 0, RectDlu(12, 20, 42, 8)),
                ("Button", "Account", 7, RectDlu(5, 3, 168, 42)),
            ]
        )

        result = build(dialog)

        root_items = result.root_widget.layout.items
        group = root_items[0].widget
        self.assertEqual(group.class_name, "QGroupBox")
        self.assertEqual(group.layout.class_name, "QGridLayout")
        self.assertEqual(
            {
                item.widget.object_name
                for item in group.layout.items
                if item.widget is not None
            },
            {"accountLabel", "accountEdit"},
        )
        action_items = [
            item
            for item in root_items
            if item.widget is not None
            and item.widget.class_name == "QPushButton"
        ]
        self.assertEqual(
            [item.widget.object_name for item in action_items],
            ["helpButton", "closeButton"],
        )
        self.assertEqual(action_items[0].row, action_items[1].row)
        group_end = root_items[0].column + root_items[0].column_span
        self.assertLessEqual(root_items[0].column, action_items[0].column)
        self.assertGreaterEqual(
            group_end,
            action_items[1].column + action_items[1].column_span,
        )
        self.assertGreater(root_items[0].column_span, 1)

    def test_distant_rows_share_left_and_right_grid_anchors(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Right bottom", 0, RectDlu(120, 58, 50, 14)),
                ("Button", "Left top", 0, RectDlu(7, 8, 30, 14)),
                ("Static", "Section", 0, RectDlu(60, 33, 60, 8)),
                ("Button", "Right top", 0, RectDlu(140, 8, 30, 14)),
                ("Button", "Left bottom", 0, RectDlu(7, 58, 50, 14)),
            ]
        )

        result = build(dialog)

        layout = result.root_widget.layout
        self.assertEqual(layout.class_name, "QGridLayout")
        by_name = {
            item.widget.object_name: item
            for item in layout.items
            if item.widget is not None
        }
        self.assertEqual(
            by_name["leftTopButton"].column,
            by_name["leftBottomButton"].column,
        )
        self.assertEqual(result.anchors_for(1)[0][0], "start")
        self.assertEqual(result.anchors_for(4)[0][0], "start")
        self.assertEqual(
            by_name["rightTopButton"].column
            + by_name["rightTopButton"].column_span,
            by_name["rightBottomButton"].column
            + by_name["rightBottomButton"].column_span,
        )
        self.assertEqual(result.anchors_for(3)[0][0], "end")
        self.assertEqual(result.anchors_for(0)[0][0], "end")

        xml = ET.fromstring(emit_ui(result.root_widget))
        right_items = [
            item
            for item in xml.findall(".//layout[@class='QGridLayout']/item")
            if "Qt::AlignRight" in item.get("alignment", "")
        ]
        # Expanding controls fill their exact source-coordinate spans.  Their
        # common right grid boundary preserves the end anchor without an item
        # alignment flag, which would make Qt collapse them to sizeHint().
        self.assertEqual(right_items, [])

    def test_distant_rows_preserve_shared_horizontal_center(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Wide", 0, RectDlu(60, 58, 60, 14)),
                ("Static", "Unrelated", 0, RectDlu(7, 33, 40, 8)),
                ("Button", "Narrow", 0, RectDlu(75, 8, 30, 14)),
            ]
        )

        result = build(dialog)

        centered = [
            item
            for item in result.root_widget.layout.items
            if item.widget is not None
            and item.widget.class_name == "QPushButton"
        ]
        layout = result.root_widget.layout
        centers2 = [
            sum(layout.stretch[: item.column]) * 2
            + sum(layout.stretch[item.column : item.column + item.column_span])
            for item in centered
        ]
        self.assertEqual(centers2[0], centers2[1])
        self.assertEqual(result.anchors_for(0)[0][0], "center")
        self.assertEqual(result.anchors_for(2)[0][0], "center")

    def test_separator_edge_cannot_outvote_control_center_column(self) -> None:
        dialog = make_dialog(
            [
                ("Static", "Line", 0, RectDlu(4, 7, 60, 8)),
                ("Static", "", 0x10, RectDlu(4, 19, 72, 1)),
                ("Static", "Balance", 0, RectDlu(4, 25, 60, 8)),
                ("msctls_trackbar32", "", 0, RectDlu(15, 35, 40, 17)),
                ("Button", "Mute", 3, RectDlu(4, 56, 55, 12)),
                ("Button", "Advanced", 0, RectDlu(7, 70, 55, 14)),
            ]
        )

        result = build(dialog)

        control_orders = (0, 2, 3, 4, 5)
        centers2 = [
            result.rect_for(order).left * 2 + result.rect_for(order).width
            for order in control_orders
        ]
        self.assertLessEqual(max(centers2) - min(centers2), 1)
        self.assertEqual(result.rect_for(1), RectDlu(4, 19, 72, 1))

    def test_same_row_can_be_aligned_by_bottom_edge(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Tall", 0, RectDlu(7, 8, 40, 20)),
                ("Button", "Short", 0, RectDlu(60, 14, 40, 14)),
            ]
        )

        result = build(dialog)

        row = result.root_widget.layout
        self.assertEqual(row.class_name, "QGridLayout")
        self.assertTrue(
            all(
                "AlignBottom" in item.alignment
                for item in source_widget_items(row)
            )
        )

    def test_mixed_height_centered_row_shares_a_scalable_cell(self) -> None:
        dialog = make_dialog(
            [
                ("Static", "Name:", 0, RectDlu(7, 10, 42, 8)),
                ("Edit", "", 0, RectDlu(55, 8, 100, 12)),
            ]
        )

        result = build(dialog)

        items = source_widget_items(result.root_widget.layout)
        self.assertEqual(
            {(item.row, item.row_span) for item in items},
            {(items[0].row, items[0].row_span)},
        )
        self.assertTrue(all("AlignVCenter" in item.alignment for item in items))

    def test_tall_control_spans_multiple_grid_rows(self) -> None:
        dialog = make_dialog(
            [
                ("ListBox", "", 0, RectDlu(7, 8, 100, 58)),
                ("Button", "Add", 0, RectDlu(120, 8, 45, 14)),
                ("Button", "Remove", 0, RectDlu(120, 29, 45, 14)),
                ("Button", "Clear", 0, RectDlu(120, 50, 45, 14)),
            ]
        )

        result = build(dialog)

        layout = result.root_widget.layout
        self.assertEqual(layout.class_name, "QGridLayout")
        list_item = next(
            item for item in layout.items if item.widget.class_name == "QListWidget"
        )
        button_items = [
            item
            for item in layout.items
            if item.widget is not None
            and item.widget.class_name == "QPushButton"
        ]
        self.assertGreater(list_item.row_span, 1)
        self.assertLessEqual(list_item.row, button_items[0].row)
        self.assertGreaterEqual(
            list_item.row + list_item.row_span,
            button_items[-1].row + button_items[-1].row_span,
        )
        self.assertTrue(all(value > 0 for value in layout.row_stretch))

    def test_vertical_separator_creates_independent_side_panels(self) -> None:
        dialog = replace(
            make_dialog(
                [
                    ("Edit", "", 0, RectDlu(165, 68, 95, 14)),
                    ("Static", "Server:", 0, RectDlu(112, 30, 42, 8)),
                    ("ListBox", "", 0, RectDlu(7, 8, 80, 105)),
                    ("Static", "", 0x11, RectDlu(99, 5, 1, 125)),
                    ("Edit", "", 0, RectDlu(165, 28, 95, 14)),
                    (
                        "Static",
                        "Connection settings",
                        0,
                        RectDlu(112, 8, 145, 10),
                    ),
                    ("Static", "Port:", 0, RectDlu(112, 50, 42, 8)),
                    ("Edit", "", 0, RectDlu(165, 48, 95, 14)),
                    ("Static", "User:", 0, RectDlu(112, 70, 42, 8)),
                ]
            ),
            rect=RectDlu(0, 0, 280, 140),
        )

        result = build(dialog)

        partition = result.root_widget.layout
        self.assertEqual(partition.class_name, "QGridLayout")
        by_class = {
            item.widget.class_name: item
            for item in partition.items
            if item.widget is not None
        }
        separator_item = by_class["QFrame"]
        left_item = by_class["QListWidget"]
        self.assertLess(
            left_item.column + left_item.column_span,
            separator_item.column + 1,
        )
        right_items = [
            item
            for item in source_widget_items(partition)
            if item.widget.class_name in {"QLabel", "QLineEdit"}
        ]
        self.assertTrue(
            all(item.column > separator_item.column for item in right_items)
        )

        xml = ET.fromstring(emit_ui(result.root_widget))
        separator = xml.find(
            "./widget/layout/item/widget[@class='QFrame']"
            "/property[@name='frameShape']/enum"
        )
        self.assertEqual(separator.text, "QFrame::VLine")

    def test_short_vertical_decoration_remains_in_coordinate_grid(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "Left", 0, RectDlu(7, 8, 45, 14)),
                ("Static", "", 0x11, RectDlu(80, 10, 1, 10)),
                ("Button", "Right", 0, RectDlu(100, 8, 45, 14)),
            ]
        )

        result = build(dialog)

        self.assertEqual(result.root_widget.layout.class_name, "QGridLayout")
        self.assertTrue(
            any(
                item.widget is not None and item.widget.class_name == "QFrame"
                for item in result.root_widget.layout.items
            )
        )

    def test_horizontal_separator_creates_top_and_bottom_regions(self) -> None:
        dialog = replace(
            make_dialog(
                [
                    ("Edit", "", 0, RectDlu(65, 93, 190, 14)),
                    ("Static", "Summary", 0, RectDlu(7, 8, 120, 10)),
                    ("Static", "", 0x10, RectDlu(7, 55, 266, 1)),
                    ("Static", "Name:", 0, RectDlu(12, 75, 42, 8)),
                    ("Edit", "", 0, RectDlu(65, 73, 190, 14)),
                    ("Static", "Path:", 0, RectDlu(12, 95, 42, 8)),
                    ("Static", "Read-only details", 0, RectDlu(7, 28, 160, 10)),
                ]
            ),
            rect=RectDlu(0, 0, 280, 130),
        )

        result = build(dialog)

        partition = result.root_widget.layout
        self.assertEqual(partition.class_name, "QGridLayout")
        separator_item = next(
            item
            for item in partition.items
            if item.widget is not None and item.widget.class_name == "QFrame"
        )
        top_items = [
            item
            for item in partition.items
            if item.widget is not None
            and item.widget.class_name == "QLabel"
            and item.widget.object_name in {"summaryLabel", "readOnlyDetailsLabel"}
        ]
        bottom_items = [
            item
            for item in partition.items
            if item.widget is not None
            and item.widget.object_name
            in {"nameLabel", "nameEdit", "pathLabel", "pathEdit"}
        ]
        self.assertTrue(
            all(item.row + item.row_span <= separator_item.row for item in top_items)
        )
        self.assertTrue(all(item.row > separator_item.row for item in bottom_items))

        xml = ET.fromstring(emit_ui(result.root_widget))
        separator = xml.find(
            "./widget/layout/item/widget[@class='QFrame']"
            "/property[@name='frameShape']/enum"
        )
        self.assertEqual(separator.text, "QFrame::HLine")

    def test_perpendicular_separators_create_nested_regions(self) -> None:
        dialog = replace(
            make_dialog(
                [
                    ("Static", "Bottom right", 0, RectDlu(112, 80, 100, 10)),
                    ("Static", "", 0x10, RectDlu(110, 58, 155, 1)),
                    ("ListBox", "", 0, RectDlu(7, 8, 80, 125)),
                    ("Static", "Top right", 0, RectDlu(112, 12, 100, 10)),
                    ("Static", "", 0x11, RectDlu(99, 5, 1, 140)),
                ]
            ),
            rect=RectDlu(0, 0, 280, 150),
        )

        result = build(dialog)

        layout = result.root_widget.layout
        self.assertEqual(layout.class_name, "QGridLayout")
        separators = [
            item
            for item in layout.items
            if item.widget is not None and item.widget.class_name == "QFrame"
        ]
        self.assertEqual(len(separators), 2)
        self.assertTrue(any(item.row_span > item.column_span for item in separators))
        self.assertTrue(any(item.column_span > item.row_span for item in separators))

    def test_runtime_alternatives_share_one_grid_cell(self) -> None:
        dialog = make_dialog(
            [
                ("Static", "Mode:", 0, RectDlu(7, 11, 42, 8)),
                ("Edit", "", 0, RectDlu(57, 8, 105, 14)),
                ("ComboBox", "", 0, RectDlu(58, 9, 104, 14)),
            ]
        )

        result = build(dialog)

        self.assertEqual(result.root_widget.layout.class_name, "QGridLayout")
        field = next(
            item.widget
            for item in result.root_widget.layout.items
            if item.widget is not None
            and item.widget.object_name.startswith("runtimeAlternatives")
        )
        self.assertEqual(field.class_name, "QWidget")
        self.assertTrue(field.object_name.startswith("runtimeAlternatives"))
        self.assertEqual(field.layout.class_name, "QGridLayout")
        self.assertEqual(
            [(item.row, item.column) for item in field.layout.items],
            [(0, 0), (0, 0)],
        )
        self.assertEqual(
            [item.widget.class_name for item in field.layout.items],
            ["QLineEdit", "QComboBox"],
        )
        codes = [diagnostic.code for diagnostic in result.diagnostics]
        self.assertIn("layout.runtime-alternatives", codes)
        self.assertNotIn("layout.overlap", codes)
        runtime_diagnostic = next(
            diagnostic
            for diagnostic in result.diagnostics
            if diagnostic.code == "layout.runtime-alternatives"
        )
        self.assertEqual(runtime_diagnostic.severity, "info")
        self.assertIn("near-z-order", runtime_diagnostic.message)
        self.assertIn("probable topmost control", runtime_diagnostic.message)
        validate_ui_xml(emit_ui(result.root_widget))

    def test_runtime_alternatives_keep_meaningful_internal_subrectangles(self) -> None:
        dialog = make_dialog(
            [
                ("Button", "First mode", 3, RectDlu(20, 10, 38, 10)),
                ("Button", "Second mode", 3, RectDlu(34, 10, 45, 10)),
            ]
        )
        pair = PairRelationHint((0, 1), 1.0, (1049,), (1049,))

        result = build(
            dialog,
            MultilingualLayoutHints(alternatives=(pair,)),
        )

        wrapper = next(
            item.widget
            for item in result.root_widget.layout.items
            if item.widget is not None
            and item.widget.object_name.startswith("runtimeAlternatives")
        )
        [first, second] = wrapper.layout.items
        self.assertNotEqual(
            (first.column, first.column_span),
            (second.column, second.column_span),
        )
        self.assertEqual(result.alternative_states_for(0), ((0, 0),))
        self.assertEqual(result.alternative_states_for(1), ((0, 1),))

    def test_runtime_alternative_member_inherits_only_its_own_edge_anchor(
        self,
    ) -> None:
        dialog = make_dialog(
            [
                ("Button", "First mode", 3, RectDlu(20, 10, 38, 10)),
                ("Button", "Second mode", 3, RectDlu(34, 10, 45, 10)),
                ("Static", "Shared left", 0, RectDlu(20, 40, 60, 8)),
            ]
        )
        pair = PairRelationHint((0, 1), 1.0, (1049,), (1049,))

        result = build(
            dialog,
            MultilingualLayoutHints(alternatives=(pair,)),
        )

        self.assertEqual(result.anchors_for(0)[0][0], "start")
        self.assertIsNone(result.anchors_for(1)[0])
        self.assertEqual(result.anchors_for(2)[0][0], "start")

    def test_near_z_order_strengthens_imperfect_geometry(self) -> None:
        dialog = make_dialog(
            [
                ("Edit", "", 0, RectDlu(57, 8, 105, 14)),
                ("ComboBox", "", 0, RectDlu(62, 11, 96, 13)),
            ]
        )

        result = build(dialog)

        runtime_diagnostic = next(
            diagnostic
            for diagnostic in result.diagnostics
            if diagnostic.code == "layout.runtime-alternatives"
        )
        self.assertIn("geometry match 64%", runtime_diagnostic.message)
        self.assertIn("z-order span 1", runtime_diagnostic.message)
        self.assertIn("near-z-order", runtime_diagnostic.message)
        self.assertNotIn(
            "layout.overlap",
            [diagnostic.code for diagnostic in result.diagnostics],
        )

    def test_repeated_order_offset_identifies_layer_blocks(self) -> None:
        dialog = make_dialog(
            [
                ("Edit", "", 0, RectDlu(7, 8, 70, 14)),
                ("Edit", "", 0, RectDlu(7, 30, 70, 14)),
                ("Edit", "", 0, RectDlu(7, 52, 70, 14)),
                ("ComboBox", "", 0, RectDlu(12, 11, 64, 13)),
                ("ComboBox", "", 0, RectDlu(12, 33, 64, 13)),
                ("ComboBox", "", 0, RectDlu(12, 55, 64, 13)),
            ]
        )

        result = build(dialog)

        runtime_diagnostics = [
            diagnostic
            for diagnostic in result.diagnostics
            if diagnostic.code == "layout.runtime-alternatives"
        ]
        self.assertEqual(len(runtime_diagnostics), 3)
        self.assertTrue(
            all(
                "layer-offset:3" in diagnostic.message
                for diagnostic in runtime_diagnostics
            )
        )
        self.assertNotIn(
            "layout.overlap",
            [diagnostic.code for diagnostic in result.diagnostics],
        )

    def test_single_distant_overlap_does_not_get_order_assistance(self) -> None:
        dialog = make_dialog(
            [
                ("Edit", "", 0, RectDlu(57, 8, 105, 14)),
                ("Button", "One", 0, RectDlu(7, 30, 35, 14)),
                ("Button", "Two", 0, RectDlu(50, 30, 35, 14)),
                ("Button", "Three", 0, RectDlu(93, 30, 35, 14)),
                ("ComboBox", "", 0, RectDlu(62, 11, 96, 13)),
            ]
        )

        result = build(dialog)

        codes = [diagnostic.code for diagnostic in result.diagnostics]
        self.assertIn("layout.overlap", codes)
        self.assertNotIn("layout.runtime-alternatives", codes)

    def test_strict_geometry_does_not_require_near_z_order(self) -> None:
        dialog = make_dialog(
            [
                ("Edit", "", 0, RectDlu(57, 8, 105, 14)),
                ("Button", "One", 0, RectDlu(7, 30, 35, 14)),
                ("Button", "Two", 0, RectDlu(50, 30, 35, 14)),
                ("Button", "Three", 0, RectDlu(93, 30, 35, 14)),
                ("ComboBox", "", 0, RectDlu(58, 9, 104, 14)),
            ]
        )

        result = build(dialog)

        runtime_diagnostic = next(
            diagnostic
            for diagnostic in result.diagnostics
            if diagnostic.code == "layout.runtime-alternatives"
        )
        self.assertIn(
            "strict geometry (order-independent)",
            runtime_diagnostic.message,
        )
        self.assertNotIn(
            "layout.overlap",
            [diagnostic.code for diagnostic in result.diagnostics],
        )

    def test_container_overlap_is_not_a_runtime_alternative(self) -> None:
        dialog = make_dialog(
            [
                ("SysTabControl32", "", 0, RectDlu(7, 8, 150, 60)),
                ("Edit", "", 0, RectDlu(12, 11, 144, 57)),
            ]
        )

        result = build(dialog)

        codes = [diagnostic.code for diagnostic in result.diagnostics]
        self.assertIn("layout.overlap", codes)
        self.assertNotIn("layout.runtime-alternatives", codes)

    def test_partial_overlap_remains_a_warning(self) -> None:
        dialog = make_dialog(
            [
                ("Edit", "", 0, RectDlu(7, 8, 100, 14)),
                ("Edit", "", 0, RectDlu(82, 8, 80, 14)),
            ]
        )

        result = build(dialog)

        codes = [diagnostic.code for diagnostic in result.diagnostics]
        self.assertIn("layout.overlap", codes)
        self.assertNotIn("layout.runtime-alternatives", codes)


if __name__ == "__main__":
    unittest.main()

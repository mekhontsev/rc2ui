from __future__ import annotations

import tomllib
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

from rc2ui.analysis.multilingual import fuse_dialog_languages
from rc2ui.domain.dialog import Control, ControlKey, Dialog, DialogKey
from rc2ui.domain.geometry import RectDlu
from rc2ui.domain.resource_id import ResourceId
from rc2ui.layout.infer import LayoutBuilder
from rc2ui.mapping.controls import ControlMapper
from rc2ui.naming.resolver import NameResolver
from rc2ui.qt.emitter import emit_ui
from rc2ui.semantics.config import SemanticMap, SemanticMapError
from rc2ui.semantics.engine import SemanticEngine
from rc2ui.semantics.model import (
    CompoundAction,
    CompoundGeometry,
    CompoundKind,
)
from rc2ui.semantics.transform import apply_semantic_mapping
from rc2ui.validation.ui_xml import validate_ui_xml


class SemanticTests(unittest.TestCase):
    def test_edit_updown_is_detected_but_not_replaced_without_rule(self) -> None:
        multilingual, mapped = _analyze(_edit_updown_dialog())

        plan = SemanticEngine().analyze(multilingual, mapped)

        self.assertEqual(len(plan.decisions), 1)
        decision = plan.decisions[0]
        self.assertEqual(decision.candidate.kind, CompoundKind.EDIT_UPDOWN)
        self.assertEqual(decision.action, CompoundAction.SUGGEST)
        self.assertGreater(decision.candidate.confidence, 0.9)
        self.assertEqual(decision.candidate.primary_ids[0], "IDC_VALUE")
        self.assertEqual(decision.candidate.member_ids[0][0], "IDC_VALUE_SPIN")
        self.assertEqual(
            decision.candidate.geometry,
            CompoundGeometry.AUTOBUDDY_RIGHT,
        )

    def test_autobuddy_runtime_geometry_aligns_separate_widgets(self) -> None:
        multilingual, mapped = _analyze(_edit_updown_dialog())
        plan = SemanticEngine().analyze(multilingual, mapped)
        naming = NameResolver().resolve(multilingual.dialog, mapped)

        layout = LayoutBuilder().build(
            multilingual.layout_dialog,
            mapped,
            naming,
            multilingual.layout_hints,
            plan,
        )

        self.assertEqual(layout.rect_for(1), RectDlu(62, 10, 82, 14))
        self.assertEqual(layout.rect_for(2), RectDlu(144, 10, 14, 14))
        xml = ET.fromstring(emit_ui(layout.root_widget))
        widget_items = {
            widget.get("class"): item
            for item in xml.findall(".//item")
            if (widget := item.find("widget")) is not None
            and widget.get("class") in {"QLineEdit", "QSpinBox"}
        }
        self.assertEqual(
            widget_items["QLineEdit"].get("row"),
            widget_items["QSpinBox"].get("row"),
        )

    def test_left_aligned_autobuddy_moves_edit_after_spinner(self) -> None:
        dialog = _dialog(
            (
                _control(
                    0,
                    "Edit",
                    "",
                    RectDlu(62, 10, 96, 14),
                    "IDC_VALUE",
                ),
                _control(
                    1,
                    "msctls_updown32",
                    "",
                    RectDlu(3, 60, 14, 6),
                    "IDC_VALUE_SPIN",
                    style=0x0018,
                ),
            )
        )
        multilingual, mapped = _analyze(dialog)
        plan = SemanticEngine().analyze(multilingual, mapped)
        naming = NameResolver().resolve(multilingual.dialog, mapped)

        layout = LayoutBuilder().build(
            multilingual.layout_dialog,
            mapped,
            naming,
            multilingual.layout_hints,
            plan,
        )

        self.assertEqual(
            plan.decisions[0].candidate.geometry,
            CompoundGeometry.AUTOBUDDY_LEFT,
        )
        self.assertEqual(layout.rect_for(0), RectDlu(76, 10, 82, 14))
        self.assertEqual(layout.rect_for(1), RectDlu(62, 10, 14, 14))

    def test_autobuddy_does_not_skip_an_intervening_control(self) -> None:
        dialog = _dialog(
            (
                _control(
                    0,
                    "Edit",
                    "",
                    RectDlu(62, 10, 96, 14),
                    "IDC_VALUE",
                ),
                _control(
                    1,
                    "Static",
                    "Unit",
                    RectDlu(7, 35, 30, 8),
                    "IDC_UNIT",
                ),
                _control(
                    2,
                    "msctls_updown32",
                    "",
                    # Geometrically adjacent to the edit, but AUTOBUDDY binds
                    # the intervening static instead and must not skip it.
                    RectDlu(158, 10, 14, 14),
                    "IDC_VALUE_SPIN",
                    style=0x0014,
                ),
            )
        )
        multilingual, mapped = _analyze(dialog)

        plan = SemanticEngine().analyze(multilingual, mapped)

        self.assertFalse(
            any(
                decision.candidate.kind is CompoundKind.EDIT_UPDOWN
                for decision in plan.decisions
            )
        )

    def test_rule_replaces_edit_updown_with_qdouble_spin_box(self) -> None:
        semantic_map = _semantic_map(
            "[[rules]]\n"
            'name = "floating-values"\n'
            'kind = "edit-updown"\n'
            'action = "replace"\n'
            'dialog_id = "IDD_PARAMETERS"\n'
            'primary_id = "IDC_VALUE"\n'
            'member_id = "IDC_VALUE_SPIN"\n'
            'result = "QDoubleSpinBox"\n'
            "[rules.properties]\n"
            "minimum = -1000.0\n"
            "maximum = 1000.0\n"
            "decimals = 3\n"
            "singleStep = 0.25\n"
        )
        multilingual, mapped = _analyze(_edit_updown_dialog())
        plan = SemanticEngine(semantic_map).analyze(multilingual, mapped)
        naming_mapped = apply_semantic_mapping(mapped, plan, for_naming=True)
        naming = NameResolver().resolve(multilingual.dialog, naming_mapped)
        layout_mapped = apply_semantic_mapping(mapped, plan)

        layout = LayoutBuilder().build(
            multilingual.layout_dialog,
            layout_mapped,
            naming,
            multilingual.layout_hints,
            plan,
        )
        text = emit_ui(layout.root_widget)
        validate_ui_xml(text)
        xml = ET.fromstring(text)

        self.assertFalse(plan.diagnostics)
        self.assertEqual(plan.used_rule_indices, (1,))
        spin = xml.find(".//widget[@class='QDoubleSpinBox']")
        self.assertIsNotNone(spin)
        self.assertIsNone(xml.find(".//widget[@class='QLineEdit']"))
        self.assertIsNone(xml.find(".//widget[@class='QSpinBox']"))
        self.assertEqual(
            spin.findtext("./property[@name='minimum']/double"),
            "-1000",
        )
        self.assertEqual(
            spin.findtext("./property[@name='singleStep']/double"),
            "0.25",
        )
        self.assertIn("DoubleSpinBox", spin.get("name"))
        self.assertEqual(plan.consumed_orders, frozenset({2}))
        self.assertEqual(layout.rect_for(1), RectDlu(62, 10, 96, 14))

    def test_browse_field_is_bundled_and_retains_both_widgets(self) -> None:
        dialog = _dialog(
            (
                _control(0, "Static", "File:", RectDlu(7, 13, 45, 8), "IDC_STATIC"),
                _control(1, "Edit", "", RectDlu(55, 10, 90, 14), "IDC_FILE"),
                _control(2, "Button", "...", RectDlu(148, 10, 18, 14), "IDC_BROWSE"),
            )
        )
        multilingual, mapped = _analyze(dialog)
        plan = SemanticEngine().analyze(multilingual, mapped)
        naming = NameResolver().resolve(dialog, mapped)
        baseline = LayoutBuilder().build(
            dialog,
            mapped,
            naming,
            multilingual.layout_hints,
        )

        layout = LayoutBuilder().build(
            dialog,
            mapped,
            naming,
            multilingual.layout_hints,
            plan,
        )
        xml = ET.fromstring(emit_ui(layout.root_widget))

        decision = next(
            item
            for item in plan.decisions
            if item.candidate.kind is CompoundKind.EDIT_BROWSE
        )
        self.assertEqual(decision.action, CompoundAction.BUNDLE)
        self.assertIsNone(xml.find(".//widget[@name='browseFieldContainer']"))
        self.assertIsNotNone(xml.find(".//widget[@class='QLineEdit']"))
        self.assertIsNotNone(xml.find(".//widget[@class='QToolButton']"))
        self.assertEqual(
            emit_ui(layout.root_widget),
            emit_ui(baseline.root_widget),
        )

    def test_slider_value_and_list_actions_are_common_candidates(self) -> None:
        dialog = _dialog(
            (
                _control(0, "msctls_trackbar32", "", RectDlu(7, 8, 90, 18), "IDC_LEVEL"),
                _control(1, "Static", "50%", RectDlu(101, 12, 25, 8), "IDC_LEVEL_VALUE"),
                _control(2, "ListBox", "", RectDlu(7, 34, 100, 38), "IDC_ITEMS"),
                _control(3, "Button", "Add", RectDlu(112, 34, 42, 14), "IDC_ADD"),
                _control(4, "Button", "Remove", RectDlu(112, 52, 42, 14), "IDC_REMOVE"),
            )
        )
        multilingual, mapped = _analyze(dialog)

        plan = SemanticEngine().analyze(multilingual, mapped)

        by_kind = {item.candidate.kind: item for item in plan.decisions}
        self.assertIn(CompoundKind.SLIDER_VALUE, by_kind)
        self.assertIn(CompoundKind.LIST_ACTIONS, by_kind)
        self.assertEqual(
            by_kind[CompoundKind.SLIDER_VALUE].action,
            CompoundAction.BUNDLE,
        )
        self.assertEqual(
            by_kind[CompoundKind.LIST_ACTIONS].action,
            CompoundAction.BUNDLE,
        )

    def test_semantic_map_rejects_unknown_replace_widget(self) -> None:
        with self.assertRaisesRegex(SemanticMapError, "replace result"):
            _semantic_map(
                "[[rules]]\n"
                'name = "bad"\n'
                'kind = "edit-updown"\n'
                'action = "replace"\n'
                'result = "QWidget"\n'
            )

    def test_semantic_map_rejects_fractional_qspinbox_range(self) -> None:
        with self.assertRaisesRegex(SemanticMapError, "must be an integer"):
            _semantic_map(
                "[[rules]]\n"
                'name = "bad-integer"\n'
                'kind = "edit-updown"\n'
                'action = "replace"\n'
                'result = "QSpinBox"\n'
                "[rules.properties]\n"
                "minimum = 0\n"
                "maximum = 10.5\n"
            )

    def test_semantic_section_rejects_unknown_rule_field(self) -> None:
        with self.assertRaisesRegex(SemanticMapError, "unexpected field"):
            _semantic_map(
                "[[rules]]\n"
                'name = "typo"\n'
                'kind = "edit-updown"\n'
                'action = "keep"\n'
                "priorty = 10\n"
            )

    def test_equal_rule_precedence_is_reported_as_ambiguous(self) -> None:
        semantic_map = _semantic_map(
            "[[rules]]\n"
            'name = "integer"\n'
            'kind = "edit-updown"\n'
            'action = "replace"\n'
            'primary_id = "IDC_VALUE"\n'
            'result = "QSpinBox"\n'
            "runtime_configured = true\n"
            "[[rules]]\n"
            'name = "double"\n'
            'kind = "edit-updown"\n'
            'action = "replace"\n'
            'primary_id = "IDC_VALUE"\n'
            'result = "QDoubleSpinBox"\n'
            "runtime_configured = true\n"
        )
        multilingual, mapped = _analyze(_edit_updown_dialog())

        plan = SemanticEngine(semantic_map).analyze(multilingual, mapped)

        self.assertEqual(plan.decisions[0].action, CompoundAction.KEEP)
        self.assertIn(
            "semantic-map.ambiguous",
            {diagnostic.code for diagnostic in plan.diagnostics},
        )


def _semantic_map(text: str) -> SemanticMap:
    return SemanticMap.from_table(
        tomllib.loads(text),
        path=Path("rc2ui.toml"),
    )


def _analyze(dialog: Dialog):
    multilingual = fuse_dialog_languages((dialog,), dialog.key.language)
    mapper = ControlMapper()
    mapped = tuple(mapper.map(control) for control in dialog.controls)
    return multilingual, mapped


def _edit_updown_dialog() -> Dialog:
    return _dialog(
        (
            _control(0, "Static", "Value:", RectDlu(7, 13, 45, 8), "IDC_STATIC"),
            _control(1, "Edit", "", RectDlu(62, 10, 96, 14), "IDC_VALUE"),
            _control(
                2,
                "msctls_updown32",
                "",
                # UDS_AUTOBUDDY makes Win32 ignore this visual position and
                # attach the spinner to the preceding edit at runtime.
                RectDlu(3, 60, 14, 6),
                "IDC_VALUE_SPIN",
                style=0x0014,
            ),
            # Group boxes may be declared after their children in RC files.
            _control(
                3,
                "Button",
                "Values",
                RectDlu(55, 2, 110, 30),
                "IDC_VALUES_GROUP",
                style=0x0007,
            ),
        )
    )


def _dialog(controls: tuple[Control, ...]) -> Dialog:
    key = DialogKey(
        PurePosixPath("parameters.rc"),
        ResourceId.from_ordinal(100, "IDD_PARAMETERS"),
        1033,
    )
    rebound = tuple(
        Control(
            key=ControlKey(
                key,
                control.key.resource_id,
                control.key.occurrence,
            ),
            class_name=control.class_name,
            text=control.text,
            rect=control.rect,
            style=control.style,
            extended_style=control.extended_style,
            order=control.order,
        )
        for control in controls
    )
    return Dialog(
        key=key,
        caption="Parameters",
        rect=RectDlu(0, 0, 180, 80),
        style=0,
        extended_style=0,
        controls=rebound,
    )


def _control(
    order: int,
    class_name: str,
    text: str,
    rect: RectDlu,
    symbol: str,
    *,
    style: int = 0,
) -> Control:
    placeholder = DialogKey(
        PurePosixPath("placeholder.rc"),
        ResourceId.from_ordinal(1),
        1033,
    )
    return Control(
        key=ControlKey(
            placeholder,
            ResourceId.from_ordinal(1000 + order, symbol),
        ),
        class_name=class_name,
        text=text,
        rect=rect,
        style=style,
        extended_style=0,
        order=order,
    )


if __name__ == "__main__":
    unittest.main()

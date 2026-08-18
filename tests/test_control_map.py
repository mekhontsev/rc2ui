from __future__ import annotations

import tomllib
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from rc2ui.analysis.multilingual import fuse_dialog_languages
from rc2ui.domain.geometry import RectDlu
from rc2ui.domain.resource_id import ResourceId
from rc2ui.layout.infer import LayoutBuilder
from rc2ui.mapping.controls import ControlMapper
from rc2ui.mapping.overrides import ControlMap, ControlMapError
from rc2ui.naming.map import NamingMap
from rc2ui.naming.resolver import NameResolver
from rc2ui.qt.emitter import emit_ui
from rc2ui.semantics.engine import SemanticEngine
from rc2ui.semantics.model import CompoundKind
from rc2ui.semantics.transform import apply_semantic_mapping
from rc2ui.validation.ui_xml import validate_ui_xml
from tests.test_layout_and_emitter import make_dialog


class ControlMapTests(unittest.TestCase):
    def load(self, body: str) -> ControlMap:
        return ControlMap.from_table(
            tomllib.loads(body),
            path=Path("rc2ui.toml"),
        )

    def test_emits_promoted_qt_widget_for_registered_win32_class(self) -> None:
        control_map = self.load(
            "[[widgets]]\n"
            'name = "project-grid"\n'
            'qt_class = "Company::GridWidget"\n'
            'role = "input"\n'
            'header = "company/gridwidget.h"\n'
            'extends = "QWidget"\n'
            "expands_horizontally = true\n"
            "expands_vertically = true\n"
            'text_property = "windowTitle"\n'
            "[widgets.properties]\n"
            "enabled = true\n"
            'formatName = "fixed"\n'
            'displayMode = { enum = "Company::GridWidget::Compact" }\n'
            "[[rules]]\n"
            'name = "registered-grids"\n'
            'widget = "project-grid"\n'
            'win_class = "MyCompanyGrid"\n'
        )
        dialog = make_dialog(
            [("MyCompanyGrid", "Records", 0, RectDlu(7, 7, 160, 60))]
        )
        mapper = ControlMapper(control_map)
        mapped = tuple(mapper.map(control) for control in dialog.controls)
        naming = NameResolver().resolve(dialog, mapped)

        result = LayoutBuilder().build(dialog, mapped, naming)
        xml = ET.fromstring(emit_ui(result.root_widget))

        widget = xml.find(".//widget[@class='Company::GridWidget']")
        self.assertIsNotNone(widget)
        custom = xml.find("./customwidgets/customwidget")
        self.assertEqual(custom.findtext("class"), "Company::GridWidget")
        self.assertEqual(custom.findtext("header"), "company/gridwidget.h")
        fixed = widget.find("./property[@name='formatName']/string")
        self.assertEqual(fixed.text, "fixed")
        self.assertEqual(fixed.get("notr"), "true")
        mode = widget.find("./property[@name='displayMode']/enum")
        self.assertEqual(mode.text, "Company::GridWidget::Compact")
        title = widget.find("./property[@name='windowTitle']/string")
        self.assertEqual(title.text, "Records")
        self.assertIsNone(title.get("notr"))
        self.assertEqual(mapped[0].mapping_rule, "registered-grids")

    def test_regex_rule_turns_runtime_placeholders_into_radio_group(self) -> None:
        control_map = self.load(
            "[[widgets]]\n"
            'name = "radio-button"\n'
            'qt_class = "QRadioButton"\n'
            'role = "input"\n'
            "expands_horizontally = true\n"
            'text_property = "text"\n'
            "[[rules]]\n"
            'name = "calculation-modes"\n'
            'widget = "radio-button"\n'
            'win_class = "ProjectPlaceholder"\n'
            "id_regex = 'IDC_CALC_MODE_.*'\n"
            'dialog_regex = "#100"\n'
            'button_group = "calculationModeGroup"\n'
            'runtime_configured = ["checked"]\n'
        )
        dialog = _dialog_with_ids(
            [
                ("ProjectPlaceholder", "Fast", "IDC_CALC_MODE_FAST"),
                ("ProjectPlaceholder", "Safe", "IDC_CALC_MODE_SAFE"),
                ("ProjectPlaceholder", "Exact", "IDC_CALC_MODE_EXACT"),
            ]
        )
        mapped = tuple(
            ControlMapper(control_map).map(control)
            for control in dialog.controls
        )
        naming = NameResolver().resolve(dialog, mapped)

        result = LayoutBuilder().build(dialog, mapped, naming)
        text = emit_ui(result.root_widget)
        validate_ui_xml(text)
        xml = ET.fromstring(text)

        radios = xml.findall(".//widget[@class='QRadioButton']")
        self.assertEqual(len(radios), 3)
        self.assertTrue(
            all(
                radio.findtext("./attribute[@name='buttonGroup']/string")
                == "calculationModeGroup"
                for radio in radios
            )
        )
        groups = xml.findall("./buttongroups/buttongroup")
        self.assertEqual(
            [(group.get("name")) for group in groups],
            ["calculationModeGroup"],
        )
        self.assertEqual(mapped[0].runtime_configured, ("checked",))

    def test_native_button_radio_style_needs_no_control_map(self) -> None:
        dialog = make_dialog(
            [("Button", "Choice", 0x0009, RectDlu(7, 7, 80, 14))]
        )

        mapped = ControlMapper().map(dialog.controls[0])

        self.assertEqual(mapped.qt_class, "QRadioButton")
        self.assertIsNone(mapped.mapping_rule)

    def test_exact_bindings_map_type_id_pairs_to_different_widgets(self) -> None:
        control_map = self.load(
            "[[widgets]]\n"
            'name = "color-button"\n'
            'qt_class = "Project::ColorButton"\n'
            'header = "project/colorbutton.h"\n'
            'extends = "QPushButton"\n'
            'role = "action"\n'
            'text_property = "text"\n'
            "[[widgets]]\n"
            'name = "path-editor"\n'
            'qt_class = "Project::PathEdit"\n'
            'header = "project/pathedit.h"\n'
            'extends = "QLineEdit"\n'
            'role = "input"\n'
            "expands_horizontally = true\n"
            "[[bindings]]\n"
            'name = "settings-project-controls"\n'
            'dialog_regex = "#100"\n'
            "priority = 100\n"
            "controls = [\n"
            '  { win_class = "Button", id = "IDC_COLOR", '
            'widget = "color-button" },\n'
            '  { win_class = "Edit", id = "IDC_PATH", '
            'widget = "path-editor", runtime_configured = ["text"] },\n'
            "]\n"
        )
        dialog = _dialog_with_ids(
            [
                ("Button", "Color", "IDC_COLOR"),
                ("Edit", "", "IDC_PATH"),
            ]
        )

        mapped = tuple(
            ControlMapper(control_map).map(control)
            for control in dialog.controls
        )

        self.assertEqual(
            [item.qt_class for item in mapped],
            ["Project::ColorButton", "Project::PathEdit"],
        )
        self.assertEqual(
            mapped[0].mapping_rule,
            "settings-project-controls[Button:IDC_COLOR]",
        )
        self.assertEqual(mapped[1].runtime_configured, ("text",))

    def test_exact_binding_precedes_regex_rule_at_equal_priority(self) -> None:
        control_map = self.load(
            "[[widgets]]\n"
            'name = "generic"\nqt_class = "QLineEdit"\n'
            "[[widgets]]\n"
            'name = "special"\nqt_class = "QDateEdit"\n'
            "[[rules]]\n"
            'name = "all-edits"\nwidget = "generic"\n'
            'win_class = "Edit"\nid_regex = "IDC_.*"\n'
            "[[bindings]]\n"
            'name = "reviewed"\n'
            "controls = [\n"
            '  { win_class = "Edit", id = "IDC_DATE", widget = "special" },\n'
            "]\n"
        )
        dialog = _dialog_with_ids([("Edit", "", "IDC_DATE")])

        mapped = ControlMapper(control_map).map(dialog.controls[0])

        self.assertEqual(mapped.qt_class, "QDateEdit")
        self.assertTrue(mapped.mapping_rule.startswith("reviewed["))

    def test_explicit_mapping_is_not_reinterpreted_as_compound(self) -> None:
        control_map = self.load(
            "[[widgets]]\n"
            'name = "project-number"\nqt_class = "QLineEdit"\n'
            "[[rules]]\n"
            'name = "project-number-editor"\n'
            'widget = "project-number"\n'
            'win_class = "Edit"\n'
            'id_regex = "IDC_VALUE"\n'
        )
        dialog = _dialog_with_ids(
            [
                ("Edit", "", "IDC_VALUE"),
                ("msctls_updown32", "", "IDC_VALUE_SPIN"),
            ]
        )
        controls = (
            replace(dialog.controls[0], rect=RectDlu(7, 7, 80, 14)),
            replace(
                dialog.controls[1],
                rect=RectDlu(87, 7, 14, 14),
                style=0x0014,
            ),
        )
        dialog = replace(dialog, controls=controls)
        multilingual = fuse_dialog_languages((dialog,), 1033)
        mapped = tuple(
            ControlMapper(control_map).map(control)
            for control in dialog.controls
        )

        plan = SemanticEngine().analyze(multilingual, mapped)

        self.assertNotIn(
            CompoundKind.EDIT_UPDOWN,
            {decision.candidate.kind for decision in plan.decisions},
        )

    def test_exact_control_set_becomes_one_promoted_widget(self) -> None:
        control_map = self.load(
            "[[widgets]]\n"
            'name = "choice-selector"\n'
            'qt_class = "Example::ChoiceSelector"\n'
            'header = "example/choiceselector.h"\n'
            'extends = "QWidget"\n'
            'role = "input"\n'
            "expands_horizontally = true\n"
            "[[compounds]]\n"
            'name = "paired-choice-selector"\n'
            'widget = "choice-selector"\n'
            'primary = { win_class = "LegacyChoice", '
            'id = "IDC_CHOICE_PRIMARY" }\n'
            "members = [\n"
            '  { win_class = "LegacyChoice", '
            'id = "IDC_CHOICE_SECONDARY" },\n'
            "]\n"
        )
        naming_map = NamingMap.from_table(
            tomllib.loads(
                "[[rules]]\n"
                'name = "choice-controls"\n'
                'kind = "control"\n'
                "[rules.names]\n"
                'IDC_CHOICE_PRIMARY = "choiceSelector"\n'
            ),
            path=Path("rc2ui.toml"),
        )
        dialog = _dialog_with_ids(
            [
                ("LegacyChoice", "Secondary", "IDC_CHOICE_SECONDARY"),
                ("LegacyChoice", "Primary", "IDC_CHOICE_PRIMARY"),
            ]
        )
        multilingual = fuse_dialog_languages((dialog,), 1033)
        mapped = tuple(
            ControlMapper(control_map).map(control)
            for control in dialog.controls
        )

        plan = SemanticEngine(control_map=control_map).analyze(
            multilingual,
            mapped,
        )
        naming_mapped = apply_semantic_mapping(
            mapped,
            plan,
            for_naming=True,
        )
        naming = NameResolver(naming_map).resolve(dialog, naming_mapped)
        layout = LayoutBuilder().build(
            dialog,
            apply_semantic_mapping(mapped, plan),
            naming,
            semantic_plan=plan,
        )
        text = emit_ui(layout.root_widget)
        validate_ui_xml(text)
        xml = ET.fromstring(text)

        self.assertFalse(plan.diagnostics)
        self.assertEqual(plan.used_control_rule_keys, ("compounds#1",))
        self.assertEqual(plan.decisions[0].candidate.kind, CompoundKind.CONTROL_SET)
        self.assertEqual(layout.rect_for(1), RectDlu(7, 7, 120, 31))
        selector = xml.find(".//widget[@class='Example::ChoiceSelector']")
        self.assertIsNotNone(selector)
        self.assertEqual(selector.get("name"), "choiceSelector")
        self.assertEqual(
            len(xml.findall(".//widget[@class='Example::ChoiceSelector']")),
            1,
        )
        self.assertIsNone(xml.find(".//widget[@name='secondaryWidget']"))
        custom = xml.find("./customwidgets/customwidget")
        self.assertEqual(custom.findtext("class"), "Example::ChoiceSelector")
        self.assertEqual(
            custom.findtext("header"),
            "example/choiceselector.h",
        )

    def test_compound_rejects_duplicate_exact_selectors(self) -> None:
        with self.assertRaisesRegex(
            ControlMapError,
            "distinct exact selectors",
        ):
            self.load(
                "[[widgets]]\n"
                'name = "selector"\nqt_class = "QWidget"\n'
                "[[compounds]]\n"
                'name = "duplicate"\nwidget = "selector"\n'
                'primary = { win_class = "Placeholder", id = "IDC_ONE" }\n'
                "members = [\n"
                '  { win_class = "Placeholder", id = "IDC_ONE" },\n'
                "]\n"
            )

    def test_compound_member_and_one_to_one_mapping_conflict(self) -> None:
        control_map = self.load(
            "[[widgets]]\n"
            'name = "selector"\nqt_class = "QWidget"\n'
            "[[widgets]]\n"
            'name = "radio"\nqt_class = "QRadioButton"\n'
            "[[rules]]\n"
            'name = "all-placeholders"\nwidget = "radio"\n'
            'win_class = "Placeholder"\n'
            "[[compounds]]\n"
            'name = "pair"\nwidget = "selector"\n'
            'primary = { win_class = "Placeholder", id = "IDC_ONE" }\n'
            "members = [\n"
            '  { win_class = "Placeholder", id = "IDC_TWO" },\n'
            "]\n"
        )
        dialog = _dialog_with_ids(
            [
                ("Placeholder", "One", "IDC_ONE"),
                ("Placeholder", "Two", "IDC_TWO"),
            ]
        )
        multilingual = fuse_dialog_languages((dialog,), 1033)
        mapped = tuple(
            ControlMapper(control_map).map(control)
            for control in dialog.controls
        )

        plan = SemanticEngine(control_map=control_map).analyze(
            multilingual,
            mapped,
        )

        self.assertEqual(plan.decisions[0].action.value, "keep")
        self.assertIn(
            "control-compound.mapping-conflict",
            {item.code for item in plan.diagnostics},
        )

    def test_compound_repeated_member_requires_occurrence(self) -> None:
        control_map = self.load(
            "[[widgets]]\n"
            'name = "selector"\nqt_class = "QWidget"\n'
            "[[compounds]]\n"
            'name = "ambiguous-member"\nwidget = "selector"\n'
            'primary = { win_class = "Placeholder", id = "IDC_ONE" }\n'
            "members = [\n"
            '  { win_class = "Placeholder", id = "IDC_REPEATED" },\n'
            "]\n"
        )
        dialog = _dialog_with_ids(
            [
                ("Placeholder", "One", "IDC_ONE"),
                ("Placeholder", "A", "IDC_REPEATED"),
                ("Placeholder", "B", "IDC_REPEATED"),
            ]
        )
        repeated_id = ResourceId.from_ordinal(1001, "IDC_REPEATED")
        dialog = replace(
            dialog,
            controls=(
                dialog.controls[0],
                replace(
                    dialog.controls[1],
                    key=replace(
                        dialog.controls[1].key,
                        resource_id=repeated_id,
                        occurrence=1,
                    ),
                ),
                replace(
                    dialog.controls[2],
                    key=replace(
                        dialog.controls[2].key,
                        resource_id=repeated_id,
                        occurrence=2,
                    ),
                ),
            ),
        )
        multilingual = fuse_dialog_languages((dialog,), 1033)
        mapped = tuple(
            ControlMapper(control_map).map(control)
            for control in dialog.controls
        )

        plan = SemanticEngine(control_map=control_map).analyze(
            multilingual,
            mapped,
        )

        self.assertFalse(plan.decisions)
        self.assertEqual(plan.used_control_rule_keys, ("compounds#1",))
        self.assertIn(
            "control-compound.ambiguous-member",
            {item.code for item in plan.diagnostics},
        )

        resolved_map = self.load(
            "[[widgets]]\n"
            'name = "selector"\nqt_class = "QWidget"\n'
            "[[compounds]]\n"
            'name = "resolved-member"\nwidget = "selector"\n'
            'primary = { win_class = "Placeholder", id = "IDC_ONE" }\n'
            "members = [\n"
            '  { win_class = "Placeholder", id = "IDC_REPEATED", '
            "occurrence = 2 },\n"
            "]\n"
        )
        resolved_mapped = tuple(
            ControlMapper(resolved_map).map(control)
            for control in dialog.controls
        )
        resolved = SemanticEngine(control_map=resolved_map).analyze(
            multilingual,
            resolved_mapped,
        )

        self.assertEqual(resolved.decisions[0].candidate.orders, (0, 2))
        self.assertTrue(resolved.decisions[0].active)

    def test_equal_compound_rules_do_not_use_toml_order(self) -> None:
        control_map = self.load(
            "[[widgets]]\n"
            'name = "first"\nqt_class = "QWidget"\n'
            "[[widgets]]\n"
            'name = "second"\nqt_class = "QFrame"\n'
            "[[compounds]]\n"
            'name = "forward"\nwidget = "first"\n'
            'primary = { win_class = "Placeholder", id = "IDC_ONE" }\n'
            "members = [\n"
            '  { win_class = "Placeholder", id = "IDC_TWO" },\n'
            "]\n"
            "[[compounds]]\n"
            'name = "reverse"\nwidget = "second"\n'
            'primary = { win_class = "Placeholder", id = "IDC_TWO" }\n'
            "members = [\n"
            '  { win_class = "Placeholder", id = "IDC_ONE" },\n'
            "]\n"
        )
        dialog = _dialog_with_ids(
            [
                ("Placeholder", "One", "IDC_ONE"),
                ("Placeholder", "Two", "IDC_TWO"),
            ]
        )
        multilingual = fuse_dialog_languages((dialog,), 1033)
        mapped = tuple(
            ControlMapper(control_map).map(control)
            for control in dialog.controls
        )

        plan = SemanticEngine(control_map=control_map).analyze(
            multilingual,
            mapped,
        )

        self.assertTrue(all(not decision.active for decision in plan.decisions))
        self.assertIn(
            "control-compound.ambiguous-rule",
            {item.code for item in plan.diagnostics},
        )

    def test_exact_compound_records_multilingual_membership(self) -> None:
        control_map = self.load(
            "[[widgets]]\n"
            'name = "selector"\nqt_class = "QWidget"\n'
            "[[compounds]]\n"
            'name = "pair"\nwidget = "selector"\n'
            'primary = { win_class = "Placeholder", id = "IDC_ONE" }\n'
            "members = [\n"
            '  { win_class = "Placeholder", id = "IDC_TWO" },\n'
            "]\n"
        )
        english = _dialog_with_ids(
            [
                ("Placeholder", "One", "IDC_ONE"),
                ("Placeholder", "Two", "IDC_TWO"),
            ]
        )
        russian_key = replace(english.key, language=1049)
        russian = replace(
            english,
            key=russian_key,
            controls=tuple(
                replace(
                    control,
                    key=replace(control.key, dialog=russian_key),
                    text=("Один" if control.order == 0 else "Два"),
                )
                for control in english.controls
            ),
        )
        multilingual = fuse_dialog_languages((english, russian), 1033)
        mapped = tuple(
            ControlMapper(control_map).map(control)
            for control in english.controls
        )

        plan = SemanticEngine(control_map=control_map).analyze(
            multilingual,
            mapped,
        )

        candidate = plan.decisions[0].candidate
        self.assertEqual(candidate.eligible_languages, (1033, 1049))
        self.assertEqual(candidate.supporting_languages, (1033, 1049))
        self.assertIn("LANGIDs", candidate.evidence[-1])

    def test_equal_precedence_is_reported_as_ambiguous(self) -> None:
        control_map = self.load(
            "[[widgets]]\n"
            'name = "one"\nqt_class = "QLineEdit"\n'
            "[[widgets]]\n"
            'name = "two"\nqt_class = "QDateEdit"\n'
            "[[rules]]\n"
            'name = "source-rule"\nwidget = "one"\n'
            'win_class = "Edit"\nsource_regex = "main.rc"\n'
            "[[rules]]\n"
            'name = "dialog-rule"\nwidget = "two"\n'
            'win_class = "Edit"\ndialog_regex = "#100"\n'
        )
        dialog = _dialog_with_ids([("Edit", "", "IDC_DATE")])

        with self.assertRaisesRegex(ControlMapError, "ambiguous control-map"):
            ControlMapper(control_map).map(dialog.controls[0])

    def test_nested_controls_array_is_rejected(self) -> None:
        with self.assertRaisesRegex(ControlMapError, r"controls\.controls"):
            self.load(
                "[[controls]]\n"
                'win_class = "Edit"\n'
                'qt_class = "QLineEdit"\n'
            )


def _dialog_with_ids(
    specs: list[tuple[str, str, str]],
):
    dialog = make_dialog(
        [
            (
                class_name,
                text,
                0,
                RectDlu(7, 7 + order * 17, 120, 14),
            )
            for order, (class_name, text, _) in enumerate(specs)
        ]
    )
    controls = tuple(
        replace(
            control,
            key=replace(
                control.key,
                resource_id=ResourceId.from_ordinal(
                    1000 + control.order,
                    specs[control.order][2],
                ),
            ),
        )
        for control in dialog.controls
    )
    return replace(dialog, controls=controls)


if __name__ == "__main__":
    unittest.main()

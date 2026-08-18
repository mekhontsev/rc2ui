from __future__ import annotations

import json
import tempfile
import tomllib
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from rc2ui.application.batch import BatchConverter
from rc2ui.application.manifest import load_manifest
from rc2ui.application.models import (
    ConversionRequest,
    DialogSelection,
    InputGroup,
    ProjectRules,
)
from rc2ui.mapping.overrides import ControlMap
from rc2ui.layout.mode import LayoutMode
from rc2ui.naming.map import NamingMap
from rc2ui.qtcheck.discovery import (
    QtBindingAvailability,
    discover_qt_binding,
)
from rc2ui.qtcheck.model import QtCheckMode
from rc2ui.semantics.config import SemanticMap
from rc2ui.validation.ui_xml import validate_ui_xml
from tests.resource_fixtures import (
    edit_updown_dialog_payload,
    extended_dialog_payload,
    null_res_record,
    pe_resource_binary,
    repeated_static_dialog_payload,
    res_record,
    standard_dialog_payload,
)


class BatchConversionTests(unittest.TestCase):
    def test_faithful_remains_default_and_simplified_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            resource = root / "main.res"
            rc.write_text(
                "#define IDD_LOGIN 100\n"
                "#define IDC_STATIC -1\n"
                "#define IDC_EDIT1 1001\n",
                encoding="utf-8",
            )
            resource.write_bytes(
                res_record(5, 100, standard_dialog_payload())
            )
            faithful = BatchConverter().convert(
                ConversionRequest(
                    project_root=root,
                    output_dir=root / "faithful",
                    input_groups=(InputGroup((rc,), (resource,)),),
                    qt_check=QtCheckMode.OFF,
                )
            )
            simplified = BatchConverter().convert(
                ConversionRequest(
                    project_root=root,
                    output_dir=root / "simplified",
                    input_groups=(InputGroup((rc,), (resource,)),),
                    layout_mode=LayoutMode.SIMPLIFIED,
                    qt_check=QtCheckMode.OFF,
                )
            )
            faithful_xml = ET.parse(faithful.forms[0].output).getroot()
            simplified_xml = ET.parse(simplified.forms[0].output).getroot()
            report = json.loads(
                simplified.report_path.read_text(encoding="utf-8")
            )

        self.assertEqual(
            faithful_xml.find("./widget/layout").get("class"),
            "QGridLayout",
        )
        self.assertEqual(faithful.forms[0].layout_mode_requested, "faithful")
        self.assertEqual(faithful.forms[0].layout_mode_used, "faithful")
        self.assertEqual(
            simplified_xml.find("./widget/layout/item/layout").get("class"),
            "QHBoxLayout",
        )
        self.assertEqual(
            simplified.forms[0].layout_mode_requested,
            "simplified",
        )
        self.assertEqual(simplified.forms[0].layout_mode_used, "simplified")
        self.assertGreater(simplified.forms[0].simplified_regions, 0)
        self.assertGreater(
            simplified.forms[0].editability_score,
            faithful.forms[0].editability_score,
        )
        self.assertEqual(report["layout_mode"], "simplified")
        self.assertEqual(
            report["forms"][0]["layout_transformations"],
            ["grid-to-hbox:1"],
        )
        self.assertFalse(
            simplified_xml.findall(
                ".//property[@name='rc2uiInternal']"
            )
        )

    @unittest.skipUnless(
        discover_qt_binding().available,
        "Qt 6 binding is not installed",
    )
    def test_simplified_batch_passes_source_geometry_and_font_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            resource = root / "main.res"
            rc.write_text(
                "#define IDD_LOGIN 100\n"
                "#define IDC_STATIC -1\n"
                "#define IDC_EDIT1 1001\n",
                encoding="utf-8",
            )
            resource.write_bytes(res_record(5, 100, standard_dialog_payload()))
            result = BatchConverter().convert(
                ConversionRequest(
                    project_root=root,
                    output_dir=root / "simplified",
                    input_groups=(InputGroup((rc,), (resource,)),),
                    layout_mode=LayoutMode.SIMPLIFIED,
                    qt_check=QtCheckMode.REQUIRED,
                    qt_font_scale=1.5,
                )
            )
            assert result.qt_report_path is not None
            qt_report = json.loads(
                result.qt_report_path.read_text(encoding="utf-8")
            )

        self.assertEqual(result.error_count, 0)
        self.assertTrue(qt_report["forms"][0]["font_test"]["passed"])
        self.assertFalse(
            any(
                diagnostic["severity"] == "error"
                for diagnostic in qt_report["diagnostics"]
            )
        )

    def test_root_class_and_name_use_original_rc_dialog_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            resource = root / "main.res"
            rc.write_text(
                "#define IDD_FIRST_ALIAS 100\n"
                "#define IDD_ACTUAL_DIALOG 100\n"
                "LANGUAGE 9, 1\n"
                "IDD_ACTUAL_DIALOG DIALOGEX 0, 0, 180, 70\n",
                encoding="utf-8",
            )
            resource.write_bytes(
                res_record(5, 100, standard_dialog_payload(), language=1033)
            )
            naming_map = NamingMap.from_table(
                tomllib.loads(
                    "[[rules]]\n"
                    'name = "renamed-dialog"\n'
                    'kind = "dialog"\n'
                    'id_regex = "IDD_FIRST_ALIAS"\n'
                    'name_template = "account_settings_dialog"\n'
                ),
                path=root / "rc2ui.toml",
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(InputGroup((rc,), (resource,)),),
                rules=ProjectRules(naming=naming_map),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)
            xml = ET.parse(result.forms[0].output).getroot()

        self.assertEqual(result.error_count, 0)
        self.assertEqual(xml.findtext("class"), "IDD_ACTUAL_DIALOG")
        self.assertEqual(xml.find("widget").get("class"), "QDialog")
        self.assertEqual(xml.find("widget").get("name"), "IDD_ACTUAL_DIALOG")
        self.assertEqual(result.forms[0].rc_id, "IDD_ACTUAL_DIALOG")
        self.assertEqual(result.forms[0].object_name, "IDD_ACTUAL_DIALOG")
        self.assertEqual(result.forms[0].output.name, "IDD_ACTUAL_DIALOG.ui")

    def test_control_map_type_id_binding_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            resource = root / "main.res"
            rc.write_text(
                "#define IDD_LOGIN 100\n"
                "#define IDC_STATIC -1\n"
                "#define IDC_EDIT1 1001\n",
                encoding="utf-8",
            )
            resource.write_bytes(
                res_record(5, 100, standard_dialog_payload())
            )
            control_map = ControlMap.from_table(
                tomllib.loads(
                    "[[widgets]]\n"
                    'name = "date-editor"\n'
                    'qt_class = "QDateEdit"\n'
                    'role = "input"\n'
                    "expands_horizontally = true\n"
                    "[[bindings]]\n"
                    'name = "login-project-controls"\n'
                    "controls = [\n"
                    '  { win_class = "Edit", id = "IDC_EDIT1", '
                    'widget = "date-editor", runtime_configured = ["date"] },\n'
                    '  { win_class = "Edit", id = "IDC_UNUSED", '
                    'widget = "date-editor" },\n'
                    "]\n"
                ),
                path=root / "rc2ui.toml",
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(InputGroup((rc,), (resource,)),),
                rules=ProjectRules(controls=control_map),
                config_path=root / "rc2ui.toml",
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)
            xml = ET.parse(result.forms[0].output).getroot()
            report = json.loads(result.report_path.read_text("utf-8"))

        self.assertEqual(result.error_count, 0)
        self.assertIsNotNone(xml.find(".//widget[@class='QDateEdit']"))
        artifact = result.forms[0].controls[1]
        self.assertEqual(
            artifact.mapping_rule,
            "login-project-controls[Edit:IDC_EDIT1]",
        )
        self.assertEqual(artifact.runtime_configured, ("date",))
        report_control = report["forms"][0]["controls"][1]
        self.assertEqual(report_control["runtime_configured"], ["date"])
        unused = [
            item
            for item in result.diagnostics
            if item.code == "control-map.unused-rule"
        ]
        self.assertEqual(len(unused), 1)
        self.assertIn("IDC_UNUSED", unused[0].message)

    def test_semantic_rule_replaces_and_reports_edit_updown_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            resource = root / "main.res"
            output = root / "generated"
            rc.write_text(
                "#define IDD_PARAMETERS 100\n"
                "#define IDC_STATIC -1\n"
                "#define IDC_VALUE 1001\n"
                "#define IDC_VALUE_SPIN 1002\n",
                encoding="utf-8",
            )
            resource.write_bytes(
                res_record(5, 100, edit_updown_dialog_payload(), language=1033)
            )
            semantic_map = SemanticMap.from_table(
                tomllib.loads(
                    "[[rules]]\n"
                    'name = "floating-values"\n'
                    'kind = "edit-updown"\n'
                    'action = "replace"\n'
                    'primary_id = "IDC_VALUE"\n'
                    'member_id = "IDC_VALUE_SPIN"\n'
                    'result = "QDoubleSpinBox"\n'
                    "[rules.properties]\n"
                    "minimum = -1000.0\n"
                    "maximum = 1000.0\n"
                    "decimals = 2\n"
                ),
                path=root / "rc2ui.toml",
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=output,
                input_groups=(InputGroup((rc,), (resource,)),),
                rules=ProjectRules(semantics=semantic_map),
                config_path=root / "rc2ui.toml",
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)
            ui = ET.parse(result.forms[0].output).getroot()
            report = json.loads(result.report_path.read_text("utf-8"))

        self.assertEqual(result.error_count, 0)
        self.assertIsNotNone(ui.find(".//widget[@class='QDoubleSpinBox']"))
        self.assertIsNone(ui.find(".//widget[@class='QLineEdit']"))
        self.assertEqual(len(result.forms[0].compounds), 1)
        compound = result.forms[0].compounds[0]
        self.assertEqual(compound.source_ids, ("IDC_VALUE", "IDC_VALUE_SPIN"))
        self.assertEqual(compound.action, "replace")
        self.assertEqual(compound.geometry, "autobuddy-right")
        self.assertEqual(compound.rule_name, "floating-values")
        self.assertTrue(result.forms[0].controls[1].emitted)
        self.assertFalse(result.forms[0].controls[2].emitted)
        self.assertEqual(
            report["forms"][0]["compounds"][0]["result_class"],
            "QDoubleSpinBox",
        )
        self.assertEqual(
            report["forms"][0]["compounds"][0]["geometry"],
            "autobuddy-right",
        )

    def test_exact_control_compound_reuses_profile_name_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            resource = root / "main.res"
            rc.write_text(
                "#define IDD_LOGIN 100\n"
                "#define IDC_STATIC -1\n"
                "#define IDC_EDIT1 1001\n",
                encoding="utf-8",
            )
            resource.write_bytes(
                res_record(5, 100, standard_dialog_payload(), language=1033)
            )
            manifest = root / "rc2ui.toml"
            manifest.write_text(
                (
                    "version = 1\n"
                    'project_root = "."\n'
                    'output = "generated"\n'
                    'qt_check = "off"\n'
                    "[[input_groups]]\n"
                    'rc = ["main.rc"]\n'
                    'resources = ["main.res"]\n'
                    "[[naming.rules]]\n"
                    'name = "selector-name"\n'
                    'kind = "control"\n'
                    "[naming.rules.names]\n"
                    'IDC_STATIC = "compositeWidget"\n'
                    "[[controls.widgets]]\n"
                    'name = "selector"\n'
                    'qt_class = "Example::CompositeWidget"\n'
                    'header = "example/compositewidget.h"\n'
                    'extends = "QWidget"\n'
                    'role = "input"\n'
                    "expands_horizontally = true\n"
                    "[[controls.compounds]]\n"
                    'name = "login-selector"\n'
                    'widget = "selector"\n'
                    'primary = { win_class = "Static", '
                    'id = "IDC_STATIC" }\n'
                    "members = [\n"
                    '  { win_class = "Edit", id = "IDC_EDIT1" },\n'
                    "]\n"
                ),
                encoding="utf-8",
            )
            request = load_manifest(manifest)

            result = BatchConverter().convert(request)
            ui = ET.parse(result.forms[0].output).getroot()
            report = json.loads(result.report_path.read_text("utf-8"))

        self.assertEqual(result.error_count, 0)
        selector = ui.find(".//widget[@class='Example::CompositeWidget']")
        self.assertIsNotNone(selector)
        self.assertEqual(selector.get("name"), "compositeWidget")
        self.assertEqual(len(result.forms[0].compounds), 1)
        self.assertTrue(result.forms[0].controls[0].emitted)
        self.assertFalse(result.forms[0].controls[1].emitted)
        self.assertEqual(
            result.forms[0].controls[0].mapping_rule,
            "login-selector",
        )
        compound = report["forms"][0]["compounds"][0]
        self.assertEqual(compound["kind"], "control-set")
        self.assertEqual(compound["source_ids"], ["IDC_STATIC", "IDC_EDIT1"])
        self.assertEqual(compound["result_class"], "Example::CompositeWidget")
        self.assertNotIn(
            "control-map.unused-compound",
            {item.code for item in result.diagnostics},
        )

    def test_unused_exact_control_compound_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            resource = root / "main.res"
            rc.write_text(
                "#define IDD_LOGIN 100\n"
                "#define IDC_STATIC -1\n"
                "#define IDC_EDIT1 1001\n",
                encoding="utf-8",
            )
            resource.write_bytes(res_record(5, 100, standard_dialog_payload()))
            control_map = ControlMap.from_table(
                tomllib.loads(
                    "[[widgets]]\n"
                    'name = "selector"\n'
                    'qt_class = "Example::CompositeWidget"\n'
                    'header = "example/compositewidget.h"\n'
                    'extends = "QWidget"\n'
                    'role = "input"\n'
                    "expands_horizontally = true\n"
                    "[[compounds]]\n"
                    'name = "missing-selector"\n'
                    'widget = "selector"\n'
                    'primary = { win_class = "Static", '
                    'id = "IDC_STATIC" }\n'
                    "members = [\n"
                    '  { win_class = "Edit", id = "IDC_MISSING" },\n'
                    "]\n"
                ),
                path=root / "rc2ui.toml",
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(InputGroup((rc,), (resource,)),),
                rules=ProjectRules(controls=control_map),
                config_path=root / "rc2ui.toml",
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.error_count, 0)
        self.assertIn(
            "control-map.unused-compound",
            {item.code for item in result.diagnostics},
        )

    def test_suggestions_disambiguate_every_repeated_id_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            res = root / "main.res"
            rc.write_text(
                "#define IDD_LABELS 100\n#define IDC_STATIC -1\n",
                encoding="utf-8",
            )
            res.write_bytes(
                res_record(5, 100, repeated_static_dialog_payload())
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(InputGroup((rc,), (res,)),),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)
            suggestion_data = tomllib.loads(
                result.suggestions_path.read_text(encoding="utf-8")
            )
            suggestion_map = NamingMap.from_table(
                suggestion_data["naming"],
                path=result.suggestions_path,
            )

        static_rules = [
            rule
            for rule in suggestion_map.rules
            if rule.id_regex.pattern == "IDC_STATIC"
        ]
        self.assertEqual(
            [rule.occurrence for rule in static_rules],
            [1, 2],
        )

    def test_malformed_pe_is_reported_without_crashing_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            executable = root / "broken.exe"
            rc.write_text("", encoding="utf-8")
            executable.write_bytes(b"MZ" + b"\0" * 30)
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(InputGroup((rc,), (executable,)),),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.error_count, 1)
        self.assertEqual(result.diagnostics[-1].code, "resource.read-error")
        self.assertEqual(result.forms, ())

    def test_rc_and_pe_binary_generate_dialog_forms(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            executable = root / "application.exe"
            output = root / "generated"
            rc.write_text(
                "#define IDD_LOGIN 100\n"
                "#define IDC_STATIC -1\n"
                "#define IDC_EDIT1 1001\n",
                encoding="utf-8",
            )
            executable.write_bytes(
                pe_resource_binary(
                    (
                        (1033, standard_dialog_payload()),
                        (1049, standard_dialog_payload()),
                    ),
                    resource_id=100,
                    pe_plus=True,
                )
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=output,
                input_groups=(InputGroup((rc,), (executable,)),),
                default_language=1033,
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.error_count, 0)
        self.assertEqual(len(result.forms), 1)
        self.assertEqual(result.forms[0].rc_id, "IDD_LOGIN")
        self.assertEqual(result.forms[0].available_languages, (1033, 1049))

    def test_default_language_ui_and_other_language_ts_are_generated(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            executable = root / "application.exe"
            output = root / "generated"
            rc.write_text(
                "#define IDD_LOGIN 100\n"
                "#define IDC_STATIC -1\n"
                "#define IDC_EDIT1 1001\n",
                encoding="utf-8",
            )
            executable.write_bytes(
                pe_resource_binary(
                    (
                        (
                            1033,
                            standard_dialog_payload(
                                caption="Login",
                                label="&User name:",
                            ),
                        ),
                        (
                            1049,
                            standard_dialog_payload(
                                caption="Вход",
                                label="&Имя пользователя:",
                                dialog_rect=(0, 0, 220, 80),
                                label_rect=(7, 12, 80, 8),
                                edit_rect=(92, 10, 120, 14),
                            ),
                        ),
                    ),
                    resource_id=100,
                    pe_plus=True,
                )
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=output,
                input_groups=(InputGroup((rc,), (executable,)),),
                default_language=1049,
                ui_comments=False,
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)
            ui_root = ET.fromstring(result.forms[0].output.read_text("utf-8"))
            catalog = ET.parse(result.translation_paths[0]).getroot()
            report = json.loads(result.report_path.read_text("utf-8"))

        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.forms[0].language, 1049)
        self.assertEqual(result.forms[0].geometry_languages, (1033, 1049))
        self.assertEqual(result.forms[0].translation_languages, (1033,))
        self.assertEqual(len(result.translation_paths), 1)
        self.assertEqual(result.translation_paths[0].name, "rc2ui_en_US.ts")
        self.assertEqual(
            ui_root.findtext("./widget/property[@name='windowTitle']/string"),
            "Вход",
        )
        title = ui_root.find("./widget/property[@name='windowTitle']/string")
        self.assertNotIn("comment", title.attrib)
        self.assertNotIn("extracomment", title.attrib)
        self.assertFalse(ui_root.findall(".//string[@comment]"))
        self.assertFalse(ui_root.findall(".//string[@extracomment]"))
        self.assertEqual(
            ui_root.findtext(
                "./widget/property[@name='geometry']/rect/width"
            ),
            "385",
        )
        self.assertEqual(catalog.get("language"), "en_US")
        self.assertEqual(catalog.get("sourcelanguage"), "ru_RU")
        self.assertFalse(catalog.findall(".//comment"))
        self.assertTrue(catalog.findall(".//extracomment"))
        self.assertEqual(
            catalog.findtext("./context/name"),
            ui_root.findtext("class"),
        )
        self.assertEqual(ui_root.find("widget").get("name"), "IDD_LOGIN")
        translations = {
            message.findtext("source"): message.findtext("translation")
            for message in catalog.findall("./context/message")
        }
        self.assertEqual(translations["Вход"], "Login")
        self.assertEqual(
            translations["&Имя пользователя:"],
            "&User name:",
        )
        form_report = report["forms"][0]
        self.assertFalse(report["ui_comments"])
        self.assertIn("layout_evidence", form_report)
        self.assertIn("layout_relations", form_report)
        for relation in form_report["layout_relations"]:
            self.assertIn("confidence", relation)
            self.assertIn("supporting_languages", relation)
            self.assertIn("eligible_languages", relation)

    def test_missing_default_language_is_error_for_multilingual_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            executable = root / "application.exe"
            rc.write_text("#define IDD_LOGIN 100\n", encoding="utf-8")
            executable.write_bytes(
                pe_resource_binary(
                    (
                        (1049, standard_dialog_payload(caption="Вход")),
                        (1031, standard_dialog_payload(caption="Anmeldung")),
                    )
                )
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(InputGroup((rc,), (executable,)),),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.forms, ())
        self.assertEqual(result.error_count, 1)
        self.assertEqual(
            result.diagnostics[-1].code,
            "language.default-unavailable",
        )

    def test_unparseable_default_is_not_replaced_by_translation(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            executable = root / "application.exe"
            rc.write_text("#define IDD_LOGIN 100\n", encoding="utf-8")
            executable.write_bytes(
                pe_resource_binary(
                    (
                        (1033, b"broken"),
                        (1049, standard_dialog_payload(caption="Вход")),
                    )
                )
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(InputGroup((rc,), (executable,)),),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.forms, ())
        self.assertIn(
            "dialog.parse-error",
            [diagnostic.code for diagnostic in result.diagnostics],
        )

    def test_one_rc_resource_group_generates_one_ui_per_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            res = root / "main.res"
            output = root / "generated"
            rc.write_text(
                "#define IDD_LOGIN 100\n"
                "#define IDD_EXTENDED 200\n"
                "#define IDC_STATIC -1\n"
                "#define IDC_EDIT1 1001\n"
                "#define IDOK 1\n",
                encoding="utf-8",
            )
            res.write_bytes(
                null_res_record()
                + res_record(5, 100, standard_dialog_payload(), language=1033)
                + res_record(5, 100, standard_dialog_payload(), language=1049)
                + res_record(5, 200, extended_dialog_payload(), language=1033)
            )
            naming_map = NamingMap.from_table(
                tomllib.loads(
                    "[[rules]]\n"
                    'name = "dialogs"\n'
                    'kind = "dialog"\n'
                    "source_regex = 'main\\.rc'\n"
                    "[rules.names]\n"
                    'IDD_LOGIN = "LOGIN_DIALOG"\n'
                    'IDD_EXTENDED = "EXTENDED_DIALOG"\n'
                    "[[rules]]\n"
                    'name = "standard-buttons"\n'
                    'kind = "control"\n'
                    "[rules.names]\n"
                    'IDOK = "OK_BUTTON"\n'
                ),
                path=root / "rc2ui.toml",
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=output,
                rules=ProjectRules(naming=naming_map),
                config_path=root / "rc2ui.toml",
                input_groups=(InputGroup((rc,), (res,)),),
                default_language=1033,
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

            self.assertEqual(result.error_count, 0)
            self.assertEqual(len(result.forms), 2)
            outputs = {form.output.name for form in result.forms}
            self.assertEqual(outputs, {"IDD_LOGIN.ui", "IDD_EXTENDED.ui"})
            for form in result.forms:
                root_widget_name = ET.parse(form.output).getroot().find(
                    "./widget"
                ).get("name")
                self.assertEqual(form.output.stem, root_widget_name)
                self.assertEqual(form.output.stem, form.object_name)
                self.assertEqual(
                    form.available_languages,
                    (1033, 1049) if form.rc_id == "IDD_LOGIN" else (1033,),
                )
                validate_ui_xml(form.output.read_text(encoding="utf-8"))
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["forms"], 2)
            self.assertTrue(result.suggestions_path.is_file())
            suggestions = result.suggestions_path.read_text(encoding="utf-8")
            self.assertIn("userNameEdit", suggestions)
            self.assertIn("[naming.rules.names]", suggestions)
            suggestion_data = tomllib.loads(suggestions)
            suggestion_map = NamingMap.from_table(
                suggestion_data["naming"],
                path=result.suggestions_path,
            )
            self.assertGreater(len(suggestion_map.rules), 0)
            first_output = {
                form.output: form.output.read_bytes() for form in result.forms
            }

            from_suggestions = BatchConverter().convert(
                replace(
                    request,
                    rules=replace(request.rules, naming=suggestion_map),
                )
            )

            self.assertEqual(from_suggestions.error_count, 0)
            self.assertFalse(
                any(
                    item.code == "naming-map.unused-rule"
                    for item in from_suggestions.diagnostics
                )
            )

            repeated = BatchConverter().convert(request)

            self.assertEqual(repeated.error_count, 0)
            self.assertEqual(
                first_output,
                {form.output: form.output.read_bytes() for form in repeated.forms},
            )

    def test_input_group_dialog_selection_filters_compiled_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            res = root / "main.res"
            rc.write_text(
                "#define IDD_ALPHA 100\n"
                "#define IDD_BETA 200\n"
                "#define IDD_REPORT_FINAL 300\n",
                encoding="utf-8",
            )
            res.write_bytes(
                null_res_record()
                + res_record(5, 100, standard_dialog_payload(), language=1033)
                + res_record(5, 200, extended_dialog_payload(), language=1033)
                + res_record(5, 300, standard_dialog_payload(), language=1033)
            )
            cases = (
                (DialogSelection(exact=("IDD_ALPHA",)), {"IDD_ALPHA"}),
                (DialogSelection(exact=("#200",)), {"IDD_BETA"}),
                (
                    DialogSelection(regex=(r"IDD_REPORT_.*",)),
                    {"IDD_REPORT_FINAL"},
                ),
                (
                    DialogSelection(
                        exact=("IDD_ALPHA",),
                        regex=(r"IDD_BETA",),
                    ),
                    {"IDD_ALPHA", "IDD_BETA"},
                ),
            )

            for index, (selection, expected) in enumerate(cases):
                with self.subTest(selection=selection):
                    request = ConversionRequest(
                        project_root=root,
                        output_dir=root / f"generated-{index}",
                        input_groups=(
                            InputGroup(
                                (rc,),
                                (res,),
                                dialog_selection=selection,
                            ),
                        ),
                        qt_check=QtCheckMode.OFF,
                    )

                    result = BatchConverter().convert(request)

                    self.assertEqual(result.error_count, 0)
                    self.assertEqual(
                        {form.rc_id for form in result.forms},
                        expected,
                    )

            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["input_groups"][0]["dialogs"], ["IDD_ALPHA"])
            self.assertEqual(
                report["input_groups"][0]["dialog_regex"],
                ["IDD_BETA"],
            )

    def test_automatic_dialog_name_collision_uses_resource_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            res = root / "main.res"
            output = root / "generated"
            rc.write_text("", encoding="utf-8")
            res.write_bytes(
                null_res_record()
                + res_record(5, 100, standard_dialog_payload(), language=1033)
                + res_record(5, 200, standard_dialog_payload(), language=1033)
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=output,
                input_groups=(InputGroup((rc,), (res,)),),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

            self.assertEqual(result.error_count, 0)
            self.assertEqual(len(result.forms), 2)
            self.assertEqual(
                {form.object_name for form in result.forms},
                {"loginDialog", "loginDialog_id200"},
            )
            self.assertEqual(
                {form.output.name for form in result.forms},
                {"loginDialog.ui", "loginDialog_id200.ui"},
            )
            diagnostic = next(
                item
                for item in result.diagnostics
                if item.code == "output.name-disambiguated"
            )
            self.assertEqual(diagnostic.severity, "info")
            self.assertIn("main.rc:#100", diagnostic.message)
            self.assertIn("loginDialog_id200", diagnostic.message)
            for form in result.forms:
                ui_text = form.output.read_text(encoding="utf-8")
                validate_ui_xml(ui_text)
                self.assertEqual(
                    ET.fromstring(ui_text).find("./widget").get("name"),
                    form.object_name,
                )
                self.assertEqual(form.output.stem, form.object_name)
            first_output = {
                form.output: form.output.read_bytes() for form in result.forms
            }

            repeated = BatchConverter().convert(request)

            self.assertEqual(repeated.error_count, 0)
            self.assertEqual(
                first_output,
                {form.output: form.output.read_bytes() for form in repeated.forms},
            )

    def test_explicit_dialog_name_collision_remains_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            res = root / "main.res"
            output = root / "generated"
            rc.write_text("", encoding="utf-8")
            res.write_bytes(
                null_res_record()
                + res_record(5, 100, standard_dialog_payload(), language=1033)
                + res_record(5, 200, standard_dialog_payload(), language=1033)
            )
            naming_map = NamingMap.from_table(
                tomllib.loads(
                    "[[rules]]\n"
                    'name = "shared-dialog-names"\n'
                    'kind = "dialog"\n'
                    "[rules.names]\n"
                    '"#100" = "SHARED_DIALOG"\n'
                    '"#200" = "SHARED_DIALOG"\n'
                ),
                path=root / "rc2ui.toml",
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=output,
                rules=ProjectRules(naming=naming_map),
                config_path=root / "rc2ui.toml",
                input_groups=(InputGroup((rc,), (res,)),),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

            self.assertEqual(result.error_count, 1)
            self.assertEqual(len(result.forms), 1)
            diagnostic = next(
                item
                for item in result.diagnostics
                if item.code == "output.collision"
            )
            self.assertIn("main.rc:#100", diagnostic.message)
            self.assertIn("main.rc:#200", diagnostic.message)
        self.assertIn("explicit root object name", diagnostic.message)

    def test_duplicate_input_group_does_not_create_a_second_form(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            res = root / "main.res"
            output = root / "generated"
            rc.write_text("", encoding="utf-8")
            res.write_bytes(
                null_res_record()
                + res_record(5, 100, standard_dialog_payload(), language=1033)
            )
            group = InputGroup((rc,), (res,))
            request = ConversionRequest(
                project_root=root,
                output_dir=output,
                input_groups=(group, group),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

            self.assertEqual(result.error_count, 1)
            self.assertEqual(len(result.forms), 1)
            diagnostic = next(
                item
                for item in result.diagnostics
                if item.code == "output.collision"
            )
            self.assertIn("duplicate dialog input", diagnostic.message)
            self.assertIn("main.rc:#100", diagnostic.message)

    def test_group_merges_languages_from_multiple_resource_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            english = root / "application.res"
            russian = root / "application.ru-RU.res"
            rc.write_text("#define IDD_LOGIN 100\n", encoding="utf-8")
            english.write_bytes(
                res_record(5, 100, standard_dialog_payload(), language=1033)
            )
            russian.write_bytes(
                res_record(5, 100, standard_dialog_payload(), language=1049)
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(
                    InputGroup((rc,), (english, russian)),
                ),
                default_language=1049,
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.error_count, 0)
        self.assertEqual(len(result.forms), 1)
        self.assertEqual(result.forms[0].language, 1049)
        self.assertEqual(result.forms[0].available_languages, (1033, 1049))

    def test_dialog_declarations_choose_owner_despite_shared_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            header = root / "resource.h"
            main_rc = root / "main.rc"
            admin_rc = root / "admin.rc"
            main_res = root / "main.res"
            admin_res = root / "admin.res"
            header.write_text(
                "#define IDD_MAIN 100\n#define IDD_ADMIN 200\n",
                encoding="utf-8",
            )
            main_rc.write_text(
                '#include "resource.h"\nIDD_MAIN DIALOGEX 0, 0, 100, 50\n',
                encoding="utf-8",
            )
            admin_rc.write_text(
                '#include "resource.h"\nIDD_ADMIN DIALOG 0, 0, 100, 50\n',
                encoding="utf-8",
            )
            main_res.write_bytes(
                res_record(5, 100, standard_dialog_payload(), language=1033)
            )
            admin_res.write_bytes(
                res_record(5, 200, standard_dialog_payload(), language=1033)
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(
                    InputGroup(
                        (main_rc, admin_rc),
                        (main_res, admin_res),
                    ),
                ),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.error_count, 0)
        self.assertEqual(len(result.forms), 2)
        self.assertEqual(
            {(form.source, form.rc_id) for form in result.forms},
            {("main.rc", "IDD_MAIN"), ("admin.rc", "IDD_ADMIN")},
        )

    def test_conflicting_same_language_payload_skips_logical_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            first = root / "first.res"
            second = root / "second.res"
            rc.write_text("#define IDD_CONFLICT 100\n", encoding="utf-8")
            first.write_bytes(
                res_record(5, 100, standard_dialog_payload(), language=1033)
            )
            second.write_bytes(
                res_record(5, 100, extended_dialog_payload(), language=1033)
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(InputGroup((rc,), (first, second)),),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.error_count, 1)
        self.assertEqual(result.forms, ())
        self.assertEqual(
            result.diagnostics[-1].code,
            "resource.conflicting-variant",
        )

    def test_shared_id_without_declaration_reports_ambiguous_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            header = root / "resource.h"
            first_rc = root / "first.rc"
            second_rc = root / "second.rc"
            resources = root / "application.res"
            header.write_text("#define IDD_SHARED 100\n", encoding="utf-8")
            first_rc.write_text('#include "resource.h"\n', encoding="utf-8")
            second_rc.write_text('#include "resource.h"\n', encoding="utf-8")
            resources.write_bytes(
                res_record(5, 100, standard_dialog_payload(), language=1033)
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(
                    InputGroup((first_rc, second_rc), (resources,)),
                ),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.forms, ())
        self.assertEqual(result.error_count, 1)
        self.assertEqual(
            result.diagnostics[-1].code,
            "input.ambiguous-dialog-owner",
        )

    def test_unresolved_rc_condition_still_identifies_dialog_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            header = root / "resource.h"
            first_rc = root / "first.rc"
            second_rc = root / "second.rc"
            resources = root / "application.res"
            header.write_text(
                "#define IDD_SHARED 100\n",
                encoding="utf-8",
            )
            first_rc.write_text(
                '#include "resource.h"\n'
                "#if EXTERNAL_LANGUAGE_SELECTION\n"
                "LANGUAGE LANG_RUSSIAN, SUBLANG_DEFAULT\n"
                "IDD_SHARED DIALOG 0, 0, 180, 70\n"
                "#endif\n",
                encoding="utf-8",
            )
            second_rc.write_text('#include "resource.h"\n', encoding="utf-8")
            resources.write_bytes(
                res_record(5, 100, standard_dialog_payload(), language=1033)
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(
                    InputGroup((first_rc, second_rc), (resources,)),
                ),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.error_count, 0)
        self.assertEqual(len(result.forms), 1)
        self.assertEqual(result.forms[0].source, "first.rc")
        self.assertIn(
            "symbols.condition-assumed-true",
            [item.code for item in result.diagnostics],
        )

    def test_language_specific_rc_uses_default_language_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            header = root / "resource.h"
            english_rc = root / "english.rc"
            russian_rc = root / "russian.rc"
            resources = root / "application.exe"
            header.write_text("#define IDD_SHARED 100\n", encoding="utf-8")
            english_rc.write_text(
                '#include "resource.h"\n'
                "#if EXTERNAL_ENGLISH_RESOURCES\n"
                "LANGUAGE LANG_ENGLISH, SUBLANG_ENGLISH_US\n"
                "IDD_SHARED DIALOG 0, 0, 180, 70\n"
                "#endif\n",
                encoding="utf-8",
            )
            russian_rc.write_text(
                '#include "resource.h"\n'
                "#if EXTERNAL_RUSSIAN_RESOURCES\n"
                "LANGUAGE LANG_RUSSIAN, SUBLANG_DEFAULT\n"
                "IDD_SHARED DIALOG 0, 0, 180, 70\n"
                "#endif\n",
                encoding="utf-8",
            )
            resources.write_bytes(
                pe_resource_binary(
                    (
                        (1033, standard_dialog_payload(caption="Login")),
                        (1049, standard_dialog_payload(caption="Вход")),
                    )
                )
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(
                    InputGroup((russian_rc, english_rc), (resources,)),
                ),
                default_language=1033,
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.error_count, 0)
        self.assertEqual(len(result.forms), 1)
        self.assertEqual(result.forms[0].source, "english.rc")
        self.assertEqual(result.forms[0].available_languages, (1033, 1049))

    def test_language_partition_recovers_owner_when_id_macro_is_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            header = root / "resource.h"
            english_rc = root / "english.rc"
            russian_rc = root / "russian.rc"
            resources = root / "application.exe"
            header.write_text(
                "#if EXTERNAL_PRODUCT\n"
                "#define IDD_SHARED 100\n"
                "#endif\n",
                encoding="utf-8",
            )
            english_rc.write_text(
                '#include "resource.h"\n'
                "LANGUAGE LANG_ENGLISH, SUBLANG_ENGLISH_US\n"
                "IDD_SHARED DIALOG 0, 0, 180, 70\n",
                encoding="utf-8",
            )
            russian_rc.write_text(
                '#include "resource.h"\n'
                "LANGUAGE LANG_RUSSIAN, SUBLANG_DEFAULT\n"
                "IDD_SHARED DIALOG 0, 0, 180, 70\n",
                encoding="utf-8",
            )
            resources.write_bytes(
                pe_resource_binary(
                    (
                        (1033, standard_dialog_payload(caption="Login")),
                        (1049, standard_dialog_payload(caption="Вход")),
                    )
                )
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(
                    InputGroup((russian_rc, english_rc), (resources,)),
                ),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.error_count, 0)
        self.assertEqual(len(result.forms), 1)
        self.assertEqual(result.forms[0].source, "english.rc")

    def test_language_partition_also_handles_single_language_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            header = root / "resource.h"
            english_rc = root / "english.rc"
            russian_rc = root / "russian.rc"
            resources = root / "application.res"
            header.write_text(
                "#if EXTERNAL_PRODUCT\n"
                "#define IDD_SHARED 100\n"
                "#endif\n",
                encoding="utf-8",
            )
            english_rc.write_text(
                '#include "resource.h"\n'
                "LANGUAGE LANG_ENGLISH, SUBLANG_ENGLISH_US\n"
                "IDD_SHARED DIALOG 0, 0, 180, 70\n",
                encoding="utf-8",
            )
            russian_rc.write_text(
                '#include "resource.h"\n'
                "LANGUAGE LANG_RUSSIAN, SUBLANG_DEFAULT\n"
                "IDD_OTHER DIALOG 0, 0, 180, 70\n",
                encoding="utf-8",
            )
            resources.write_bytes(
                res_record(
                    5,
                    100,
                    standard_dialog_payload(caption="Login"),
                    language=1033,
                )
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=root / "generated",
                input_groups=(
                    InputGroup((russian_rc, english_rc), (resources,)),
                ),
                qt_check=QtCheckMode.OFF,
            )

            result = BatchConverter().convert(request)

        self.assertEqual(result.error_count, 0)
        self.assertEqual(len(result.forms), 1)
        self.assertEqual(result.forms[0].source, "english.rc")

    def test_required_qt_check_reports_unavailable_pyqt(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            res = root / "main.res"
            output = root / "generated"
            rc.write_text("", encoding="utf-8")
            res.write_bytes(
                null_res_record()
                + res_record(5, 100, standard_dialog_payload(), language=1033)
            )
            request = ConversionRequest(
                project_root=root,
                output_dir=output,
                input_groups=(InputGroup((rc,), (res,)),),
                qt_check=QtCheckMode.REQUIRED,
            )

            with patch(
                "rc2ui.qtcheck.runner.discover_qt_binding",
                return_value=QtBindingAvailability(False, "not installed"),
            ):
                result = BatchConverter().convert(request)

            self.assertEqual(result.error_count, 1)
            self.assertEqual(result.diagnostics[-1].code, "qt.unavailable")
            self.assertEqual(
                result.qt_report_path,
                output / "rc2ui-qt-report.json",
            )
            self.assertTrue(result.qt_report_path.is_file())


if __name__ == "__main__":
    unittest.main()

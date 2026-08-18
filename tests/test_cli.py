from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from rc2ui.cli import main
from rc2ui.domain.diagnostics import Diagnostic, Severity
from rc2ui.qtcheck.model import QtCheckRun
from tests.resource_fixtures import (
    edit_updown_dialog_payload,
    pe_resource_binary,
    res_record,
    standard_dialog_payload,
)


class CliTests(unittest.TestCase):
    def test_convert_layout_mode_overrides_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            resource = root / "main.res"
            config = root / "rc2ui.toml"
            rc.write_text(
                "#define IDD_LOGIN 100\n#define IDC_EDIT1 1001\n",
                encoding="utf-8",
            )
            resource.write_bytes(
                res_record(5, 100, standard_dialog_payload())
            )
            config.write_text(
                "version = 1\n"
                'output = "generated"\n'
                'layout_mode = "faithful"\n'
                "ui_comments = true\n"
                'qt_check = "off"\n'
                "[[input_groups]]\n"
                'rc = ["main.rc"]\n'
                'resources = ["main.res"]\n',
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(
                io.StringIO()
            ), contextlib.redirect_stderr(io.StringIO()):
                exit_code = main(
                    [
                        "convert",
                        "--manifest",
                        str(config),
                        "--layout-mode",
                        "simplified",
                        "--no-ui-comments",
                    ]
                )
            ui = ET.parse(root / "generated/main/IDD_LOGIN.ui")

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            ui.find("./widget/layout/item/layout").get("class"),
            "QHBoxLayout",
        )
        for string in ui.findall(".//string"):
            self.assertNotIn("comment", string.attrib)
            self.assertNotIn("extracomment", string.attrib)

    def test_convert_uses_naming_rules_from_unified_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            resource = root / "main.res"
            config = root / "rc2ui.toml"
            rc.write_text(
                "#define IDD_LOGIN 100\n#define IDC_EDIT1 1001\n",
                encoding="utf-8",
            )
            resource.write_bytes(
                res_record(5, 100, standard_dialog_payload())
            )
            config.write_text(
                "version = 1\n"
                'output = "generated"\n'
                'qt_check = "off"\n'
                "[[input_groups]]\n"
                'rc = ["main.rc"]\n'
                'resources = ["main.res"]\n'
                "[[naming.rules]]\n"
                'name = "editors"\n'
                'kind = "control"\n'
                "[naming.rules.names]\n"
                'IDC_EDIT1 = "customDateEdit"\n'
                "[[controls.widgets]]\n"
                'name = "date-editor"\n'
                'qt_class = "QDateEdit"\n'
                'role = "input"\n'
                "[[controls.bindings]]\n"
                'name = "login-editors"\n'
                'controls = [{ win_class = "Edit", id = "IDC_EDIT1", '
                'widget = "date-editor" }]\n',
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main(
                    [
                        "convert",
                        "--manifest",
                        str(config),
                    ]
                )

            ui = (root / "generated/main/IDD_LOGIN.ui").read_text("utf-8")

        self.assertEqual(exit_code, 0, stderr.getvalue())
        self.assertIn('<class>IDD_LOGIN</class>', ui)
        self.assertIn('<widget class="QDialog" name="IDD_LOGIN">', ui)
        self.assertIn('class="QDateEdit" name="customDateEdit"', ui)

    def test_convert_uses_semantic_rules_from_unified_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            resource = root / "main.res"
            config = root / "rc2ui.toml"
            rc.write_text(
                "#define IDD_PARAMETERS 100\n"
                "#define IDC_STATIC -1\n"
                "#define IDC_VALUE 1001\n"
                "#define IDC_VALUE_SPIN 1002\n",
                encoding="utf-8",
            )
            resource.write_bytes(
                res_record(5, 100, edit_updown_dialog_payload())
            )
            config.write_text(
                "version = 1\n"
                'output = "generated"\n'
                'qt_check = "off"\n'
                "[[input_groups]]\n"
                'rc = ["main.rc"]\n'
                'resources = ["main.res"]\n'
                "[[semantics.rules]]\n"
                'name = "parameter-values"\n'
                'kind = "edit-updown"\n'
                'action = "replace"\n'
                'primary_id = "IDC_VALUE"\n'
                'member_id = "IDC_VALUE_SPIN"\n'
                'result = "QDoubleSpinBox"\n'
                "[semantics.rules.properties]\n"
                "minimum = -1000.0\n"
                "maximum = 1000.0\n",
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(["convert", "--manifest", str(config)])
            ui = ET.parse(root / "generated/main/IDD_PARAMETERS.ui")

        self.assertEqual(exit_code, 0)
        self.assertIsNotNone(ui.find(".//widget[@class='QDoubleSpinBox']"))

    def test_validate_config_accepts_all_inline_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            path = root / "rc2ui.toml"
            path.write_text(
                "version = 1\n"
                'output = "generated"\n'
                "[[input_groups]]\n"
                'rc = ["main.rc"]\n'
                'resources = ["main.res"]\n'
                "[[naming.rules]]\n"
                'name = "controls"\n'
                'kind = "control"\n'
                "id_regex = 'IDC_(?P<name>[A-Z_]+)'\n"
                'name_template = "${name}_WIDGET"\n'
                "[controls]\n"
                "widgets = []\n"
                "rules = []\n"
                "bindings = []\n"
                "[semantics]\n"
                "rules = []\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["validate-config", str(path)])

        self.assertEqual(exit_code, 0)
        self.assertIn("valid configuration: 1 input group(s)", stdout.getvalue())
        self.assertIn("1 naming rule(s)", stdout.getvalue())

    def test_convert_command_accepts_pe_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            executable = root / "application.exe"
            rc.write_text("#define IDD_LOGIN 100\n", encoding="utf-8")
            executable.write_bytes(
                pe_resource_binary(((1033, standard_dialog_payload()),))
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "convert",
                        "--project-root",
                        str(root),
                        "--output",
                        "generated",
                        "--qt-check",
                        "off",
                        str(rc),
                        str(executable),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("generated 1 form(s)", stdout.getvalue())

    def test_convert_command_classifies_positional_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            res = root / "main.res"
            rc.write_text(
                "#define IDD_LOGIN 100\n"
                "#define IDC_STATIC -1\n"
                "#define IDC_EDIT1 1001\n",
                encoding="utf-8",
            )
            res.write_bytes(res_record(5, 100, standard_dialog_payload()))
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "convert",
                        "--project-root",
                        str(root),
                        "--output",
                        "generated",
                        "--qt-check",
                        "off",
                        str(rc),
                        str(res),
                    ]
                )

            self.assertEqual(exit_code, 0, stderr.getvalue())
            self.assertIn("generated 1 form(s)", stdout.getvalue())
            self.assertTrue((root / "generated/main/IDD_LOGIN.ui").is_file())

    def test_convert_command_accepts_two_rc_and_one_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            main_rc = root / "main.RC"
            admin_rc = root / "admin.rc"
            resources = root / "application.res"
            main_rc.write_text(
                "#define IDD_MAIN 100\nIDD_MAIN DIALOG 0, 0, 100, 50\n",
                encoding="utf-8",
            )
            admin_rc.write_text(
                "#define IDD_ADMIN 200\nIDD_ADMIN DIALOGEX 0, 0, 100, 50\n",
                encoding="utf-8",
            )
            resources.write_bytes(
                res_record(5, 100, standard_dialog_payload())
                + res_record(5, 200, standard_dialog_payload())
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "convert",
                        "--project-root",
                        str(root),
                        "--qt-check",
                        "off",
                        str(main_rc),
                        str(admin_rc),
                        str(resources),
                    ]
                )

        self.assertEqual(exit_code, 0, stderr.getvalue())
        self.assertIn("generated 2 form(s)", stdout.getvalue())

    def test_qt_check_command_prints_report_and_honors_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            ui = root / "sample.ui"
            report = root / "report.json"
            ui.write_text("<ui/>", encoding="utf-8")
            run = QtCheckRun(
                available=True,
                checked_forms=1,
                diagnostics=(
                    Diagnostic(
                        "qt.test-warning",
                        Severity.WARNING,
                        "test warning",
                        str(ui),
                    ),
                ),
                report_path=report,
                binding="PyQt6",
                binding_version="6.test",
                qt_version="6.test",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch(
                "rc2ui.cli.run_qt_checks",
                return_value=run,
            ) as run_checks, (
                contextlib.redirect_stdout(stdout)
            ), contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "qt-check",
                        str(ui),
                        "--report",
                        str(report),
                        "--font-scale",
                        "1.5",
                        "--strict",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertEqual(run_checks.call_args.kwargs["font_scale"], 1.5)
        self.assertIn("checked 1 form(s)", stdout.getvalue())
        self.assertIn("qt.test-warning", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rc2ui.application.manifest import load_manifest
from rc2ui.layout.mode import LayoutMode
from rc2ui.qtcheck.model import QtCheckMode


class ManifestTests(unittest.TestCase):
    def test_resolves_project_and_input_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            base = Path(directory_name)
            config = base / "config"
            config.mkdir()
            manifest = config / "rc2ui.toml"
            manifest.write_text(
                "version = 1\n"
                'project_root = ".."\n'
                'output = "generated"\n'
                'include_paths = ["include"]\n'
                'default_language = 1033\n'
                'rc_encoding = "cp1251"\n'
                'layout_mode = "simplified"\n'
                "ui_comments = false\n"
                'qt_check = "required"\n'
                'qt_preview = "previews"\n'
                "qt_font_scale = 1.5\n"
                "[naming]\n"
                "[[naming.rules]]\n"
                'name = "login-dialog"\n'
                'kind = "dialog"\n'
                'id_regex = "IDD_LOGIN"\n'
                'name_template = "LOGIN_DIALOG"\n'
                "[controls]\n"
                "widgets = []\n"
                "rules = []\n"
                "bindings = []\n"
                "[semantics]\n"
                "rules = []\n"
                "[defines]\nENTERPRISE = 1\n"
                "[[input_groups]]\n"
                'rc = ["resources/main.rc", "resources/admin.rc"]\n'
                'resources = ["build/application.exe", "build/app.mui"]\n'
                'dialogs = ["IDD_LOGIN", "#200"]\n'
                'dialog_regex = ["IDD_REPORT_.*"]\n',
                encoding="utf-8",
            )

            request = load_manifest(manifest)

        self.assertEqual(request.project_root, base.resolve())
        self.assertEqual(request.output_dir, base / "generated")
        self.assertEqual(
            request.input_groups[0].rc_files,
            (base / "resources/main.rc", base / "resources/admin.rc"),
        )
        self.assertEqual(
            request.input_groups[0].resource_files,
            (base / "build/application.exe", base / "build/app.mui"),
        )
        self.assertEqual(
            request.input_groups[0].dialog_selection.exact,
            ("IDD_LOGIN", "#200"),
        )
        self.assertEqual(
            request.input_groups[0].dialog_selection.regex,
            ("IDD_REPORT_.*",),
        )
        self.assertEqual(request.defines, (("ENTERPRISE", 1),))
        self.assertEqual(len(request.rules.naming.rules), 1)
        self.assertEqual(request.rules.controls.rules, ())
        self.assertEqual(request.rules.semantics.rules, ())
        self.assertEqual(request.config_path, manifest.resolve())
        self.assertEqual(request.rc_encoding, "cp1251")
        self.assertEqual(request.layout_mode, LayoutMode.SIMPLIFIED)
        self.assertFalse(request.ui_comments)
        self.assertEqual(request.qt_check, QtCheckMode.REQUIRED)
        self.assertEqual(request.qt_preview_dir, base / "previews")
        self.assertEqual(request.qt_font_scale, 1.5)

    def test_customization_sections_are_optional(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            base = Path(directory_name)
            manifest = base / "rc2ui.toml"
            manifest.write_text(
                "version = 1\n"
                'output = "generated"\n'
                "[[input_groups]]\n"
                'rc = ["main.rc"]\n'
                'resources = ["main.res"]\n',
                encoding="utf-8",
            )

            request = load_manifest(manifest)

        self.assertEqual(request.rules.naming.rules, ())
        self.assertEqual(request.rules.controls.rules, ())
        self.assertEqual(request.rules.semantics.rules, ())
        self.assertEqual(request.qt_check, QtCheckMode.AUTO)
        self.assertEqual(request.default_language, 1033)
        self.assertEqual(request.qt_font_scale, 1.0)
        self.assertEqual(request.layout_mode, LayoutMode.FAITHFUL)
        self.assertTrue(request.ui_comments)

    def test_rejects_non_boolean_ui_comments(self) -> None:
        for value in ('"false"', "0"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as name:
                manifest = Path(name, "rc2ui.toml")
                manifest.write_text(
                    "version = 1\n"
                    'output = "generated"\n'
                    f"ui_comments = {value}\n"
                    "[[input_groups]]\n"
                    'rc = ["main.rc"]\n'
                    'resources = ["main.res"]\n',
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "ui_comments must be a boolean",
                ):
                    load_manifest(manifest)

    def test_rejects_invalid_layout_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            manifest = Path(directory_name, "rc2ui.toml")
            manifest.write_text(
                "version = 1\n"
                'output = "generated"\n'
                'layout_mode = "editable-ish"\n'
                "[[input_groups]]\n"
                'rc = ["main.rc"]\n'
                'resources = ["main.res"]\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "layout_mode must be one of"):
                load_manifest(manifest)

    def test_rejects_invalid_qt_font_scale(self) -> None:
        for value in ("0", "-1", '"large"', "true"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as name:
                manifest = Path(name, "rc2ui.toml")
                manifest.write_text(
                    "version = 1\n"
                    'output = "generated"\n'
                    f"qt_font_scale = {value}\n"
                    "[[input_groups]]\n"
                    'rc = ["main.rc"]\n'
                    'resources = ["main.res"]\n',
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "qt_font_scale must be a positive finite number",
                ):
                    load_manifest(manifest)

    def test_rejects_invalid_dialog_selection(self) -> None:
        cases = (
            ('dialogs = []\n', "non-empty array"),
            ('dialogs = [100]\n', "array of strings"),
            ('dialog_regex = ["("]\n', "invalid dialog regex"),
        )
        for selection, message in cases:
            with (
                self.subTest(selection=selection),
                tempfile.TemporaryDirectory() as directory_name,
            ):
                manifest = Path(directory_name, "rc2ui.toml")
                manifest.write_text(
                    "version = 1\n"
                    'output = "generated"\n'
                    "[[input_groups]]\n"
                    'rc = ["main.rc"]\n'
                    'resources = ["main.res"]\n'
                    + selection,
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(ValueError, message):
                    load_manifest(manifest)

    def test_rejects_unknown_top_level_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            manifest = Path(directory_name, "rc2ui.toml")
            manifest.write_text(
                "version = 1\n"
                'output = "generated"\n'
                'extra_map = "names.toml"\n'
                "[[input_groups]]\n"
                'rc = ["main.rc"]\n'
                'resources = ["main.res"]\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unexpected top-level"):
                load_manifest(manifest)

    def test_rejects_invalid_inline_rule_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            manifest = Path(directory_name, "rc2ui.toml")
            manifest.write_text(
                "version = 1\n"
                'output = "generated"\n'
                "[[input_groups]]\n"
                'rc = ["main.rc"]\n'
                'resources = ["main.res"]\n'
                "[naming]\n"
                'rules = "not-an-array"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, r"\[\[naming\.rules\]\]"):
                load_manifest(manifest)

    def test_rejects_invalid_qt_check_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            manifest = Path(directory_name, "rc2ui.toml")
            manifest.write_text(
                "version = 1\n"
                'output = "generated"\n'
                'qt_check = "sometimes"\n'
                "[[input_groups]]\n"
                'rc = ["main.rc"]\n'
                'resources = ["main.res"]\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "qt_check must be one of"):
                load_manifest(manifest)

    def test_requires_input_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            manifest = Path(directory_name, "rc2ui.toml")
            manifest.write_text(
                "version = 1\n"
                'output = "generated"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, r"\[\[input_groups\]\]"):
                load_manifest(manifest)

    def test_rejects_empty_group_side(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            manifest = Path(directory_name, "rc2ui.toml")
            manifest.write_text(
                "version = 1\n"
                'output = "generated"\n'
                "[[input_groups]]\n"
                'rc = ["main.rc"]\n'
                "resources = []\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "non-empty array"):
                load_manifest(manifest)

    def test_rejects_unknown_rc_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            manifest = Path(directory_name, "rc2ui.toml")
            manifest.write_text(
                "version = 1\n"
                'output = "generated"\n'
                'rc_encoding = "not-a-code-page"\n'
                "[[input_groups]]\n"
                'rc = ["main.rc"]\n'
                'resources = ["main.res"]\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown rc_encoding"):
                load_manifest(manifest)

    def test_requires_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            manifest = Path(directory_name, "rc2ui.toml")
            manifest.write_text(
                'output = "generated"\n'
                "[[input_groups]]\n"
                'rc = ["main.rc"]\n'
                'resources = ["main.res"]\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "version must be 1"):
                load_manifest(manifest)


if __name__ == "__main__":
    unittest.main()

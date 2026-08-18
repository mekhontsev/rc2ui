from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rc2ui.adapters.headers.symbols import SymbolLoader


class SymbolLoaderTests(unittest.TestCase):
    def test_follows_includes_and_evaluates_integer_macros(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            (directory / "resource.h").write_text(
                "#define IDD_LOGIN 100\n"
                "#define IDC_STATIC -1\n"
                "#define IDC_USER (IDD_LOGIN << 4) + 1U\n"
                "#define IDC_USER_ALIAS IDC_USER\n",
                encoding="utf-8",
            )
            rc = directory / "main.rc"
            rc.write_text('#include "resource.h"\n', encoding="utf-8")

            result = SymbolLoader().load(rc)

        self.assertEqual(result.table.value_of("IDD_LOGIN"), 100)
        self.assertEqual(result.table.value_of("IDC_STATIC"), -1)
        self.assertEqual(result.table.value_of("IDC_USER"), 1601)
        self.assertEqual(
            result.table.symbols_for(1601), ("IDC_USER", "IDC_USER_ALIAS")
        )
        self.assertEqual(result.diagnostics, ())

    def test_honors_simple_preprocessor_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            rc = Path(directory_name, "main.rc")
            rc.write_text(
                "#ifdef ENTERPRISE\n"
                "#define IDD_MODE 200\n"
                "#else\n"
                "#define IDD_MODE 201\n"
                "#endif\n",
                encoding="utf-8",
            )

            result = SymbolLoader(predefined={"ENTERPRISE": 1}).load(rc)

        self.assertEqual(result.table.value_of("IDD_MODE"), 200)

    def test_reports_unresolved_object_macro_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            rc = Path(directory_name, "main.rc")
            rc.write_text("#define IDC_UNKNOWN EXTERNAL_VALUE\n", encoding="utf-8")

            result = SymbolLoader().load(rc)

        self.assertIsNone(result.table.value_of("IDC_UNKNOWN"))
        self.assertEqual(result.diagnostics[0].code, "symbols.unresolved-expression")

    def test_reads_utf16_windows_resource_headers(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            rc = Path(directory_name, "main.rc")
            rc.write_text("#define IDD_UNICODE 301\n", encoding="utf-16")

            result = SymbolLoader().load(rc)

        self.assertEqual(result.table.value_of("IDD_UNICODE"), 301)

    def test_unsupported_rc_condition_is_assumed_true(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            rc = Path(directory_name, "main.rc")
            rc.write_text(
                "#if SOME_FUNCTION(1)\n#define IDD_HIDDEN 1\n#endif\n",
                encoding="utf-8",
            )

            result = SymbolLoader().load(rc)

        self.assertEqual(result.table.value_of("IDD_HIDDEN"), 1)
        self.assertEqual(
            result.diagnostics[0].code,
            "symbols.condition-assumed-true",
        )

    def test_unsupported_header_condition_is_not_assumed_true(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            header = root / "project.h"
            header.write_text(
                "#if SOME_FUNCTION(1)\n#define IDD_HIDDEN 1\n#endif\n",
                encoding="utf-8",
            )
            rc = root / "main.rc"
            rc.write_text('#include "project.h"\n', encoding="utf-8")

            result = SymbolLoader().load(rc)

        self.assertIsNone(result.table.value_of("IDD_HIDDEN"))
        self.assertEqual(
            result.diagnostics[0].code,
            "symbols.unresolved-condition",
        )

    def test_unresolved_defined_macro_makes_whole_rc_condition_true(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            rc = Path(directory_name, "main.rc")
            rc.write_text(
                "#define SELECT_DIALOG PROJECT_CHOICE(7)\n"
                "#if !SELECT_DIALOG\n"
                "#define IDD_VISIBLE 1\n"
                "#endif\n",
                encoding="utf-8",
            )

            result = SymbolLoader().load(rc)

        self.assertEqual(result.table.value_of("IDD_VISIBLE"), 1)
        self.assertEqual(
            [item.code for item in result.diagnostics],
            [
                "symbols.unresolved-expression",
                "symbols.condition-assumed-true",
            ],
        )

    def test_ifdef_recognizes_macro_even_when_value_is_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            rc = Path(directory_name, "main.rc")
            rc.write_text(
                "#define SELECT_DIALOG PROJECT_CHOICE(7)\n"
                "#ifdef SELECT_DIALOG\n"
                "#define IDD_VISIBLE 1\n"
                "#endif\n",
                encoding="utf-8",
            )

            result = SymbolLoader().load(rc)

        self.assertEqual(result.table.value_of("IDD_VISIBLE"), 1)

    def test_empty_defined_macro_makes_rc_condition_true(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            rc = Path(directory_name, "main.rc")
            rc.write_text(
                "#define SELECT_DIALOG\n"
                "#if SELECT_DIALOG\n"
                "#define IDD_VISIBLE 1\n"
                "#endif\n",
                encoding="utf-8",
            )

            result = SymbolLoader().load(rc)

        self.assertEqual(result.table.value_of("IDD_VISIBLE"), 1)
        self.assertEqual(
            result.diagnostics[0].code,
            "symbols.condition-assumed-true",
        )

    def test_undefined_identifier_makes_whole_rc_condition_true(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            rc = Path(directory_name, "main.rc")
            rc.write_text(
                "#if NOT_DEFINED\n"
                "#define IDD_VISIBLE 1\n"
                "#endif\n",
                encoding="utf-8",
            )

            result = SymbolLoader().load(rc)

        self.assertEqual(result.table.value_of("IDD_VISIBLE"), 1)
        self.assertEqual(
            result.diagnostics[0].code,
            "symbols.condition-assumed-true",
        )

    def test_undefined_ifdef_is_assumed_true_only_in_rc(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            rc = root / "main.rc"
            rc.write_text(
                "#ifdef EXTERNAL_BUILD_MACRO\n"
                "#define IDD_VISIBLE 1\n"
                "#endif\n",
                encoding="utf-8",
            )

            result = SymbolLoader().load(rc)

        self.assertEqual(result.table.value_of("IDD_VISIBLE"), 1)
        self.assertEqual(
            result.diagnostics[0].code,
            "symbols.condition-assumed-true",
        )

    def test_undefined_identifier_in_header_keeps_standard_zero_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            header = root / "project.h"
            header.write_text(
                "#if NOT_DEFINED\n#define IDD_HIDDEN 1\n#endif\n",
                encoding="utf-8",
            )
            rc = root / "main.rc"
            rc.write_text('#include "project.h"\n', encoding="utf-8")

            result = SymbolLoader().load(rc)

        self.assertIsNone(result.table.value_of("IDD_HIDDEN"))
        self.assertEqual(result.diagnostics, ())

    def test_ignores_missing_platform_headers_without_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            rc = Path(directory_name, "main.rc")
            rc.write_text(
                '#include "windows.h"\n'
                '#include "winres.h"\n'
                '#include "afxres.h"\n'
                "#define IDD_LOCAL 100\n",
                encoding="utf-8",
            )

            result = SymbolLoader().load(rc)

        self.assertEqual(result.table.value_of("IDD_LOCAL"), 100)
        self.assertEqual(result.diagnostics, ())

    def test_still_warns_for_missing_project_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            rc = Path(directory_name, "main.rc")
            rc.write_text('#include "project_ids.h"\n', encoding="utf-8")

            result = SymbolLoader().load(rc)

        self.assertEqual(len(result.diagnostics), 1)
        self.assertIn("project_ids.h", result.diagnostics[0].message)


if __name__ == "__main__":
    unittest.main()

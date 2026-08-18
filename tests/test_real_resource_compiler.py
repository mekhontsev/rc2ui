from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from rc2ui.adapters.headers.symbols import SymbolLoader
from rc2ui.adapters.res.dialog_template import parse_dialog
from rc2ui.adapters.res.reader import read_res
from rc2ui.adapters.resources.source import read_resource_source


@unittest.skipUnless(shutil.which("windres"), "windres is not installed")
class RealResourceCompilerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("lld-link"), "lld-link is not installed")
    def test_reads_dialog_from_real_pe32_plus_resource_dll(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            source = directory / "dialog.rc"
            resource_object = directory / "dialog.res.obj"
            library = directory / "dialog.dll"
            source.write_text(
                "#define IDD_LOGIN 100\n"
                "#define IDD_ABOUT 101\n"
                "#define IDC_STATIC -1\n"
                "#define WS_POPUP 0x80000000L\n"
                "#define WS_CAPTION 0x00C00000L\n"
                "IDD_LOGIN DIALOG 0, 0, 120, 45\n"
                "STYLE WS_POPUP | WS_CAPTION\n"
                'CAPTION "PE dialog"\n'
                "BEGIN\n"
                '    LTEXT "Example", IDC_STATIC, 7, 8, 100, 8\n'
                "END\n"
                "IDD_ABOUT DIALOG 0, 0, 100, 35\n"
                "STYLE WS_POPUP | WS_CAPTION\n"
                'CAPTION "About from PE"\n'
                "BEGIN\n"
                '    LTEXT "About", IDC_STATIC, 7, 8, 80, 8\n'
                "END\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "windres",
                    "--target=pe-x86-64",
                    "--input-format=rc",
                    "--output-format=coff",
                    f"--input={source}",
                    f"--output={resource_object}",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    "lld-link",
                    "/dll",
                    "/noentry",
                    "/machine:x64",
                    f"/out:{library}",
                    str(resource_object),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            entries = tuple(
                entry for entry in read_resource_source(library) if entry.is_dialog
            )
            symbols = SymbolLoader(include_paths=()).load(source).table
            dialogs = tuple(
                parse_dialog(
                    entry,
                    source=PurePosixPath("dialog.rc"),
                    symbols=symbols,
                )
                for entry in entries
            )

        self.assertEqual(len(dialogs), 2)
        by_name = {dialog.key.resource_id.display_name: dialog for dialog in dialogs}
        self.assertEqual(by_name["IDD_LOGIN"].caption, "PE dialog")
        self.assertEqual(by_name["IDD_ABOUT"].caption, "About from PE")

    def test_reads_standard_and_extended_dialogs_compiled_by_windres(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            header = directory / "resource.h"
            source = directory / "dialogs.rc"
            compiled = directory / "dialogs.res"
            header.write_text(
                "#define IDD_LOGIN 100\n"
                "#define IDD_ABOUT 101\n"
                "#define IDC_NAME 1001\n"
                "#define IDC_STATIC -1\n"
                "#define IDOK 1\n"
                "#define DS_SETFONT 0x40L\n"
                "#define WS_POPUP 0x80000000L\n"
                "#define WS_CAPTION 0x00C00000L\n"
                "#define WS_SYSMENU 0x00080000L\n"
                "#define ES_AUTOHSCROLL 0x0080L\n",
                encoding="utf-8",
            )
            source.write_text(
                '#include "resource.h"\n'
                "LANGUAGE 9, 1\n"
                "IDD_LOGIN DIALOGEX 0, 0, 180, 70\n"
                "STYLE DS_SETFONT | WS_POPUP | WS_CAPTION | WS_SYSMENU\n"
                'CAPTION "Login"\n'
                'FONT 9, "Segoe UI", 400, 0, 1\n'
                "BEGIN\n"
                '    LTEXT "&Name:", IDC_STATIC, 7, 10, 45, 8\n'
                "    EDITTEXT IDC_NAME, 57, 8, 110, 14, ES_AUTOHSCROLL\n"
                '    DEFPUSHBUTTON "OK", IDOK, 119, 49, 48, 14\n'
                "END\n"
                "IDD_ABOUT DIALOG 0, 0, 120, 45\n"
                "STYLE WS_POPUP | WS_CAPTION | WS_SYSMENU\n"
                'CAPTION "About"\n'
                "BEGIN\n"
                '    LTEXT "Example", IDC_STATIC, 7, 8, 100, 8\n'
                "END\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "windres",
                    "--input",
                    str(source),
                    "--output",
                    str(compiled),
                    "--output-format=res",
                    f"--include-dir={directory}",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            symbols = SymbolLoader(include_paths=(directory,)).load(source).table
            dialogs = [
                parse_dialog(
                    entry,
                    source=PurePosixPath("dialogs.rc"),
                    symbols=symbols,
                )
                for entry in read_res(compiled)
                if entry.is_dialog
            ]

        self.assertEqual(len(dialogs), 2)
        by_name = {dialog.key.resource_id.display_name: dialog for dialog in dialogs}
        self.assertTrue(by_name["IDD_LOGIN"].is_extended)
        self.assertEqual(by_name["IDD_LOGIN"].font.typeface, "Segoe UI")
        self.assertFalse(by_name["IDD_ABOUT"].is_extended)


if __name__ == "__main__":
    unittest.main()

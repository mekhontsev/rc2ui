from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rc2ui.adapters.headers.symbols import SymbolLoader
from rc2ui.adapters.rc.dialog_declarations import find_dialog_declarations


class DialogDeclarationTests(unittest.TestCase):
    def test_follows_active_rc_fragment_and_ignores_disabled_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            (root / "resource.h").write_text(
                "#define IDD_ACTIVE 100\n#define IDD_DISABLED 200\n",
                encoding="utf-8",
            )
            fragment = root / "dialogs.rc2"
            fragment.write_text(
                "#if ENABLE_DIALOG\n"
                "IDD_ACTIVE DIALOGEX 0, 0, 100, 50\n"
                "#else\n"
                "IDD_DISABLED DIALOG 0, 0, 100, 50\n"
                "#endif\n",
                encoding="utf-8",
            )
            source = root / "main.rc"
            source.write_text(
                '#include "resource.h"\n#include "dialogs.rc2"\n',
                encoding="utf-8",
            )

            symbols = SymbolLoader(predefined={"ENABLE_DIALOG": 1}).load(source)
            declarations = find_dialog_declarations(
                symbols.active_lines,
                symbols.table,
            )

        self.assertEqual(len(declarations), 1)
        self.assertEqual(declarations[0].token, "IDD_ACTIVE")
        assert declarations[0].resource_id is not None
        self.assertEqual(declarations[0].resource_id.ordinal, 100)
        self.assertEqual(declarations[0].source, fragment.resolve())

    def test_unresolved_identifier_is_a_named_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            source = Path(directory_name, "named.rc")
            source.write_text(
                "NAMED_DIALOG DIALOG 0, 0, 100, 50\n",
                encoding="utf-8",
            )

            symbols = SymbolLoader().load(source)
            declarations = find_dialog_declarations(
                symbols.active_lines,
                symbols.table,
            )

        assert declarations[0].resource_id is not None
        self.assertEqual(declarations[0].resource_id.name, "NAMED_DIALOG")

    def test_reads_quoted_named_dialog_from_windows_1251_rc(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            source = Path(directory_name, "named.rc")
            source.write_bytes(
                '"ДИАЛОГ" DIALOG 0, 0, 100, 50\n'.encode("cp1251")
            )

            symbols = SymbolLoader(source_encoding="cp1251").load(source)
            declarations = find_dialog_declarations(
                symbols.active_lines,
                symbols.table,
            )

        assert declarations[0].resource_id is not None
        self.assertEqual(declarations[0].resource_id.name, "ДИАЛОГ")

    def test_tracks_numeric_language_for_following_dialogs(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            source = Path(directory_name, "languages.rc")
            source.write_text(
                "LANGUAGE 9, 1\n"
                "IDD_ENGLISH DIALOG 0, 0, 100, 50\n"
                "LANGUAGE 25, 1\n"
                "IDD_RUSSIAN DIALOGEX 0, 0, 100, 50\n",
                encoding="utf-8",
            )

            symbols = SymbolLoader().load(source)
            declarations = find_dialog_declarations(
                symbols.active_lines,
                symbols.table,
            )

        self.assertEqual(
            [item.language for item in declarations],
            [1033, 1049],
        )

    def test_understands_standard_english_and_russian_language_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            source = Path(directory_name, "languages.rc")
            source.write_text(
                "LANGUAGE LANG_ENGLISH, SUBLANG_ENGLISH_US\n"
                "IDD_ENGLISH DIALOG 0, 0, 100, 50\n"
                "LANGUAGE LANG_RUSSIAN, SUBLANG_DEFAULT\n"
                "IDD_RUSSIAN DIALOGEX 0, 0, 100, 50\n",
                encoding="utf-8",
            )

            symbols = SymbolLoader().load(source)
            declarations = find_dialog_declarations(
                symbols.active_lines,
                symbols.table,
            )

        self.assertEqual(
            [item.language for item in declarations],
            [1033, 1049],
        )

    def test_understands_the_full_standard_language_constant_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            source = Path(directory_name, "languages.rc")
            source.write_text(
                "LANGUAGE LANG_BULGARIAN, SUBLANG_DEFAULT\n"
                "IDD_BG DIALOG 0, 0, 10, 10\n"
                "LANGUAGE LANG_PORTUGUESE, SUBLANG_PORTUGUESE_BRAZILIAN\n"
                "IDD_PT DIALOG 0, 0, 10, 10\n"
                "LANGUAGE LANG_CHINESE, SUBLANG_CHINESE_TRADITIONAL\n"
                "IDD_ZH DIALOG 0, 0, 10, 10\n"
                "LANGUAGE LANG_ESPERANTO, SUBLANG_DEFAULT\n"
                "IDD_EO DIALOG 0, 0, 10, 10\n",
                encoding="utf-8",
            )

            symbols = SymbolLoader().load(source)
            declarations = find_dialog_declarations(
                symbols.active_lines,
                symbols.table,
            )

        self.assertEqual(
            [item.language for item in declarations],
            [1026, 1046, 1028, 1167],
        )


if __name__ == "__main__":
    unittest.main()

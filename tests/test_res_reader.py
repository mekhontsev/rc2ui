from __future__ import annotations

import unittest
from pathlib import PurePosixPath

from rc2ui.adapters.res.dialog_template import parse_dialog
from rc2ui.adapters.res.reader import parse_res
from rc2ui.adapters.resources.model import is_dialog_type
from tests.resource_fixtures import (
    extended_dialog_payload,
    null_res_record,
    res_record,
    standard_dialog_payload,
)


class ResReaderTests(unittest.TestCase):
    def test_type_filter_skips_unrelated_payloads(self) -> None:
        data = res_record(10, 1, b"unrelated") + res_record(
            5,
            100,
            standard_dialog_payload(),
        )

        entries = parse_res(data, resource_filter=is_dialog_type)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].resource_id.ordinal, 100)

    def test_reads_all_dialog_entries_and_skips_null_header(self) -> None:
        data = (
            null_res_record()
            + res_record(5, 100, standard_dialog_payload(), language=1033)
            + res_record(5, 200, extended_dialog_payload(), language=1049)
        )

        entries = parse_res(data)

        self.assertEqual([entry.resource_id.ordinal for entry in entries], [100, 200])
        self.assertEqual([entry.language for entry in entries], [1033, 1049])

    def test_parses_standard_dialog_and_static_id(self) -> None:
        [entry] = parse_res(res_record(5, 100, standard_dialog_payload()))

        dialog = parse_dialog(entry, source=PurePosixPath("main.rc"))

        self.assertFalse(dialog.is_extended)
        self.assertEqual(dialog.caption, "Login")
        self.assertEqual(dialog.font.typeface, "Segoe UI")
        self.assertEqual(len(dialog.controls), 2)
        self.assertEqual(dialog.controls[0].key.resource_id.ordinal, -1)
        self.assertEqual(dialog.controls[0].class_name, "Static")
        self.assertEqual(dialog.controls[1].key.resource_id.ordinal, 1001)

    def test_parses_extended_dialog(self) -> None:
        [entry] = parse_res(res_record(5, 200, extended_dialog_payload()))

        dialog = parse_dialog(entry, source=PurePosixPath("main.rc"))

        self.assertTrue(dialog.is_extended)
        self.assertEqual(dialog.help_id, 42)
        self.assertEqual(dialog.font.weight, 500)
        self.assertTrue(dialog.font.italic)
        self.assertEqual(dialog.controls[0].help_id, 7)
        self.assertEqual(dialog.controls[0].text, "OK")

    def test_strips_embedded_gettext_context_from_visible_control_text(self) -> None:
        [entry] = parse_res(
            res_record(
                5,
                100,
                standard_dialog_payload(
                    label="#msgctxt#do not translate#&IsDirty"
                ),
            )
        )

        dialog = parse_dialog(entry, source=PurePosixPath("main.rc"))

        self.assertEqual(dialog.controls[0].text, "&IsDirty")


if __name__ == "__main__":
    unittest.main()

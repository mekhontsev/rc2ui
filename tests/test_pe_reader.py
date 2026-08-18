from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from rc2ui.adapters.pe.reader import PeFormatError, parse_pe
from rc2ui.adapters.res.dialog_template import parse_dialog
from rc2ui.adapters.resources.source import (
    read_dialog_resources,
    read_resource_source,
)
from tests.resource_fixtures import (
    extended_dialog_payload,
    pe_resource_binary,
    res_record,
    standard_dialog_payload,
)


class PeReaderTests(unittest.TestCase):
    def test_reads_pe32_dialog_and_language_variants(self) -> None:
        image = pe_resource_binary(
            (
                (1033, standard_dialog_payload()),
                (1049, standard_dialog_payload()),
            ),
            resource_id=100,
        )

        entries = parse_pe(image)

        self.assertEqual(len(entries), 2)
        self.assertTrue(all(entry.is_dialog for entry in entries))
        self.assertEqual([entry.language for entry in entries], [1033, 1049])
        self.assertEqual(entries[0].resource_id.ordinal, 100)
        dialog = parse_dialog(entries[0], source=PurePosixPath("dialogs.rc"))
        self.assertEqual(dialog.caption, "Login")

    def test_reads_pe32_plus_arm64_and_named_dialog(self) -> None:
        image = pe_resource_binary(
            ((1033, extended_dialog_payload()),),
            resource_id="IDD_NAMED",
            pe_plus=True,
            machine=0xAA64,
        )

        [entry] = parse_pe(image)

        self.assertEqual(entry.resource_id.name, "IDD_NAMED")
        self.assertEqual(entry.code_page, 1200)
        dialog = parse_dialog(entry, source=PurePosixPath("dialogs.rc"))
        self.assertTrue(dialog.is_extended)

    def test_source_format_is_detected_by_signature_not_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            res_named_bin = root / "standalone.bin"
            pe_named_res = root / "application.res"
            res_named_bin.write_bytes(
                res_record(5, 100, standard_dialog_payload(), language=1033)
            )
            pe_named_res.write_bytes(
                pe_resource_binary(((1033, standard_dialog_payload()),))
            )

            [res_entry] = read_resource_source(res_named_bin)
            [pe_entry] = read_resource_source(pe_named_res)

        self.assertEqual(res_entry.resource_id.ordinal, 100)
        self.assertEqual(pe_entry.resource_id.ordinal, 100)

    def test_dialog_reader_skips_unrelated_pe_resource_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            path = Path(directory_name, "application.exe")
            path.write_bytes(
                pe_resource_binary(
                    ((1033, b"large unrelated payload"),),
                    resource_type=10,
                )
            )

            all_entries = read_resource_source(path)
            dialog_entries = read_dialog_resources(path)

        self.assertEqual(len(all_entries), 1)
        self.assertEqual(dialog_entries, ())

    def test_rejects_truncated_pe_image(self) -> None:
        image = b"MZ" + b"\0" * 30

        with self.assertRaisesRegex(PeFormatError, "DOS MZ header"):
            parse_pe(image)

    def test_pe_without_resource_directory_is_valid_and_empty(self) -> None:
        image = bytearray(
            pe_resource_binary(((1033, standard_dialog_payload()),))
        )
        pe_offset = struct.unpack_from("<I", image, 0x3C)[0]
        resource_directory_offset = pe_offset + 24 + 96 + 16
        struct.pack_into("<II", image, resource_directory_offset, 0, 0)

        self.assertEqual(parse_pe(bytes(image)), ())

    def test_rejects_resource_tree_outside_declared_directory(self) -> None:
        image = bytearray(
            pe_resource_binary(((1033, standard_dialog_payload()),))
        )
        pe_offset = struct.unpack_from("<I", image, 0x3C)[0]
        optional_offset = pe_offset + 24
        resource_directory_offset = optional_offset + 96 + 16
        struct.pack_into("<I", image, resource_directory_offset + 4, 16)

        with self.assertRaisesRegex(PeFormatError, "exceeds resource directory"):
            parse_pe(bytes(image))


if __name__ == "__main__":
    unittest.main()

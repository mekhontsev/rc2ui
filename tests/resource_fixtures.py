from __future__ import annotations

import struct


def align(data: bytes, boundary: int = 4) -> bytes:
    return data + b"\0" * (-len(data) % boundary)


def utf16z(text: str) -> bytes:
    return text.encode("utf-16-le") + b"\0\0"


def ordinal(value: int) -> bytes:
    return struct.pack("<HH", 0xFFFF, value & 0xFFFF)


def res_record(
    resource_type: int,
    resource_id: int,
    payload: bytes,
    *,
    language: int = 0,
) -> bytes:
    variable = ordinal(resource_type) + ordinal(resource_id)
    header_prefix = struct.pack("<II", len(payload), 0)
    partial = align(header_prefix + variable)
    fixed = struct.pack("<IHHII", 0, 0x1030, language, 0, 0)
    header = partial + fixed
    header = struct.pack("<II", len(payload), len(header)) + header[8:]
    return header + align(payload)


def null_res_record() -> bytes:
    return res_record(0, 0, b"")


def standard_dialog_payload(
    *,
    caption: str = "Login",
    label: str = "&User name:",
    dialog_rect: tuple[int, int, int, int] = (0, 0, 180, 70),
    label_rect: tuple[int, int, int, int] = (7, 12, 55, 8),
    edit_rect: tuple[int, int, int, int] = (67, 10, 105, 14),
) -> bytes:
    # DS_SETFONT, two controls: label and edit.
    data = struct.pack("<IIHhhhh", 0x40, 0, 2, *dialog_rect)
    data += struct.pack("<H", 0)  # menu
    data += struct.pack("<H", 0)  # dialog class
    data += utf16z(caption)
    data += struct.pack("<H", 9) + utf16z("Segoe UI")

    data = align(data)
    data += struct.pack("<IIhhhhH", 0, 0, *label_rect, 0xFFFF)
    data += ordinal(0x82) + utf16z(label) + struct.pack("<H", 0)

    data = align(data)
    data += struct.pack("<IIhhhhH", 0x00810080, 0, *edit_rect, 1001)
    data += ordinal(0x81) + struct.pack("<H", 0) + struct.pack("<H", 0)
    return data


def repeated_static_dialog_payload() -> bytes:
    data = struct.pack("<IIHhhhh", 0, 0, 2, 0, 0, 120, 45)
    data += struct.pack("<H", 0) * 2
    data += utf16z("Repeated statics")

    data = align(data)
    data += struct.pack("<IIhhhhH", 0, 0, 7, 8, 90, 8, 0xFFFF)
    data += ordinal(0x82) + utf16z("First") + struct.pack("<H", 0)

    data = align(data)
    data += struct.pack("<IIhhhhH", 0, 0, 7, 23, 90, 8, 0xFFFF)
    data += ordinal(0x82) + utf16z("Second") + struct.pack("<H", 0)
    return data


def edit_updown_dialog_payload() -> bytes:
    """Standard dialog containing a label, EDIT, and adjacent up-down."""

    data = struct.pack("<IIHhhhh", 0, 0, 3, 0, 0, 180, 55)
    data += struct.pack("<H", 0) * 2
    data += utf16z("Parameters")

    data = align(data)
    data += struct.pack("<IIhhhhH", 0, 0, 7, 13, 45, 8, 0xFFFF)
    data += ordinal(0x82) + utf16z("Value:") + struct.pack("<H", 0)

    data = align(data)
    data += struct.pack("<IIhhhhH", 0, 0, 62, 10, 96, 14, 1001)
    data += ordinal(0x81) + struct.pack("<H", 0) + struct.pack("<H", 0)

    data = align(data)
    # UDS_AUTOBUDDY | UDS_ALIGNRIGHT. Win32 derives the visual position from
    # the preceding edit, so this deliberately misleading y coordinate is a
    # regression fixture for runtime buddy geometry.
    data += struct.pack("<IIhhhhH", 0x0014, 0, 158, 3, 14, 14, 1002)
    data += utf16z("msctls_updown32")
    data += struct.pack("<H", 0) * 2
    return data


def extended_dialog_payload() -> bytes:
    data = struct.pack(
        "<HHIIIHhhhh",
        1,
        0xFFFF,
        42,
        0,
        0x40,
        1,
        0,
        0,
        120,
        50,
    )
    data += struct.pack("<H", 0) * 2
    data += utf16z("Extended")
    data += struct.pack("<HHBB", 10, 500, 1, 1) + utf16z("Tahoma")
    data = align(data)
    data += struct.pack("<IIIhhhhI", 7, 0, 1, 75, 30, 38, 14, 1)
    data += ordinal(0x80) + utf16z("OK") + struct.pack("<H", 0)
    return data


def pe_resource_binary(
    resources: tuple[tuple[int, bytes], ...],
    *,
    resource_type: int | str = 5,
    resource_id: int | str = 100,
    pe_plus: bool = False,
    machine: int | None = None,
    code_page: int = 1200,
) -> bytes:
    """Build a minimal PE image with one resource ID and language variants."""

    if not resources:
        raise ValueError("at least one resource language is required")
    languages = tuple(sorted(resources))
    if len({language for language, _ in languages}) != len(languages):
        raise ValueError("resource languages must be unique")

    directory_end = 64 + len(languages) * 8
    strings = bytearray()

    def identifier(value: int | str) -> int:
        if isinstance(value, int):
            if not 0 <= value <= 0xFFFF:
                raise ValueError("numeric PE resource ID must fit in 16 bits")
            return value
        offset = directory_end + len(strings)
        encoded = value.encode("utf-16-le")
        strings.extend(struct.pack("<H", len(encoded) // 2))
        strings.extend(encoded)
        return 0x80000000 | offset

    type_identifier = identifier(resource_type)
    name_identifier = identifier(resource_id)
    data_entries_offset = _align_value(directory_end + len(strings), 4)
    payload_offset = data_entries_offset + len(languages) * 16
    payload_offsets: list[int] = []
    for _, payload in languages:
        payload_offset = _align_value(payload_offset, 4)
        payload_offsets.append(payload_offset)
        payload_offset += len(payload)

    resource_rva = 0x1000
    resource_blob = bytearray(payload_offset)
    _resource_directory_header(resource_blob, 0, resource_type)
    struct.pack_into("<II", resource_blob, 16, type_identifier, 0x80000018)
    _resource_directory_header(resource_blob, 24, resource_id)
    struct.pack_into("<II", resource_blob, 40, name_identifier, 0x80000030)
    struct.pack_into(
        "<IIHHHH",
        resource_blob,
        48,
        0,
        0,
        0,
        0,
        0,
        len(languages),
    )
    resource_blob[directory_end : directory_end + len(strings)] = strings
    for index, ((language, payload), raw_offset) in enumerate(
        zip(languages, payload_offsets)
    ):
        data_entry_offset = data_entries_offset + index * 16
        struct.pack_into(
            "<II",
            resource_blob,
            64 + index * 8,
            language,
            data_entry_offset,
        )
        struct.pack_into(
            "<IIII",
            resource_blob,
            data_entry_offset,
            resource_rva + raw_offset,
            len(payload),
            code_page,
            0,
        )
        resource_blob[raw_offset : raw_offset + len(payload)] = payload

    pe_offset = 0x80
    headers_size = 0x200
    raw_size = _align_value(len(resource_blob), 0x200)
    optional_size = 0xF0 if pe_plus else 0xE0
    headers = bytearray(headers_size)
    headers[:2] = b"MZ"
    struct.pack_into("<I", headers, 0x3C, pe_offset)
    headers[pe_offset : pe_offset + 4] = b"PE\0\0"
    coff_offset = pe_offset + 4
    struct.pack_into(
        "<HHIIIHH",
        headers,
        coff_offset,
        machine if machine is not None else (0x8664 if pe_plus else 0x14C),
        1,
        0,
        0,
        0,
        optional_size,
        0x210E,
    )
    optional_offset = coff_offset + 20
    optional = bytearray(optional_size)
    struct.pack_into("<H", optional, 0, 0x20B if pe_plus else 0x10B)
    struct.pack_into("<II", optional, 32, 0x1000, 0x200)
    struct.pack_into("<II", optional, 56, 0x2000, headers_size)
    count_offset = 108 if pe_plus else 92
    directories_offset = 112 if pe_plus else 96
    struct.pack_into("<I", optional, count_offset, 16)
    struct.pack_into(
        "<II",
        optional,
        directories_offset + 16,
        resource_rva,
        len(resource_blob),
    )
    headers[optional_offset : optional_offset + optional_size] = optional
    section_offset = optional_offset + optional_size
    struct.pack_into(
        "<8sIIIIIIHHI",
        headers,
        section_offset,
        b".rsrc\0\0\0",
        len(resource_blob),
        resource_rva,
        raw_size,
        headers_size,
        0,
        0,
        0,
        0,
        0x40000040,
    )
    return bytes(headers) + bytes(resource_blob).ljust(raw_size, b"\0")


def _resource_directory_header(
    target: bytearray,
    offset: int,
    identifier: int | str,
) -> None:
    struct.pack_into(
        "<IIHHHH",
        target,
        offset,
        0,
        0,
        0,
        0,
        int(isinstance(identifier, str)),
        int(isinstance(identifier, int)),
    )


def _align_value(value: int, boundary: int) -> int:
    return (value + boundary - 1) & -boundary

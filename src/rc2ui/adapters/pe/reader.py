from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rc2ui.adapters.resources.model import is_dialog_type
from rc2ui.domain.resource_id import ResourceId


_PE_SIGNATURE = b"PE\0\0"
_PE32_MAGIC = 0x10B
_PE32_PLUS_MAGIC = 0x20B
_RESOURCE_DIRECTORY_INDEX = 2
_DIRECTORY_FLAG = 0x80000000
_OFFSET_MASK = 0x7FFFFFFF
_MAX_VISITED_ENTRIES = 1_000_000


class PeFormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PeResourceEntry:
    resource_type: ResourceId
    resource_id: ResourceId
    data: bytes
    language: int
    code_page: int
    reserved: int
    file_offset: int

    @property
    def is_dialog(self) -> bool:
        return is_dialog_type(self.resource_type)


@dataclass(frozen=True, slots=True)
class _Section:
    name: str
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int


@dataclass(frozen=True, slots=True)
class _DirectoryEntry:
    identifier: ResourceId
    target_offset: int
    is_directory: bool


def read_pe(path: Path) -> tuple[PeResourceEntry, ...]:
    return parse_pe(path.read_bytes(), context=str(path))


def parse_pe(
    data: bytes,
    *,
    context: str = "PE image",
    resource_filter: Callable[[ResourceId], bool] | None = None,
) -> tuple[PeResourceEntry, ...]:
    try:
        return _PeImage(data, context=context).read_resources(resource_filter)
    except (UnicodeDecodeError, struct.error) as error:
        raise PeFormatError(f"{context}: malformed PE data: {error}") from error


class _PeImage:
    def __init__(self, data: bytes, *, context: str) -> None:
        self.data = data
        self.context = context
        self.sections: tuple[_Section, ...] = ()
        self.size_of_headers = 0
        self.resource_rva = 0
        self.resource_size = 0
        self.visited_entries = 0
        self._read_headers()

    def read_resources(
        self,
        resource_filter: Callable[[ResourceId], bool] | None = None,
    ) -> tuple[PeResourceEntry, ...]:
        if self.resource_rva == 0 and self.resource_size == 0:
            return ()
        if self.resource_rva == 0 or self.resource_size == 0:
            self._fail("resource data directory has an incomplete RVA/size pair")
        self._rva_to_offset(
            self.resource_rva,
            1,
            label="resource directory",
        )

        resources: list[PeResourceEntry] = []
        for type_entry in self._directory_entries(0):
            if resource_filter is not None and not resource_filter(
                type_entry.identifier
            ):
                continue
            if not type_entry.is_directory:
                self._fail("resource type entry does not point to a directory")
            for name_entry in self._directory_entries(type_entry.target_offset):
                if not name_entry.is_directory:
                    self._fail("resource name entry does not point to a directory")
                for language_entry in self._directory_entries(
                    name_entry.target_offset
                ):
                    if language_entry.is_directory:
                        self._fail(
                            "resource language entry points to another directory"
                        )
                    if language_entry.identifier.ordinal is None:
                        self._fail("resource language must be a numeric LANGID")
                    resources.append(
                        self._read_data_entry(
                            language_entry.target_offset,
                            resource_type=type_entry.identifier,
                            resource_id=name_entry.identifier,
                            language=language_entry.identifier.ordinal,
                        )
                    )
        return tuple(resources)

    def _read_headers(self) -> None:
        if len(self.data) < 0x40 or self.data[:2] != b"MZ":
            self._fail("missing DOS MZ header")
        pe_offset = self._u32(0x3C, label="DOS e_lfanew")
        if self._slice(pe_offset, 4, label="PE signature") != _PE_SIGNATURE:
            self._fail(f"missing PE signature at offset {pe_offset}")

        coff_offset = pe_offset + 4
        coff = self._slice(coff_offset, 20, label="COFF header")
        section_count = struct.unpack_from("<H", coff, 2)[0]
        optional_size = struct.unpack_from("<H", coff, 16)[0]
        optional_offset = coff_offset + 20
        optional = self._slice(
            optional_offset,
            optional_size,
            label="optional header",
        )
        if len(optional) < 2:
            self._fail("optional header is missing")
        magic = struct.unpack_from("<H", optional, 0)[0]
        if magic == _PE32_MAGIC:
            count_offset = 92
            directories_offset = 96
        elif magic == _PE32_PLUS_MAGIC:
            count_offset = 108
            directories_offset = 112
        else:
            self._fail(f"unsupported PE optional-header magic {magic:#x}")

        self.size_of_headers = self._optional_u32(
            optional,
            60,
            label="SizeOfHeaders",
        )
        directory_count = self._optional_u32(
            optional,
            count_offset,
            label="NumberOfRvaAndSizes",
        )
        if directory_count > _RESOURCE_DIRECTORY_INDEX:
            resource_offset = directories_offset + 8 * _RESOURCE_DIRECTORY_INDEX
            directory = self._optional_slice(
                optional,
                resource_offset,
                8,
                label="resource data directory",
            )
            self.resource_rva, self.resource_size = struct.unpack("<II", directory)

        section_table_offset = optional_offset + optional_size
        section_table = self._slice(
            section_table_offset,
            section_count * 40,
            label="section table",
        )
        sections: list[_Section] = []
        for index in range(section_count):
            raw = section_table[index * 40 : (index + 1) * 40]
            name = raw[:8].split(b"\0", 1)[0].decode("ascii", errors="replace")
            virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
                "<IIII",
                raw,
                8,
            )
            if raw_size:
                self._slice(raw_offset, raw_size, label=f"section {name!r}")
            sections.append(
                _Section(
                    name=name,
                    virtual_address=virtual_address,
                    virtual_size=virtual_size,
                    raw_offset=raw_offset,
                    raw_size=raw_size,
                )
            )
        self.sections = tuple(sections)

    def _directory_entries(self, relative_offset: int) -> tuple[_DirectoryEntry, ...]:
        header = self._resource_slice(relative_offset, 16, label="directory header")
        named_count, id_count = struct.unpack_from("<HH", header, 12)
        count = named_count + id_count
        self.visited_entries += count
        if self.visited_entries > _MAX_VISITED_ENTRIES:
            self._fail("resource directory contains too many entries")
        raw_entries = self._resource_slice(
            relative_offset + 16,
            count * 8,
            label="directory entries",
        )
        result: list[_DirectoryEntry] = []
        for index in range(count):
            name_raw, target_raw = struct.unpack_from("<II", raw_entries, index * 8)
            result.append(
                _DirectoryEntry(
                    identifier=self._resource_identifier(name_raw),
                    target_offset=target_raw & _OFFSET_MASK,
                    is_directory=bool(target_raw & _DIRECTORY_FLAG),
                )
            )
        return tuple(result)

    def _resource_identifier(self, raw: int) -> ResourceId:
        if raw & _DIRECTORY_FLAG:
            name_offset = raw & _OFFSET_MASK
            length_raw = self._resource_slice(name_offset, 2, label="resource name")
            length = struct.unpack("<H", length_raw)[0]
            encoded = self._resource_slice(
                name_offset + 2,
                length * 2,
                label="resource name",
            )
            name = encoded.decode("utf-16-le")
            if not name:
                self._fail("resource name cannot be empty")
            return ResourceId.from_name(name)
        if raw > 0xFFFF:
            self._fail(f"numeric resource identifier exceeds 16 bits: {raw:#x}")
        return ResourceId.from_ordinal(raw)

    def _read_data_entry(
        self,
        relative_offset: int,
        *,
        resource_type: ResourceId,
        resource_id: ResourceId,
        language: int,
    ) -> PeResourceEntry:
        raw = self._resource_slice(relative_offset, 16, label="resource data entry")
        data_rva, size, code_page, reserved = struct.unpack("<IIII", raw)
        file_offset = self._rva_to_offset(
            data_rva,
            size,
            label=(
                f"resource {resource_type.display_name}/"
                f"{resource_id.display_name}/{language}"
            ),
        )
        return PeResourceEntry(
            resource_type=resource_type,
            resource_id=resource_id,
            data=self._slice(file_offset, size, label="resource payload"),
            language=language,
            code_page=code_page,
            reserved=reserved,
            file_offset=file_offset,
        )

    def _resource_slice(self, offset: int, size: int, *, label: str) -> bytes:
        if offset < 0 or size < 0 or offset + size > self.resource_size:
            self._fail(
                f"{label} at resource offset {offset} exceeds resource directory"
            )
        file_offset = self._rva_to_offset(
            self.resource_rva + offset,
            size,
            label=label,
        )
        return self._slice(file_offset, size, label=label)

    def _rva_to_offset(self, rva: int, size: int, *, label: str) -> int:
        if rva < self.size_of_headers and rva + size <= self.size_of_headers:
            self._slice(rva, size, label=label)
            return rva
        for section in self.sections:
            span = max(section.virtual_size, section.raw_size)
            if not section.virtual_address <= rva < section.virtual_address + span:
                continue
            delta = rva - section.virtual_address
            if delta + size > section.raw_size:
                self._fail(
                    f"{label} at RVA {rva:#x} exceeds raw section "
                    f"{section.name!r}"
                )
            offset = section.raw_offset + delta
            self._slice(offset, size, label=label)
            return offset
        self._fail(f"cannot map {label} RVA {rva:#x} to a file section")

    def _optional_u32(self, data: bytes, offset: int, *, label: str) -> int:
        raw = self._optional_slice(data, offset, 4, label=label)
        return struct.unpack("<I", raw)[0]

    def _optional_slice(
        self,
        data: bytes,
        offset: int,
        size: int,
        *,
        label: str,
    ) -> bytes:
        if offset < 0 or size < 0 or offset + size > len(data):
            self._fail(f"optional header does not contain {label}")
        return data[offset : offset + size]

    def _u32(self, offset: int, *, label: str) -> int:
        return struct.unpack("<I", self._slice(offset, 4, label=label))[0]

    def _slice(self, offset: int, size: int, *, label: str) -> bytes:
        if offset < 0 or size < 0 or offset + size > len(self.data):
            self._fail(f"{label} at file offset {offset} exceeds the file")
        return self.data[offset : offset + size]

    def _fail(self, message: str) -> None:
        raise PeFormatError(f"{self.context}: {message}")

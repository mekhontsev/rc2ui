from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rc2ui.adapters.res.binary import BinaryFormatError, BinaryReader
from rc2ui.adapters.resources.model import RT_DIALOG, is_dialog_type
from rc2ui.domain.resource_id import ResourceId


class ResFormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResEntry:
    resource_type: ResourceId
    resource_id: ResourceId
    data: bytes
    language: int
    memory_flags: int
    data_version: int
    version: int
    characteristics: int
    file_offset: int

    @property
    def is_dialog(self) -> bool:
        return is_dialog_type(self.resource_type)


def read_res(path: Path) -> tuple[ResEntry, ...]:
    try:
        return parse_res(path.read_bytes(), context=str(path))
    except BinaryFormatError as error:
        raise ResFormatError(str(error)) from error


def parse_res(
    data: bytes,
    *,
    context: str = ".res data",
    resource_filter: Callable[[ResourceId], bool] | None = None,
) -> tuple[ResEntry, ...]:
    reader = BinaryReader(data, context=context)
    entries: list[ResEntry] = []

    while reader.remaining:
        record_start = reader.offset
        if reader.remaining < 8:
            if any(reader.read(reader.remaining)):
                raise ResFormatError(
                    f"{context}: nonzero trailing data at offset {record_start}"
                )
            break

        data_size = reader.u32()
        header_size = reader.u32()
        if header_size < 8:
            raise ResFormatError(
                f"{context}: invalid header size {header_size} at {record_start}"
            )
        header_end = record_start + header_size
        if header_end > len(data):
            raise ResFormatError(
                f"{context}: resource header at {record_start} exceeds the file"
            )

        resource_type = _read_name_or_ordinal(reader)
        resource_id = _read_name_or_ordinal(reader)
        reader.align(4)
        data_version = reader.u32()
        memory_flags = reader.u16()
        language = reader.u16()
        version = reader.u32()
        characteristics = reader.u32()
        if reader.offset > header_end:
            raise ResFormatError(
                f"{context}: resource header fields exceed HeaderSize at {record_start}"
            )
        reader.seek(header_end)

        if data_size > reader.remaining:
            raise ResFormatError(
                f"{context}: resource data at {header_end} exceeds the file"
            )
        include = resource_filter is None or resource_filter(resource_type)
        if include:
            payload = reader.read(data_size)
        else:
            reader.seek(reader.offset + data_size)
            payload = b""
        if reader.remaining:
            reader.align(4)

        # Resource compilers begin .res files with a conventional null entry.
        is_null_header = (
            data_size == 0
            and resource_type.ordinal == 0
            and resource_id.ordinal == 0
        )
        if not is_null_header and include:
            entries.append(
                ResEntry(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    data=payload,
                    language=language,
                    memory_flags=memory_flags,
                    data_version=data_version,
                    version=version,
                    characteristics=characteristics,
                    file_offset=record_start,
                )
            )

    return tuple(entries)


def _read_name_or_ordinal(reader: BinaryReader) -> ResourceId:
    first = reader.u16()
    if first == 0xFFFF:
        return ResourceId.from_ordinal(reader.u16())
    return ResourceId.from_name(reader.utf16z(first=first))

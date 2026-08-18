from __future__ import annotations

from pathlib import PurePosixPath
from typing import Protocol

from rc2ui.adapters.res.binary import BinaryFormatError, BinaryReader
from rc2ui.adapters.resources.model import ResourceEntry
from rc2ui.domain.dialog import (
    Control,
    ControlKey,
    Dialog,
    DialogFont,
    DialogKey,
)
from rc2ui.domain.geometry import RectDlu
from rc2ui.domain.resource_id import ResourceId


DS_SETFONT = 0x00000040

_SYSTEM_CLASSES = {
    0x0080: "Button",
    0x0081: "Edit",
    0x0082: "Static",
    0x0083: "ListBox",
    0x0084: "ScrollBar",
    0x0085: "ComboBox",
}


class SymbolLookup(Protocol):
    def symbols_for(self, value: int) -> tuple[str, ...]: ...


class DialogTemplateError(ValueError):
    pass


def parse_dialog(
    entry: ResourceEntry,
    *,
    source: PurePosixPath,
    symbols: SymbolLookup | None = None,
) -> Dialog:
    if not entry.is_dialog:
        raise DialogTemplateError("resource entry is not RT_DIALOG")

    resource_id = _with_symbols(entry.resource_id, symbols)
    key = DialogKey(source=source, resource_id=resource_id, language=entry.language)
    reader = BinaryReader(
        entry.data,
        context=f"dialog {resource_id.display_name} in {source}",
    )
    try:
        if reader.remaining >= 4 and int.from_bytes(entry.data[2:4], "little") == 0xFFFF:
            return _parse_extended(reader, key=key, symbols=symbols)
        return _parse_standard(reader, key=key, symbols=symbols)
    except BinaryFormatError as error:
        raise DialogTemplateError(str(error)) from error


def _parse_standard(
    reader: BinaryReader,
    *,
    key: DialogKey,
    symbols: SymbolLookup | None,
) -> Dialog:
    style = reader.u32()
    extended_style = reader.u32()
    item_count = reader.u16()
    rect = _read_rect(reader)
    menu = _read_optional_resource(reader)
    window_class = _read_optional_resource(reader)
    caption = _read_text(reader)
    font = None
    if style & DS_SETFONT:
        font = DialogFont(point_size=reader.u16(), typeface=_read_text(reader) or "")

    controls: list[Control] = []
    occurrences: dict[int, int] = {}
    for order in range(item_count):
        reader.align(4)
        control_style = reader.u32()
        control_extended_style = reader.u32()
        control_rect = _read_rect(reader)
        raw_id = reader.u16()
        control_id = _signed_control_id(raw_id, bits=16)
        class_name = _read_control_class(reader)
        text, content_resource = _read_control_title(reader)
        creation_size = reader.u16()
        if creation_size == 1:
            raise DialogTemplateError("standard control creation data size cannot be 1")
        creation_data = reader.read(creation_size - 2) if creation_size >= 2 else b""
        occurrence = occurrences.get(control_id, 0) + 1
        occurrences[control_id] = occurrence
        resource_id = _with_symbols(ResourceId.from_ordinal(control_id), symbols)
        controls.append(
            Control(
                key=ControlKey(key, resource_id, occurrence),
                class_name=class_name,
                text=text,
                rect=control_rect,
                style=control_style,
                extended_style=control_extended_style,
                order=order,
                content_resource=content_resource,
                creation_data=creation_data,
            )
        )

    return Dialog(
        key=key,
        caption=caption,
        rect=rect,
        style=style,
        extended_style=extended_style,
        controls=tuple(controls),
        menu=menu,
        window_class=window_class,
        font=font,
    )


def _parse_extended(
    reader: BinaryReader,
    *,
    key: DialogKey,
    symbols: SymbolLookup | None,
) -> Dialog:
    version = reader.u16()
    signature = reader.u16()
    if version != 1 or signature != 0xFFFF:
        raise DialogTemplateError(
            f"unsupported extended dialog header {version:#x}/{signature:#x}"
        )
    help_id = reader.u32()
    extended_style = reader.u32()
    style = reader.u32()
    item_count = reader.u16()
    rect = _read_rect(reader)
    menu = _read_optional_resource(reader)
    window_class = _read_optional_resource(reader)
    caption = _read_text(reader)
    font = None
    if style & DS_SETFONT:
        point_size = reader.u16()
        weight = reader.u16()
        italic = bool(reader.u8())
        charset = reader.u8()
        font = DialogFont(
            point_size=point_size,
            weight=weight,
            italic=italic,
            charset=charset,
            typeface=_read_text(reader) or "",
        )

    controls: list[Control] = []
    occurrences: dict[int, int] = {}
    for order in range(item_count):
        reader.align(4)
        control_help_id = reader.u32()
        control_extended_style = reader.u32()
        control_style = reader.u32()
        control_rect = _read_rect(reader)
        raw_id = reader.u32()
        control_id = _signed_control_id(raw_id, bits=32)
        class_name = _read_control_class(reader)
        text, content_resource = _read_control_title(reader)
        creation_size = reader.u16()
        creation_data = reader.read(creation_size)
        occurrence = occurrences.get(control_id, 0) + 1
        occurrences[control_id] = occurrence
        resource_id = _with_symbols(ResourceId.from_ordinal(control_id), symbols)
        controls.append(
            Control(
                key=ControlKey(key, resource_id, occurrence),
                class_name=class_name,
                text=text,
                rect=control_rect,
                style=control_style,
                extended_style=control_extended_style,
                order=order,
                help_id=control_help_id,
                content_resource=content_resource,
                creation_data=creation_data,
            )
        )

    return Dialog(
        key=key,
        caption=caption,
        rect=rect,
        style=style,
        extended_style=extended_style,
        controls=tuple(controls),
        help_id=help_id,
        menu=menu,
        window_class=window_class,
        font=font,
        is_extended=True,
    )


def _read_rect(reader: BinaryReader) -> RectDlu:
    return RectDlu(
        x=reader.i16(),
        y=reader.i16(),
        width=reader.i16(),
        height=reader.i16(),
    )


def _read_optional_resource(reader: BinaryReader) -> ResourceId | None:
    first = reader.u16()
    if first == 0:
        return None
    if first == 0xFFFF:
        return ResourceId.from_ordinal(reader.u16())
    return ResourceId.from_name(reader.utf16z(first=first))


def _read_text(reader: BinaryReader) -> str | None:
    first = reader.u16()
    if first == 0:
        return None
    return _visible_message_text(reader.utf16z(first=first))


def _read_control_class(reader: BinaryReader) -> str:
    value = _read_optional_resource(reader)
    if value is None:
        return "Widget"
    if value.name is not None:
        return value.name
    assert value.ordinal is not None
    return _SYSTEM_CLASSES.get(value.ordinal, f"#{value.ordinal}")


def _read_control_title(
    reader: BinaryReader,
) -> tuple[str | None, ResourceId | None]:
    first = reader.u16()
    if first == 0:
        return None, None
    if first == 0xFFFF:
        return None, ResourceId.from_ordinal(reader.u16())
    return _visible_message_text(reader.utf16z(first=first)), None


def _visible_message_text(value: str) -> str:
    """Strip gettext context embedded by Wine resource conventions."""

    marker = "#msgctxt#"
    if not value.startswith(marker):
        return value
    separator = value.find("#", len(marker))
    return value[separator + 1 :] if separator >= 0 else value


def _signed_control_id(value: int, *, bits: int) -> int:
    sign = 1 << (bits - 1)
    return value - (1 << bits) if value & sign else value


def _with_symbols(
    resource_id: ResourceId,
    symbols: SymbolLookup | None,
) -> ResourceId:
    if symbols is None or resource_id.ordinal is None:
        return resource_id
    return ResourceId.from_ordinal(
        resource_id.ordinal,
        *symbols.symbols_for(resource_id.ordinal),
    )

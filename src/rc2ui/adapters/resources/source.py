from __future__ import annotations

from pathlib import Path

from rc2ui.adapters.pe.reader import PeFormatError, parse_pe
from rc2ui.adapters.res.binary import BinaryFormatError
from rc2ui.adapters.res.reader import ResFormatError, parse_res
from rc2ui.adapters.resources.model import (
    ResourceEntry,
    is_dialog_type,
)


class ResourceSourceError(ValueError):
    pass


def read_resource_source(path: Path) -> tuple[ResourceEntry, ...]:
    """Read a standalone .res file or resources embedded in a PE image."""

    return _read_resource_source(path, dialogs_only=False)


def read_dialog_resources(path: Path) -> tuple[ResourceEntry, ...]:
    """Read RT_DIALOG entries without materializing unrelated payloads."""

    return _read_resource_source(path, dialogs_only=True)


def _read_resource_source(
    path: Path,
    *,
    dialogs_only: bool,
) -> tuple[ResourceEntry, ...]:
    data = path.read_bytes()
    context = str(path)
    try:
        if data.startswith(b"MZ"):
            return parse_pe(
                data,
                context=context,
                resource_filter=is_dialog_type if dialogs_only else None,
            )
        return parse_res(
            data,
            context=context,
            resource_filter=is_dialog_type if dialogs_only else None,
        )
    except (BinaryFormatError, PeFormatError, ResFormatError) as error:
        raise ResourceSourceError(str(error)) from error

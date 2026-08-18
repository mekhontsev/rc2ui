from __future__ import annotations

from typing import Protocol

from rc2ui.domain.resource_id import ResourceId


RT_DIALOG = 5


class ResourceEntry(Protocol):
    """The subset of a compiled resource needed by the conversion pipeline."""

    resource_type: ResourceId
    resource_id: ResourceId
    data: bytes
    language: int
    file_offset: int

    @property
    def is_dialog(self) -> bool: ...


def is_dialog_type(resource_type: ResourceId) -> bool:
    return resource_type.ordinal == RT_DIALOG or (
        resource_type.name is not None
        and resource_type.name.casefold() == "rt_dialog"
    )

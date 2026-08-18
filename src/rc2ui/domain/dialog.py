from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from rc2ui.domain.geometry import RectDlu
from rc2ui.domain.resource_id import ResourceId


@dataclass(frozen=True, slots=True)
class DialogFont:
    point_size: int
    typeface: str
    weight: int = 400
    italic: bool = False
    charset: int = 1

    def __post_init__(self) -> None:
        if self.point_size < 0:
            raise ValueError("font point size cannot be negative")
        if not self.typeface:
            raise ValueError("font typeface cannot be empty")


@dataclass(frozen=True, slots=True)
class DialogKey:
    source: PurePosixPath
    resource_id: ResourceId
    language: int | None = None


@dataclass(frozen=True, slots=True)
class ControlKey:
    dialog: DialogKey
    resource_id: ResourceId
    occurrence: int = 1

    def __post_init__(self) -> None:
        if self.occurrence < 1:
            raise ValueError("control occurrence must be positive")


@dataclass(frozen=True, slots=True)
class Control:
    key: ControlKey
    class_name: str
    text: str | None
    rect: RectDlu
    style: int
    extended_style: int
    order: int
    help_id: int = 0
    content_resource: ResourceId | None = None
    creation_data: bytes = b""

    def __post_init__(self) -> None:
        if not self.class_name:
            raise ValueError("control class name cannot be empty")
        if self.order < 0:
            raise ValueError("control order cannot be negative")


@dataclass(frozen=True, slots=True)
class Dialog:
    key: DialogKey
    caption: str | None
    rect: RectDlu
    style: int
    extended_style: int
    controls: tuple[Control, ...]
    help_id: int = 0
    menu: ResourceId | None = None
    window_class: ResourceId | None = None
    font: DialogFont | None = None
    is_extended: bool = False

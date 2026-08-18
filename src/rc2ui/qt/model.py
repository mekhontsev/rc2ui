from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QtEnum:
    value: str


@dataclass(frozen=True, slots=True)
class QtCString:
    value: str


@dataclass(frozen=True, slots=True)
class QtString:
    value: str
    comment: str | None = None
    extra_comment: str | None = None
    translatable: bool = True


@dataclass(frozen=True, slots=True)
class QtFont:
    family: str
    point_size: int
    weight: int = 400
    italic: bool = False


@dataclass(frozen=True, slots=True)
class QtRect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class QtSize:
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class QtSizePolicy:
    horizontal: str
    vertical: str
    horizontal_stretch: int = 0
    vertical_stretch: int = 0


QtPropertyValue = (
    str
    | bool
    | int
    | float
    | QtEnum
    | QtCString
    | QtString
    | QtFont
    | QtRect
    | QtSize
    | QtSizePolicy
)


@dataclass(frozen=True, slots=True)
class QtProperty:
    name: str
    value: QtPropertyValue
    dynamic: bool = False


@dataclass(frozen=True, slots=True)
class QtSpacer:
    object_name: str
    orientation: str
    size_type: str = "Expanding"
    size_hint: int = 20


@dataclass(frozen=True, slots=True)
class QtCustomWidget:
    class_name: str
    extends: str
    header: str
    container: bool = False


@dataclass(frozen=True, slots=True)
class QtLayoutItem:
    widget: QtWidget | None = None
    layout: QtLayout | None = None
    spacer: QtSpacer | None = None
    row: int | None = None
    column: int | None = None
    row_span: int = 1
    column_span: int = 1
    alignment: str | None = None

    def __post_init__(self) -> None:
        members = (self.widget, self.layout, self.spacer)
        if sum(member is not None for member in members) != 1:
            raise ValueError("a layout item must contain exactly one child")


@dataclass(frozen=True, slots=True)
class QtLayout:
    class_name: str
    object_name: str
    items: tuple[QtLayoutItem, ...]
    properties: tuple[QtProperty, ...] = ()
    stretch: tuple[int, ...] = ()
    row_stretch: tuple[int, ...] = ()
    minimum_widths: tuple[int, ...] = ()
    minimum_heights: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class QtWidget:
    class_name: str
    object_name: str
    properties: tuple[QtProperty, ...] = ()
    layout: QtLayout | None = None
    custom_widget: QtCustomWidget | None = None
    children: tuple[QtWidget, ...] = ()
    button_group: str | None = None

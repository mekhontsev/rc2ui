from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rc2ui.domain.dialog import Control
from rc2ui.qt.model import QtCustomWidget, QtProperty


class ControlRole(StrEnum):
    LABEL = "label"
    INPUT = "input"
    ACTION = "action"
    DISPLAY = "display"
    GROUP = "group"
    CONTAINER = "container"
    DECORATION = "decoration"
    UNKNOWN = "unknown"


class SeparatorOrientation(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


@dataclass(frozen=True, slots=True)
class MappedControl:
    control: Control
    qt_class: str
    role: ControlRole
    properties: tuple[QtProperty, ...] = ()
    expands_horizontally: bool = False
    expands_vertically: bool = False
    warning: str | None = None
    custom_widget: QtCustomWidget | None = None
    separator_orientation: SeparatorOrientation | None = None
    button_group: str | None = None
    mapping_rule: str | None = None
    mapping_rule_key: str | None = None
    runtime_configured: tuple[str, ...] = ()

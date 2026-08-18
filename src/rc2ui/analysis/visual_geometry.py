from __future__ import annotations

from rc2ui.domain.dialog import Control
from rc2ui.domain.geometry import RectDlu


_CBS_TYPEMASK = 0x0003
_CBS_SIMPLE = 0x0001
_DEFAULT_COMBO_SELECTION_HEIGHT_DLU = 14
_DROPDOWN_HEIGHT_THRESHOLD_DLU = 18


def control_visual_rect(control: Control) -> RectDlu:
    """Return the rectangle occupied while a Win32 control is at rest.

    For dropdown combo boxes the RC ``cy`` includes the normally hidden list.
    Layout and containment inference need the closed selection field instead;
    the original rectangle remains untouched in the domain model and report.
    """

    rect = control.rect
    if (
        control.class_name.casefold() in {"combobox", "comboboxex32"}
        and control.style & _CBS_TYPEMASK != _CBS_SIMPLE
        and rect.height > _DROPDOWN_HEIGHT_THRESHOLD_DLU
    ):
        return RectDlu(
            rect.x,
            rect.y,
            rect.width,
            _DEFAULT_COMBO_SELECTION_HEIGHT_DLU,
        )
    return rect

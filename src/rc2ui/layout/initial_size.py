from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from rc2ui.domain.geometry import RectDlu
from rc2ui.mapping.model import ControlRole, MappedControl
from rc2ui.mapping.text_layout import estimated_control_text_width_dlu


_HORIZONTAL_PIXELS_PER_DLU = 1.75
_VERTICAL_PIXELS_PER_DLU = 1.875
_MINIMUM_STANDALONE_WIDTH_DLU = 60


@dataclass(frozen=True, slots=True)
class InitialFormSize:
    width: int
    height: int
    width_dlu: int


def initial_form_size(
    bounds: RectDlu,
    mapped_controls: tuple[MappedControl, ...],
    *,
    text_width_safety_factor: float = 1.1,
    max_designer_width_factor: float = 1.5,
) -> InitialFormSize:
    """Estimate a useful serialized Designer canvas from font-relative DLU.

    Qt can compute text-bearing widget size hints only after loading the .ui.
    Designer, however, initially displays the serialized root geometry.
    Estimate the width needed by single-line text in DLU and enlarge the
    canvas proportionally, matching the result of manually stretching the form
    while preserving every coordinate-grid relation.
    """

    required_width_dlu = float(max(1, bounds.width))
    # Very small templates are commonly embedded property-page extensions.
    # Their declared DLU rectangle is a contract with the host, not a useful
    # standalone Designer canvas that may be widened to fit placeholder text.
    if bounds.width < _MINIMUM_STANDALONE_WIDTH_DLU:
        return InitialFormSize(
            width=max(1, round(bounds.width * _HORIZONTAL_PIXELS_PER_DLU)),
            height=max(1, round(bounds.height * _VERTICAL_PIXELS_PER_DLU)),
            width_dlu=max(1, bounds.width),
        )
    for mapped in mapped_controls:
        control = mapped.control
        text_width = estimated_control_text_width_dlu(
            control.text or "",
            mapped.qt_class,
            safety_factor=text_width_safety_factor,
        )
        if (
            text_width is None
            or not control.text
            or "\n" in control.text
            or "\r" in control.text
            or mapped.multiline_text
            or (
                mapped.role is ControlRole.LABEL
                and control.rect.height >= 18
            )
            or control.rect.width <= 0
        ):
            continue
        if text_width <= control.rect.width:
            continue
        required_width_dlu = max(
            required_width_dlu,
            bounds.width * text_width / control.rect.width,
        )
    required_width_dlu = min(
        required_width_dlu,
        bounds.width * max_designer_width_factor,
    )
    effective_width_dlu = max(1, ceil(required_width_dlu))
    return InitialFormSize(
        width=max(1, ceil(effective_width_dlu * _HORIZONTAL_PIXELS_PER_DLU)),
        height=max(1, round(bounds.height * _VERTICAL_PIXELS_PER_DLU)),
        width_dlu=effective_width_dlu,
    )

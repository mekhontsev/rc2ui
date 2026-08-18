from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from unicodedata import combining, east_asian_width

from rc2ui.domain.geometry import RectDlu
from rc2ui.mapping.model import ControlRole, MappedControl


_HORIZONTAL_PIXELS_PER_DLU = 1.75
_VERTICAL_PIXELS_PER_DLU = 1.875
_AVERAGE_CHARACTER_WIDTH_DLU = 4.0
_TEXT_WIDTH_SAFETY_FACTOR = 1.1
_MINIMUM_STANDALONE_WIDTH_DLU = 60
_MAXIMUM_DESIGNER_WIDTH_FACTOR = 1.5
_NARROW_CHARACTERS = frozenset(" !'(),.:;I[]`ijl|\u00b7")
_WIDE_CHARACTERS = frozenset("%&@MWQmw")
_TEXT_PADDING_DLU = {
    "QLabel": 2.0,
    "QGroupBox": 8.0,
    "QCheckBox": 14.0,
    "QRadioButton": 14.0,
    "QPushButton": 10.0,
    "QToolButton": 8.0,
}


@dataclass(frozen=True, slots=True)
class InitialFormSize:
    width: int
    height: int
    width_dlu: int


def initial_form_size(
    bounds: RectDlu,
    mapped_controls: tuple[MappedControl, ...],
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
        padding_dlu = _TEXT_PADDING_DLU.get(mapped.qt_class)
        if (
            padding_dlu is None
            or not control.text
            or "\n" in control.text
            or "\r" in control.text
            or (
                mapped.role is ControlRole.LABEL
                and control.rect.height >= 18
            )
            or control.rect.width <= 0
        ):
            continue
        text_width = _control_text_width_dlu(
            control.text,
            padding_dlu=padding_dlu,
        )
        if text_width <= control.rect.width:
            continue
        required_width_dlu = max(
            required_width_dlu,
            bounds.width * text_width / control.rect.width,
        )
    required_width_dlu = min(
        required_width_dlu,
        bounds.width * _MAXIMUM_DESIGNER_WIDTH_FACTOR,
    )
    effective_width_dlu = max(1, ceil(required_width_dlu))
    return InitialFormSize(
        width=max(1, ceil(effective_width_dlu * _HORIZONTAL_PIXELS_PER_DLU)),
        height=max(1, round(bounds.height * _VERTICAL_PIXELS_PER_DLU)),
        width_dlu=effective_width_dlu,
    )


def _control_text_width_dlu(text: str, *, padding_dlu: float) -> float:
    display_text = _without_mnemonics(text)
    units = sum(_character_width(character) for character in display_text)
    return (
        units * _AVERAGE_CHARACTER_WIDTH_DLU + padding_dlu
    ) * _TEXT_WIDTH_SAFETY_FACTOR


def _without_mnemonics(text: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == "&" and index + 1 < len(text):
            if text[index + 1] == "&":
                result.append("&")
                index += 2
                continue
            index += 1
            continue
        result.append(character)
        index += 1
    return "".join(result)


def _character_width(character: str) -> float:
    if combining(character):
        return 0.0
    if east_asian_width(character) in {"W", "F"}:
        return 2.0
    if character in _NARROW_CHARACTERS:
        return 0.55
    if character in _WIDE_CHARACTERS:
        return 1.35
    return 1.0

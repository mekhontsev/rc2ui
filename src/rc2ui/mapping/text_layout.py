from __future__ import annotations

from unicodedata import combining, east_asian_width


_AVERAGE_CHARACTER_WIDTH_DLU = 4.0
_DEFAULT_TEXT_WIDTH_SAFETY_FACTOR = 1.1
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


def estimated_control_text_width_dlu(
    text: str,
    qt_class: str,
    *,
    safety_factor: float = _DEFAULT_TEXT_WIDTH_SAFETY_FACTOR,
) -> float | None:
    """Estimate the single-line width including native Qt decoration."""

    padding_dlu = _TEXT_PADDING_DLU.get(qt_class)
    if padding_dlu is None:
        return None
    return (estimated_text_width_dlu(text) + padding_dlu) * safety_factor


def wrap_control_text_dlu(
    text: str,
    *,
    qt_class: str,
    width_dlu: int,
    safety_factor: float = _DEFAULT_TEXT_WIDTH_SAFETY_FACTOR,
) -> str:
    """Insert stable word breaks for Qt buttons lacking native word wrap."""

    padding_dlu = _TEXT_PADDING_DLU.get(qt_class, 0.0)
    available = max(
        _AVERAGE_CHARACTER_WIDTH_DLU,
        width_dlu / safety_factor - padding_dlu,
    )
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(
        _wrap_paragraph(paragraph, available)
        for paragraph in normalized.split("\n")
    )


def estimated_text_width_dlu(text: str) -> float:
    display_text = _without_mnemonics(text)
    return sum(_character_width(character) for character in display_text) * (
        _AVERAGE_CHARACTER_WIDTH_DLU
    )


def _wrap_paragraph(paragraph: str, available_dlu: float) -> str:
    words = paragraph.split()
    if not words:
        return ""
    lines: list[str] = []
    current = ""
    for word in words:
        if not current:
            fragments = _split_long_word(word, available_dlu)
            lines.extend(fragments[:-1])
            current = fragments[-1]
            continue
        candidate = f"{current} {word}"
        if estimated_text_width_dlu(candidate) <= available_dlu:
            current = candidate
            continue
        lines.append(current)
        fragments = _split_long_word(word, available_dlu)
        lines.extend(fragments[:-1])
        current = fragments[-1]
    lines.append(current)
    return "\n".join(lines)


def _split_long_word(word: str, available_dlu: float) -> list[str]:
    if estimated_text_width_dlu(word) <= available_dlu:
        return [word]
    fragments: list[str] = []
    current = ""
    for atom in _mnemonic_atoms(word):
        candidate = current + atom
        if current and estimated_text_width_dlu(candidate) > available_dlu:
            fragments.append(current)
            current = atom
        else:
            current = candidate
    if current:
        fragments.append(current)
    return fragments or [word]


def _mnemonic_atoms(text: str) -> tuple[str, ...]:
    atoms: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == "&" and index + 1 < len(text):
            atoms.append(text[index : index + 2])
            index += 2
            continue
        atoms.append(character)
        index += 1
    return tuple(atoms)


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

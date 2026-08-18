from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CustomWidgetSubstitution:
    custom_class: str
    base_class: str
    count: int


@dataclass(frozen=True, slots=True)
class BuddyBinding:
    label_name: str
    buddy_name: str


@dataclass(frozen=True, slots=True)
class PreparedUiXml:
    text: str
    substitutions: tuple[CustomWidgetSubstitution, ...]
    buddies: tuple[BuddyBinding, ...]
    widget_names: tuple[str, ...]
    serialized_size: tuple[int, int] | None


def prepare_ui_xml(text: str) -> PreparedUiXml:
    """Replace promoted widgets for runtime checks without changing source UI."""

    root = ET.fromstring(text)
    replacements: dict[str, str] = {}
    custom_widgets = root.find("./customwidgets")
    if custom_widgets is not None:
        for custom in custom_widgets.findall("./customwidget"):
            class_name = (custom.findtext("class") or "").strip()
            base_class = (custom.findtext("extends") or "QWidget").strip()
            if class_name:
                replacements[class_name] = base_class or "QWidget"
    replacements = {
        class_name: _ultimate_base(class_name, replacements)
        for class_name in replacements
    }

    counts = {class_name: 0 for class_name in replacements}
    widget_names = tuple(
        name
        for widget in root.iter("widget")
        if (name := widget.get("name"))
    )
    serialized_size = _root_widget_size(root)
    for widget in root.iter("widget"):
        class_name = widget.get("class") or ""
        if class_name in replacements:
            widget.set("class", replacements[class_name])
            counts[class_name] += 1

    if custom_widgets is not None:
        root.remove(custom_widgets)

    buddies: list[BuddyBinding] = []
    for widget in root.iter("widget"):
        label_name = widget.get("name")
        if not label_name:
            continue
        buddy = widget.find("./property[@name='buddy']/cstring")
        if buddy is not None and buddy.text:
            buddies.append(BuddyBinding(label_name, buddy.text))

    substitutions = tuple(
        CustomWidgetSubstitution(class_name, replacements[class_name], count)
        for class_name, count in sorted(counts.items())
        if count
    )
    ET.indent(root, space=" ")
    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return PreparedUiXml(
        text='<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n",
        substitutions=substitutions,
        buddies=tuple(buddies),
        widget_names=widget_names,
        serialized_size=serialized_size,
    )


def _root_widget_size(root: ET.Element) -> tuple[int, int] | None:
    geometry = root.find("./widget/property[@name='geometry']/rect")
    if geometry is None:
        return None
    try:
        width = int(geometry.findtext("width", ""))
        height = int(geometry.findtext("height", ""))
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _ultimate_base(class_name: str, replacements: dict[str, str]) -> str:
    current = replacements[class_name]
    visited = {class_name}
    while current in replacements:
        if current in visited:
            return "QWidget"
        visited.add(current)
        current = replacements[current]
    return current or "QWidget"

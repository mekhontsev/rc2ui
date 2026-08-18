from __future__ import annotations

import xml.etree.ElementTree as ET


class UiValidationError(ValueError):
    pass


def validate_ui_xml(text: str) -> None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise UiValidationError(f"invalid XML: {error}") from error
    if root.tag != "ui" or root.get("version") != "4.0":
        raise UiValidationError("root element must be <ui version=\"4.0\">")
    widgets = root.findall("./widget")
    if len(widgets) != 1:
        raise UiValidationError("a .ui file must contain exactly one root widget")
    if root.findtext("class") is None:
        raise UiValidationError("a .ui file must declare its form class")

    seen: set[str] = set()
    for element in root.iter():
        if element.tag not in {"widget", "layout", "spacer", "buttongroup"}:
            continue
        name = element.get("name")
        if not name:
            raise UiValidationError(f"<{element.tag}> is missing object name")
        if name in seen:
            raise UiValidationError(f"duplicate object name {name!r}")
        seen.add(name)

    root_widget = widgets[0]
    # Geometry is forbidden only for widgets owned by a layout item. Qt
    # Designer also permits direct, unmanaged child widgets; rc2ui uses those
    # for Win32 controls deliberately parked off-screen for later repositioning.
    for child_widget in root_widget.findall(".//item/widget"):
        geometry = child_widget.find("./property[@name='geometry']")
        if geometry is not None:
            raise UiValidationError(
                f"layout-managed widget {child_widget.get('name')!r} has geometry"
            )

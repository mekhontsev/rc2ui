from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from rc2ui.qt.model import (
    QtCString,
    QtCustomWidget,
    QtEnum,
    QtFont,
    QtLayout,
    QtLayoutItem,
    QtProperty,
    QtRect,
    QtSize,
    QtSizePolicy,
    QtSpacer,
    QtString,
    QtWidget,
)


def emit_ui(
    root_widget: QtWidget,
    *,
    form_class: str | None = None,
    include_comments: bool = True,
) -> str:
    root = ET.Element("ui", {"version": "4.0"})
    class_element = ET.SubElement(root, "class")
    class_element.text = form_class or form_class_name(root_widget.object_name)
    _emit_widget(root, root_widget)
    custom_widgets = _collect_custom_widgets(root_widget)
    if custom_widgets:
        custom_element = ET.SubElement(root, "customwidgets")
        for custom_widget in custom_widgets:
            item = ET.SubElement(custom_element, "customwidget")
            _text_child(item, "class", custom_widget.class_name)
            _text_child(item, "extends", custom_widget.extends)
            _text_child(item, "header", custom_widget.header)
            if custom_widget.container:
                _text_child(item, "container", "1")
    ET.SubElement(root, "resources")
    ET.SubElement(root, "connections")
    button_groups = _collect_button_groups(root_widget)
    if button_groups:
        groups_element = ET.SubElement(root, "buttongroups")
        for name in button_groups:
            ET.SubElement(groups_element, "buttongroup", {"name": name})
    if not include_comments:
        _remove_string_comments(root)
    ET.indent(root, space=" ")
    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def write_ui(
    path: Path,
    root_widget: QtWidget,
    *,
    form_class: str | None = None,
    include_comments: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        emit_ui(
            root_widget,
            form_class=form_class,
            include_comments=include_comments,
        ),
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _remove_string_comments(root: ET.Element) -> None:
    for element in root.iter("string"):
        element.attrib.pop("comment", None)
        element.attrib.pop("extracomment", None)


def _emit_widget(parent: ET.Element, widget: QtWidget) -> None:
    element = ET.SubElement(
        parent,
        "widget",
        {"class": widget.class_name, "name": widget.object_name},
    )
    for property_ in widget.properties:
        _emit_property(element, property_)
    if widget.button_group:
        attribute = ET.SubElement(
            element,
            "attribute",
            {"name": "buttonGroup"},
        )
        value = ET.SubElement(attribute, "string", {"notr": "true"})
        value.text = widget.button_group
    for child in widget.children:
        _emit_widget(element, child)
    if widget.layout:
        _emit_layout(element, widget.layout)


def _emit_layout(parent: ET.Element, layout: QtLayout) -> None:
    attributes = {"class": layout.class_name, "name": layout.object_name}
    if layout.stretch:
        key = "columnstretch" if layout.class_name == "QGridLayout" else "stretch"
        attributes[key] = ",".join(str(value) for value in layout.stretch)
    if layout.row_stretch and layout.class_name == "QGridLayout":
        attributes["rowstretch"] = ",".join(
            str(value) for value in layout.row_stretch
        )
    if layout.minimum_widths and layout.class_name == "QGridLayout":
        attributes["columnminimumwidth"] = ",".join(
            str(value) for value in layout.minimum_widths
        )
    if layout.minimum_heights and layout.class_name == "QGridLayout":
        attributes["rowminimumheight"] = ",".join(
            str(value) for value in layout.minimum_heights
        )
    element = ET.SubElement(parent, "layout", attributes)
    for property_ in layout.properties:
        _emit_property(element, property_)
    for item in layout.items:
        _emit_layout_item(element, item)


def _emit_layout_item(parent: ET.Element, item: QtLayoutItem) -> None:
    attributes: dict[str, str] = {}
    if item.row is not None:
        attributes["row"] = str(item.row)
    if item.column is not None:
        attributes["column"] = str(item.column)
    if item.row_span != 1:
        attributes["rowspan"] = str(item.row_span)
    if item.column_span != 1:
        attributes["colspan"] = str(item.column_span)
    if item.alignment:
        attributes["alignment"] = item.alignment
    element = ET.SubElement(parent, "item", attributes)
    if item.widget:
        _emit_widget(element, item.widget)
    elif item.layout:
        _emit_layout(element, item.layout)
    else:
        assert item.spacer is not None
        _emit_spacer(element, item.spacer)


def _emit_spacer(parent: ET.Element, spacer: QtSpacer) -> None:
    element = ET.SubElement(parent, "spacer", {"name": spacer.object_name})
    _emit_property(
        element,
        QtProperty(
            "orientation",
            QtEnum(
                "Qt::Orientation::Horizontal"
                if spacer.orientation == "horizontal"
                else "Qt::Orientation::Vertical"
            ),
        ),
    )
    _emit_property(
        element,
        QtProperty(
            "sizeType",
            QtEnum(f"QSizePolicy::Policy::{spacer.size_type}"),
        ),
    )
    property_element = ET.SubElement(
        element, "property", {"name": "sizeHint", "stdset": "0"}
    )
    size = ET.SubElement(property_element, "size")
    width = ET.SubElement(size, "width")
    height = ET.SubElement(size, "height")
    if spacer.orientation == "horizontal":
        width.text = str(spacer.size_hint)
        height.text = "0"
    else:
        width.text = "0"
        height.text = str(spacer.size_hint)


def _emit_property(parent: ET.Element, property_: QtProperty) -> None:
    attributes = {"name": property_.name}
    if property_.dynamic:
        # Qt Designer marks dynamic QObject properties with stdset="0".
        # uic then calls QObject.setProperty() instead of inventing a setter
        # such as QLabel.setRc2uiInternal().
        attributes["stdset"] = "0"
    element = ET.SubElement(parent, "property", attributes)
    value = property_.value
    if isinstance(value, bool):
        child = ET.SubElement(element, "bool")
        child.text = "true" if value else "false"
    elif isinstance(value, int):
        child = ET.SubElement(element, "number")
        child.text = str(value)
    elif isinstance(value, float):
        child = ET.SubElement(element, "double")
        child.text = format(value, ".15g")
    elif isinstance(value, str):
        child = ET.SubElement(element, "string")
        child.text = value
    elif isinstance(value, QtString):
        attributes: dict[str, str] = {}
        if not value.translatable:
            attributes["notr"] = "true"
        if value.comment:
            attributes["comment"] = value.comment
        if value.extra_comment:
            attributes["extracomment"] = value.extra_comment
        child = ET.SubElement(element, "string", attributes)
        child.text = value.value
    elif isinstance(value, QtEnum):
        child = ET.SubElement(element, "set" if "|" in value.value else "enum")
        child.text = value.value
    elif isinstance(value, QtCString):
        child = ET.SubElement(element, "cstring")
        child.text = value.value
    elif isinstance(value, QtFont):
        child = ET.SubElement(element, "font")
        _text_child(child, "family", value.family)
        _text_child(child, "pointsize", str(value.point_size))
        _text_child(child, "weight", str(value.weight))
        _text_child(child, "italic", "true" if value.italic else "false")
    elif isinstance(value, QtRect):
        child = ET.SubElement(element, "rect")
        _text_child(child, "x", str(value.x))
        _text_child(child, "y", str(value.y))
        _text_child(child, "width", str(value.width))
        _text_child(child, "height", str(value.height))
    elif isinstance(value, QtSize):
        child = ET.SubElement(element, "size")
        _text_child(child, "width", str(value.width))
        _text_child(child, "height", str(value.height))
    elif isinstance(value, QtSizePolicy):
        child = ET.SubElement(
            element,
            "sizepolicy",
            {
                "hsizetype": value.horizontal,
                "vsizetype": value.vertical,
            },
        )
        _text_child(child, "horstretch", str(value.horizontal_stretch))
        _text_child(child, "verstretch", str(value.vertical_stretch))
    else:
        raise TypeError(f"unsupported Qt property value: {type(value).__name__}")


def _text_child(parent: ET.Element, tag: str, value: str) -> None:
    child = ET.SubElement(parent, tag)
    child.text = value


def form_class_name(object_name: str) -> str:
    parts = [part for part in object_name.split("_") if part]
    value = "".join(part[:1].upper() + part[1:] for part in parts) or "Dialog"
    return value[:1].upper() + value[1:]


def _collect_custom_widgets(root: QtWidget) -> tuple[QtCustomWidget, ...]:
    found: dict[str, QtCustomWidget] = {}

    def visit_widget(widget: QtWidget) -> None:
        if widget.custom_widget:
            previous = found.get(widget.custom_widget.class_name)
            if previous is not None and previous != widget.custom_widget:
                raise ValueError(
                    f"conflicting custom widget declarations for "
                    f"{widget.custom_widget.class_name}"
                )
            found[widget.custom_widget.class_name] = widget.custom_widget
        for child in widget.children:
            visit_widget(child)
        if widget.layout:
            visit_layout(widget.layout)

    def visit_layout(layout: QtLayout) -> None:
        for item in layout.items:
            if item.widget:
                visit_widget(item.widget)
            elif item.layout:
                visit_layout(item.layout)

    visit_widget(root)
    return tuple(found[name] for name in sorted(found))


def _collect_button_groups(root: QtWidget) -> tuple[str, ...]:
    found: set[str] = set()

    def visit_widget(widget: QtWidget) -> None:
        if widget.button_group:
            found.add(widget.button_group)
        for child in widget.children:
            visit_widget(child)
        if widget.layout:
            visit_layout(widget.layout)

    def visit_layout(layout: QtLayout) -> None:
        for item in layout.items:
            if item.widget:
                visit_widget(item.widget)
            elif item.layout:
                visit_layout(item.layout)

    visit_widget(root)
    return tuple(sorted(found))

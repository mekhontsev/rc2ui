from __future__ import annotations

from dataclasses import replace

from rc2ui.domain.dialog import Control
from rc2ui.mapping.model import (
    ControlRole,
    MappedControl,
    SeparatorOrientation,
)
from rc2ui.mapping.overrides import ControlMap
from rc2ui.mapping.text_layout import wrap_control_text_dlu
from rc2ui.qt.model import QtEnum, QtProperty, QtSize, QtSizePolicy, QtString


WS_DISABLED = 0x08000000

BS_TYPEMASK = 0x0000000F
BS_DEFPUSHBUTTON = 0x00000001
BS_CHECKBOX = 0x00000002
BS_AUTOCHECKBOX = 0x00000003
BS_RADIOBUTTON = 0x00000004
BS_3STATE = 0x00000005
BS_AUTO3STATE = 0x00000006
BS_GROUPBOX = 0x00000007
BS_AUTORADIOBUTTON = 0x00000009
BS_OWNERDRAW = 0x0000000B
BS_MULTILINE = 0x00002000

ES_MULTILINE = 0x00000004
ES_PASSWORD = 0x00000020
ES_READONLY = 0x00000800

CBS_TYPEMASK = 0x00000003
CBS_SIMPLE = 0x00000001
CBS_DROPDOWN = 0x00000002

SS_TYPEMASK = 0x0000001F
SS_CENTER = 0x00000001
SS_RIGHT = 0x00000002
SS_ICON = 0x00000003
SS_BITMAP = 0x0000000E
SS_BLACKRECT = 0x00000004
SS_GRAYRECT = 0x00000005
SS_WHITERECT = 0x00000006
SS_BLACKFRAME = 0x00000007
SS_GRAYFRAME = 0x00000008
SS_WHITEFRAME = 0x00000009
SS_ETCHEDHORZ = 0x00000010
SS_ETCHEDVERT = 0x00000011
SS_ETCHEDFRAME = 0x00000012
SS_NOPREFIX = 0x00000080

SBS_VERT = 0x00000001

LBS_MULTIPLESEL = 0x00000008
LBS_OWNERDRAWFIXED = 0x00000010
LBS_OWNERDRAWVARIABLE = 0x00000020
LBS_EXTENDEDSEL = 0x00000800

CBS_OWNERDRAWFIXED = 0x00000010
CBS_OWNERDRAWVARIABLE = 0x00000020

PBS_VERTICAL = 0x00000004
TBS_VERT = 0x00000002


class ControlMapper:
    def __init__(self, overrides: ControlMap | None = None) -> None:
        self.overrides = overrides

    def map(self, control: Control) -> MappedControl:
        if self.overrides and (mapped := self.overrides.map(control)):
            return _finalize_mapping(mapped)
        class_name = control.class_name.casefold()
        if class_name == "button":
            mapped = self._button(control)
        elif class_name == "edit" or class_name.startswith("richedit"):
            mapped = self._edit(control, rich=class_name.startswith("richedit"))
        elif class_name == "static":
            mapped = self._static(control)
        elif class_name == "listbox":
            mapped = self._listbox(control)
        elif class_name == "combobox":
            mapped = self._combo(control)
        elif class_name == "scrollbar":
            mapped = self._scrollbar(control)
        else:
            mapped = self._common_or_custom(control, class_name)
        return _finalize_mapping(mapped)

    def _button(self, control: Control) -> MappedControl:
        type_ = control.style & BS_TYPEMASK
        text = _text_property(control)
        if type_ == BS_GROUPBOX:
            return MappedControl(
                control,
                "QGroupBox",
                ControlRole.GROUP,
                properties=_property_if_text("title", control.text)
                + (
                    QtProperty(
                        "sizePolicy",
                        QtSizePolicy("Ignored", "Preferred"),
                    ),
                ),
                expands_horizontally=True,
                expands_vertically=True,
            )
        if type_ in {BS_CHECKBOX, BS_AUTOCHECKBOX, BS_3STATE, BS_AUTO3STATE}:
            properties = list(text)
            if type_ in {BS_3STATE, BS_AUTO3STATE}:
                properties.append(QtProperty("tristate", True))
            multiline = bool(
                control.style & BS_MULTILINE
            ) and control.rect.height >= 18
            tall = multiline or control.rect.height >= 24
            properties.append(
                QtProperty(
                    "sizePolicy",
                    QtSizePolicy(
                        "Ignored",
                        "Preferred" if tall else "Fixed",
                    ),
                )
            )
            return MappedControl(
                control,
                "QCheckBox",
                ControlRole.INPUT,
                tuple(properties),
                expands_horizontally=True,
                expands_vertically=tall,
            )
        if type_ in {BS_RADIOBUTTON, BS_AUTORADIOBUTTON}:
            multiline = bool(
                control.style & BS_MULTILINE
            ) and control.rect.height >= 18
            tall = multiline or control.rect.height >= 24
            return MappedControl(
                control,
                "QRadioButton",
                ControlRole.INPUT,
                text
                + (
                    QtProperty(
                        "sizePolicy",
                        QtSizePolicy(
                            "Ignored",
                            "Preferred" if tall else "Fixed",
                        ),
                    ),
                ),
                expands_horizontally=True,
                expands_vertically=tall,
            )
        if type_ == BS_OWNERDRAW:
            return MappedControl(
                control,
                "QWidget",
                ControlRole.UNKNOWN,
                expands_horizontally=True,
                expands_vertically=True,
                warning="owner-draw button requires a custom Qt widget",
            )
        if (
            control.text
            and len(control.text) <= 3
            and control.rect.width <= 24
        ):
            return MappedControl(
                control,
                "QToolButton",
                ControlRole.ACTION,
                text
                + (
                    QtProperty(
                        "maximumSize",
                        QtSize(
                            max(1, round(control.rect.width * 1.75)),
                            16_777_215,
                        ),
                    ),
                ),
            )
        multiline = bool(
            control.style & BS_MULTILINE
        ) and control.rect.height >= 18
        properties = list(text)
        # The RC rectangle, not a translated text sizeHint, defines the grid.
        # Ignored prevents captions from shifting tracks; filling the source
        # cell also keeps button edges/centres aligned throughout resize.
        properties.append(
            QtProperty(
                "sizePolicy",
                QtSizePolicy(
                    "Ignored",
                    "Preferred" if multiline else "Fixed",
                ),
            )
        )
        if type_ == BS_DEFPUSHBUTTON:
            properties.append(QtProperty("default", True))
        return MappedControl(
            control,
            "QPushButton",
            ControlRole.ACTION,
            tuple(properties),
            expands_horizontally=True,
            expands_vertically=multiline,
        )

    def _edit(self, control: Control, *, rich: bool) -> MappedControl:
        multiline = bool(control.style & ES_MULTILINE) or rich
        tall_single_line = not multiline and control.rect.height >= 24
        properties: list[QtProperty] = []
        if control.text:
            properties.append(
                QtProperty(
                    "plainText" if multiline else "text",
                    QtString(control.text),
                )
            )
        if control.style & ES_READONLY:
            properties.append(QtProperty("readOnly", True))
        if not multiline and control.style & ES_PASSWORD:
            properties.append(QtProperty("echoMode", QtEnum("QLineEdit::Password")))
        if tall_single_line:
            properties.append(
                QtProperty(
                    "sizePolicy",
                    QtSizePolicy("Ignored", "Preferred"),
                )
            )
        else:
            properties.append(
                QtProperty(
                    "sizePolicy",
                    QtSizePolicy(
                        "Ignored",
                        "Ignored" if multiline else "Fixed",
                    ),
                )
            )
        return MappedControl(
            control,
            "QTextEdit" if multiline else "QLineEdit",
            ControlRole.INPUT,
            tuple(properties),
            expands_horizontally=True,
            # Some hand-written/test resources intentionally make a
            # single-line Win32 EDIT much taller than its normal text field.
            # Preserve that occupied rectangle instead of letting QLineEdit's
            # natural height collapse it.
            expands_vertically=multiline or tall_single_line,
        )

    def _static(self, control: Control) -> MappedControl:
        type_ = control.style & SS_TYPEMASK
        if not control.text and (
            (control.rect.width <= 2 and control.rect.height >= 6)
            or (control.rect.height <= 2 and control.rect.width >= 6)
        ):
            vertical = control.rect.width <= 2
            orientation = (
                SeparatorOrientation.VERTICAL
                if vertical
                else SeparatorOrientation.HORIZONTAL
            )
            return MappedControl(
                control,
                "QFrame",
                ControlRole.DECORATION,
                (
                    QtProperty(
                        "frameShape",
                        QtEnum("QFrame::VLine" if vertical else "QFrame::HLine"),
                    ),
                    QtProperty("frameShadow", QtEnum("QFrame::Sunken")),
                ),
                expands_horizontally=not vertical,
                expands_vertically=vertical,
                separator_orientation=orientation,
            )
        if type_ in {
            SS_BLACKRECT,
            SS_GRAYRECT,
            SS_WHITERECT,
            SS_BLACKFRAME,
            SS_GRAYFRAME,
            SS_WHITEFRAME,
        }:
            orientation = _thin_frame_orientation(control)
            return MappedControl(
                control,
                "QFrame",
                ControlRole.DECORATION,
                (QtProperty("frameShape", QtEnum("QFrame::Box")),),
                expands_horizontally=(
                    orientation is not SeparatorOrientation.VERTICAL
                ),
                expands_vertically=(
                    orientation is not SeparatorOrientation.HORIZONTAL
                ),
                warning="colored Win32 static frame is approximated by QFrame",
                separator_orientation=orientation,
            )
        if type_ in {SS_ETCHEDHORZ, SS_ETCHEDVERT, SS_ETCHEDFRAME}:
            shape = {
                SS_ETCHEDHORZ: "QFrame::HLine",
                SS_ETCHEDVERT: "QFrame::VLine",
                SS_ETCHEDFRAME: "QFrame::StyledPanel",
            }[type_]
            return MappedControl(
                control,
                "QFrame",
                ControlRole.DECORATION,
                (
                    QtProperty("frameShape", QtEnum(shape)),
                    QtProperty("frameShadow", QtEnum("QFrame::Sunken")),
                ),
                expands_horizontally=type_ != SS_ETCHEDVERT,
                expands_vertically=type_ != SS_ETCHEDHORZ,
                separator_orientation={
                    SS_ETCHEDHORZ: SeparatorOrientation.HORIZONTAL,
                    SS_ETCHEDVERT: SeparatorOrientation.VERTICAL,
                    SS_ETCHEDFRAME: None,
                }[type_],
            )
        if type_ in {SS_ICON, SS_BITMAP}:
            return MappedControl(
                control,
                "QLabel",
                ControlRole.DISPLAY,
                expands_horizontally=True,
                expands_vertically=True,
                warning="icon/bitmap resource export is not implemented yet",
            )
        text = control.text
        if text and control.style & SS_NOPREFIX:
            text = text.replace("&", "&&")
        properties = list(_property_if_text("text", text))
        multiline = bool(text) and control.rect.height >= 18
        if multiline:
            properties.append(QtProperty("wordWrap", True))
        if type_ == SS_CENTER:
            properties.append(QtProperty("alignment", QtEnum("Qt::AlignCenter")))
        elif type_ == SS_RIGHT:
            properties.append(
                QtProperty("alignment", QtEnum("Qt::AlignRight|Qt::AlignVCenter"))
            )
        empty_placeholder = (
            not text and control.rect.width >= 8 and control.rect.height >= 18
        )
        expands_vertically = empty_placeholder or multiline
        # A single-line label must contribute its actual text width to the
        # layout minimum; otherwise Designer can open the form with clipped
        # text even though a manual horizontal resize immediately fixes it.
        # Wrapped and empty labels still use their author-chosen RC width so
        # text wraps instead of forcing an arbitrarily wide dialog.
        properties.append(
            QtProperty(
                "sizePolicy",
                QtSizePolicy(
                    "Ignored" if multiline or not text else "Minimum",
                    "Preferred",
                ),
            )
        )
        return MappedControl(
            control,
            "QLabel",
            ControlRole.LABEL,
            tuple(properties),
            expands_horizontally=True,
            expands_vertically=expands_vertically,
            multiline_text=multiline,
        )

    def _listbox(self, control: Control) -> MappedControl:
        properties: list[QtProperty] = []
        if control.style & LBS_EXTENDEDSEL:
            properties.append(
                QtProperty(
                    "selectionMode",
                    QtEnum("QAbstractItemView::ExtendedSelection"),
                )
            )
        elif control.style & LBS_MULTIPLESEL:
            properties.append(
                QtProperty(
                    "selectionMode",
                    QtEnum("QAbstractItemView::MultiSelection"),
                )
            )
        owner_draw = control.style & (LBS_OWNERDRAWFIXED | LBS_OWNERDRAWVARIABLE)
        return MappedControl(
            control,
            "QListWidget",
            ControlRole.INPUT,
            tuple(properties),
            expands_horizontally=True,
            expands_vertically=True,
            warning=("owner-draw list box requires review" if owner_draw else None),
        )

    def _combo(self, control: Control) -> MappedControl:
        type_ = control.style & CBS_TYPEMASK
        properties: tuple[QtProperty, ...] = (
            QtProperty(
                "sizePolicy",
                QtSizePolicy(
                    "Ignored",
                    "Ignored" if type_ == CBS_SIMPLE else "Fixed",
                ),
            ),
        )
        if type_ in {CBS_SIMPLE, CBS_DROPDOWN}:
            properties += (QtProperty("editable", True),)
        return MappedControl(
            control,
            "QComboBox",
            ControlRole.INPUT,
            properties,
            expands_horizontally=True,
            expands_vertically=type_ == CBS_SIMPLE,
            warning=(
                "owner-draw combo box requires review"
                if control.style & (CBS_OWNERDRAWFIXED | CBS_OWNERDRAWVARIABLE)
                else None
            ),
        )

    def _scrollbar(self, control: Control) -> MappedControl:
        vertical = bool(control.style & SBS_VERT)
        return MappedControl(
            control,
            "QScrollBar",
            ControlRole.INPUT,
            (
                QtProperty(
                    "orientation",
                    QtEnum("Qt::Vertical" if vertical else "Qt::Horizontal"),
                ),
            ),
            expands_horizontally=not vertical,
            expands_vertically=vertical,
        )

    def _common_or_custom(
        self, control: Control, class_name: str
    ) -> MappedControl:
        if class_name == "systreeview32":
            return _expanding(control, "QTreeWidget")
        if class_name == "syslistview32":
            qt_class = "QTableWidget" if control.style & 0x0003 == 0x0001 else "QListWidget"
            return _expanding(control, qt_class)
        if class_name == "sysheader32":
            return MappedControl(control, "QHeaderView", ControlRole.DISPLAY)
        if class_name == "systabcontrol32":
            return MappedControl(
                control,
                "QTabWidget",
                ControlRole.CONTAINER,
                expands_horizontally=True,
                expands_vertically=True,
                warning=(
                    "tab page membership is runtime information and requires review"
                ),
            )
        if class_name == "msctls_progress32":
            vertical = bool(control.style & PBS_VERTICAL)
            return MappedControl(
                control,
                "QProgressBar",
                ControlRole.DISPLAY,
                (
                    QtProperty(
                        "orientation",
                        QtEnum("Qt::Vertical" if vertical else "Qt::Horizontal"),
                    ),
                ),
                expands_horizontally=not vertical,
                expands_vertically=vertical,
            )
        if class_name == "msctls_trackbar32":
            vertical = bool(control.style & TBS_VERT)
            properties = [
                QtProperty(
                    "orientation",
                    QtEnum("Qt::Vertical" if vertical else "Qt::Horizontal"),
                )
            ]
            if vertical:
                properties.append(
                    QtProperty(
                        "sizePolicy",
                        QtSizePolicy("Expanding", "Expanding"),
                    )
                )
            return MappedControl(
                control,
                "QSlider",
                ControlRole.INPUT,
                tuple(properties),
                # Win32 vertical trackbars often reserve substantial width
                # for ticks on one or both sides.  QSlider's narrow native
                # sizeHint would otherwise collapse that source rectangle.
                expands_horizontally=True,
                expands_vertically=vertical,
            )
        if class_name == "sysdatetimepick32":
            return MappedControl(
                control,
                "QDateTimeEdit",
                ControlRole.INPUT,
                expands_horizontally=True,
            )
        if class_name == "sysmonthcal32":
            return _expanding(control, "QCalendarWidget")
        if class_name == "msctls_updown32":
            return MappedControl(
                control,
                "QSpinBox",
                ControlRole.INPUT,
                (
                    QtProperty(
                        "maximumSize",
                        QtSize(
                            max(1, round(control.rect.width * 1.75)),
                            16_777_215,
                        ),
                    ),
                ),
            )
        if class_name == "comboboxex32":
            return MappedControl(
                control,
                "QComboBox",
                ControlRole.INPUT,
                expands_horizontally=True,
            )
        if class_name == "sysipaddress32":
            return MappedControl(
                control,
                "QLineEdit",
                ControlRole.INPUT,
                expands_horizontally=True,
                warning="IP address validation must be supplied by application code",
            )
        if class_name == "msctls_hotkey32":
            return MappedControl(control, "QKeySequenceEdit", ControlRole.INPUT)
        if class_name == "nativefontctl":
            return MappedControl(
                control,
                "QFontComboBox",
                ControlRole.INPUT,
                expands_horizontally=True,
            )
        if class_name == "msctls_statusbar32":
            return MappedControl(
                control,
                "QStatusBar",
                ControlRole.DISPLAY,
                expands_horizontally=True,
            )
        if class_name == "toolbarwindow32":
            vertical = control.rect.height > control.rect.width * 2
            properties: tuple[QtProperty, ...] = (
                QtProperty(
                    "sizePolicy",
                    QtSizePolicy("Expanding", "Expanding"),
                ),
                QtProperty(
                    "minimumSize",
                    QtSize(
                        max(1, round(control.rect.width * 1.75)),
                        max(1, round(control.rect.height * 1.875)),
                    ),
                ),
            )
            if vertical:
                properties += (
                    QtProperty("orientation", QtEnum("Qt::Vertical")),
                )
            return MappedControl(
                control,
                "QToolBar",
                ControlRole.CONTAINER,
                properties,
                expands_horizontally=True,
                expands_vertically=True,
                warning="toolbar actions are runtime data and require review",
            )
        if class_name == "sysanimate32":
            return MappedControl(
                control,
                "QLabel",
                ControlRole.DISPLAY,
                expands_horizontally=True,
                expands_vertically=True,
                warning="animation resource export is not implemented yet",
            )
        if class_name in {"syslink", "link window"}:
            multiline = control.rect.height >= 18
            properties = _text_property(control)
            if multiline:
                properties += (QtProperty("wordWrap", True),)
            properties += (
                QtProperty(
                    "sizePolicy",
                    QtSizePolicy(
                        "Ignored",
                        "Preferred",
                    ),
                ),
            )
            return MappedControl(
                control,
                "QLabel",
                ControlRole.ACTION,
                properties,
                expands_horizontally=True,
                expands_vertically=multiline,
                multiline_text=multiline,
            )
        return MappedControl(
            control,
            "QWidget",
            ControlRole.UNKNOWN,
            expands_horizontally=True,
            expands_vertically=True,
            warning=f"unmapped Win32 class {control.class_name!r}",
        )


def _expanding(
    control: Control,
    qt_class: str,
    *,
    role: ControlRole = ControlRole.INPUT,
) -> MappedControl:
    return MappedControl(
        control,
        qt_class,
        role,
        expands_horizontally=True,
        expands_vertically=True,
    )


def _text_property(control: Control) -> tuple[QtProperty, ...]:
    return _property_if_text("text", control.text)


def _property_if_text(name: str, text: str | None) -> tuple[QtProperty, ...]:
    return (QtProperty(name, QtString(text)),) if text is not None else ()


def _finalize_mapping(mapped: MappedControl) -> MappedControl:
    return _with_common_properties(_with_multiline_button_text(mapped))


def _with_multiline_button_text(mapped: MappedControl) -> MappedControl:
    control = mapped.control
    if (
        mapped.qt_class
        not in {"QCheckBox", "QRadioButton", "QPushButton", "QToolButton"}
        or not control.style & BS_MULTILINE
    ):
        return mapped
    mapped = replace(mapped, multiline_text=True)
    if control.rect.height < 18 or not control.text:
        return mapped
    wrapped = wrap_control_text_dlu(
        control.text,
        qt_class=mapped.qt_class,
        width_dlu=control.rect.width,
    )
    if "\n" not in wrapped:
        return mapped
    found = False
    properties: list[QtProperty] = []
    for property_ in mapped.properties:
        if property_.name != "text":
            properties.append(property_)
            continue
        value = property_.value
        if isinstance(value, QtString):
            value = replace(value, value=wrapped)
        elif isinstance(value, str):
            value = wrapped
        else:
            properties.append(property_)
            continue
        properties.append(replace(property_, value=value))
        found = True
    if not found:
        return mapped
    return replace(mapped, properties=tuple(properties))


def _with_common_properties(mapped: MappedControl) -> MappedControl:
    if not mapped.control.style & WS_DISABLED:
        return mapped
    return MappedControl(
        control=mapped.control,
        qt_class=mapped.qt_class,
        role=mapped.role,
        properties=mapped.properties + (QtProperty("enabled", False),),
        expands_horizontally=mapped.expands_horizontally,
        expands_vertically=mapped.expands_vertically,
        warning=mapped.warning,
        custom_widget=mapped.custom_widget,
        separator_orientation=mapped.separator_orientation,
        button_group=mapped.button_group,
        mapping_rule=mapped.mapping_rule,
        mapping_rule_key=mapped.mapping_rule_key,
        runtime_configured=mapped.runtime_configured,
        multiline_text=mapped.multiline_text,
    )


def _thin_frame_orientation(control: Control) -> SeparatorOrientation | None:
    if (
        0 < control.rect.width <= 3
        and control.rect.height >= control.rect.width * 4
    ):
        return SeparatorOrientation.VERTICAL
    if (
        0 < control.rect.height <= 3
        and control.rect.width >= control.rect.height * 4
    ):
        return SeparatorOrientation.HORIZONTAL
    return None

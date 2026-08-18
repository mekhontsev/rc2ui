from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from rc2ui.analysis.labels import LabelAssociation, match_labels
from rc2ui.domain.diagnostics import Diagnostic, Severity
from rc2ui.domain.dialog import Control, Dialog
from rc2ui.domain.resource_id import ResourceId
from rc2ui.mapping.model import ControlRole, MappedControl
from rc2ui.naming.identifier import lower_camel_identifier
from rc2ui.naming.map import NamingKind, NamingMap, NamingMapError


class NameSource(StrEnum):
    EXPLICIT = "explicit"
    LABEL = "label"
    TEXT = "text"
    RESOURCE_ID = "resource-id"
    GENERATED = "generated"


@dataclass(frozen=True, slots=True)
class NameDecision:
    object_name: str
    source: NameSource
    confidence: float
    evidence: tuple[str, ...]
    rule_key: tuple[int, str | None] | None = None


@dataclass(frozen=True, slots=True)
class NamingResult:
    dialog: NameDecision
    controls: tuple[NameDecision, ...]
    label_associations: tuple[LabelAssociation, ...]
    diagnostics: tuple[Diagnostic, ...]

    def for_order(self, order: int) -> NameDecision:
        return self.controls[order]


class NameResolver:
    def __init__(self, naming_map: NamingMap | None = None) -> None:
        self.naming_map = naming_map

    def resolve(
        self,
        dialog: Dialog,
        mapped_controls: tuple[MappedControl, ...],
    ) -> NamingResult:
        diagnostics: list[Diagnostic] = []
        associations = match_labels(mapped_controls)
        by_target = {item.target_order: item for item in associations}
        by_label = {item.label_order: item for item in associations}
        mapped_by_order = {item.control.order: item for item in mapped_controls}

        dialog_decision = self._dialog_name(dialog, diagnostics)
        if dialog_decision.source is NameSource.GENERATED:
            diagnostics.append(
                Diagnostic(
                    code="naming.low-confidence",
                    severity=Severity.WARNING,
                    message=(
                        f"dialog uses generated name "
                        f"{dialog_decision.object_name!r}"
                    ),
                    location=f"{dialog.key.source}:{dialog.key.resource_id.display_name}",
                )
            )
        decisions: list[NameDecision] = []
        generated_control_count = 0
        used: dict[str, int] = {}
        for control in sorted(dialog.controls, key=lambda item: item.order):
            mapped = mapped_by_order[control.order]
            decision = self._control_name(
                dialog,
                mapped,
                mapped_by_order,
                by_target,
                by_label,
                diagnostics,
            )
            unique_name = _make_unique(decision.object_name, used)
            if unique_name != decision.object_name:
                diagnostics.append(
                    Diagnostic(
                        code="naming.duplicate",
                        severity=(
                            Severity.ERROR
                            if decision.source is NameSource.EXPLICIT
                            else Severity.WARNING
                        ),
                        message=(
                            f"duplicate objectName {decision.object_name!r}; "
                            f"generated {unique_name!r}"
                        ),
                        location=_control_location(dialog, control),
                    )
                )
                decision = NameDecision(
                    unique_name,
                    decision.source,
                    decision.confidence,
                    decision.evidence + ("disambiguated duplicate name",),
                    decision.rule_key,
                )
            if decision.source is NameSource.GENERATED:
                generated_control_count += 1
            decisions.append(decision)

        if generated_control_count:
            noun = "control" if generated_control_count == 1 else "controls"
            diagnostics.append(
                Diagnostic(
                    code="naming.generated-controls",
                    severity=Severity.INFO,
                    message=(
                        f"{generated_control_count} {noun} use generated "
                        "object names; details are available in name suggestions"
                    ),
                    location=(
                        f"{dialog.key.source}:"
                        f"{dialog.key.resource_id.display_name}"
                    ),
                )
            )

        return NamingResult(
            dialog=dialog_decision,
            controls=tuple(decisions),
            label_associations=associations,
            diagnostics=tuple(diagnostics),
        )

    def _dialog_name(
        self, dialog: Dialog, diagnostics: list[Diagnostic]
    ) -> NameDecision:
        explicit = self._naming_map_match(
            dialog,
            kind=NamingKind.DIALOG,
            resource_id=dialog.key.resource_id,
            occurrence=1,
            diagnostics=diagnostics,
        )
        if explicit:
            return explicit
        if base := _base_from_resource_id(dialog.key.resource_id.symbols, dialog=True):
            return _decision(base, "Dialog", NameSource.RESOURCE_ID, 0.9, "dialog ID")
        if base := semantic_base(dialog.caption):
            return _decision(base, "Dialog", NameSource.TEXT, 0.72, "dialog caption")
        identity = dialog.key.resource_id.ordinal
        return NameDecision(
            f"dialog_{identity if identity is not None else 'resource'}",
            NameSource.GENERATED,
            0.3,
            ("no semantic dialog name was available",),
        )

    def _control_name(
        self,
        dialog: Dialog,
        mapped: MappedControl,
        mapped_by_order: dict[int, MappedControl],
        by_target: dict[int, LabelAssociation],
        by_label: dict[int, LabelAssociation],
        diagnostics: list[Diagnostic],
    ) -> NameDecision:
        control = mapped.control
        explicit = self._naming_map_match(
            dialog,
            kind=NamingKind.CONTROL,
            resource_id=control.key.resource_id,
            occurrence=control.key.occurrence,
            diagnostics=diagnostics,
        )
        if explicit:
            return explicit

        association = by_target.get(control.order) or by_label.get(control.order)
        if association:
            label = mapped_by_order[association.label_order].control
            if base := semantic_base(label.text):
                return _decision(
                    base,
                    _suffix(mapped.qt_class),
                    NameSource.LABEL,
                    association.confidence,
                    f"associated label {label.text!r}",
                    *association.evidence,
                )

        if mapped.role in {
            ControlRole.ACTION,
            ControlRole.GROUP,
        } or mapped.qt_class in {"QCheckBox", "QRadioButton"}:
            if base := semantic_base(control.text):
                return _decision(
                    base,
                    _suffix(mapped.qt_class),
                    NameSource.TEXT,
                    0.84,
                    "control text",
                )

        if base := _base_from_resource_id(control.key.resource_id.symbols):
            return _decision(
                base,
                _suffix(mapped.qt_class),
                NameSource.RESOURCE_ID,
                0.78,
                "control ID",
            )

        class_stem = mapped.qt_class.rsplit("::", 1)[-1]
        stem = _lower_first(class_stem.removeprefix("Q")) or "widget"
        return NameDecision(
            f"{stem}_{control.order + 1}",
            NameSource.GENERATED,
            0.25,
            ("no explicit or semantic name was available",),
        )

    def _naming_map_match(
        self,
        dialog: Dialog,
        *,
        kind: NamingKind,
        resource_id: ResourceId,
        occurrence: int,
        diagnostics: list[Diagnostic],
    ) -> NameDecision | None:
        if self.naming_map is None:
            return None
        try:
            match = self.naming_map.resolve(
                source=dialog.key.source,
                dialog=dialog.key.resource_id,
                kind=kind,
                resource_id=resource_id,
                occurrence=occurrence,
            )
        except NamingMapError as error:
            diagnostics.append(
                Diagnostic(
                    code="naming.map-rule-error",
                    severity=Severity.ERROR,
                    message=str(error),
                    location=str(dialog.key.source),
                )
            )
            return None
        if match is None:
            return None
        capture_evidence = ", ".join(
            f"{name}={value!r}" for name, value in match.captures
        )
        rule_evidence = (
            f"naming rule {match.rule.display_name!r} "
            f"(#{match.rule.index})"
        )
        evidence = (
            rule_evidence,
            (
                f"ID {match.matched_id!r} matched regex "
                f"{match.rule.id_regex.pattern!r}"
            ),
        )
        if capture_evidence:
            evidence += (f"captures: {capture_evidence}",)
        return NameDecision(
            match.object_name,
            NameSource.EXPLICIT,
            1.0,
            evidence,
            match.rule.key,
        )


def semantic_base(text: str | None) -> str | None:
    if not text:
        return None
    text = text.replace("&&", "\0").replace("&", "").replace("\0", "&")
    text = re.sub(r"\(&.\)\s*$", "", text)
    text = re.sub(r"[:：…\.]+\s*$", "", text).strip()
    return lower_camel_identifier(text)


def _base_from_resource_id(
    symbols: tuple[str, ...], *, dialog: bool = False
) -> str | None:
    generic_tokens = {
        "IDC", "ID", "IDD", "STATIC", "CONTROL", "EDIT", "EDITTEXT", "BUTTON",
        "PUSHBUTTON", "LABEL", "TEXT", "COMBO", "COMBOBOX", "LIST", "LISTBOX",
        "CHECK", "CHECKBOX", "RADIO", "RADIOBUTTON",
    }
    standard = {"IDOK": "ok", "IDCANCEL": "cancel", "IDHELP": "help"}
    ordered_symbols = sorted(
        symbols,
        key=lambda symbol: (
            0
            if (symbol.startswith("IDD_") if dialog else symbol.startswith("IDC_"))
            else 1,
            symbol,
        ),
    )
    for symbol in ordered_symbols:
        if dialog and not symbol.startswith("IDD_"):
            continue
        if not dialog and symbol.startswith("IDD_"):
            continue
        if symbol in standard:
            return standard[symbol]
        value = symbol
        for prefix in (("IDD_",) if dialog else ("IDC_", "ID_")):
            if value.startswith(prefix):
                value = value[len(prefix) :]
                break
        tokens = [token for token in value.split("_") if token]
        meaningful = [token for token in tokens if token.upper() not in generic_tokens]
        if not meaningful or all(token.isdigit() for token in meaningful):
            continue
        if len(meaningful) == 1 and re.fullmatch(
            r"(?:EDIT|BUTTON|STATIC|COMBO|LIST|CONTROL)\d+",
            meaningful[0],
            flags=re.IGNORECASE,
        ):
            continue
        return semantic_base(" ".join(meaningful))
    return None


def _suffix(qt_class: str) -> str:
    return {
        "QLabel": "Label",
        "QLineEdit": "Edit",
        "QTextEdit": "TextEdit",
        "QPlainTextEdit": "TextEdit",
        "QPushButton": "Button",
        "QCheckBox": "CheckBox",
        "QRadioButton": "RadioButton",
        "QGroupBox": "GroupBox",
        "QComboBox": "ComboBox",
        "QListWidget": "List",
        "QTreeWidget": "Tree",
        "QTableWidget": "Table",
        "QScrollBar": "ScrollBar",
        "QSlider": "Slider",
        "QProgressBar": "ProgressBar",
        "QDateTimeEdit": "DateTimeEdit",
        "QCalendarWidget": "Calendar",
        "QSpinBox": "SpinBox",
        "QTabWidget": "TabWidget",
        "QFrame": "Frame",
        "QWidget": "Widget",
    }.get(
        qt_class,
        qt_class.rsplit("::", 1)[-1].removeprefix("Q") or "Widget",
    )


def _decision(
    base: str,
    suffix: str,
    source: NameSource,
    confidence: float,
    *evidence: str,
) -> NameDecision:
    name = base if base.casefold().endswith(suffix.casefold()) else base + suffix
    return NameDecision(name, source, confidence, tuple(evidence))


def _make_unique(name: str, used: dict[str, int]) -> str:
    count = used.get(name, 0) + 1
    used[name] = count
    return name if count == 1 else f"{name}_{count}"


def _lower_first(value: str) -> str:
    return value[:1].lower() + value[1:]


def _control_location(dialog: Dialog, control: Control) -> str:
    return (
        f"{dialog.key.source}:{dialog.key.resource_id.display_name}:"
        f"{control.key.resource_id.display_name}[{control.key.occurrence}]"
    )

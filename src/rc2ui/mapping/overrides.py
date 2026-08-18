from __future__ import annotations

import re
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Pattern

from rc2ui.domain.dialog import Control
from rc2ui.domain.resource_id import ResourceId
from rc2ui.mapping.model import ControlRole, MappedControl
from rc2ui.qt.model import (
    QtCString,
    QtCustomWidget,
    QtEnum,
    QtProperty,
    QtString,
)


_CPP_CLASS = re.compile(r"^[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*$")
_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")
_CONFIG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SECTION_FIELDS = frozenset({"widgets", "rules", "bindings", "compounds"})
_WIDGET_FIELDS = frozenset(
    {
        "name",
        "qt_class",
        "role",
        "header",
        "extends",
        "container",
        "expands_horizontally",
        "expands_vertically",
        "text_property",
        "properties",
        "warning",
    }
)
_RULE_FIELDS = frozenset(
    {
        "name",
        "widget",
        "source_regex",
        "dialog_regex",
        "win_class",
        "win_class_regex",
        "id_regex",
        "occurrence",
        "style_mask",
        "style_value",
        "priority",
        "button_group",
        "runtime_configured",
    }
)
_BINDING_FIELDS = frozenset(
    {"name", "source_regex", "dialog_regex", "priority", "controls"}
)
_BINDING_CONTROL_FIELDS = frozenset(
    {
        "win_class",
        "id",
        "widget",
        "occurrence",
        "style_mask",
        "style_value",
        "button_group",
        "runtime_configured",
    }
)
_COMPOUND_FIELDS = frozenset(
    {
        "name",
        "widget",
        "source_regex",
        "dialog_regex",
        "primary",
        "members",
        "priority",
        "runtime_configured",
    }
)
_COMPOUND_SELECTOR_FIELDS = frozenset({"win_class", "id", "occurrence"})
_STRUCTURED_PROPERTY_FIELDS = frozenset({"enum", "cstring"})


class ControlMapError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WidgetProfile:
    name: str
    qt_class: str
    role: ControlRole
    expands_horizontally: bool
    expands_vertically: bool
    text_property: str | None
    properties: tuple[QtProperty, ...]
    custom_widget: QtCustomWidget | None
    warning: str | None
    index: int


@dataclass(frozen=True, slots=True)
class ControlMapRule:
    name: str
    widget: WidgetProfile
    source_regex: Pattern[str] | None
    dialog_regex: Pattern[str] | None
    win_class: str | None
    win_class_regex: Pattern[str] | None
    id_regex: Pattern[str] | None
    exact_id: str | None
    occurrence: int | None
    style_mask: int
    style_value: int
    priority: int
    button_group: str | None
    runtime_configured: tuple[str, ...]
    key: str
    location_suffix: str

    @property
    def display_name(self) -> str:
        if self.exact_id is None:
            return self.name
        assert self.win_class is not None
        return f"{self.name}[{self.win_class}:{self.exact_id}]"

    @property
    def specificity(self) -> int:
        return sum(
            item is not None
            for item in (
                self.source_regex,
                self.dialog_regex,
                self.win_class,
                self.id_regex,
                self.exact_id,
                self.occurrence,
            )
        )

    @property
    def rank(self) -> tuple[int, int, int, int]:
        return (
            self.priority,
            int(self.exact_id is not None),
            self.specificity,
            self.style_mask.bit_count(),
        )

    def matches(self, control: Control) -> bool:
        source = control.key.dialog.source.as_posix()
        if (
            self.source_regex is not None
            and self.source_regex.fullmatch(source) is None
        ):
            return False
        if self.dialog_regex is not None and not _matches_resource_id(
            self.dialog_regex,
            control.key.dialog.resource_id,
            preferred_prefix="IDD_",
        ):
            return False
        if (
            self.win_class is not None
            and control.class_name.casefold() != self.win_class.casefold()
        ):
            return False
        if (
            self.win_class_regex is not None
            and self.win_class_regex.fullmatch(control.class_name) is None
        ):
            return False
        if self.id_regex is not None and not _matches_resource_id(
            self.id_regex,
            control.key.resource_id,
            preferred_prefix="IDC_",
        ):
            return False
        return (
            (self.occurrence is None or self.occurrence == control.key.occurrence)
            and control.style & self.style_mask == self.style_value
        )

    def map(self, control: Control) -> MappedControl:
        properties = self.widget.properties
        if self.widget.text_property and control.text is not None:
            properties = tuple(
                item
                for item in properties
                if item.name != self.widget.text_property
            ) + (
                QtProperty(
                    self.widget.text_property,
                    QtString(control.text),
                ),
            )
        return MappedControl(
            control=control,
            qt_class=self.widget.qt_class,
            role=self.widget.role,
            properties=properties,
            expands_horizontally=self.widget.expands_horizontally,
            expands_vertically=self.widget.expands_vertically,
            warning=self.widget.warning,
            custom_widget=self.widget.custom_widget,
            button_group=self.button_group,
            mapping_rule=self.display_name,
            mapping_rule_key=self.key,
            runtime_configured=self.runtime_configured,
        )


@dataclass(frozen=True, slots=True)
class ExactControlSelector:
    win_class: str
    exact_id: str
    occurrence: int | None = None

    @property
    def identity(self) -> tuple[str, str, int | None]:
        return self.win_class.casefold(), self.exact_id, self.occurrence

    def matches(self, control: Control) -> bool:
        return (
            control.class_name.casefold() == self.win_class.casefold()
            and self.exact_id
            in _resource_candidates(
                control.key.resource_id,
                preferred_prefix="IDC_",
            )
            and (
                self.occurrence is None
                or control.key.occurrence == self.occurrence
            )
        )


@dataclass(frozen=True, slots=True)
class ControlCompoundRule:
    name: str
    widget: WidgetProfile
    source_regex: Pattern[str] | None
    dialog_regex: Pattern[str] | None
    primary: ExactControlSelector
    members: tuple[ExactControlSelector, ...]
    priority: int
    runtime_configured: tuple[str, ...]
    index: int
    key: str
    location_suffix: str

    @property
    def selectors(self) -> tuple[ExactControlSelector, ...]:
        return (self.primary,) + self.members

    @property
    def specificity(self) -> int:
        return sum(
            pattern is not None
            for pattern in (self.source_regex, self.dialog_regex)
        )

    @property
    def rank(self) -> tuple[int, int]:
        return self.priority, self.specificity

    def matches_dialog(self, control: Control) -> bool:
        source = control.key.dialog.source.as_posix()
        return (
            (
                self.source_regex is None
                or self.source_regex.fullmatch(source) is not None
            )
            and (
                self.dialog_regex is None
                or _matches_resource_id(
                    self.dialog_regex,
                    control.key.dialog.resource_id,
                    preferred_prefix="IDD_",
                )
            )
        )


@dataclass(frozen=True, slots=True)
class ControlMap:
    widgets: tuple[WidgetProfile, ...]
    rules: tuple[ControlMapRule, ...]
    compounds: tuple[ControlCompoundRule, ...] = ()

    @classmethod
    def from_table(cls, data: object, *, path: Path) -> ControlMap:
        if not isinstance(data, dict):
            raise ControlMapError(f"{path}: controls must be a TOML table")
        unexpected = sorted(set(data) - _SECTION_FIELDS)
        if unexpected:
            if "controls" in unexpected:
                raise ControlMapError(
                    f"{path}: nested [[controls.controls]] is not supported; "
                    "use [[controls.widgets]], [[controls.rules]], and "
                    "[[controls.bindings]] or [[controls.compounds]]"
                )
            raise ControlMapError(
                f"{path}: controls has unexpected field(s): "
                + ", ".join(unexpected)
            )

        raw_widgets = data.get("widgets", [])
        raw_rules = data.get("rules", [])
        raw_bindings = data.get("bindings", [])
        raw_compounds = data.get("compounds", [])
        if not isinstance(raw_widgets, list):
            raise ControlMapError(
                f"{path}: widgets must use [[controls.widgets]]"
            )
        if not isinstance(raw_rules, list):
            raise ControlMapError(
                f"{path}: rules must use [[controls.rules]]"
            )
        if not isinstance(raw_bindings, list):
            raise ControlMapError(
                f"{path}: bindings must use [[controls.bindings]]"
            )
        if not isinstance(raw_compounds, list):
            raise ControlMapError(
                f"{path}: compounds must use [[controls.compounds]]"
            )

        widgets = tuple(
            _parse_widget(raw, index=index, path=path)
            for index, raw in enumerate(raw_widgets, 1)
        )
        _reject_duplicate_names(widgets, path=path, kind="widget")
        by_name = {widget.name: widget for widget in widgets}

        rules = tuple(
            _parse_rule(
                raw,
                index=index,
                path=path,
                widgets=by_name,
            )
            for index, raw in enumerate(raw_rules, 1)
        )
        binding_rules: list[ControlMapRule] = []
        binding_names: dict[str, int] = {}
        for index, raw in enumerate(raw_bindings, 1):
            name, expanded = _parse_binding(
                raw,
                index=index,
                path=path,
                widgets=by_name,
            )
            if previous := binding_names.get(name):
                raise ControlMapError(
                    f"{path}: duplicate binding name {name!r} in bindings "
                    f"#{previous} and #{index}"
                )
            binding_names[name] = index
            binding_rules.extend(expanded)
        _validate_rule_names(rules, binding_names, path)
        all_rules = rules + tuple(binding_rules)
        _reject_duplicate_matchers(all_rules, path)
        compounds = tuple(
            _parse_compound(
                raw,
                index=index,
                path=path,
                widgets=by_name,
            )
            for index, raw in enumerate(raw_compounds, 1)
        )
        _reject_duplicate_compound_names(compounds, path)
        _reject_duplicate_compound_matchers(compounds, path)
        return cls(widgets, all_rules, compounds)

    def map(self, control: Control) -> MappedControl | None:
        matches = [rule for rule in self.rules if rule.matches(control)]
        if not matches:
            return None
        best_rank = max(rule.rank for rule in matches)
        leaders = [rule for rule in matches if rule.rank == best_rank]
        if len(leaders) > 1:
            names = ", ".join(repr(rule.display_name) for rule in leaders)
            raise ControlMapError(
                f"ambiguous control-map rules {names} for "
                f"{control.class_name}:{control.key.resource_id.display_name}"
            )
        return leaders[0].map(control)


def _parse_widget(raw: object, *, index: int, path: Path) -> WidgetProfile:
    location = f"{path}: controls.widgets #{index}"
    table = _table(raw, location)
    _reject_unknown(table, _WIDGET_FIELDS, location)
    name = _config_name(table, "name", location)
    qt_class = _string(table, "qt_class", location)
    if not _CPP_CLASS.fullmatch(qt_class):
        raise ControlMapError(
            f"{location} has invalid Qt class {qt_class!r}"
        )
    try:
        role = ControlRole(str(table.get("role", "input")))
    except ValueError as error:
        choices = ", ".join(role.value for role in ControlRole)
        raise ControlMapError(
            f"{location} role must be one of: {choices}"
        ) from error
    text_property = _optional_identifier(table, "text_property", location)
    properties = _properties(table.get("properties", {}), location)

    header = table.get("header")
    extends = table.get("extends", "QWidget")
    container = _boolean(table.get("container", False), "container", location)
    custom_widget = None
    if header is not None:
        if not isinstance(header, str) or not header:
            raise ControlMapError(f"{location} header must be a non-empty string")
        if not isinstance(extends, str) or not _CPP_CLASS.fullmatch(extends):
            raise ControlMapError(f"{location} has invalid extends class")
        custom_widget = QtCustomWidget(qt_class, extends, header, container)
    elif "extends" in table or "container" in table:
        raise ControlMapError(
            f"{location} extends/container require a promoted widget header"
        )

    warning = table.get("warning")
    if warning is not None and (
        not isinstance(warning, str) or not warning
    ):
        raise ControlMapError(f"{location} warning must be a non-empty string")
    return WidgetProfile(
        name=name,
        qt_class=qt_class,
        role=role,
        expands_horizontally=_boolean(
            table.get("expands_horizontally", False),
            "expands_horizontally",
            location,
        ),
        expands_vertically=_boolean(
            table.get("expands_vertically", False),
            "expands_vertically",
            location,
        ),
        text_property=text_property,
        properties=properties,
        custom_widget=custom_widget,
        warning=warning,
        index=index,
    )


def _parse_rule(
    raw: object,
    *,
    index: int,
    path: Path,
    widgets: dict[str, WidgetProfile],
) -> ControlMapRule:
    location = f"{path}: controls.rules #{index}"
    table = _table(raw, location)
    _reject_unknown(table, _RULE_FIELDS, location)
    name = _config_name(table, "name", location)
    widget = _widget_reference(table, widgets, location)
    win_class, win_class_regex = _class_selector(table, location)
    style_mask, style_value = _style_selector(table, location)
    return ControlMapRule(
        name=name,
        widget=widget,
        source_regex=_optional_regex(table, "source_regex", location),
        dialog_regex=_optional_regex(table, "dialog_regex", location),
        win_class=win_class,
        win_class_regex=win_class_regex,
        id_regex=_optional_regex(table, "id_regex", location),
        exact_id=None,
        occurrence=_optional_occurrence(table, location),
        style_mask=style_mask,
        style_value=style_value,
        priority=_integer(table.get("priority", 0), "priority", location),
        button_group=_optional_identifier(table, "button_group", location),
        runtime_configured=_runtime_properties(table, location),
        key=f"rules#{index}",
        location_suffix=f"rules#{index}",
    )


def _parse_binding(
    raw: object,
    *,
    index: int,
    path: Path,
    widgets: dict[str, WidgetProfile],
) -> tuple[str, tuple[ControlMapRule, ...]]:
    location = f"{path}: controls.bindings #{index}"
    table = _table(raw, location)
    _reject_unknown(table, _BINDING_FIELDS, location)
    name = _config_name(table, "name", location)
    source_regex = _optional_regex(table, "source_regex", location)
    dialog_regex = _optional_regex(table, "dialog_regex", location)
    priority = _integer(table.get("priority", 0), "priority", location)
    raw_controls = table.get("controls")
    if not isinstance(raw_controls, list) or not raw_controls:
        raise ControlMapError(
            f"{location} controls must be a non-empty array of inline tables"
        )
    rules = tuple(
        _parse_binding_control(
            raw_control,
            group_name=name,
            binding_index=index,
            entry_index=entry_index,
            source_regex=source_regex,
            dialog_regex=dialog_regex,
            priority=priority,
            path=path,
            widgets=widgets,
        )
        for entry_index, raw_control in enumerate(raw_controls, 1)
    )
    return name, rules


def _parse_binding_control(
    raw: object,
    *,
    group_name: str,
    binding_index: int,
    entry_index: int,
    source_regex: Pattern[str] | None,
    dialog_regex: Pattern[str] | None,
    priority: int,
    path: Path,
    widgets: dict[str, WidgetProfile],
) -> ControlMapRule:
    location = (
        f"{path}: controls.bindings #{binding_index} controls #{entry_index}"
    )
    table = _table(raw, location)
    _reject_unknown(table, _BINDING_CONTROL_FIELDS, location)
    win_class = _string(table, "win_class", location)
    exact_id = _string(table, "id", location)
    widget = _widget_reference(table, widgets, location)
    style_mask, style_value = _style_selector(table, location)
    return ControlMapRule(
        name=group_name,
        widget=widget,
        source_regex=source_regex,
        dialog_regex=dialog_regex,
        win_class=win_class,
        win_class_regex=None,
        id_regex=re.compile(re.escape(exact_id)),
        exact_id=exact_id,
        occurrence=_optional_occurrence(table, location),
        style_mask=style_mask,
        style_value=style_value,
        priority=priority,
        button_group=_optional_identifier(table, "button_group", location),
        runtime_configured=_runtime_properties(table, location),
        key=f"bindings#{binding_index}.controls#{entry_index}",
        location_suffix=f"bindings#{binding_index}.controls#{entry_index}",
    )


def _parse_compound(
    raw: object,
    *,
    index: int,
    path: Path,
    widgets: dict[str, WidgetProfile],
) -> ControlCompoundRule:
    location = f"{path}: controls.compounds #{index}"
    table = _table(raw, location)
    _reject_unknown(table, _COMPOUND_FIELDS, location)
    name = _config_name(table, "name", location)
    primary = _parse_compound_selector(
        table.get("primary"),
        location=f"{location} primary",
    )
    raw_members = table.get("members")
    if not isinstance(raw_members, list) or not raw_members:
        raise ControlMapError(
            f"{location} members must be a non-empty array of inline tables"
        )
    members = tuple(
        _parse_compound_selector(
            item,
            location=f"{location} members #{member_index}",
        )
        for member_index, item in enumerate(raw_members, 1)
    )
    selectors = (primary,) + members
    identities = [selector.identity for selector in selectors]
    if len(set(identities)) != len(identities):
        raise ControlMapError(
            f"{location} primary and members must use distinct exact selectors"
        )
    return ControlCompoundRule(
        name=name,
        widget=_widget_reference(table, widgets, location),
        source_regex=_optional_regex(table, "source_regex", location),
        dialog_regex=_optional_regex(table, "dialog_regex", location),
        primary=primary,
        members=members,
        priority=_integer(table.get("priority", 0), "priority", location),
        runtime_configured=_runtime_properties(table, location),
        index=index,
        key=f"compounds#{index}",
        location_suffix=f"compounds#{index}",
    )


def _parse_compound_selector(
    raw: object,
    *,
    location: str,
) -> ExactControlSelector:
    table = _table(raw, location)
    _reject_unknown(table, _COMPOUND_SELECTOR_FIELDS, location)
    return ExactControlSelector(
        win_class=_string(table, "win_class", location),
        exact_id=_string(table, "id", location),
        occurrence=_optional_occurrence(table, location),
    )


def _class_selector(
    table: dict[str, object],
    location: str,
) -> tuple[str | None, Pattern[str] | None]:
    has_exact = "win_class" in table
    has_regex = "win_class_regex" in table
    if has_exact == has_regex:
        raise ControlMapError(
            f"{location} requires exactly one of win_class or win_class_regex"
        )
    if has_exact:
        return _string(table, "win_class", location), None
    return None, _required_regex(table, "win_class_regex", location)


def _style_selector(
    table: dict[str, object],
    location: str,
) -> tuple[int, int]:
    mask = _integer(table.get("style_mask", 0), "style_mask", location)
    value = _integer(table.get("style_value", 0), "style_value", location)
    if value & ~mask:
        raise ControlMapError(
            f"{location} style_value contains bits outside style_mask"
        )
    return mask, value


def _widget_reference(
    table: dict[str, object],
    widgets: dict[str, WidgetProfile],
    location: str,
) -> WidgetProfile:
    name = _string(table, "widget", location)
    try:
        return widgets[name]
    except KeyError as error:
        raise ControlMapError(
            f"{location} references unknown widget profile {name!r}"
        ) from error


def _runtime_properties(
    table: dict[str, object],
    location: str,
) -> tuple[str, ...]:
    value = table.get("runtime_configured", [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) and _IDENTIFIER.fullmatch(item)
        for item in value
    ):
        raise ControlMapError(
            f"{location} runtime_configured must be an array of property names"
        )
    if len(set(value)) != len(value):
        raise ControlMapError(
            f"{location} runtime_configured properties must be unique"
        )
    return tuple(value)


def _properties(raw: object, location: str) -> tuple[QtProperty, ...]:
    if not isinstance(raw, dict):
        raise ControlMapError(f"{location} properties must be a TOML table")
    properties: list[QtProperty] = []
    for name, value in raw.items():
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            raise ControlMapError(
                f"{location} has invalid Qt property name {name!r}"
            )
        converted: object
        if isinstance(value, str):
            converted = QtString(value, translatable=False)
        elif isinstance(value, (bool, int, float)):
            if isinstance(value, float) and not isfinite(value):
                raise ControlMapError(
                    f"{location} property {name!r} must be finite"
                )
            converted = value
        elif isinstance(value, dict):
            unexpected = sorted(set(value) - _STRUCTURED_PROPERTY_FIELDS)
            if unexpected or len(value) != 1:
                raise ControlMapError(
                    f"{location} property {name!r} must contain exactly one "
                    "of enum or cstring"
                )
            if "enum" in value:
                enum = value["enum"]
                if not isinstance(enum, str) or not enum:
                    raise ControlMapError(
                        f"{location} property {name!r} enum must be a string"
                    )
                converted = QtEnum(enum)
            else:
                cstring = value["cstring"]
                if not isinstance(cstring, str) or not cstring:
                    raise ControlMapError(
                        f"{location} property {name!r} cstring must be a string"
                    )
                converted = QtCString(cstring)
        else:
            raise ControlMapError(
                f"{location} property {name!r} has unsupported value"
            )
        properties.append(QtProperty(name, converted))
    return tuple(properties)


def _matches_resource_id(
    pattern: Pattern[str],
    resource_id: ResourceId,
    *,
    preferred_prefix: str,
) -> bool:
    return any(
        pattern.fullmatch(candidate)
        for candidate in _resource_candidates(
            resource_id,
            preferred_prefix=preferred_prefix,
        )
    )


def _resource_candidates(
    resource_id: ResourceId,
    *,
    preferred_prefix: str,
) -> tuple[str, ...]:
    values: list[str] = []
    for symbol in sorted(
        resource_id.symbols,
        key=lambda item: int(not item.startswith(preferred_prefix)),
    ):
        if symbol not in values:
            values.append(symbol)
    if resource_id.name is not None and resource_id.name not in values:
        values.append(resource_id.name)
    if resource_id.ordinal is not None:
        values.append(f"#{resource_id.ordinal}")
    return tuple(values)


def _optional_regex(
    table: dict[str, object],
    field: str,
    location: str,
) -> Pattern[str] | None:
    if field not in table:
        return None
    return _required_regex(table, field, location)


def _required_regex(
    table: dict[str, object],
    field: str,
    location: str,
) -> Pattern[str]:
    value = _string(table, field, location)
    try:
        return re.compile(value)
    except re.error as error:
        raise ControlMapError(
            f"{location} has invalid {field}: {error}"
        ) from error


def _optional_occurrence(
    table: dict[str, object],
    location: str,
) -> int | None:
    if "occurrence" not in table:
        return None
    value = _integer(table["occurrence"], "occurrence", location)
    if value < 1:
        raise ControlMapError(f"{location} occurrence must be positive")
    return value


def _optional_identifier(
    table: dict[str, object],
    field: str,
    location: str,
) -> str | None:
    if field not in table:
        return None
    value = _string(table, field, location)
    if not _IDENTIFIER.fullmatch(value):
        raise ControlMapError(
            f"{location} {field} must be a valid Qt identifier"
        )
    return value


def _config_name(
    table: dict[str, object],
    field: str,
    location: str,
) -> str:
    value = _string(table, field, location)
    if not _CONFIG_NAME.fullmatch(value):
        raise ControlMapError(
            f"{location} {field} must use letters, digits, '.', '_' or '-'"
        )
    return value


def _string(
    table: dict[str, object],
    field: str,
    location: str,
) -> str:
    value = table.get(field)
    if not isinstance(value, str) or not value:
        raise ControlMapError(f"{location} {field} must be a non-empty string")
    return value


def _integer(value: object, field: str, location: str) -> int:
    if isinstance(value, bool):
        raise ControlMapError(f"{location} {field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError as error:
            raise ControlMapError(
                f"{location} {field} must be an integer"
            ) from error
    raise ControlMapError(f"{location} {field} must be an integer")


def _boolean(value: object, field: str, location: str) -> bool:
    if not isinstance(value, bool):
        raise ControlMapError(f"{location} {field} must be a boolean")
    return value


def _table(raw: object, location: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ControlMapError(f"{location} must be a TOML table")
    return raw


def _reject_unknown(
    table: dict[str, object],
    allowed: frozenset[str],
    location: str,
) -> None:
    unexpected = sorted(set(table) - allowed)
    if unexpected:
        raise ControlMapError(
            f"{location} has unexpected field(s): " + ", ".join(unexpected)
        )


def _reject_duplicate_names(
    values: tuple[WidgetProfile, ...],
    *,
    path: Path,
    kind: str,
) -> None:
    seen: dict[str, int] = {}
    for value in values:
        if previous := seen.get(value.name):
            raise ControlMapError(
                f"{path}: duplicate {kind} name {value.name!r} in "
                f"{kind}s #{previous} and #{value.index}"
            )
        seen[value.name] = value.index


def _validate_rule_names(
    rules: tuple[ControlMapRule, ...],
    binding_names: dict[str, int],
    path: Path,
) -> None:
    seen: dict[str, int] = {}
    for index, rule in enumerate(rules, 1):
        if previous := seen.get(rule.name):
            raise ControlMapError(
                f"{path}: duplicate rule name {rule.name!r} in rules "
                f"#{previous} and #{index}"
            )
        if rule.name in binding_names:
            raise ControlMapError(
                f"{path}: name {rule.name!r} is used by both a rule and binding"
            )
        seen[rule.name] = index


def _reject_duplicate_matchers(
    rules: tuple[ControlMapRule, ...],
    path: Path,
) -> None:
    seen: dict[tuple[object, ...], ControlMapRule] = {}
    for rule in rules:
        key = (
            rule.source_regex.pattern if rule.source_regex else None,
            rule.dialog_regex.pattern if rule.dialog_regex else None,
            rule.win_class.casefold() if rule.win_class else None,
            rule.win_class_regex.pattern if rule.win_class_regex else None,
            rule.id_regex.pattern if rule.id_regex else None,
            rule.occurrence,
            rule.style_mask,
            rule.style_value,
        )
        if previous := seen.get(key):
            raise ControlMapError(
                f"{path}: duplicate control-map matchers in "
                f"{previous.display_name!r} and {rule.display_name!r}"
            )
        seen[key] = rule


def _reject_duplicate_compound_names(
    rules: tuple[ControlCompoundRule, ...],
    path: Path,
) -> None:
    seen: dict[str, int] = {}
    for rule in rules:
        if previous := seen.get(rule.name):
            raise ControlMapError(
                f"{path}: duplicate compound name {rule.name!r} in compounds "
                f"#{previous} and #{rule.index}"
            )
        seen[rule.name] = rule.index


def _reject_duplicate_compound_matchers(
    rules: tuple[ControlCompoundRule, ...],
    path: Path,
) -> None:
    seen: dict[tuple[object, ...], ControlCompoundRule] = {}
    for rule in rules:
        key = (
            rule.source_regex.pattern if rule.source_regex else None,
            rule.dialog_regex.pattern if rule.dialog_regex else None,
            tuple(selector.identity for selector in rule.selectors),
        )
        if previous := seen.get(key):
            raise ControlMapError(
                f"{path}: duplicate exact compound matchers in "
                f"{previous.name!r} and {rule.name!r}"
            )
        seen[key] = rule

from __future__ import annotations

import re
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Pattern

from rc2ui.semantics.model import (
    CompoundAction,
    CompoundCandidate,
    CompoundKind,
    SemanticValue,
)


_REPLACE_CLASSES = frozenset({"QSpinBox", "QDoubleSpinBox"})
_PROPERTY_NAME = re.compile(r"^[A-Za-z_]\w*$")
_SECTION_FIELDS = frozenset({"rules"})
_RULE_FIELDS = frozenset(
    {
        "name",
        "kind",
        "action",
        "source_regex",
        "dialog_id",
        "primary_id",
        "member_id",
        "label_regex",
        "result",
        "properties",
        "runtime_configured",
        "priority",
    }
)


class SemanticMapError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SemanticRule:
    name: str
    kind: CompoundKind
    action: CompoundAction
    source_regex: Pattern[str] | None
    dialog_regex: Pattern[str] | None
    primary_regex: Pattern[str] | None
    member_regex: Pattern[str] | None
    label_regex: Pattern[str] | None
    result_class: str | None
    properties: tuple[tuple[str, SemanticValue], ...]
    runtime_configured: bool
    priority: int
    index: int

    @property
    def specificity(self) -> int:
        return sum(
            pattern is not None
            for pattern in (
                self.source_regex,
                self.dialog_regex,
                self.primary_regex,
                self.member_regex,
                self.label_regex,
            )
        )

    def matches(
        self,
        candidate: CompoundCandidate,
        *,
        source: str,
        dialog_ids: tuple[str, ...],
    ) -> bool:
        return (
            candidate.kind is self.kind
            and _matches_value(self.source_regex, (source,))
            and _matches_value(self.dialog_regex, dialog_ids)
            and _matches_value(self.primary_regex, candidate.primary_ids)
            and _matches_members(self.member_regex, candidate.member_ids)
            and _matches_value(self.label_regex, candidate.label_texts)
        )


@dataclass(frozen=True, slots=True)
class SemanticMap:
    rules: tuple[SemanticRule, ...]

    @classmethod
    def from_table(cls, data: object, *, path: Path) -> SemanticMap:
        if not isinstance(data, dict):
            raise SemanticMapError(f"{path}: semantics must be a TOML table")
        unexpected = sorted(set(data) - _SECTION_FIELDS)
        if unexpected:
            raise SemanticMapError(
                f"{path}: semantics has unexpected field(s): "
                + ", ".join(unexpected)
            )
        raw_rules = data.get("rules")
        if not isinstance(raw_rules, list):
            raise SemanticMapError(
                f"{path}: semantics must contain [[semantics.rules]] tables"
            )
        rules = tuple(
            _parse_rule(raw, index=index, path=path)
            for index, raw in enumerate(raw_rules, 1)
        )
        names: dict[str, int] = {}
        for rule in rules:
            if previous := names.get(rule.name):
                raise SemanticMapError(
                    f"{path}: duplicate semantic rule name {rule.name!r} "
                    f"in rules #{previous} and #{rule.index}"
                )
            names[rule.name] = rule.index
        return cls(rules)


def _parse_rule(
    raw: object,
    *,
    index: int,
    path: Path,
) -> SemanticRule:
    location = f"{path}: semantics.rules #{index}"
    if not isinstance(raw, dict):
        raise SemanticMapError(f"{location} must be a TOML table")
    unexpected = sorted(set(raw) - _RULE_FIELDS)
    if unexpected:
        raise SemanticMapError(
            f"{location} has unexpected field(s): " + ", ".join(unexpected)
        )
    name = _required_string(raw, "name", location)
    try:
        kind = CompoundKind(_required_string(raw, "kind", location))
    except ValueError as error:
        choices = ", ".join(
            item.value
            for item in CompoundKind
            if item is not CompoundKind.CONTROL_SET
        )
        raise SemanticMapError(
            f"{location} kind must be one of: {choices}"
        ) from error
    if kind is CompoundKind.CONTROL_SET:
        raise SemanticMapError(
            f"{location} kind 'control-set' is configured with "
            "[[controls.compounds]], not [[semantics.rules]]"
        )
    try:
        action = CompoundAction(_required_string(raw, "action", location))
    except ValueError as error:
        choices = ", ".join(item.value for item in CompoundAction)
        raise SemanticMapError(
            f"{location} action must be one of: {choices}"
        ) from error

    result_class = raw.get("result")
    if result_class is not None and (
        not isinstance(result_class, str) or not result_class
    ):
        raise SemanticMapError(f"{location} result must be a non-empty string")
    if action is CompoundAction.REPLACE:
        if result_class not in _REPLACE_CLASSES:
            choices = ", ".join(sorted(_REPLACE_CLASSES))
            raise SemanticMapError(
                f"{location} replace result must be one of: {choices}"
            )
    elif result_class is not None:
        raise SemanticMapError(
            f"{location} result is only valid for action = 'replace'"
        )

    properties = _properties(raw.get("properties", {}), location)
    if properties and action is not CompoundAction.REPLACE:
        raise SemanticMapError(
            f"{location} properties require action = 'replace'"
        )
    _validate_spin_properties(result_class, properties, location)
    runtime_configured = _boolean(
        raw.get("runtime_configured", False),
        "runtime_configured",
        location,
    )
    if runtime_configured and action is not CompoundAction.REPLACE:
        raise SemanticMapError(
            f"{location} runtime_configured requires action = 'replace'"
        )
    priority = _integer(raw.get("priority", 0), "priority", location)

    return SemanticRule(
        name=name,
        kind=kind,
        action=action,
        source_regex=_optional_regex(raw, "source_regex", location),
        dialog_regex=_optional_regex(raw, "dialog_id", location),
        primary_regex=_optional_regex(raw, "primary_id", location),
        member_regex=_optional_regex(raw, "member_id", location),
        label_regex=_optional_regex(raw, "label_regex", location),
        result_class=result_class,
        properties=properties,
        runtime_configured=runtime_configured,
        priority=priority,
        index=index,
    )


def _properties(
    raw: object,
    location: str,
) -> tuple[tuple[str, SemanticValue], ...]:
    if not isinstance(raw, dict):
        raise SemanticMapError(f"{location} properties must be a TOML table")
    result: list[tuple[str, SemanticValue]] = []
    for name, value in raw.items():
        if not isinstance(name, str) or not _PROPERTY_NAME.fullmatch(name):
            raise SemanticMapError(
                f"{location} has invalid Qt property name {name!r}"
            )
        if not isinstance(value, (str, bool, int, float)):
            raise SemanticMapError(
                f"{location} property {name!r} must be string, bool, int, or float"
            )
        if isinstance(value, float) and not isfinite(value):
            raise SemanticMapError(
                f"{location} property {name!r} must be finite"
            )
        result.append((name, value))
    return tuple(result)


def _validate_spin_properties(
    result_class: str | None,
    properties: tuple[tuple[str, SemanticValue], ...],
    location: str,
) -> None:
    values = dict(properties)
    numeric = {"minimum", "maximum", "singleStep", "value"}
    for name in numeric & values.keys():
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SemanticMapError(
                f"{location} property {name!r} must be numeric"
            )
        if result_class == "QSpinBox" and not isinstance(value, int):
            raise SemanticMapError(
                f"{location} QSpinBox property {name!r} must be an integer"
            )
    if "decimals" in values and (
        result_class != "QDoubleSpinBox"
        or isinstance(values["decimals"], bool)
        or not isinstance(values["decimals"], int)
    ):
        raise SemanticMapError(
            f"{location} decimals must be an integer QDoubleSpinBox property"
        )


def _optional_regex(
    raw: dict[str, object],
    key: str,
    location: str,
) -> Pattern[str] | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SemanticMapError(f"{location} {key} must be a non-empty string")
    try:
        return re.compile(value)
    except re.error as error:
        raise SemanticMapError(
            f"{location} has invalid {key}: {error}"
        ) from error


def _required_string(
    raw: dict[str, object],
    key: str,
    location: str,
) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise SemanticMapError(f"{location} {key} must be a non-empty string")
    return value


def _boolean(value: object, field: str, location: str) -> bool:
    if not isinstance(value, bool):
        raise SemanticMapError(f"{location} {field} must be a boolean")
    return value


def _integer(value: object, field: str, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SemanticMapError(f"{location} {field} must be an integer")
    return value


def _matches_value(
    pattern: Pattern[str] | None,
    values: tuple[str, ...],
) -> bool:
    return pattern is None or any(pattern.fullmatch(value) for value in values)


def _matches_members(
    pattern: Pattern[str] | None,
    members: tuple[tuple[str, ...], ...],
) -> bool:
    return pattern is None or (
        bool(members)
        and all(any(pattern.fullmatch(value) for value in ids) for ids in members)
    )

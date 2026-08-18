from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Pattern

from rc2ui.domain.resource_id import ResourceId
from rc2ui.naming.identifier import lower_camel_identifier


_TEMPLATE_REFERENCE = re.compile(r"\$\{([A-Za-z_]\w*|\d+)\}")
_UI_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SECTION_FIELDS = frozenset({"rules"})
_RULE_FIELDS = frozenset(
    {
        "name",
        "kind",
        "source_regex",
        "dialog_regex",
        "id_regex",
        "occurrence",
        "name_template",
        "priority",
        "confidence",
        "derived_from",
        "names",
    }
)
_NAME_VALUE_FIELDS = frozenset({"name", "confidence", "derived_from"})


class NamingMapError(ValueError):
    pass


class NamingKind(StrEnum):
    DIALOG = "dialog"
    CONTROL = "control"


@dataclass(frozen=True, slots=True)
class NamingRule:
    name: str
    kind: NamingKind
    id_regex: Pattern[str]
    name_template: str
    source_regex: Pattern[str] | None
    dialog_regex: Pattern[str] | None
    occurrence: int | None
    priority: int
    confidence: float | None
    derived_from: str | None
    index: int
    exact_id: str | None = None

    @property
    def key(self) -> tuple[int, str | None]:
        return self.index, self.exact_id

    @property
    def display_name(self) -> str:
        if self.exact_id is None:
            return self.name
        return f"{self.name}[{self.exact_id!r}]"

    @property
    def location_suffix(self) -> str:
        if self.exact_id is None:
            return f"rules#{self.index}"
        return f"rules#{self.index}.names[{self.exact_id!r}]"

    @property
    def specificity(self) -> int:
        return sum(
            item is not None
            for item in (
                self.source_regex,
                self.dialog_regex,
                self.occurrence,
            )
        )

    @property
    def precedence(self) -> tuple[int, int, int]:
        return (
            self.priority,
            int(self.exact_id is not None),
            self.specificity,
        )

    def match(
        self,
        *,
        source: PurePosixPath,
        dialog: ResourceId,
        kind: NamingKind,
        resource_id: ResourceId,
        occurrence: int,
    ) -> NamingMatch | None:
        if self.kind is not kind:
            return None
        if self.occurrence is not None and self.occurrence != occurrence:
            return None
        if (
            self.source_regex is not None
            and self.source_regex.fullmatch(source.as_posix()) is None
        ):
            return None
        if self.dialog_regex is not None and _first_resource_match(
            self.dialog_regex,
            dialog,
            preferred_prefix="IDD_",
        ) is None:
            return None
        id_match = _first_resource_match(
            self.id_regex,
            resource_id,
            preferred_prefix=(
                "IDD_" if kind is NamingKind.DIALOG else "IDC_"
            ),
        )
        if id_match is None:
            return None
        return NamingMatch(
            rule=self,
            object_name=_render_name(self, id_match),
            matched_id=id_match.group(0),
            captures=tuple(
                sorted(
                    (name, value or "")
                    for name, value in id_match.groupdict().items()
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class NamingMatch:
    rule: NamingRule
    object_name: str
    matched_id: str
    captures: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class NamingMap:
    rules: tuple[NamingRule, ...]

    @classmethod
    def from_table(cls, data: object, *, path: Path) -> NamingMap:
        if not isinstance(data, dict):
            raise NamingMapError(f"{path}: naming must be a TOML table")
        unexpected = sorted(set(data) - _SECTION_FIELDS)
        if unexpected:
            raise NamingMapError(
                f"{path}: naming has unexpected field(s): "
                + ", ".join(unexpected)
            )
        raw_rules = data.get("rules")
        if not isinstance(raw_rules, list):
            raise NamingMapError(
                f"{path}: naming must contain [[naming.rules]]"
            )
        rules = tuple(
            rule
            for index, raw in enumerate(raw_rules, 1)
            for rule in _parse_rules(raw, index=index, path=path)
        )
        _validate_rules(rules, path)
        return cls(rules)

    def resolve(
        self,
        *,
        source: PurePosixPath,
        dialog: ResourceId,
        kind: NamingKind,
        resource_id: ResourceId,
        occurrence: int = 1,
    ) -> NamingMatch | None:
        normalized_source = _normalize_target_source(source)
        matches = [
            match
            for rule in self.rules
            if (
                match := rule.match(
                    source=normalized_source,
                    dialog=dialog,
                    kind=kind,
                    resource_id=resource_id,
                    occurrence=occurrence,
                )
            )
            is not None
        ]
        if not matches:
            return None
        best_key = max(match.rule.precedence for match in matches)
        leaders = [
            match
            for match in matches
            if match.rule.precedence == best_key
        ]
        if len(leaders) > 1:
            names = ", ".join(
                repr(match.rule.display_name) for match in leaders
            )
            raise NamingMapError(
                f"ambiguous naming rules {names} for {kind.value} "
                f"{resource_id.display_name} in {dialog.display_name}"
            )
        return leaders[0]


def _parse_rules(
    raw: object,
    *,
    index: int,
    path: Path,
) -> tuple[NamingRule, ...]:
    location = f"{path}: naming.rules #{index}"
    if not isinstance(raw, dict):
        raise NamingMapError(f"{location} must be a TOML table")
    unexpected = sorted(set(raw) - _RULE_FIELDS)
    if unexpected:
        raise NamingMapError(
            f"{location} has unexpected field(s): " + ", ".join(unexpected)
        )
    name = _required_string(raw, "name", location)
    if not _RULE_NAME_PATTERN.fullmatch(name):
        raise NamingMapError(
            f"{location} name must use letters, digits, '.', '_' or '-'"
        )
    try:
        kind = NamingKind(_required_string(raw, "kind", location))
    except ValueError as error:
        choices = ", ".join(item.value for item in NamingKind)
        raise NamingMapError(
            f"{location} kind must be one of: {choices}"
        ) from error
    occurrence = raw.get("occurrence")
    if occurrence is not None:
        occurrence = _integer(occurrence, "occurrence", location)
        if occurrence < 1:
            raise NamingMapError(f"{location} occurrence must be positive")

    source_regex = _optional_regex(raw, "source_regex", location)
    dialog_regex = _optional_regex(raw, "dialog_regex", location)
    priority = _integer(raw.get("priority", 0), "priority", location)
    confidence = _optional_confidence(raw.get("confidence"), location)
    derived_from = _optional_derived_from(raw.get("derived_from"), location)

    raw_names = raw.get("names")
    if raw_names is not None:
        if "id_regex" in raw or "name_template" in raw:
            raise NamingMapError(
                f"{location} names cannot be combined with id_regex or "
                "name_template"
            )
        if not isinstance(raw_names, dict) or not raw_names:
            raise NamingMapError(
                f"{location} names must be a non-empty TOML table"
            )
        return tuple(
            _exact_rule(
                exact_id,
                value,
                group_name=name,
                kind=kind,
                source_regex=source_regex,
                dialog_regex=dialog_regex,
                occurrence=occurrence,
                priority=priority,
                default_confidence=confidence,
                default_derived_from=derived_from,
                index=index,
                location=location,
            )
            for exact_id, value in raw_names.items()
        )

    id_regex = _required_regex(raw, "id_regex", location)
    name_template = _required_string(raw, "name_template", location)
    _validate_template(name_template, id_regex=id_regex, location=location)
    return (
        NamingRule(
            name=name,
            kind=kind,
            id_regex=id_regex,
            name_template=name_template,
            source_regex=source_regex,
            dialog_regex=dialog_regex,
            occurrence=occurrence,
            priority=priority,
            confidence=confidence,
            derived_from=derived_from,
            index=index,
        ),
    )


def _exact_rule(
    exact_id: object,
    raw_value: object,
    *,
    group_name: str,
    kind: NamingKind,
    source_regex: Pattern[str] | None,
    dialog_regex: Pattern[str] | None,
    occurrence: int | None,
    priority: int,
    default_confidence: float | None,
    default_derived_from: str | None,
    index: int,
    location: str,
) -> NamingRule:
    if not isinstance(exact_id, str) or not exact_id:
        raise NamingMapError(f"{location} names keys must be non-empty strings")
    entry_location = f"{location} names[{exact_id!r}]"
    if isinstance(raw_value, str):
        name_template = raw_value
        confidence = default_confidence
        derived_from = default_derived_from
    elif isinstance(raw_value, dict):
        unexpected = sorted(set(raw_value) - _NAME_VALUE_FIELDS)
        if unexpected:
            raise NamingMapError(
                f"{entry_location} has unexpected field(s): "
                + ", ".join(unexpected)
            )
        name_template = _required_string(raw_value, "name", entry_location)
        confidence = _optional_confidence(
            raw_value.get("confidence", default_confidence),
            entry_location,
        )
        derived_from = _optional_derived_from(
            raw_value.get("derived_from", default_derived_from),
            entry_location,
        )
    else:
        raise NamingMapError(
            f"{entry_location} must be a string or inline table"
        )
    id_regex = re.compile(re.escape(exact_id))
    _validate_template(
        name_template,
        id_regex=id_regex,
        location=entry_location,
    )
    return NamingRule(
        name=group_name,
        kind=kind,
        id_regex=id_regex,
        name_template=name_template,
        source_regex=source_regex,
        dialog_regex=dialog_regex,
        occurrence=occurrence,
        priority=priority,
        confidence=confidence,
        derived_from=derived_from,
        index=index,
        exact_id=exact_id,
    )


def _optional_confidence(value: object, location: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NamingMapError(f"{location} confidence must be numeric")
    confidence = float(value)
    if not 0 <= confidence <= 1:
        raise NamingMapError(
            f"{location} confidence must be between zero and one"
        )
    return confidence


def _optional_derived_from(value: object, location: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise NamingMapError(
            f"{location} derived_from must be a non-empty string"
        )
    return value


def _required_string(
    raw: dict[str, object],
    key: str,
    location: str,
) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise NamingMapError(f"{location} {key} must be a non-empty string")
    return value


def _required_regex(
    raw: dict[str, object],
    key: str,
    location: str,
) -> Pattern[str]:
    value = _required_string(raw, key, location)
    return _compile_regex(value, field=key, location=location)


def _optional_regex(
    raw: dict[str, object],
    key: str,
    location: str,
) -> Pattern[str] | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise NamingMapError(f"{location} {key} must be a non-empty string")
    return _compile_regex(value, field=key, location=location)


def _compile_regex(
    value: str,
    *,
    field: str,
    location: str,
) -> Pattern[str]:
    try:
        return re.compile(value)
    except re.error as error:
        raise NamingMapError(
            f"{location} has invalid {field}: {error}"
        ) from error


def _integer(value: object, field: str, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NamingMapError(f"{location} {field} must be an integer")
    return value


def _validate_template(
    value: str,
    *,
    id_regex: Pattern[str],
    location: str,
) -> None:
    references = tuple(_TEMPLATE_REFERENCE.finditer(value))
    remainder = _TEMPLATE_REFERENCE.sub("", value)
    if "${" in remainder:
        raise NamingMapError(
            f"{location} has malformed reference in name_template {value!r}"
        )
    for reference in references:
        group = reference.group(1)
        if group.isdigit():
            if int(group) > id_regex.groups:
                raise NamingMapError(
                    f"{location} name_template references missing group {group}"
                )
        elif group not in id_regex.groupindex:
            raise NamingMapError(
                f"{location} name_template references missing group {group!r}"
            )
    sample = _TEMPLATE_REFERENCE.sub("capture", value)
    if lower_camel_identifier(sample) is None:
        raise NamingMapError(
            f"{location} name_template cannot produce a Qt object name"
        )


def _render_name(rule: NamingRule, match: re.Match[str]) -> str:
    def replace_reference(reference: re.Match[str]) -> str:
        group = reference.group(1)
        value = match.group(int(group) if group.isdigit() else group)
        return value or ""

    rendered = _TEMPLATE_REFERENCE.sub(replace_reference, rule.name_template)
    object_name = lower_camel_identifier(rendered)
    if object_name is None or not _UI_NAME_PATTERN.fullmatch(object_name):
        raise NamingMapError(
            f"naming rule {rule.name!r} produced invalid Qt object name "
            f"from {rendered!r}"
        )
    return object_name


def _first_resource_match(
    pattern: Pattern[str],
    resource_id: ResourceId,
    *,
    preferred_prefix: str,
) -> re.Match[str] | None:
    for candidate in _resource_candidates(
        resource_id,
        preferred_prefix=preferred_prefix,
    ):
        if match := pattern.fullmatch(candidate):
            return match
    return None


def _resource_candidates(
    resource_id: ResourceId,
    *,
    preferred_prefix: str,
) -> tuple[str, ...]:
    values: list[str] = []
    symbols = sorted(
        resource_id.symbols,
        key=lambda symbol: int(not symbol.startswith(preferred_prefix)),
    )
    for symbol in symbols:
        if symbol not in values:
            values.append(symbol)
    if resource_id.name is not None and resource_id.name not in values:
        values.append(resource_id.name)
    if resource_id.ordinal is not None:
        values.append(f"#{resource_id.ordinal}")
    return tuple(values)


def _normalize_target_source(source: PurePosixPath) -> PurePosixPath:
    normalized = PurePosixPath(str(source).replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError("source must be relative to the project root")
    return normalized


def _validate_rules(rules: tuple[NamingRule, ...], path: Path) -> None:
    names: dict[str, int] = {}
    matchers: dict[tuple[object, ...], NamingRule] = {}
    for rule in rules:
        if (previous := names.get(rule.name)) is not None and (
            previous != rule.index
        ):
            raise NamingMapError(
                f"{path}: duplicate naming rule name {rule.name!r} in "
                f"naming.rules #{previous} and #{rule.index}"
            )
        names[rule.name] = rule.index
        matcher = (
            rule.source_regex.pattern if rule.source_regex else None,
            rule.dialog_regex.pattern if rule.dialog_regex else None,
            rule.kind,
            rule.id_regex.pattern,
            rule.occurrence,
        )
        if previous_rule := matchers.get(matcher):
            raise NamingMapError(
                f"{path}: duplicate naming matchers in rules "
                f"{previous_rule.name!r} and {rule.name!r}"
            )
        matchers[matcher] = rule

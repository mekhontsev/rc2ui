from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rc2ui.adapters.headers.symbols import PreprocessedLine, SymbolTable
from rc2ui.adapters.rc.windows_languages import PRIMARY_LANGUAGES, SUBLANGUAGES
from rc2ui.domain.resource_id import ResourceId


_DIALOG_DECLARATION = re.compile(
    r'^\s*(?P<identifier>"(?:[^"]|"")*"|[A-Za-z_]\w*|0[xX][0-9A-Fa-f]+|\d+)'
    r"\s+(?:DIALOGEX|DIALOG)\b",
    flags=re.IGNORECASE,
)
_LANGUAGE_DECLARATION = re.compile(
    r"^\s*LANGUAGE\s+(?P<primary>[^,]+)\s*,\s*(?P<sublanguage>.+?)\s*$",
    flags=re.IGNORECASE,
)
_RC_INCLUDE_SUFFIXES = frozenset({".rc", ".rc2", ".dlg"})


@dataclass(frozen=True, slots=True)
class DialogDeclaration:
    resource_id: ResourceId | None
    token: str
    source: Path
    line: int
    language: int | None = None


def find_dialog_declarations(
    active_lines: tuple[PreprocessedLine, ...],
    symbols: SymbolTable,
) -> tuple[DialogDeclaration, ...]:
    """Index dialog declarations owned by one top-level RC source.

    ``SymbolLoader`` supplies only active lines and has already followed local
    includes. Header symbols are resolved but header text does not itself
    establish dialog ownership.
    """

    declarations: list[DialogDeclaration] = []
    language: int | None = None
    for active_line in active_lines:
        if active_line.source.suffix.casefold() not in _RC_INCLUDE_SUFFIXES:
            continue
        language_match = _LANGUAGE_DECLARATION.match(active_line.text)
        if language_match:
            language = resolve_language_id(
                language_match.group("primary"),
                language_match.group("sublanguage"),
                symbols,
            )
            continue
        declaration = _DIALOG_DECLARATION.match(active_line.text)
        if not declaration:
            continue
        token = declaration.group("identifier")
        declarations.append(
            DialogDeclaration(
                resource_id=resolve_resource_id(token, symbols),
                token=token,
                source=active_line.source,
                line=active_line.line,
                language=language,
            )
        )
    return tuple(declarations)


def resolve_resource_id(token: str, symbols: SymbolTable) -> ResourceId | None:
    """Resolve an RC resource token without requiring a full RC parser."""

    if token.startswith('"'):
        return ResourceId.from_name(token[1:-1].replace('""', '"'))
    if token[0].isdigit():
        base = 16 if token.lower().startswith("0x") else 10
        return ResourceId.from_ordinal(int(token, base))
    value = symbols.value_of(token)
    if value is None:
        # RC also permits an unquoted identifier as a named resource. A macro
        # definition, when present, takes precedence and makes it ordinal.
        return ResourceId.from_name(token)
    return ResourceId.from_ordinal(value, token)


def resolve_language_id(
    primary_token: str,
    sublanguage_token: str,
    symbols: SymbolTable,
) -> int | None:
    """Resolve a ``LANGUAGE primary, sublanguage`` declaration when possible."""

    primary = _language_component(
        primary_token,
        symbols,
        builtins=PRIMARY_LANGUAGES,
    )
    sublanguage = _language_component(
        sublanguage_token,
        symbols,
        builtins=SUBLANGUAGES,
    )
    if primary is None or sublanguage is None:
        return None
    return primary | (sublanguage << 10)


def _language_component(
    token: str,
    symbols: SymbolTable,
    *,
    builtins: dict[str, int],
) -> int | None:
    normalized = token.strip()
    if normalized.upper() in builtins:
        return builtins[normalized.upper()]
    value = symbols.value_of(normalized)
    if value is not None:
        return value
    try:
        return int(normalized, 0)
    except ValueError:
        return None

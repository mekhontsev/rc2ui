from __future__ import annotations

import hashlib
import json
import locale
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from rc2ui.adapters.headers.symbols import PreprocessedLine, SymbolLoader, SymbolTable
from rc2ui.adapters.rc.dialog_declarations import (
    resolve_language_id,
    resolve_resource_id,
)
from rc2ui.corpus.discovery import discover_corpus
from rc2ui.corpus.model import CorpusCase, CorpusCaseKind
from rc2ui.domain.resource_id import ResourceId


_DIALOG = re.compile(
    r'^\s*(?P<identifier>"(?:[^"]|"")*"|[A-Za-z_]\w*|0[xX][0-9A-Fa-f]+|\d+)'
    r"\s+(?:DIALOGEX|DIALOG)\b",
    flags=re.IGNORECASE,
)
_LANGUAGE = re.compile(
    r"^\s*LANGUAGE\s+(?P<primary>[^,]+)\s*,\s*(?P<sub>.+?)\s*$",
    flags=re.IGNORECASE,
)
_STRING_LITERAL_SOURCE = r'"(?:\\.|[^"\r\n])*"'
_QUOTED = re.compile(_STRING_LITERAL_SOURCE)
_NUMBER = re.compile(r"(?<![A-Za-z_])(?:0[xX][0-9A-Fa-f]+|\d+)(?![A-Za-z_])")
_IDENTIFIER = re.compile(r"\b[A-Za-z_]\w*\b")
_PROJECT_IDENTIFIER = re.compile(r"^ID[A-Z0-9_]*$", flags=re.IGNORECASE)
_MACRO_IDENTIFIER = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_STRING_PROPERTY = re.compile(
    r"^(?P<prefix>\s*(?:CAPTION|CLASS)\s+)(?P<value>[A-Za-z_]\w*)(?P<suffix>\s*)$",
    flags=re.IGNORECASE,
)
_FONT_PROPERTY = re.compile(
    r"^(?P<prefix>\s*FONT\s+[^,]+,\s*)(?P<value>[A-Za-z_]\w*)(?P<suffix>.*)$",
    flags=re.IGNORECASE,
)
_TEXT_CONTROL = re.compile(
    r"^(?P<prefix>\s*(?:LTEXT|CTEXT|RTEXT|PUSHBUTTON|DEFPUSHBUTTON|"
    r"GROUPBOX|CHECKBOX|AUTOCHECKBOX|RADIOBUTTON|AUTORADIOBUTTON|"
    r"STATE3|AUTO3STATE|CONTROL)\s+)(?P<value>[A-Za-z_]\w*)"
    r"(?P<suffix>\s*,.*)$",
    flags=re.IGNORECASE,
)
_BROKEN_QUOTED_CONTROL = re.compile(
    r'^(?P<prefix>\s*(?:LTEXT|CTEXT|RTEXT|PUSHBUTTON|DEFPUSHBUTTON|'
    r'GROUPBOX|CHECKBOX|AUTOCHECKBOX|RADIOBUTTON|AUTORADIOBUTTON|'
    r'STATE3|AUTO3STATE|CONTROL)\s+)'
    r'(?P<value>[^",]*\s+[^",]*)"(?P<suffix>\s*,.*)$',
    flags=re.IGNORECASE,
)
_CONTROL_CLASS = re.compile(
    rf"(?P<prefix>\bCONTROL\s+(?:{_STRING_LITERAL_SOURCE}|[A-Za-z_]\w*)\s*,\s*"
    rf"(?:[A-Za-z_]\w*|-?\d+)\s*,\s*)(?P<value>[A-Za-z_]\w*)"
    r"(?P<suffix>\s*,)",
    flags=re.IGNORECASE,
)
_CONTROL_WITHOUT_ID_COMMA = re.compile(
    rf"(?m)^(?P<prefix>\s*CONTROL\s+(?:{_STRING_LITERAL_SOURCE}|[A-Za-z_]\w*)"
    rf"\s*,\s*(?:[A-Za-z_]\w*|-?\d+))\s+"
    rf"(?=(?:{_STRING_LITERAL_SOURCE}|[A-Za-z_]\w*)\s*,)"
)
_CONTROL_DATA = re.compile(
    r"(?m)^(?P<control>\s*CONTROL\b.*?)\s+\{[^{}]*\}\s*$",
    flags=re.IGNORECASE,
)
_ICON_SHORTHAND = re.compile(
    rf"(?m)^(?P<control>\s*ICON\s+(?:{_STRING_LITERAL_SOURCE}|[A-Za-z_]\w*|"
    r"-?\d+)\s*,\s*(?:[A-Za-z_]\w*|-?\d+)\s*,\s*-?\d+\s*,\s*-?\d+)\s*$",
    flags=re.IGNORECASE,
)
_DIALOG_COORDINATES = re.compile(
    r"(?m)^(?P<prefix>\s*(?:\"[^\"]+\"|[A-Za-z_]\w*|0[xX][0-9A-Fa-f]+|\d+)"
    r"\s+DIALOG(?:EX)?\b(?:\s+[A-Za-z_]+)*\s+)"
    r"(?P<x>-?\d+)\s*,\s*(?P<y>-?\d+)\s*,\s*"
    r"(?P<w>-?\d+)\s*,\s*(?P<h>-?\d+)",
    flags=re.IGNORECASE,
)
_LOCALE = re.compile(r"(?<![A-Za-z])([a-z]{2,3})[-_]([a-z]{2})(?![A-Za-z])", re.I)
_LANGUAGE_DIRS = frozenset(
    {"lang", "langs", "language", "languages", "locale", "locales", "mui"}
)
_ENGLISH_US = 1033
_UTF8_CODEPAGE = 65001
_FALLBACK_NUMERIC_MACROS = {
    # ReactOS rapps uses this project-local aggregate without defining it in
    # the language fragments.  Zero preserves geometry, which is what the
    # source corpus exercises, without guessing a visual list-view feature.
    "LVCHECKSTYLES": 0,
}
_CONTROL_CLASS_NAMES = {
    "PROGRESS_CLASS": "msctls_progress32",
    "PROGRESS_CLASSA": "msctls_progress32",
    "PROGRESS_CLASSW": "msctls_progress32",
    "TOOLBARCLASSNAME": "ToolbarWindow32",
    "TOOLBARCLASSNAMEA": "ToolbarWindow32",
    "TOOLBARCLASSNAMEW": "ToolbarWindow32",
    "TRACKBAR_CLASS": "msctls_trackbar32",
    "TRACKBAR_CLASSA": "msctls_trackbar32",
    "TRACKBAR_CLASSW": "msctls_trackbar32",
    "UPDOWN_CLASS": "msctls_updown32",
    "UPDOWN_CLASSA": "msctls_updown32",
    "UPDOWN_CLASSW": "msctls_updown32",
    "WC_LINK": "SysLink",
}
_RC_KEYWORDS = frozenset(
    {
        "ACCELERATORS", "AUTO3STATE", "AUTOCHECKBOX", "AUTORADIOBUTTON", "BEGIN",
        "BITMAP", "BLOCK", "CAPTION", "CHARACTERISTICS", "CHECKBOX", "CLASS",
        "COMBOBOX", "CONTROL", "CTEXT", "CURSOR", "DEFPUSHBUTTON", "DIALOG",
        "DIALOGEX", "DISCARDABLE", "DLGINCLUDE", "EDITTEXT", "END", "EXSTYLE",
        "FIXED", "FONT", "GROUPBOX", "HELP", "HTML", "ICON", "IMPURE",
        "LANGUAGE", "LISTBOX", "LOADONCALL", "LTEXT", "MENU", "MENUITEM",
        "MENUEX", "MESSAGETABLE", "MOVEABLE", "NONDISCARDABLE", "NOT", "POPUP",
        "PRELOAD", "PURE", "PUSHBUTTON", "RADIOBUTTON", "RCDATA", "RTEXT",
        "SCROLLBAR", "SEPARATOR", "SHARED", "STATE3", "STRINGTABLE", "STYLE",
        "USERBUTTON", "VALUE", "VERSION", "VERSIONINFO", "VXD",
    }
)
_STYLE_PREFIXES = (
    "BS_", "CBS_", "CCS_", "DS_", "ES_", "LBS_", "LVS_", "PBS_", "SS_",
    "TBS_", "UDS_", "WS_",
)


@dataclass(frozen=True, slots=True)
class SourceDialogVariant:
    project_root: Path
    source: Path
    relative_source: PurePosixPath
    line: int
    token: str
    resource_id: ResourceId
    language: int | None
    language_expression: str | None
    block: str
    definitions: tuple[tuple[str, int], ...]
    scope: PurePosixPath
    skeleton: str

    @property
    def language_key(self) -> tuple[str, int | str]:
        if self.language is not None:
            return "langid", self.language
        if self.language_expression:
            return "expression", self.language_expression.casefold()
        return "neutral", 0


@dataclass(frozen=True, slots=True)
class ExtractedDialogCase:
    source: Path
    preferred_language: int
    variants: tuple[SourceDialogVariant, ...]


@dataclass(frozen=True, slots=True)
class SourceExtractionResult:
    output_dir: Path
    cases: tuple[ExtractedDialogCase, ...]
    source_files: int
    declared_dialogs: int
    extracted_variants: int
    exact_duplicates: int
    malformed_dialogs: int
    preprocessor_warnings: int
    report_path: Path
    markdown_path: Path


def extract_source_corpus(
    roots: Iterable[Path],
    output_dir: Path,
    *,
    fallback_encoding: str = "cp1251",
    on_source: Callable[[int, int, CorpusCase, int], None] | None = None,
) -> SourceExtractionResult:
    """Materialize direct RC dialog blocks as independently compilable cases.

    Every physical resource script is visited once.  Included language files
    therefore contribute variants without being compiled again through each
    parent RC.  Variants are grouped only inside one project/module/resource
    namespace and are kept together when their structural skeleton matches.
    """

    output = output_dir.resolve()
    _prepare_output(output)
    discovered = discover_corpus(roots, fallback_encoding=fallback_encoding)
    candidates = tuple(item for item in discovered if item.direct_dialogs)
    variants: list[SourceDialogVariant] = []
    malformed = 0
    preprocessor_warnings = 0
    for index, case in enumerate(candidates, 1):
        extracted, warnings = _extract_case(case, fallback_encoding)
        variants.extend(extracted)
        malformed += max(0, case.direct_dialogs - len(extracted))
        preprocessor_warnings += warnings
        if on_source is not None:
            on_source(index, len(candidates), case, len(extracted))

    families, duplicates = _families(variants)
    materialized: list[ExtractedDialogCase] = []
    for family in families:
        path = _case_path(output, family)
        preferred = _preferred_language(family)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _render_case(family, preferred),
            encoding="utf-8",
            newline="\n",
        )
        materialized.append(ExtractedDialogCase(path, preferred, family))

    materialized.sort(key=lambda item: item.source.as_posix().casefold())
    report_path = output / "source-corpus.json"
    markdown_path = output / "source-corpus.md"
    payload = _report_payload(
        output,
        discovered,
        tuple(materialized),
        declared_dialogs=sum(item.direct_dialogs for item in discovered),
        extracted_variants=len(variants),
        exact_duplicates=duplicates,
        malformed_dialogs=malformed,
        preprocessor_warnings=preprocessor_warnings,
    )
    _write_json(report_path, payload)
    _write_markdown(markdown_path, payload)
    return SourceExtractionResult(
        output_dir=output,
        cases=tuple(materialized),
        source_files=len(discovered),
        declared_dialogs=sum(item.direct_dialogs for item in discovered),
        extracted_variants=len(variants),
        exact_duplicates=duplicates,
        malformed_dialogs=malformed,
        preprocessor_warnings=preprocessor_warnings,
        report_path=report_path,
        markdown_path=markdown_path,
    )


def _extract_case(
    case: CorpusCase,
    fallback_encoding: str,
) -> tuple[tuple[SourceDialogVariant, ...], int]:
    include_paths = _ancestor_include_paths(case.source, case.project_root)
    loaded = SymbolLoader(
        include_paths=include_paths,
        source_encoding=fallback_encoding,
    ).load(case.source)
    source = case.source.resolve()
    lines = tuple(line for line in loaded.active_lines if line.source == source)
    result: list[SourceDialogVariant] = []
    language: int | None = _language_from_path(case.relative_source)
    language_expression: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        language_match = _LANGUAGE.match(line.text)
        if language_match:
            primary = language_match.group("primary").strip()
            sub = language_match.group("sub").strip()
            language_expression = f"{primary}, {sub}"
            language = resolve_language_id(primary, sub, loaded.table)
            if language is None:
                language = _language_from_path(case.relative_source)
            index += 1
            continue
        declaration = _DIALOG.match(line.text)
        if declaration is None:
            index += 1
            continue
        end = _dialog_end(lines, index)
        if end is None:
            index += 1
            continue
        block = _normalize_source_block(
            "\n".join(item.text for item in lines[index : end + 1])
        )
        token = declaration.group("identifier")
        resource_id = resolve_resource_id(token, loaded.table)
        result.append(
            SourceDialogVariant(
                project_root=case.project_root,
                source=case.source,
                relative_source=case.relative_source,
                line=line.line,
                token=token,
                resource_id=resource_id,
                language=language,
                language_expression=language_expression,
                block=block,
                definitions=_referenced_definitions(block, loaded.table),
                scope=_source_scope(case),
                skeleton=_skeleton(block),
            )
        )
        index = end + 1
    warnings = sum(item.severity.value == "warning" for item in loaded.diagnostics)
    return tuple(result), warnings


def _dialog_end(lines: tuple[PreprocessedLine, ...], start: int) -> int | None:
    depth = 0
    opened = False
    for index in range(start, len(lines)):
        for token in _structural_tokens(lines[index].text):
            if token in {"begin", "{"}:
                opened = True
                depth += 1
            elif token in {"end", "}"} and opened:
                depth -= 1
                if depth == 0:
                    return index
    return None


def _structural_tokens(text: str) -> tuple[str, ...]:
    without_strings = _QUOTED.sub("", text)
    return tuple(
        match.group(0).casefold()
        for match in re.finditer(r"\bBEGIN\b|\bEND\b|[{}]", without_strings, re.I)
    )


def _families(
    variants: list[SourceDialogVariant],
) -> tuple[tuple[tuple[SourceDialogVariant, ...], ...], int]:
    grouped: dict[
        tuple[str, str, tuple[int | None, str | None], str],
        list[SourceDialogVariant],
    ] = defaultdict(list)
    for variant in variants:
        grouped[
            (
                str(variant.project_root.resolve()),
                variant.scope.as_posix().casefold(),
                _resource_key(variant.resource_id),
                variant.skeleton,
            )
        ].append(variant)

    result: list[tuple[SourceDialogVariant, ...]] = []
    duplicates = 0
    for _, candidates in sorted(grouped.items(), key=lambda item: str(item[0])):
        by_language: dict[tuple[str, int | str], list[SourceDialogVariant]] = defaultdict(list)
        fingerprints: set[tuple[tuple[str, int | str], str]] = set()
        for variant in sorted(candidates, key=_variant_sort_key):
            fingerprint = (
                variant.language_key,
                hashlib.sha256(_normalized_block(variant.block).encode()).hexdigest(),
            )
            if fingerprint in fingerprints:
                duplicates += 1
                continue
            fingerprints.add(fingerprint)
            by_language[variant.language_key].append(variant)
        width = max((len(items) for items in by_language.values()), default=0)
        for family_index in range(width):
            family = tuple(
                items[family_index]
                for _, items in sorted(by_language.items(), key=lambda item: str(item[0]))
                if family_index < len(items)
            )
            if family:
                result.append(tuple(sorted(family, key=_variant_sort_key)))
    result.sort(key=lambda family: _variant_sort_key(family[0]))
    return tuple(result), duplicates


def _render_case(
    variants: tuple[SourceDialogVariant, ...],
    preferred_language: int,
) -> str:
    definitions = _merged_definitions(variants)
    synthetic = _synthetic_definitions(variants, definitions)
    origins = ", ".join(
        f"{item.relative_source.as_posix()}:{item.line}" for item in variants
    )
    lines = [
        "// Generated by rc2ui corpus extract; do not edit.",
        f"// rc2ui-preferred-language: {preferred_language}",
        f"// rc2ui-compiler-codepage: {_UTF8_CODEPAGE}",
        f"// origins: {origins}",
        "#include <windows.h>",
        "#include <commctrl.h>",
        "#include <richedit.h>",
        "",
    ]
    for name, value in sorted({**definitions, **synthetic}.items()):
        lines.extend((f"#ifndef {name}", f"#define {name} {value}", "#endif"))
    if definitions or synthetic:
        lines.append("")
    known_names = set(definitions) | set(synthetic)
    for variant in variants:
        language_line = _render_language(variant)
        if language_line:
            lines.append(language_line)
        rendered = _sanitize_unresolved_strings(variant.block, known_names)
        # Wine's sources use C-style escaped quotes while Windows RC compilers
        # require the native doubled-quote spelling inside string literals.
        rendered = rendered.replace(r'\"', '""')
        lines.extend((rendered, ""))
    return "\n".join(lines).rstrip() + "\n"


def _render_language(variant: SourceDialogVariant) -> str | None:
    if variant.language is not None:
        return f"LANGUAGE {variant.language & 0x3ff}, {variant.language >> 10}"
    if variant.language_expression:
        # A few projects introduce custom LANG_* constants only through their
        # build system.  Keep the variant compilable as neutral when that
        # expression could not be resolved; the original expression remains
        # available in source-corpus.json for auditability.
        return "LANGUAGE 0, 0"
    return None


def _normalize_source_block(block: str) -> str:
    """Normalize common RC dialect extensions without changing geometry."""

    result: list[str] = []
    for line in block.splitlines():
        match = _BROKEN_QUOTED_CONTROL.match(line)
        if match:
            line = (
                f'{match.group("prefix")}"{match.group("value")}"'
                f'{match.group("suffix")}'
            )
        result.append(line)
    block = "\n".join(result)
    block = _merge_adjacent_string_literals(block)
    block = _CONTROL_WITHOUT_ID_COMMA.sub(r"\g<prefix>, ", block)
    block = _repair_missing_control_text_commas(block)
    block = _CONTROL_DATA.sub(r"\g<control>", block)
    block = _ICON_SHORTHAND.sub(r"\g<control>, 0, 0", block)
    return _DIALOG_COORDINATES.sub(_signed_dialog_coordinates, block)


def _merge_adjacent_string_literals(text: str) -> str:
    """Join C/Wine-style adjacent literals without confusing empty strings."""

    result: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != '"':
            result.append(text[index])
            index += 1
            continue
        end = _string_literal_end(text, index)
        if end is None:
            result.append(text[index:])
            break
        literal = text[index : end + 1]
        cursor = end + 1
        while True:
            separator = cursor
            while separator < len(text) and text[separator].isspace():
                separator += 1
            if separator >= len(text) or text[separator] != '"':
                break
            adjacent_end = _string_literal_end(text, separator)
            if adjacent_end is None:
                break
            literal = literal[:-1] + text[separator + 1 : adjacent_end + 1]
            cursor = adjacent_end + 1
        result.append(literal)
        if cursor == end + 1:
            index = cursor
        else:
            index = cursor
    return "".join(result)


def _string_literal_end(text: str, start: int) -> int | None:
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return index
        if text[index] in "\r\n":
            return None
        index += 1
    return None


def _repair_missing_control_text_commas(block: str) -> str:
    control_names = (
        "LTEXT|CTEXT|RTEXT|PUSHBUTTON|DEFPUSHBUTTON|GROUPBOX|CHECKBOX|"
        "AUTOCHECKBOX|RADIOBUTTON|AUTORADIOBUTTON|STATE3|AUTO3STATE|CONTROL"
        "|ICON"
    )
    inline = re.compile(
        rf"(?m)(?P<caption>^\s*(?:{control_names})\s+{_STRING_LITERAL_SOURCE})"
        r"\s*(?=(?:[A-Za-z_]\w*|-?\d+)\s*,)",
        flags=re.IGNORECASE,
    )
    block = inline.sub(r"\g<caption>, ", block)
    pattern = re.compile(
        rf"(?m)^(?P<caption>\s*(?:{control_names})\s+{_STRING_LITERAL_SOURCE})\s*$"
        r"(?=\n\s*(?:[A-Za-z_]\w*|-?\d+)\s*,)",
        flags=re.IGNORECASE,
    )
    return pattern.sub(r"\g<caption>,", block)


def _signed_dialog_coordinates(match: re.Match[str]) -> str:
    values = []
    for name in ("x", "y", "w", "h"):
        value = int(match.group(name))
        values.append(value - 65_536 if 32_767 < value <= 65_535 else value)
    return f'{match.group("prefix")}{values[0]}, {values[1]}, {values[2]}, {values[3]}'


def _sanitize_unresolved_strings(block: str, known_names: set[str]) -> str:
    """Keep geometry testable when a build-generated text macro is absent."""

    def control_class(match: re.Match[str]) -> str:
        value = match.group("value")
        if value in known_names:
            return match.group(0)
        class_name = _CONTROL_CLASS_NAMES.get(value.upper(), value)
        return f'{match.group("prefix")}"{class_name}"{match.group("suffix")}'

    block = _CONTROL_CLASS.sub(control_class, block)
    result: list[str] = []
    for line in block.splitlines():
        for pattern in (_STRING_PROPERTY, _FONT_PROPERTY, _TEXT_CONTROL):
            match = pattern.match(line)
            if match and match.group("value") not in known_names:
                value = match.group("value")
                line = (
                    f'{match.group("prefix")}"{value}"'
                    f'{match.group("suffix")}'
                )
                break
        result.append(line)
    return "\n".join(result)


def _merged_definitions(
    variants: tuple[SourceDialogVariant, ...],
) -> dict[str, int]:
    values: dict[str, int] = {}
    conflicts: set[str] = set()
    for variant in variants:
        for name, value in variant.definitions:
            if name in values and values[name] != value:
                conflicts.add(name)
            else:
                values[name] = value
    for name in conflicts:
        values.pop(name, None)
    return values


def _synthetic_definitions(
    variants: tuple[SourceDialogVariant, ...],
    definitions: dict[str, int],
) -> dict[str, int]:
    names: set[str] = set()
    for variant in variants:
        identifiers = _identifiers_outside_strings(variant.block)
        string_operands = _string_operand_names(variant.block)
        names.update(
            name
            for name in identifiers
            if (_PROJECT_IDENTIFIER.fullmatch(name) or _MACRO_IDENTIFIER.fullmatch(name))
            and name.upper() not in _RC_KEYWORDS
            and name not in string_operands
            and name not in definitions
        )
        if "CW_USEDEFAULT16" in identifiers and "CW_USEDEFAULT16" not in definitions:
            names.add("CW_USEDEFAULT16")
    result: dict[str, int] = {}
    used = set(definitions.values())
    if "IDC_STATIC" in names:
        result["IDC_STATIC"] = -1
        used.add(-1)
        names.remove("IDC_STATIC")
    if "CW_USEDEFAULT16" in names:
        result["CW_USEDEFAULT16"] = -32_768
        used.add(-32_768)
        names.remove("CW_USEDEFAULT16")
    for name, value in _FALLBACK_NUMERIC_MACROS.items():
        if any(name in _identifiers_outside_strings(item.block) for item in variants):
            result[name] = value
            used.add(value)
            names.discard(name)
    for name in sorted(names):
        if name.upper().startswith(_STYLE_PREFIXES):
            result[name] = 0
            continue
        candidate = 10_000 + int(
            hashlib.sha256(name.casefold().encode()).hexdigest()[:8], 16
        ) % 50_000
        while candidate in used:
            candidate = 10_000 + ((candidate - 9_999) % 50_000)
        result[name] = candidate
        used.add(candidate)
    return result


def _string_operand_names(block: str) -> set[str]:
    result: set[str] = set()
    for line in block.splitlines():
        for pattern in (_STRING_PROPERTY, _FONT_PROPERTY, _TEXT_CONTROL):
            match = pattern.match(line)
            if match:
                result.add(match.group("value"))
                break
    result.update(match.group("value") for match in _CONTROL_CLASS.finditer(block))
    return result


def _referenced_definitions(
    block: str,
    symbols: SymbolTable,
) -> tuple[tuple[str, int], ...]:
    result = []
    for name in sorted(set(_identifiers_outside_strings(block))):
        value = symbols.value_of(name)
        if value is not None:
            result.append((name, value))
    return tuple(result)


def _identifiers_outside_strings(text: str) -> tuple[str, ...]:
    return tuple(_IDENTIFIER.findall(_QUOTED.sub("", text)))


def _source_scope(case: CorpusCase) -> PurePosixPath:
    relative = case.relative_source
    if case.kind is CorpusCaseKind.LANGUAGE_FRAGMENT:
        parts = relative.parts[:-1]
        for index, part in enumerate(parts):
            if part.casefold() in _LANGUAGE_DIRS:
                retained = parts[:index]
                return PurePosixPath(*retained) if retained else PurePosixPath("root")
        return relative.parent if relative.parent.parts else PurePosixPath("root")
    return relative.with_suffix("")


def _language_from_path(relative: PurePosixPath) -> int | None:
    reverse: dict[str, int] = {}
    for key, value in locale.windows_locale.items():
        normalized = value.replace("-", "_").casefold()
        reverse[normalized] = min(key, reverse.get(normalized, key))
    for part in reversed(relative.parts):
        match = _LOCALE.search(part)
        if not match:
            continue
        locale_name = f"{match.group(1)}_{match.group(2)}".casefold()
        if locale_name in reverse:
            return reverse[locale_name]
    return None


def _preferred_language(variants: tuple[SourceDialogVariant, ...]) -> int:
    languages = sorted({item.language for item in variants if item.language is not None})
    if _ENGLISH_US in languages:
        return _ENGLISH_US
    return languages[0] if languages else 0


def _resource_key(resource_id: ResourceId) -> tuple[int | None, str | None]:
    return resource_id.ordinal, resource_id.name.casefold() if resource_id.name else None


def _skeleton(block: str) -> str:
    lines = block.splitlines()
    if lines:
        lines[0] = _DIALOG.sub("RESOURCE DIALOG", lines[0], count=1)
    normalized = _QUOTED.sub('"S"', "\n".join(lines))
    normalized = _NUMBER.sub("N", normalized)
    return hashlib.sha256(" ".join(normalized.casefold().split()).encode()).hexdigest()[:16]


def _normalized_block(block: str) -> str:
    return " ".join(block.split())


def _variant_sort_key(item: SourceDialogVariant) -> tuple[str, int, str, int]:
    return (
        item.project_root.name.casefold(),
        item.language if item.language is not None else 0,
        item.relative_source.as_posix().casefold(),
        item.line,
    )


def _case_path(
    output: Path,
    variants: tuple[SourceDialogVariant, ...],
) -> Path:
    first = variants[0]
    project = _slug(first.project_root.name)
    scope = _slug(first.scope.as_posix().replace("/", "__"))
    resource = _slug(first.resource_id.display_name.upper())
    identity = "\0".join(
        (
            str(first.project_root.resolve()),
            first.scope.as_posix().casefold(),
            str(_resource_key(first.resource_id)),
            first.skeleton,
            *(f"{item.relative_source}:{item.line}" for item in variants),
        )
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()[:10]
    return output / "sources" / project / scope / f"{resource}__{digest}.rc"


def _slug(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-_.")
    return result[:120] or "resource"


def _ancestor_include_paths(source: Path, project_root: Path) -> tuple[Path, ...]:
    result: list[Path] = []
    current = source.parent.resolve()
    root = project_root.resolve()
    while True:
        result.append(current)
        if current == root:
            break
        if root not in current.parents:
            break
        current = current.parent
    return tuple(result)


def _prepare_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(
            f"source corpus output directory is not empty: {path}; "
            "choose a new directory"
        )
    path.mkdir(parents=True, exist_ok=True)


def _report_payload(
    output: Path,
    discovered: tuple[CorpusCase, ...],
    cases: tuple[ExtractedDialogCase, ...],
    *,
    declared_dialogs: int,
    extracted_variants: int,
    exact_duplicates: int,
    malformed_dialogs: int,
    preprocessor_warnings: int,
) -> dict[str, object]:
    languages = Counter(
        item.language if item.language is not None else "unknown"
        for case in cases
        for item in case.variants
    )
    return {
        "summary": {
            "resource_sources": len(discovered),
            "sources_with_direct_dialogs": sum(bool(item.direct_dialogs) for item in discovered),
            "declared_dialogs": declared_dialogs,
            "extracted_variants": extracted_variants,
            "exact_duplicates": exact_duplicates,
            "malformed_dialogs": malformed_dialogs,
            "preprocessor_warnings": preprocessor_warnings,
            "cases": len(cases),
            "multilingual_cases": sum(len(item.variants) > 1 for item in cases),
            "languages": {str(key): value for key, value in sorted(languages.items(), key=lambda item: str(item[0]))},
        },
        "cases": [
            {
                "source": case.source.relative_to(output).as_posix(),
                "preferred_language": case.preferred_language,
                "variants": [
                    {
                        "project": item.project_root.name,
                        "source": item.relative_source.as_posix(),
                        "line": item.line,
                        "resource_id": item.resource_id.display_name,
                        "language": item.language,
                        "language_expression": item.language_expression,
                    }
                    for item in case.variants
                ],
            }
            for case in cases
        ],
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_markdown(path: Path, payload: dict[str, object]) -> None:
    summary = payload["summary"]
    assert isinstance(summary, dict)
    lines = [
        "# rc2ui source corpus",
        "",
        f"- Resource scripts scanned: {summary['resource_sources']}",
        f"- Scripts with direct dialogs: {summary['sources_with_direct_dialogs']}",
        f"- Dialog declarations: {summary['declared_dialogs']}",
        f"- Extracted variants: {summary['extracted_variants']}",
        f"- Independent cases: {summary['cases']}",
        f"- Multilingual cases: {summary['multilingual_cases']}",
        f"- Exact duplicates removed: {summary['exact_duplicates']}",
        f"- Malformed/incomplete blocks: {summary['malformed_dialogs']}",
        f"- Preprocessor warnings: {summary['preprocessor_warnings']}",
        "",
        "Each generated RC contains one dialog family, its available language variants,",
        "resolved project identifiers, origin comments, a preferred LANGID and UTF-8",
        "compiler codepage metadata consumed by `rc2ui corpus run`.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")

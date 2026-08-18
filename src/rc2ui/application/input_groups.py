from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from rc2ui.adapters.headers.symbols import SymbolLoader, SymbolTable
from rc2ui.adapters.rc.dialog_declarations import (
    DialogDeclaration,
    find_dialog_declarations,
)
from rc2ui.adapters.resources.model import ResourceEntry
from rc2ui.adapters.resources.source import (
    ResourceSourceError,
    read_dialog_resources,
)
from rc2ui.application.models import InputGroup
from rc2ui.domain.diagnostics import Diagnostic, Severity
from rc2ui.domain.resource_id import ResourceId


@dataclass(frozen=True, slots=True)
class ResourceVariant:
    entry: ResourceEntry
    container: Path
    container_order: int


@dataclass(frozen=True, slots=True)
class ResolvedDialogInput:
    source: PurePosixPath
    dialog_id: str
    symbols: SymbolTable
    variants: tuple[ResourceVariant, ...]


@dataclass(frozen=True, slots=True)
class InputGroupResult:
    dialogs: tuple[ResolvedDialogInput, ...]
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True, slots=True)
class _RcSource:
    path: Path
    source: PurePosixPath
    symbols: SymbolTable
    declarations: tuple[DialogDeclaration, ...]


class InputGroupLoader:
    """Resolve an arbitrary RC/resource group into owned dialog variants."""

    def __init__(
        self,
        *,
        project_root: Path,
        include_paths: tuple[Path, ...] = (),
        predefined: dict[str, int] | None = None,
        rc_encoding: str = "cp1251",
        default_language: int = 1033,
    ) -> None:
        self.root = project_root.resolve()
        self.include_paths = tuple(path.resolve() for path in include_paths)
        self.predefined = dict(predefined or {})
        self.rc_encoding = rc_encoding
        self.default_language = default_language

    def load(self, group: InputGroup) -> InputGroupResult:
        diagnostics: list[Diagnostic] = []
        rc_sources = self._load_rc_sources(group.rc_files, diagnostics)
        variants = self._load_resource_variants(
            group.resource_files,
            diagnostics,
        )
        dialogs: list[ResolvedDialogInput] = []
        for grouped in _group_variants(variants):
            if not group.dialog_selection.matches(
                _dialog_selectors(grouped[0].entry.resource_id, rc_sources)
            ):
                continue
            normalized = _normalize_languages(grouped, diagnostics)
            if normalized is None:
                continue
            owner = _resolve_owner(
                normalized[0].entry.resource_id,
                rc_sources,
                tuple(item.entry.language for item in normalized),
                self.default_language,
                diagnostics,
            )
            if owner is None:
                continue
            dialogs.append(
                ResolvedDialogInput(
                    source=owner.source,
                    dialog_id=_source_dialog_id(
                        owner,
                        normalized[0].entry.resource_id,
                        tuple(item.entry.language for item in normalized),
                        self.default_language,
                    ),
                    symbols=owner.symbols,
                    variants=normalized,
                )
            )
        return InputGroupResult(tuple(dialogs), tuple(diagnostics))

    def _load_rc_sources(
        self,
        paths: tuple[Path, ...],
        diagnostics: list[Diagnostic],
    ) -> tuple[_RcSource, ...]:
        result: list[_RcSource] = []
        seen: set[Path] = set()
        for raw_path in paths:
            path = _resolve_from(self.root, raw_path).resolve()
            if path in seen:
                diagnostics.append(
                    Diagnostic(
                        code="input.duplicate-rc",
                        severity=Severity.INFO,
                        message="duplicate RC path ignored",
                        location=str(path),
                    )
                )
                continue
            seen.add(path)
            try:
                relative = path.relative_to(self.root)
            except ValueError:
                diagnostics.append(
                    Diagnostic(
                        code="input.outside-project",
                        severity=Severity.ERROR,
                        message="RC source must be inside project_root",
                        location=str(path),
                    )
                )
                continue
            symbol_result = SymbolLoader(
                include_paths=self.include_paths,
                predefined=self.predefined,
                source_encoding=self.rc_encoding,
            ).load(path)
            diagnostics.extend(symbol_result.diagnostics)
            result.append(
                _RcSource(
                    path=path,
                    source=PurePosixPath(relative.as_posix()),
                    symbols=symbol_result.table,
                    declarations=find_dialog_declarations(
                        symbol_result.active_lines,
                        symbol_result.table,
                    ),
                )
            )
        return tuple(result)

    def _load_resource_variants(
        self,
        paths: tuple[Path, ...],
        diagnostics: list[Diagnostic],
    ) -> tuple[ResourceVariant, ...]:
        result: list[ResourceVariant] = []
        seen: set[Path] = set()
        for order, raw_path in enumerate(paths):
            path = _resolve_from(self.root, raw_path).resolve()
            if path in seen:
                diagnostics.append(
                    Diagnostic(
                        code="input.duplicate-resource",
                        severity=Severity.INFO,
                        message="duplicate compiled-resource path ignored",
                        location=str(path),
                    )
                )
                continue
            seen.add(path)
            try:
                entries = read_dialog_resources(path)
            except (OSError, ResourceSourceError) as error:
                diagnostics.append(
                    Diagnostic(
                        code="resource.read-error",
                        severity=Severity.ERROR,
                        message=str(error),
                        location=str(path),
                    )
                )
                continue
            if not entries:
                diagnostics.append(
                    Diagnostic(
                        code="resource.no-dialogs",
                        severity=Severity.WARNING,
                        message="compiled resource contains no RT_DIALOG entries",
                        location=str(path),
                    )
                )
                continue
            result.extend(ResourceVariant(entry, path, order) for entry in entries)
        return tuple(result)


def _resolve_from(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _resource_key(resource_id: ResourceId) -> tuple[int | None, str | None]:
    return resource_id.ordinal, resource_id.name


def _dialog_selectors(
    resource_id: ResourceId,
    sources: tuple[_RcSource, ...],
) -> tuple[str, ...]:
    values = list(resource_id.symbols)
    if resource_id.ordinal is not None:
        for source in sources:
            values.extend(source.symbols.symbols_for(resource_id.ordinal))
        values.append(f"#{resource_id.ordinal}")
    else:
        assert resource_id.name is not None
        values.append(resource_id.name)
    return tuple(dict.fromkeys(values))


def _source_dialog_id(
    source: _RcSource,
    resource_id: ResourceId,
    available_languages: tuple[int, ...],
    default_language: int,
) -> str:
    """Return the ID token used by the authoritative RC declaration."""

    declarations = tuple(
        declaration
        for declaration in source.declarations
        if declaration.resource_id is not None
        and _resource_key(declaration.resource_id) == _resource_key(resource_id)
    )
    authoritative_language = (
        default_language
        if default_language in available_languages
        else available_languages[0]
        if len(available_languages) == 1
        else None
    )
    if authoritative_language is not None:
        for declaration in declarations:
            if declaration.language == authoritative_language:
                return _declaration_dialog_id(declaration)
    for declaration in declarations:
        if declaration.language is None:
            return _declaration_dialog_id(declaration)
    if declarations:
        return _declaration_dialog_id(declarations[0])

    if resource_id.ordinal is not None:
        symbols = source.symbols.symbols_for(resource_id.ordinal)
        if symbols:
            return min(
                symbols,
                key=lambda symbol: (
                    not symbol.upper().startswith("IDD_"),
                    symbol,
                ),
            )
        return f"#{resource_id.ordinal}"
    assert resource_id.name is not None
    return resource_id.name


def _declaration_dialog_id(declaration: DialogDeclaration) -> str:
    assert declaration.resource_id is not None
    if declaration.resource_id.name is not None:
        return declaration.resource_id.name
    return declaration.token


def _group_variants(
    variants: tuple[ResourceVariant, ...],
) -> tuple[tuple[ResourceVariant, ...], ...]:
    groups: dict[tuple[int | None, str | None], list[ResourceVariant]] = {}
    for variant in variants:
        groups.setdefault(_resource_key(variant.entry.resource_id), []).append(
            variant
        )
    return tuple(
        tuple(
            sorted(
                group,
                key=lambda item: (
                    item.entry.language,
                    item.container_order,
                    item.entry.file_offset,
                ),
            )
        )
        for _, group in sorted(
            groups.items(),
            key=lambda item: (
                item[0][0] is None,
                item[0][0] if item[0][0] is not None else 0,
                item[0][1] or "",
            ),
        )
    )


def _normalize_languages(
    variants: tuple[ResourceVariant, ...],
    diagnostics: list[Diagnostic],
) -> tuple[ResourceVariant, ...] | None:
    by_language: dict[int, list[ResourceVariant]] = {}
    for variant in variants:
        by_language.setdefault(variant.entry.language, []).append(variant)
    normalized: list[ResourceVariant] = []
    conflicted = False
    for language, candidates in sorted(by_language.items()):
        first = candidates[0]
        different = [
            item for item in candidates[1:] if item.entry.data != first.entry.data
        ]
        if different:
            conflicted = True
            locations = ", ".join(str(item.container) for item in candidates)
            diagnostics.append(
                Diagnostic(
                    code="resource.conflicting-variant",
                    severity=Severity.ERROR,
                    message=(
                        f"dialog {_resource_label(first.entry.resource_id)} has "
                        f"different payloads for LANGID {language} in: {locations}; "
                        "put unrelated modules in separate input_groups"
                    ),
                    location=str(first.container),
                )
            )
            continue
        if len(candidates) > 1:
            diagnostics.append(
                Diagnostic(
                    code="resource.duplicate-variant",
                    severity=Severity.INFO,
                    message=(
                        f"identical duplicate of dialog "
                        f"{_resource_label(first.entry.resource_id)} for LANGID "
                        f"{language} ignored"
                    ),
                    location=str(first.container),
                )
            )
        normalized.append(first)
    if conflicted:
        return None
    return tuple(normalized)


def _resolve_owner(
    resource_id: ResourceId,
    sources: tuple[_RcSource, ...],
    available_languages: tuple[int, ...],
    default_language: int,
    diagnostics: list[Diagnostic],
) -> _RcSource | None:
    if not sources:
        return None
    authoritative_language = (
        default_language
        if default_language in available_languages
        else available_languages[0]
        if len(available_languages) == 1
        else None
    )
    declaration_matches = tuple(
        source
        for source in sources
        if any(
            declaration.resource_id is not None
            and _resource_key(declaration.resource_id) == _resource_key(resource_id)
            for declaration in source.declarations
        )
    )
    if len(declaration_matches) == 1:
        return declaration_matches[0]
    if len(declaration_matches) > 1:
        if authoritative_language is not None:
            language_matches = tuple(
                source
                for source in declaration_matches
                if any(
                    declaration.resource_id is not None
                    and _resource_key(declaration.resource_id)
                    == _resource_key(resource_id)
                    and declaration.language == authoritative_language
                    for declaration in source.declarations
                )
            )
            if len(language_matches) == 1:
                return language_matches[0]
        _owner_error(resource_id, declaration_matches, diagnostics, ambiguous=True)
        return None
    language_partition_owner = _language_partition_owner(
        sources,
        available_languages,
        authoritative_language,
    )
    if language_partition_owner is not None:
        return language_partition_owner
    if len(sources) == 1:
        return sources[0]

    symbol_matches: tuple[_RcSource, ...] = ()
    if resource_id.ordinal is not None:
        symbol_matches = tuple(
            source
            for source in sources
            if any(
                symbol.upper().startswith("IDD_")
                for symbol in source.symbols.symbols_for(resource_id.ordinal)
            )
        )
    if len(symbol_matches) == 1:
        return symbol_matches[0]
    if len(symbol_matches) > 1:
        _owner_error(resource_id, symbol_matches, diagnostics, ambiguous=True)
        return None
    _owner_error(resource_id, sources, diagnostics, ambiguous=False)
    return None


def _language_partition_owner(
    sources: tuple[_RcSource, ...],
    available_languages: tuple[int, ...],
    authoritative_language: int | None,
) -> _RcSource | None:
    if authoritative_language is None:
        return None
    sources_with_declarations = tuple(
        source for source in sources if source.declarations
    )
    if not sources_with_declarations or any(
        not any(
            declaration.language is not None
            for declaration in source.declarations
        )
        for source in sources_with_declarations
    ):
        return None
    owners: dict[int, _RcSource] = {}
    for language in available_languages:
        matches = tuple(
            source
            for source in sources_with_declarations
            if any(
                declaration.language == language
                for declaration in source.declarations
            )
        )
        if len(matches) != 1:
            return None
        owners[language] = matches[0]
    return owners.get(authoritative_language)


def _owner_error(
    resource_id: ResourceId,
    sources: tuple[_RcSource, ...],
    diagnostics: list[Diagnostic],
    *,
    ambiguous: bool,
) -> None:
    paths = ", ".join(source.source.as_posix() for source in sources)
    if ambiguous:
        code = "input.ambiguous-dialog-owner"
        message = (
            f"dialog {_resource_label(resource_id)} is declared or defined by "
            f"multiple RC sources: {paths}; split them into separate input_groups"
        )
    else:
        code = "input.dialog-owner-not-found"
        message = (
            f"cannot determine which RC source owns dialog "
            f"{_resource_label(resource_id)} among: {paths}"
        )
    diagnostics.append(
        Diagnostic(
            code=code,
            severity=Severity.ERROR,
            message=message,
            location=paths,
        )
    )


def _resource_label(resource_id: ResourceId) -> str:
    if resource_id.ordinal is not None:
        return f"#{resource_id.ordinal}"
    assert resource_id.name is not None
    return resource_id.name

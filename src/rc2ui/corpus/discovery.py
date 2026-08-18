from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from rc2ui.adapters.rc.text import read_rc_text, strip_rc_comments
from rc2ui.corpus.model import CorpusCase, CorpusCaseKind


_RESOURCE_SUFFIXES = frozenset({".rc", ".rc2", ".dlg"})
_INCLUDE = re.compile(
    r'^\s*#\s*include\s*["<]([^">]+)[">]',
    flags=re.IGNORECASE | re.MULTILINE,
)
_DIALOG = re.compile(
    r'^\s*(?:[A-Za-z_]\w*|\d+|"[^"]+")\s+DIALOG(?:EX)?\b',
    flags=re.IGNORECASE | re.MULTILINE,
)
_LANGUAGE = re.compile(
    r'^\s*LANGUAGE\s+([^\r\n]+)',
    flags=re.IGNORECASE | re.MULTILINE,
)
_PREFERRED_LANGUAGE = re.compile(
    r"^\s*//\s*rc2ui-preferred-language:\s*(\d+)\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)
_COMPILER_CODEPAGE = re.compile(
    r"^\s*//\s*rc2ui-compiler-codepage:\s*(\d+)\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)
_LANGUAGE_DIRS = frozenset(
    {"lang", "langs", "language", "languages", "locale", "locales", "mui"}
)
_LOCALE_STEM = re.compile(
    r"(?:^|[_-])(?:[a-z]{2,3}[-_][a-z]{2}|\d{4})(?:$|[_-])",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _SourceFacts:
    path: Path
    relative: PurePosixPath
    byte_size: int
    direct_dialogs: int
    include_names: tuple[str, ...]
    languages: tuple[str, ...]
    read_error: str | None = None
    preferred_language: int | None = None
    compiler_codepage: int | None = None


def discover_corpus(
    roots: Iterable[Path],
    *,
    fallback_encoding: str = "cp1251",
) -> tuple[CorpusCase, ...]:
    """Classify RC roots and include fragments without evaluating build logic."""

    result: list[CorpusCase] = []
    for raw_root in _expand_roots(roots):
        root = raw_root.resolve()
        if not root.is_dir():
            raise ValueError(f"corpus root is not a directory: {raw_root}")
        result.extend(_discover_project(root, fallback_encoding))
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.project_root.as_posix().casefold(),
                item.relative_source.as_posix().casefold(),
            ),
        )
    )


def _expand_roots(roots: Iterable[Path]) -> tuple[Path, ...]:
    """Treat a directory of Git repositories as a collection of projects."""

    expanded: list[Path] = []
    for raw_root in roots:
        root = raw_root.resolve()
        if root.is_dir() and not (root / ".git").exists():
            children = tuple(
                sorted(
                    (
                        child
                        for child in root.iterdir()
                        if child.is_dir() and (child / ".git").exists()
                    ),
                    key=lambda item: item.name.casefold(),
                )
            )
            if children:
                expanded.extend(children)
                continue
        expanded.append(root)
    return tuple(dict.fromkeys(expanded))


def _discover_project(root: Path, fallback_encoding: str) -> list[CorpusCase]:
    extracted = _discover_extracted_project(root)
    if extracted is not None:
        return extracted
    paths = tuple(_resource_sources(root))
    relative_index = {
        _relative_key(path.relative_to(root)): path for path in paths
    }
    facts = {
        path: _read_facts(root, path, fallback_encoding) for path in paths
    }
    includes: dict[Path, tuple[Path, ...]] = {}
    included_by: dict[Path, list[Path]] = defaultdict(list)
    for path, item in facts.items():
        resolved = tuple(
            target
            for name in item.include_names
            if (
                target := _resolve_include(
                    root,
                    path,
                    name,
                    relative_index,
                )
            )
            is not None
        )
        includes[path] = tuple(dict.fromkeys(resolved))
        for target in includes[path]:
            included_by[target].append(path)

    reachable_cache: dict[Path, int] = {}

    def reachable_dialogs(path: Path, active: frozenset[Path] = frozenset()) -> int:
        if path in reachable_cache:
            return reachable_cache[path]
        if path in active:
            return 0
        next_active = active | {path}
        count = facts[path].direct_dialogs
        count += sum(
            reachable_dialogs(child, next_active) for child in includes[path]
        )
        reachable_cache[path] = count
        return count

    result: list[CorpusCase] = []
    for path in paths:
        item = facts[path]
        parents = tuple(
            sorted(
                included_by[path],
                key=lambda parent: parent.as_posix().casefold(),
            )
        )
        reachable = reachable_dialogs(path)
        kind = _classify(item, parents, reachable)
        result.append(
            CorpusCase(
                case_id=_case_id(root, item.relative),
                project_root=root,
                source=path,
                relative_source=item.relative,
                kind=kind,
                byte_size=item.byte_size,
                direct_dialogs=item.direct_dialogs,
                reachable_dialogs=reachable,
                includes=tuple(facts[child].relative for child in includes[path]),
                included_by=tuple(facts[parent].relative for parent in parents),
                languages=item.languages,
                read_error=item.read_error,
                preferred_language=item.preferred_language,
                compiler_codepage=item.compiler_codepage,
            )
        )
    return result


def _discover_extracted_project(root: Path) -> list[CorpusCase] | None:
    """Load the generated source-corpus index without rescanning every RC."""

    index_path = root / "source-corpus.json"
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid extracted source corpus index: {index_path}: {error}") from error
    raw_cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(raw_cases, list):
        raise ValueError(f"invalid extracted source corpus index: {index_path}")
    result: list[CorpusCase] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict) or not isinstance(raw_case.get("source"), str):
            raise ValueError(f"invalid extracted source corpus case in: {index_path}")
        relative = PurePosixPath(raw_case["source"])
        source = (root / relative).resolve()
        if not source.is_file() or not _is_inside(source, root):
            raise ValueError(f"extracted source corpus case is missing: {source}")
        raw_variants = raw_case.get("variants", [])
        if not isinstance(raw_variants, list) or not raw_variants:
            raise ValueError(f"extracted source corpus case has no variants: {source}")
        languages = tuple(
            dict.fromkeys(
                str(language)
                for variant in raw_variants
                if isinstance(variant, dict)
                and (language := variant.get("language")) is not None
            )
        )
        preferred = raw_case.get("preferred_language")
        result.append(
            CorpusCase(
                case_id=_case_id(root, relative),
                project_root=root,
                source=source,
                relative_source=relative,
                kind=CorpusCaseKind.ROOT,
                byte_size=_safe_size(source),
                direct_dialogs=len(raw_variants),
                reachable_dialogs=len(raw_variants),
                languages=languages,
                preferred_language=preferred if isinstance(preferred, int) else None,
                compiler_codepage=65001,
            )
        )
    return result


def _resource_sources(root: Path) -> Iterable[Path]:
    for directory, names, files in os.walk(root):
        names[:] = sorted(name for name in names if name != ".git")
        for name in sorted(files):
            path = Path(directory, name)
            if path.suffix.casefold() in _RESOURCE_SUFFIXES:
                yield path.resolve()


def _read_facts(root: Path, path: Path, fallback_encoding: str) -> _SourceFacts:
    relative = PurePosixPath(path.relative_to(root).as_posix())
    try:
        raw_text = read_rc_text(path, fallback_encoding=fallback_encoding)
        text = strip_rc_comments(raw_text)
    except (OSError, UnicodeError) as error:
        return _SourceFacts(
            path=path,
            relative=relative,
            byte_size=_safe_size(path),
            direct_dialogs=0,
            include_names=(),
            languages=(),
            read_error=str(error),
        )
    return _SourceFacts(
        path=path,
        relative=relative,
        byte_size=_safe_size(path),
        direct_dialogs=len(_DIALOG.findall(text)),
        include_names=tuple(match.group(1).strip() for match in _INCLUDE.finditer(text)),
        languages=tuple(
            dict.fromkeys(match.group(1).strip() for match in _LANGUAGE.finditer(text))
        ),
        preferred_language=_metadata_integer(_PREFERRED_LANGUAGE, raw_text),
        compiler_codepage=_metadata_integer(_COMPILER_CODEPAGE, raw_text),
    )


def _metadata_integer(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    return int(match.group(1)) if match else None


def _resolve_include(
    root: Path,
    source: Path,
    raw_name: str,
    relative_index: dict[str, Path],
) -> Path | None:
    normalized = raw_name.replace("\\", "/")
    if Path(normalized).suffix.casefold() not in _RESOURCE_SUFFIXES:
        return None
    direct = (source.parent / normalized).resolve()
    if direct.is_file() and _is_inside(direct, root):
        return direct
    rooted = (root / normalized).resolve()
    if rooted.is_file() and _is_inside(rooted, root):
        return rooted
    try:
        source_parent = source.parent.relative_to(root)
    except ValueError:
        return None
    relative = PurePosixPath(source_parent.as_posix()) / PurePosixPath(normalized)
    collapsed = _collapse_relative(relative)
    if collapsed is not None:
        return relative_index.get(_relative_key(collapsed))
    return None


def _classify(
    facts: _SourceFacts,
    parents: tuple[Path, ...],
    reachable_dialogs: int,
) -> CorpusCaseKind:
    if facts.read_error is not None:
        return CorpusCaseKind.UNREADABLE
    if reachable_dialogs == 0:
        return CorpusCaseKind.NON_DIALOG
    if (
        facts.preferred_language is not None
        and facts.compiler_codepage is not None
        and facts.path.suffix.casefold() == ".rc"
    ):
        return CorpusCaseKind.ROOT
    language_hint = _is_language_path(facts.relative)
    if language_hint:
        return CorpusCaseKind.LANGUAGE_FRAGMENT
    if parents or facts.path.suffix.casefold() != ".rc":
        return CorpusCaseKind.DIALOG_FRAGMENT
    return CorpusCaseKind.ROOT


def _is_language_path(relative: PurePosixPath) -> bool:
    parents = {part.casefold() for part in relative.parts[:-1]}
    return bool(parents & _LANGUAGE_DIRS) or bool(
        _LOCALE_STEM.search(relative.stem)
    )


def _case_id(root: Path, relative: PurePosixPath) -> str:
    project = re.sub(r"[^A-Za-z0-9_.-]+", "-", root.name).strip("-_") or "corpus"
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", relative.stem).strip("-_") or "resource"
    digest = hashlib.sha256(
        f"{root.name.casefold()}\0{relative.as_posix().casefold()}".encode("utf-8")
    ).hexdigest()[:10]
    return f"{project}__{stem}__{digest}"


def _relative_key(relative: Path | PurePosixPath) -> str:
    return PurePosixPath(relative.as_posix()).as_posix().casefold()


def _collapse_relative(path: PurePosixPath) -> PurePosixPath | None:
    parts: list[str] = []
    for part in path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return PurePosixPath(*parts)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0

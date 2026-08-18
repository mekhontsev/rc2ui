from __future__ import annotations

import re
import shutil
from functools import lru_cache
from pathlib import Path, PurePosixPath

from rc2ui.adapters.rc.text import read_rc_text, strip_rc_comments


_QUOTED = re.compile(r'"((?:[^"\\]|\\.)+)"')
_INCLUDE = re.compile(
    r'^\s*#\s*include\s*(?P<delimiter>["<])(?P<path>[^">]+)[">]',
    flags=re.IGNORECASE | re.MULTILINE,
)
_RESOURCE_SCRIPT_SUFFIXES = frozenset({".rc", ".rc2", ".dlg"})
_TEXT_INCLUDE_SUFFIXES = frozenset(
    {
        "",
        ".h",
        ".hh",
        ".hpp",
        ".inc",
        ".inl",
        *_RESOURCE_SCRIPT_SUFFIXES,
    }
)
_ASSET_SUFFIXES = frozenset(
    {
        ".ani",
        ".avi",
        ".bin",
        ".bmp",
        ".css",
        ".cur",
        ".gif",
        ".htm",
        ".html",
        ".ico",
        ".jpeg",
        ".jpg",
        ".manifest",
        ".png",
        ".rtf",
        ".tlb",
        ".txt",
        ".wav",
        ".xml",
        ".xsl",
    }
)


def prepare_asset_overlay(
    source: Path,
    project_root: Path,
    overlay_root: Path,
    *,
    fallback_encoding: str,
) -> Path:
    """Mirror includes and assets with Windows-like path resolution.

    Resource projects commonly rely on case-insensitive paths and build-system
    include roots which are not recorded in an individual RC file.  The corpus
    runner deliberately operates without a project's native build system, so
    this overlay reconstructs a minimal, case-correct include tree from files
    which can be found inside the checkout.
    """

    relative_parent = source.resolve().parent.relative_to(project_root.resolve())
    working_directory = overlay_root / relative_parent
    working_directory.mkdir(parents=True, exist_ok=True)
    pending = [(source.resolve(), working_directory)]
    visited: set[Path] = set()
    while pending:
        script, virtual_parent = pending.pop()
        if script in visited or not _is_inside(script, project_root.resolve()):
            continue
        visited.add(script)
        try:
            text = strip_rc_comments(
                read_rc_text(script, fallback_encoding=fallback_encoding)
            )
        except (OSError, UnicodeError):
            continue

        for raw_name in _resource_paths(text):
            relative = _safe_relative(raw_name)
            if relative is None:
                continue
            actual = _resolve_project_reference(
                script,
                source,
                project_root,
                relative,
            )
            if (
                actual is None
                or not actual.is_file()
                or not _is_inside(actual.resolve(), project_root.resolve())
            ):
                continue
            destinations = {
                (working_directory / relative).resolve(),
                (virtual_parent / relative).resolve(),
            }
            for destination in destinations:
                if not _is_inside(destination, overlay_root.resolve()):
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(actual, destination)

        for match in _INCLUDE.finditer(text):
            relative = _safe_relative(match.group("path").replace("\\", "/"))
            if relative is None:
                continue
            # Let the installed Windows SDK/MinGW headers satisfy ordinary
            # angle-bracket headers.  Mirroring a checkout's private windef.h
            # over the compiler's coherent SDK causes a much deeper and often
            # build-generated dependency chain.  RC/RC2/DLG includes are the
            # exception: those are project resource fragments by definition.
            if (
                match.group("delimiter") == "<"
                and relative.suffix.casefold() not in _RESOURCE_SCRIPT_SUFFIXES
            ):
                continue
            direct = _resolve_direct_reference(
                script,
                source,
                project_root,
                relative,
            )
            if (
                direct is None
                and relative.suffix.casefold() not in _RESOURCE_SCRIPT_SUFFIXES
            ):
                # A globally matching SDK header is not necessarily part of
                # this target's include graph.  Copy only locally resolvable
                # headers; otherwise leave the compiler's coherent SDK alone.
                continue
            actual = direct or _resolve_project_reference(
                script, source, project_root, relative
            )
            if (
                actual is None
                or not actual.is_file()
                or not _is_inside(actual.resolve(), project_root.resolve())
            ):
                continue
            destination = (virtual_parent / relative).resolve()
            if not _is_inside(destination, overlay_root.resolve()):
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(actual, destination)
            if relative.suffix.casefold() in _TEXT_INCLUDE_SUFFIXES:
                pending.append((actual.resolve(), destination.parent))
    return working_directory


def _resource_paths(text: str) -> tuple[str, ...]:
    result: list[str] = []
    for match in _QUOTED.finditer(text):
        value = match.group(1).replace("\\\\", "\\").replace("\\", "/")
        if PurePosixPath(value).suffix.casefold() in _ASSET_SUFFIXES:
            result.append(value)
    return tuple(dict.fromkeys(result))


def _safe_relative(value: str) -> PurePosixPath | None:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts:
        return None
    return path


def _find_case_insensitive(base: Path, relative: PurePosixPath) -> Path | None:
    current = base.resolve()
    for part in relative.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            current = current.parent
            continue
        direct = current / part
        if direct.exists():
            current = direct
            continue
        try:
            match = next(
                (
                    candidate
                    for candidate in current.iterdir()
                    if candidate.name.casefold() == part.casefold()
                ),
                None,
            )
        except OSError:
            return None
        if match is None:
            return None
        current = match
    return current


def _resolve_project_reference(
    script: Path,
    source: Path,
    project_root: Path,
    relative: PurePosixPath,
) -> Path | None:
    if direct := _resolve_direct_reference(
        script,
        source,
        project_root,
        relative,
    ):
        return direct

    root = project_root.resolve()
    suffix = tuple(part.casefold() for part in relative.parts if part not in {".", ".."})
    if not suffix:
        return None
    candidates = [
        candidate
        for candidate in _project_files(root).get(suffix[-1], ())
        if _has_casefold_suffix(candidate.relative_to(root), suffix)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: _reference_rank(item, script, source, root))


def _resolve_direct_reference(
    script: Path,
    source: Path,
    project_root: Path,
    relative: PurePosixPath,
) -> Path | None:
    for base in (script.parent, source.parent, project_root):
        resolved = _find_case_insensitive(base, relative)
        if resolved is not None:
            return resolved
    return None


@lru_cache(maxsize=16)
def _project_files(project_root: Path) -> dict[str, tuple[Path, ...]]:
    by_name: dict[str, list[Path]] = {}
    try:
        paths = project_root.rglob("*")
        for path in paths:
            if path.is_file():
                by_name.setdefault(path.name.casefold(), []).append(path.resolve())
    except OSError:
        pass
    return {name: tuple(paths) for name, paths in by_name.items()}


def _has_casefold_suffix(path: Path, suffix: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    return len(parts) >= len(suffix) and parts[-len(suffix) :] == suffix


def _reference_rank(
    candidate: Path,
    script: Path,
    source: Path,
    project_root: Path,
) -> tuple[int, int, int, str]:
    candidate_parent = candidate.parent
    script_parts = script.parent.resolve().relative_to(project_root).parts
    source_parts = source.parent.resolve().relative_to(project_root).parts
    candidate_parts = candidate_parent.relative_to(project_root).parts
    shared_script = _shared_prefix_length(script_parts, candidate_parts)
    shared_source = _shared_prefix_length(source_parts, candidate_parts)
    distance = (
        len(script_parts)
        + len(candidate_parts)
        - 2 * shared_script
    )
    return (-shared_script, distance, -shared_source, candidate.as_posix().casefold())


def _shared_prefix_length(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    result = 0
    for left_part, right_part in zip(left, right):
        if left_part.casefold() != right_part.casefold():
            break
        result += 1
    return result


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True

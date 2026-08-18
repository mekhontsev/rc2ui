from __future__ import annotations

import codecs
import math
import tomllib
from pathlib import Path

from rc2ui.application.models import (
    ConversionRequest,
    DialogSelection,
    InputGroup,
    ProjectRules,
)
from rc2ui.layout.mode import LayoutMode
from rc2ui.mapping.overrides import ControlMap, ControlMapError
from rc2ui.naming.map import NamingMap, NamingMapError
from rc2ui.qtcheck.model import QtCheckMode
from rc2ui.semantics.config import SemanticMap, SemanticMapError


_TOP_LEVEL_FIELDS = frozenset(
    {
        "version",
        "project_root",
        "output",
        "include_paths",
        "default_language",
        "rc_encoding",
        "strict",
        "layout_mode",
        "ui_comments",
        "qt_check",
        "qt_font_scale",
        "qt_preview",
        "defines",
        "input_groups",
        "naming",
        "controls",
        "semantics",
    }
)


class ManifestError(ValueError):
    pass


def load_manifest(path: Path) -> ConversionRequest:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ManifestError(f"cannot read manifest {path}: {error}") from error
    unexpected = sorted(set(data) - _TOP_LEVEL_FIELDS)
    if unexpected:
        raise ManifestError(
            f"{path}: unexpected top-level field(s): "
            + ", ".join(unexpected)
        )
    version = data.get("version")
    if isinstance(version, bool) or version != 1:
        raise ManifestError(f"{path}: version must be 1")
    base = path.resolve().parent
    project_root = _path(base, data.get("project_root", ".")).resolve()
    output = _path(project_root, _required_string(data, "output"))
    rules = _project_rules(data, path.resolve())

    raw_groups = data.get("input_groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        raise ManifestError(
            "manifest must contain at least one [[input_groups]] table"
        )
    input_groups: list[InputGroup] = []
    for index, raw in enumerate(raw_groups, 1):
        if not isinstance(raw, dict):
            raise ManifestError(f"input_groups #{index} must be a TOML table")
        rc_files = _required_string_array(raw, "rc", index=index)
        resource_files = _required_string_array(
            raw,
            "resources",
            index=index,
        )
        dialogs = _optional_string_array(raw, "dialogs", index=index)
        dialog_regex = _optional_string_array(
            raw,
            "dialog_regex",
            index=index,
        )
        try:
            input_groups.append(
                InputGroup(
                    rc_files=tuple(
                        _path(project_root, item) for item in rc_files
                    ),
                    resource_files=tuple(
                        _path(project_root, item) for item in resource_files
                    ),
                    dialog_selection=DialogSelection(dialogs, dialog_regex),
                )
            )
        except ValueError as error:
            raise ManifestError(f"input_groups #{index}: {error}") from error

    raw_include = data.get("include_paths", [])
    if not isinstance(raw_include, list) or not all(
        isinstance(item, str) for item in raw_include
    ):
        raise ManifestError("include_paths must be an array of strings")
    include_paths = tuple(_path(project_root, item) for item in raw_include)

    raw_defines = data.get("defines", {})
    if not isinstance(raw_defines, dict):
        raise ManifestError("defines must be a TOML table")
    defines = tuple(
        (name, _integer(value, f"defines.{name}"))
        for name, value in raw_defines.items()
    )
    rc_encoding = data.get("rc_encoding", "cp1251")
    if not isinstance(rc_encoding, str) or not rc_encoding:
        raise ManifestError("rc_encoding must be a non-empty string")
    try:
        codecs.lookup(rc_encoding)
    except LookupError as error:
        raise ManifestError(f"unknown rc_encoding: {rc_encoding!r}") from error
    language = _integer(data.get("default_language", 1033), "default_language")
    strict = data.get("strict", False)
    if not isinstance(strict, bool):
        raise ManifestError("strict must be a boolean")
    raw_layout_mode = data.get("layout_mode", LayoutMode.FAITHFUL.value)
    try:
        layout_mode = LayoutMode(raw_layout_mode)
    except (TypeError, ValueError) as error:
        choices = ", ".join(mode.value for mode in LayoutMode)
        raise ManifestError(f"layout_mode must be one of: {choices}") from error
    ui_comments = data.get("ui_comments", True)
    if not isinstance(ui_comments, bool):
        raise ManifestError("ui_comments must be a boolean")
    raw_qt_check = data.get("qt_check", QtCheckMode.AUTO.value)
    try:
        qt_check = QtCheckMode(raw_qt_check)
    except (TypeError, ValueError) as error:
        choices = ", ".join(mode.value for mode in QtCheckMode)
        raise ManifestError(f"qt_check must be one of: {choices}") from error
    raw_qt_preview = data.get("qt_preview")
    if raw_qt_preview is not None and (
        not isinstance(raw_qt_preview, str) or not raw_qt_preview
    ):
        raise ManifestError("qt_preview must be a non-empty string")
    qt_preview = (
        _path(project_root, raw_qt_preview)
        if raw_qt_preview is not None
        else None
    )
    qt_font_scale = _positive_float(
        data.get("qt_font_scale", 1.0),
        "qt_font_scale",
    )

    return ConversionRequest(
        project_root=project_root,
        output_dir=output,
        input_groups=tuple(input_groups),
        rules=rules,
        config_path=path.resolve(),
        include_paths=include_paths,
        defines=defines,
        rc_encoding=rc_encoding,
        default_language=language,
        strict=strict,
        layout_mode=layout_mode,
        ui_comments=ui_comments,
        qt_check=qt_check,
        qt_preview_dir=qt_preview,
        qt_font_scale=qt_font_scale,
    )


def _project_rules(data: dict[str, object], path: Path) -> ProjectRules:
    try:
        naming = (
            NamingMap.from_table(data["naming"], path=path)
            if "naming" in data
            else NamingMap(())
        )
        controls = (
            ControlMap.from_table(data["controls"], path=path)
            if "controls" in data
            else ControlMap((), ())
        )
        semantics = (
            SemanticMap.from_table(data["semantics"], path=path)
            if "semantics" in data
            else SemanticMap(())
        )
    except (NamingMapError, ControlMapError, SemanticMapError) as error:
        raise ManifestError(str(error)) from error
    return ProjectRules(naming, controls, semantics)


def _required_string(table: dict[str, object], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ManifestError(f"manifest field {key!r} must be a non-empty string")
    return value


def _required_string_array(
    table: dict[str, object],
    key: str,
    *,
    index: int,
) -> tuple[str, ...]:
    value = table.get(key)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ManifestError(
            f"input_groups #{index} field {key!r} must be a non-empty "
            "array of strings"
        )
    return tuple(value)


def _optional_string_array(
    table: dict[str, object],
    key: str,
    *,
    index: int,
) -> tuple[str, ...]:
    if key not in table:
        return ()
    return _required_string_array(table, key, index=index)


def _path(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ManifestError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError as error:
            raise ManifestError(f"{field} must be an integer") from error
    raise ManifestError(f"{field} must be an integer")


def _positive_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestError(f"{field} must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ManifestError(f"{field} must be a positive finite number")
    return result

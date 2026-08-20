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
from rc2ui.layout.policy import (
    GapGrowth,
    LayoutOverride,
    LayoutPolicy,
    LayoutPolicySet,
    RuntimeAlternativesPolicy,
    SimplifiedPolicy,
    SimplifiedProfile,
)
from rc2ui.mapping.overrides import ControlMap, ControlMapError
from rc2ui.naming.map import NamingMap, NamingMapError
from rc2ui.qtcheck.model import QtCheckMode, ValidationPolicy
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
        "layout",
        "ui_comments",
        "qt_check",
        "qt_font_scale",
        "qt_preview",
        "validation",
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
    layout_policies = _layout_policies(data)
    layout_mode = layout_policies.default.mode
    ui_comments = data.get("ui_comments", True)
    if not isinstance(ui_comments, bool):
        raise ManifestError("ui_comments must be a boolean")
    raw_validation = _table(data, "validation")
    _reject_unknown(
        raw_validation,
        {
            "qt",
            "preview",
            "preview_font_scale",
            "font_scales",
            "resize_scales",
        },
        "validation",
    )
    if "qt_check" in data and "qt" in raw_validation:
        raise ManifestError(
            "use either qt_check or validation.qt, not both"
        )
    raw_qt_check = raw_validation.get(
        "qt",
        data.get("qt_check", QtCheckMode.AUTO.value),
    )
    try:
        qt_check = QtCheckMode(raw_qt_check)
    except (TypeError, ValueError) as error:
        choices = ", ".join(mode.value for mode in QtCheckMode)
        field = "validation.qt" if "qt" in raw_validation else "qt_check"
        raise ManifestError(f"{field} must be one of: {choices}") from error
    if "qt_preview" in data and "preview" in raw_validation:
        raise ManifestError(
            "use either qt_preview or validation.preview, not both"
        )
    raw_qt_preview = raw_validation.get("preview", data.get("qt_preview"))
    if raw_qt_preview is not None and (
        not isinstance(raw_qt_preview, str) or not raw_qt_preview
    ):
        field = (
            "validation.preview"
            if "preview" in raw_validation
            else "qt_preview"
        )
        raise ManifestError(f"{field} must be a non-empty string")
    qt_preview = (
        _path(project_root, raw_qt_preview)
        if raw_qt_preview is not None
        else None
    )
    if "qt_font_scale" in data and "preview_font_scale" in raw_validation:
        raise ManifestError(
            "use either qt_font_scale or validation.preview_font_scale, not both"
        )
    preview_scale_field = (
        "validation.preview_font_scale"
        if "preview_font_scale" in raw_validation
        else "qt_font_scale"
    )
    qt_font_scale = _positive_float(
        raw_validation.get(
            "preview_font_scale",
            data.get("qt_font_scale", 1.0),
        ),
        preview_scale_field,
    )
    try:
        validation = ValidationPolicy(
            font_scales=_positive_float_array(
                raw_validation.get("font_scales", [2.0]),
                "validation.font_scales",
            ),
            resize_scales=_positive_float_array(
                raw_validation.get("resize_scales", [0.75, 1.0, 1.5]),
                "validation.resize_scales",
            ),
        )
    except ValueError as error:
        raise ManifestError(str(error)) from error

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
        layout_policies=layout_policies,
        ui_comments=ui_comments,
        qt_check=qt_check,
        qt_preview_dir=qt_preview,
        qt_font_scale=qt_font_scale,
        validation=validation,
    )


def _layout_policies(data: dict[str, object]) -> LayoutPolicySet:
    raw = _table(data, "layout")
    _reject_unknown(
        raw,
        {
            "mode",
            "alignment_tolerance_dlu",
            "text_width_safety_factor",
            "max_designer_width_factor",
            "gap_growth",
            "runtime_alternatives",
            "simplified",
            "overrides",
        },
        "layout",
    )
    if "layout_mode" in data and "mode" in raw:
        raise ManifestError("use either layout_mode or layout.mode, not both")
    mode = _enum(
        LayoutMode,
        raw.get("mode", data.get("layout_mode", LayoutMode.FAITHFUL.value)),
        "layout.mode" if "mode" in raw else "layout_mode",
    )
    simplified = _simplified_policy(
        _table(raw, "simplified"),
        "layout.simplified",
    )
    try:
        default = LayoutPolicy(
            mode=mode,
            alignment_tolerance_dlu=_nonnegative_integer(
                raw.get("alignment_tolerance_dlu", 3),
                "layout.alignment_tolerance_dlu",
            ),
            text_width_safety_factor=_positive_float(
                raw.get("text_width_safety_factor", 1.1),
                "layout.text_width_safety_factor",
            ),
            max_designer_width_factor=_positive_float(
                raw.get("max_designer_width_factor", 1.5),
                "layout.max_designer_width_factor",
            ),
            gap_growth=_enum(
                GapGrowth,
                raw.get("gap_growth", GapGrowth.PROPORTIONAL.value),
                "layout.gap_growth",
            ),
            runtime_alternatives=_enum(
                RuntimeAlternativesPolicy,
                raw.get(
                    "runtime_alternatives",
                    RuntimeAlternativesPolicy.AUTO.value,
                ),
                "layout.runtime_alternatives",
            ),
            simplified=simplified,
        )
    except ValueError as error:
        raise ManifestError(str(error)) from error

    raw_overrides = raw.get("overrides", [])
    if not isinstance(raw_overrides, list):
        raise ManifestError("layout.overrides must be an array of tables")
    overrides = tuple(
        _layout_override(item, index)
        for index, item in enumerate(raw_overrides, 1)
    )
    return LayoutPolicySet(default, overrides)


def _simplified_policy(
    raw: dict[str, object],
    location: str,
) -> SimplifiedPolicy:
    _reject_unknown(raw, {"profile", "max_serialized_tracks"}, location)
    try:
        return SimplifiedPolicy(
            profile=_enum(
                SimplifiedProfile,
                raw.get("profile", SimplifiedProfile.BALANCED.value),
                f"{location}.profile",
            ),
            max_serialized_tracks=_integer(
                raw.get("max_serialized_tracks", 5),
                f"{location}.max_serialized_tracks",
            ),
        )
    except ValueError as error:
        raise ManifestError(str(error)) from error


def _layout_override(raw: object, index: int) -> LayoutOverride:
    location = f"layout.overrides #{index}"
    if not isinstance(raw, dict):
        raise ManifestError(f"{location} must be a table")
    _reject_unknown(
        raw,
        {
            "name",
            "dialog",
            "dialog_regex",
            "priority",
            "mode",
            "alignment_tolerance_dlu",
            "text_width_safety_factor",
            "max_designer_width_factor",
            "gap_growth",
            "runtime_alternatives",
            "simplified",
        },
        location,
    )
    raw_simplified = _table(raw, "simplified")
    _reject_unknown(
        raw_simplified,
        {"profile", "max_serialized_tracks"},
        f"{location}.simplified",
    )
    name = raw.get("name", f"override-{index}")
    if not isinstance(name, str) or not name:
        raise ManifestError(f"{location}.name must be a non-empty string")
    for field in ("dialog", "dialog_regex"):
        value = raw.get(field)
        if value is not None and (not isinstance(value, str) or not value):
            raise ManifestError(
                f"{location}.{field} must be a non-empty string"
            )
    try:
        return LayoutOverride(
            name=name,
            dialog=raw.get("dialog"),
            dialog_regex=raw.get("dialog_regex"),
            priority=_integer(raw.get("priority", 0), f"{location}.priority"),
            mode=(
                _enum(LayoutMode, raw["mode"], f"{location}.mode")
                if "mode" in raw
                else None
            ),
            alignment_tolerance_dlu=(
                _nonnegative_integer(
                    raw["alignment_tolerance_dlu"],
                    f"{location}.alignment_tolerance_dlu",
                )
                if "alignment_tolerance_dlu" in raw
                else None
            ),
            text_width_safety_factor=(
                _positive_float(
                    raw["text_width_safety_factor"],
                    f"{location}.text_width_safety_factor",
                )
                if "text_width_safety_factor" in raw
                else None
            ),
            max_designer_width_factor=(
                _positive_float(
                    raw["max_designer_width_factor"],
                    f"{location}.max_designer_width_factor",
                )
                if "max_designer_width_factor" in raw
                else None
            ),
            gap_growth=(
                _enum(GapGrowth, raw["gap_growth"], f"{location}.gap_growth")
                if "gap_growth" in raw
                else None
            ),
            runtime_alternatives=(
                _enum(
                    RuntimeAlternativesPolicy,
                    raw["runtime_alternatives"],
                    f"{location}.runtime_alternatives",
                )
                if "runtime_alternatives" in raw
                else None
            ),
            simplified_profile=(
                _enum(
                    SimplifiedProfile,
                    raw_simplified["profile"],
                    f"{location}.simplified.profile",
                )
                if "profile" in raw_simplified
                else None
            ),
            max_serialized_tracks=(
                _integer(
                    raw_simplified["max_serialized_tracks"],
                    f"{location}.simplified.max_serialized_tracks",
                )
                if "max_serialized_tracks" in raw_simplified
                else None
            ),
        )
    except ValueError as error:
        raise ManifestError(str(error)) from error


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


def _positive_float_array(value: object, field: str) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise ManifestError(f"{field} must be a non-empty array")
    return tuple(
        _positive_float(item, f"{field}[{index}]")
        for index, item in enumerate(value, 1)
    )


def _nonnegative_integer(value: object, field: str) -> int:
    result = _integer(value, field)
    if result < 0:
        raise ManifestError(f"{field} must be a non-negative integer")
    return result


def _table(table: dict[str, object], key: str) -> dict[str, object]:
    value = table.get(key, {})
    if not isinstance(value, dict):
        raise ManifestError(f"{key} must be a table")
    return value


def _reject_unknown(
    table: dict[str, object],
    allowed: set[str],
    location: str,
) -> None:
    unexpected = sorted(set(table) - allowed)
    if unexpected:
        raise ManifestError(
            f"{location}: unexpected field(s): " + ", ".join(unexpected)
        )


def _enum(enum_type: type, value: object, field: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        choices = ", ".join(item.value for item in enum_type)
        raise ManifestError(f"{field} must be one of: {choices}") from error

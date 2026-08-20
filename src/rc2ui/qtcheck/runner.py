from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

from rc2ui.domain.diagnostics import Diagnostic, Severity
from rc2ui.qtcheck.discovery import discover_qt_binding
from rc2ui.qtcheck.gallery import write_preview_gallery
from rc2ui.qtcheck.model import (
    ControlGeometryReference,
    FormGeometryReference,
    QtCheckRun,
)
from rc2ui.qtcheck.summary import (
    build_report_summary,
    preview_summary_diagnostic,
    summarize_diagnostics,
)


def run_qt_checks(
    ui_paths: tuple[Path, ...],
    *,
    report_path: Path,
    required: bool,
    preview_dir: Path | None = None,
    ui_root: Path | None = None,
    font_scale: float = 1.0,
    font_factors: tuple[float, ...] = (2.0,),
    size_factors: tuple[float, ...] = (0.75, 1.0, 1.5),
    geometry_references: Mapping[Path, FormGeometryReference] | None = None,
    worker_module: str = "rc2ui.qtcheck.worker",
) -> QtCheckRun:
    if not math.isfinite(font_scale) or font_scale <= 0:
        raise ValueError("font_scale must be a positive finite number")
    _validate_factors(font_factors, "font_factors")
    _validate_factors(size_factors, "size_factors")
    paths = tuple(sorted({path.resolve() for path in ui_paths}, key=str))
    if geometry_references is None:
        geometry_references = _discover_geometry_references(
            paths,
            ui_root=ui_root,
        )
    availability = discover_qt_binding()
    if not availability.available:
        diagnostics = ()
        if required:
            diagnostics = (
                Diagnostic(
                    code="qt.unavailable",
                    severity=Severity.ERROR,
                    message=(
                        f"{availability.reason}; install it with "
                        "'python -m pip install PyQt6' or "
                        "'python -m pip install PySide6'"
                    ),
                ),
            )
            _write_report(
                report_path,
                {
                    "available": False,
                    "reason": availability.reason,
                    "forms": [],
                    "diagnostics": [asdict(item) for item in diagnostics],
                },
            )
            return QtCheckRun(False, 0, diagnostics, report_path=report_path)
        return QtCheckRun(False, 0, ())

    entries = _request_entries(
        paths,
        preview_dir=preview_dir,
        ui_root=ui_root,
        geometry_references=geometry_references,
    )
    request = {
        "forms": entries,
        "size_factors": list(size_factors),
        "font_factors": list(font_factors),
        # Kept for older external workers that implement the original wire
        # protocol.  The in-tree worker prefers font_factors.
        "font_factor": max(font_factors),
        "font_scale": font_scale,
    }
    try:
        response = _run_worker(request, worker_module=worker_module)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return _worker_failure(report_path, error)

    try:
        raw_diagnostics = response.get("diagnostics", [])
        if not isinstance(raw_diagnostics, list):
            raise ValueError("Qt worker returned invalid diagnostics")
        detailed_diagnostics = tuple(
            _diagnostic_from_wire(item) for item in raw_diagnostics
        )
        forms = response.get("forms", [])
        if not isinstance(forms, list):
            raise ValueError("Qt worker returned invalid form results")
    except (KeyError, TypeError, ValueError) as error:
        return _worker_failure(report_path, error)

    diagnostics = summarize_diagnostics(detailed_diagnostics)
    report_summary = build_report_summary(forms)
    preview_diagnostic = preview_summary_diagnostic(report_summary)
    if preview_diagnostic is not None:
        diagnostics += (preview_diagnostic,)
    if not response.get("available", True) and required:
        diagnostics += (
            Diagnostic(
                code="qt.unavailable",
                severity=Severity.ERROR,
                message=(
                    f"{response.get('reason', 'Qt binding could not be imported')}; "
                    "install PyQt6 or PySide6"
                ),
            ),
        )
    response["summary"] = report_summary
    response["font_scale"] = font_scale
    response["font_factors"] = list(font_factors)
    response["size_factors"] = list(size_factors)
    response["diagnostics"] = [asdict(item) for item in diagnostics]
    _write_report(report_path, response)
    preview_index = (
        write_preview_gallery(preview_dir, forms)
        if preview_dir is not None
        else None
    )
    return QtCheckRun(
        available=bool(response.get("available", True)),
        checked_forms=sum(
            bool(form.get("loaded"))
            for form in forms
            if isinstance(form, dict)
        ),
        diagnostics=diagnostics,
        report_path=report_path,
        preview_index=preview_index,
        qt_version=_optional_string(response.get("qt_version")),
        binding=_optional_string(response.get("binding")),
        binding_version=_optional_string(response.get("binding_version")),
    )


def _validate_factors(values: tuple[float, ...], name: str) -> None:
    if not values or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        for value in values
    ):
        raise ValueError(f"{name} must contain positive finite numbers")


def find_ui_files(inputs: tuple[Path, ...]) -> tuple[Path, ...]:
    found: set[Path] = set()
    for path in inputs:
        if path.is_dir():
            found.update(
                item.resolve()
                for item in path.rglob("*")
                if item.is_file() and item.suffix.casefold() == ".ui"
            )
        elif path.is_file() and path.suffix.casefold() == ".ui":
            found.add(path.resolve())
    return tuple(sorted(found, key=str))


def _run_worker(
    request: dict[str, object],
    *,
    worker_module: str,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="rc2ui-qtcheck-") as directory_name:
        directory = Path(directory_name)
        request_path = directory / "request.json"
        response_path = directory / "response.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        environment = _worker_environment(request)
        raw_forms = request.get("forms", [])
        form_count = len(raw_forms) if isinstance(raw_forms, list) else 0
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                worker_module,
                "--request",
                str(request_path),
                "--response",
                str(response_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=max(60, form_count * 5),
            check=False,
        )
        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip()
            raise subprocess.SubprocessError(
                f"Qt worker exited with code {process.returncode}: {detail}"
            )
        if not response_path.is_file():
            raise ValueError("Qt worker did not produce a response")
        response = json.loads(response_path.read_text(encoding="utf-8"))
        if not isinstance(response, dict):
            raise ValueError("Qt worker returned an invalid response")
        return response


def _worker_environment(request: dict[str, object]) -> dict[str, str]:
    """Select a Qt platform suitable for validation or faithful previews."""

    environment = os.environ.copy()
    if "QT_QPA_PLATFORM" in environment:
        return environment
    raw_forms = request.get("forms")
    preview_requested = isinstance(raw_forms, list) and any(
        isinstance(form, dict) and bool(form.get("preview_path"))
        for form in raw_forms
    )
    if preview_requested and _native_desktop_available(environment):
        # Let Qt select qwindows/cocoa/xcb/wayland so text is painted by the
        # native font stack.  The worker marks widgets DontShowOnScreen.
        return environment
    environment["QT_QPA_PLATFORM"] = "offscreen"
    return environment


def _native_desktop_available(environment: Mapping[str, str]) -> bool:
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return True
    return bool(
        environment.get("DISPLAY") or environment.get("WAYLAND_DISPLAY")
    )


def _request_entries(
    paths: tuple[Path, ...],
    *,
    preview_dir: Path | None,
    ui_root: Path | None,
    geometry_references: Mapping[Path, FormGeometryReference] | None,
) -> list[dict[str, object]]:
    root = (ui_root or _common_root(paths)).resolve() if paths else Path.cwd()
    references = {
        path.resolve(): reference
        for path, reference in (geometry_references or {}).items()
    }
    used_preview_names: set[str] = set()
    entries: list[dict[str, object]] = []
    for path in paths:
        preview_path = None
        if preview_dir is not None:
            preview_name = _preview_name(path, root, used_preview_names)
            preview_path = str((preview_dir / preview_name).resolve())
        reference = references.get(path)
        entries.append(
            {
                "path": str(path),
                "preview_path": preview_path,
                "geometry_reference": (
                    asdict(reference) if reference is not None else None
                ),
            }
        )
    return entries


def _preview_name(path: Path, root: Path, used: set[str]) -> Path:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path(path.name)
    candidate = relative.with_suffix(".png")
    counter = 2
    while candidate.as_posix().casefold() in used:
        candidate = candidate.with_name(
            f"{relative.stem}_{counter}.png"
        )
        counter += 1
    used.add(candidate.as_posix().casefold())
    return candidate


def _common_root(paths: tuple[Path, ...]) -> Path:
    if not paths:
        return Path.cwd()
    if len(paths) == 1:
        return paths[0].parent
    return Path(os.path.commonpath([str(path.parent) for path in paths]))


def _discover_geometry_references(
    paths: tuple[Path, ...],
    *,
    ui_root: Path | None,
) -> dict[Path, FormGeometryReference]:
    if not paths:
        return {}
    roots = [ui_root.resolve()] if ui_root is not None else []
    roots.append(_common_root(paths).resolve())
    checked = set(paths)
    for root in dict.fromkeys(roots):
        references = read_geometry_report(root / "rc2ui-report.json")
        if references:
            return {
                path: reference
                for path, reference in references.items()
                if path in checked
            }
    return {}


def read_geometry_report(path: Path) -> dict[Path, FormGeometryReference]:
    """Read source-DLU geometry references from a conversion report."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw_forms = payload.get("forms") if isinstance(payload, dict) else None
    if not isinstance(raw_forms, list):
        return {}

    result: dict[Path, FormGeometryReference] = {}
    for raw_form in raw_forms:
        if not isinstance(raw_form, dict):
            continue
        output = raw_form.get("output")
        form_rect = _integer_rect(raw_form.get("default_rect_dlu"))
        layout_form_rect = _integer_rect(raw_form.get("layout_rect_dlu"))
        if form_rect is None:
            form_rect = layout_form_rect
        if layout_form_rect is None:
            layout_form_rect = form_rect
        raw_controls = raw_form.get("controls")
        if (
            not isinstance(output, str)
            or form_rect is None
            or not isinstance(raw_controls, list)
        ):
            continue
        controls: list[ControlGeometryReference] = []
        for raw_control in raw_controls:
            if not isinstance(raw_control, dict):
                continue
            name = raw_control.get("object_name")
            rect = _integer_rect(raw_control.get("rect_dlu"))
            layout_rect = _integer_rect(raw_control.get("layout_rect_dlu"))
            if rect is None:
                rect = layout_rect
            if layout_rect is None:
                layout_rect = rect
            orientation = raw_control.get("separator_orientation")
            horizontal_anchor = _anchor_reference(
                raw_control.get("horizontal_anchor")
            )
            vertical_anchor = _anchor_reference(
                raw_control.get("vertical_anchor")
            )
            if not isinstance(name, str) or rect is None:
                continue
            controls.append(
                ControlGeometryReference(
                    object_name=name,
                    rect_dlu=rect,
                    layout_rect_dlu=layout_rect,
                    separator_orientation=(
                        orientation
                        if orientation in {"horizontal", "vertical"}
                        else None
                    ),
                    qt_class=(
                        raw_control.get("qt_class")
                        if isinstance(raw_control.get("qt_class"), str)
                        else None
                    ),
                    horizontal_anchor=horizontal_anchor,
                    vertical_anchor=vertical_anchor,
                    alternative_states=_alternative_states(
                        raw_control.get("alternative_states")
                    ),
                )
            )
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = path.parent / output_path
        result[output_path.resolve()] = FormGeometryReference(
            rect_dlu=form_rect,
            controls=tuple(controls),
            layout_rect_dlu=layout_form_rect,
        )
    return result


def _integer_rect(value: object) -> tuple[int, int, int, int] | None:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or not all(isinstance(item, int) for item in value)
    ):
        return None
    return tuple(value)


def _alternative_states(value: object) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[tuple[int, int]] = []
    for state in value:
        if (
            isinstance(state, (list, tuple))
            and len(state) == 2
            and all(isinstance(item, int) for item in state)
        ):
            result.append((state[0], state[1]))
    return tuple(result)


def _anchor_reference(value: object) -> tuple[str, int] | None:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or value[0] not in {"start", "center", "end"}
        or not isinstance(value[1], int)
    ):
        return None
    return value[0], value[1]


def _diagnostic_from_wire(raw: object) -> Diagnostic:
    if not isinstance(raw, dict):
        raise ValueError("invalid diagnostic returned by Qt worker")
    return Diagnostic(
        code=str(raw["code"]),
        severity=Severity(str(raw["severity"])),
        message=str(raw["message"]),
        location=str(raw["location"]) if raw.get("location") else None,
    )


def _worker_failure(report_path: Path, error: Exception) -> QtCheckRun:
    diagnostic = Diagnostic(
        code="qt.worker-error",
        severity=Severity.ERROR,
        message=str(error),
    )
    payload = {
        "available": True,
        "forms": [],
        "diagnostics": [asdict(diagnostic)],
    }
    _write_report(report_path, payload)
    return QtCheckRun(True, 0, (diagnostic,), report_path=report_path)


def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None

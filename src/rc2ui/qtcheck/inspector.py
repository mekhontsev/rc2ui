from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from rc2ui.qtcheck.protocol import diagnostic
from rc2ui.qtcheck.runtime import RuntimeInspector
from rc2ui.qtcheck.ui_transform import prepare_ui_xml


def inspect_form(
    raw_form: object,
    *,
    index: int,
    temporary_dir: Path,
    factors: tuple[float, ...],
    font_factor: float,
    runtime: RuntimeInspector,
    uic: Any,
    font_scale: float = 1.0,
) -> dict[str, object]:
    if not isinstance(raw_form, dict):
        raise ValueError("qt-check form request must be an object")
    path = Path(str(raw_form["path"]))
    preview_path = (
        Path(str(raw_form["preview_path"]))
        if raw_form.get("preview_path")
        else None
    )
    diagnostics: list[dict[str, str]] = []
    geometry_reference = raw_form.get("geometry_reference")
    raw_reference_controls = (
        geometry_reference.get("controls")
        if isinstance(geometry_reference, dict)
        else None
    )
    result: dict[str, object] = {
        "path": str(path),
        "prepared": False,
        "compiled": False,
        "loaded": False,
        "preview_requested": preview_path is not None,
        "preview_attempted": False,
        "preview": None,
        "substitutions": [],
        "tested_sizes": [],
        "runtime_snapshots": [],
        "font_test": None,
        "source_geometry_checked": isinstance(raw_reference_controls, list),
        "source_control_count": (
            len(raw_reference_controls)
            if isinstance(raw_reference_controls, list)
            else 0
        ),
        "metrics": {},
        "diagnostics": diagnostics,
    }

    try:
        prepared = prepare_ui_xml(path.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError, ValueError) as error:
        diagnostics.append(
            diagnostic("qt.prepare-error", "error", str(error), path)
        )
        return result

    prepared_path = temporary_dir / f"form_{index}.ui"
    prepared_path.write_text(prepared.text, encoding="utf-8", newline="\n")
    result["prepared"] = True
    result["substitutions"] = [
        {
            "custom_class": item.custom_class,
            "base_class": item.base_class,
            "count": item.count,
        }
        for item in prepared.substitutions
    ]
    if prepared.substitutions:
        summary = ", ".join(
            f"{item.custom_class}->{item.base_class} ({item.count})"
            for item in prepared.substitutions
        )
        diagnostics.append(
            diagnostic(
                "qt.custom-widgets-substituted",
                "info",
                f"custom widgets substituted for runtime validation: {summary}",
                path,
            )
        )

    try:
        uic.compileUi(str(prepared_path), io.StringIO())
        result["compiled"] = True
    except Exception as error:
        diagnostics.append(
            diagnostic("qt.compile-error", "error", str(error), path)
        )
        # Python source generation and runtime loading are independent uic
        # operations.  A compile failure must not suppress a requested PNG:
        # loadUi may still be able to instantiate and render the form.

    try:
        root = uic.loadUi(str(prepared_path))
    except Exception as error:
        diagnostics.append(diagnostic("qt.load-error", "error", str(error), path))
        return result

    result["loaded"] = True
    try:
        if abs(font_scale - 1.0) >= 1e-9:
            try:
                runtime.scale_explicit_widget_fonts(root, font_scale)
            except Exception as error:
                diagnostics.append(
                    diagnostic(
                        "qt.font-scale-error",
                        "warning",
                        str(error),
                        path,
                    )
                )
        if preview_path is not None:
            result["preview_attempted"] = True
            try:
                runtime.capture_preview(root, preview_path)
                result["preview"] = str(preview_path)
            except Exception as error:
                diagnostics.append(
                    diagnostic(
                        "qt.preview-error",
                        "warning",
                        str(error),
                        path,
                    )
                )
        try:
            runtime.inspect(
                root,
                prepared,
                path=path,
                factors=factors,
                font_factor=font_factor,
                geometry_reference=geometry_reference,
                result=result,
                diagnostics=diagnostics,
            )
        except Exception as error:
            diagnostics.append(
                diagnostic("qt.runtime-error", "error", str(error), path)
            )
    finally:
        root.close()
        root.deleteLater()
        runtime.process_events()
    return result

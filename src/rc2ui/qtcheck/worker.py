from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Sequence

from rc2ui.qtcheck.bindings import load_qt_binding
from rc2ui.qtcheck.inspector import inspect_form
from rc2ui.qtcheck.protocol import write_response
from rc2ui.qtcheck.runtime import RuntimeInspector


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--response", required=True, type=Path)
    args = parser.parse_args(argv)
    request = json.loads(args.request.read_text(encoding="utf-8"))

    try:
        binding = load_qt_binding()
    except ImportError as error:
        write_response(
            args.response,
            {
                "available": False,
                "reason": str(error),
                "forms": [],
                "diagnostics": [],
            },
        )
        return 0

    QtCore = binding.QtCore
    QtWidgets = binding.QtWidgets
    uic = binding.uic

    application = QtWidgets.QApplication.instance()
    if application is None:
        application = QtWidgets.QApplication(["rc2ui-qt-check"])
    application.setQuitOnLastWindowClosed(False)
    runtime = RuntimeInspector(application, QtCore, QtWidgets)
    font_scale = float(request.get("font_scale", 1.0))
    runtime.scale_application_font(font_scale)

    factors = tuple(float(value) for value in request.get("size_factors", [1.0]))
    font_factor = float(request.get("font_factor", 2.0))
    forms: list[dict[str, object]] = []
    diagnostics: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="rc2ui-qt-") as directory_name:
        temporary_dir = Path(directory_name)
        for index, raw_form in enumerate(request.get("forms", [])):
            form = inspect_form(
                raw_form,
                index=index,
                temporary_dir=temporary_dir,
                factors=factors,
                font_factor=font_factor,
                runtime=runtime,
                uic=uic,
                font_scale=font_scale,
            )
            forms.append(form)
            diagnostics.extend(form["diagnostics"])

    write_response(
        args.response,
        {
            "available": True,
            "binding": binding.name,
            "binding_version": binding.version,
            "qt_version": binding.qt_version,
            "environment": runtime.environment_metrics(),
            "font_scale": font_scale,
            "forms": forms,
            "diagnostics": diagnostics,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

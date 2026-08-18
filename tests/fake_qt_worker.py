from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--response", required=True, type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    forms = []
    diagnostics = []
    for raw in request["forms"]:
        preview = raw.get("preview_path")
        if preview:
            preview_path = Path(preview)
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            preview_path.write_bytes(b"fake png")
        form_diagnostics = [
            {
                "code": "qt.fake-warning",
                "severity": "warning",
                "message": "fake worker warning",
                "location": raw["path"],
            }
        ]
        diagnostics.extend(form_diagnostics)
        forms.append(
            {
                "path": raw["path"],
                "prepared": True,
                "compiled": True,
                "loaded": True,
                "preview_requested": preview is not None,
                "preview_attempted": preview is not None,
                "preview": preview,
                "substitutions": [],
                "tested_sizes": [[320, 200], [480, 300]],
                "metrics": {
                    "sampleDialog": {
                        "size_hint": [320, 200],
                        "font": {"height": 14},
                    }
                },
                "diagnostics": form_diagnostics,
            }
        )
    response = {
        "available": True,
        "binding": "PyQt6",
        "binding_version": "6.fake",
        "qt_version": "6.fake",
        "font_scale": request.get("font_scale", 1.0),
        "environment": {"platform": "offscreen"},
        "forms": forms,
        "diagnostics": diagnostics,
    }
    args.response.write_text(
        json.dumps(response, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

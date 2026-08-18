from __future__ import annotations

import json
from pathlib import Path


def diagnostic(
    code: str,
    severity: str,
    message: str,
    location: Path,
) -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "location": str(location),
    }


def write_response(path: Path, response: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)

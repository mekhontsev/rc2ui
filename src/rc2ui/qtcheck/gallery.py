from __future__ import annotations

import html
import os
from pathlib import Path


def write_preview_gallery(
    preview_dir: Path,
    forms: list[dict[str, object]],
) -> Path | None:
    preview_forms = [form for form in forms if form.get("preview")]
    if not preview_forms:
        return None

    preview_dir.mkdir(parents=True, exist_ok=True)
    index = preview_dir / "index.html"
    cards = "\n".join(_form_card(index, form) for form in preview_forms)
    text = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>rc2ui Qt previews</title>
<style>
body {{ font-family: sans-serif; margin: 1.5rem; background: #f4f5f7; }}
h1 {{ margin-top: 0; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit,
        minmax(360px, 1fr)); gap: 1rem; }}
.card {{ background: white; border: 1px solid #ccd0d5;
         border-radius: 8px; padding: 1rem; }}
.card img {{ display: block; max-width: 100%; height: auto; border: 1px solid #ddd; }}
.path {{ overflow-wrap: anywhere; font-family: monospace; font-size: .85rem; }}
.errors {{ color: #a00000; }} .warnings {{ color: #8a5700; }}
</style>
</head>
<body>
<h1>rc2ui Qt previews</h1>
<div class="grid">
{cards}
</div>
</body>
</html>
"""
    temporary = index.with_name(index.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(index)
    return index


def _form_card(index: Path, form: dict[str, object]) -> str:
    preview = Path(str(form["preview"]))
    relative_preview = Path(os.path.relpath(preview, index.parent)).as_posix()
    diagnostics = form.get("diagnostics", [])
    assert isinstance(diagnostics, list)
    errors = sum(item.get("severity") == "error" for item in diagnostics)
    warnings = sum(item.get("severity") == "warning" for item in diagnostics)
    source = html.escape(str(form.get("path", "")))
    image = html.escape(relative_preview, quote=True)
    return "\n".join(
        (
            '<article class="card">',
            f'<div class="path">{source}</div>',
            (
                f'<p><span class="errors">{errors} error(s)</span>, '
                f'<span class="warnings">{warnings} warning(s)</span></p>'
            ),
            f'<a href="{image}"><img src="{image}" alt="{source}"></a>',
            "</article>",
        )
    )

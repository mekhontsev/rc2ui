from __future__ import annotations

from collections import defaultdict
from typing import Any

from rc2ui.domain.diagnostics import Diagnostic, Severity


_PREVIEW_BLOCKER_PRIORITY = (
    "qt.preview-error",
    "qt.load-error",
    "qt.prepare-error",
    "qt.compile-error",
    "qt.runtime-error",
)


def summarize_diagnostics(
    diagnostics: tuple[Diagnostic, ...],
) -> tuple[Diagnostic, ...]:
    """Keep console diagnostics compact while preserving form details in JSON."""

    groups: dict[tuple[str, str], list[Diagnostic]] = defaultdict(list)
    order: list[tuple[str, str]] = []
    for item in diagnostics:
        key = (item.severity.value, item.code)
        if key not in groups:
            order.append(key)
        groups[key].append(item)

    result: list[Diagnostic] = []
    for key in order:
        items = groups[key]
        if len(items) == 1:
            result.append(items[0])
            continue
        locations = {item.location for item in items if item.location}
        result.append(
            Diagnostic(
                code=items[0].code,
                severity=items[0].severity,
                message=(
                    f"{len(items)} occurrence(s) across {len(locations)} form(s); "
                    "see per-form diagnostics in the Qt report"
                ),
            )
        )
    return tuple(result)


def build_report_summary(
    forms: list[dict[str, object]],
) -> dict[str, object]:
    """Build an actionable aggregate without hiding per-form evidence."""

    valid_forms = [form for form in forms if isinstance(form, dict)]
    requested = [form for form in valid_forms if form.get("preview_requested")]
    failed = [form for form in requested if not form.get("preview")]
    return {
        "forms": len(valid_forms),
        "prepared": _count(valid_forms, "prepared"),
        "compiled": _count(valid_forms, "compiled"),
        "loaded": _count(valid_forms, "loaded"),
        "preview": {
            "requested": len(requested),
            "attempted": _count(requested, "preview_attempted"),
            "saved": sum(bool(form.get("preview")) for form in requested),
            "failed": len(failed),
            "failure_diagnostics": _preview_failure_groups(failed),
        },
    }


def preview_summary_diagnostic(
    summary: dict[str, object],
) -> Diagnostic | None:
    raw_preview = summary.get("preview")
    if not isinstance(raw_preview, dict):
        return None
    requested = _integer(raw_preview.get("requested"))
    if requested == 0:
        return None
    attempted = _integer(raw_preview.get("attempted"))
    saved = _integer(raw_preview.get("saved"))
    failed = _integer(raw_preview.get("failed"))
    message = (
        f"preview requested for {requested} form(s), attempted for "
        f"{attempted}, saved for {saved}"
    )
    raw_groups = raw_preview.get("failure_diagnostics")
    if failed and isinstance(raw_groups, list) and raw_groups:
        leading = raw_groups[0]
        if isinstance(leading, dict):
            code = str(leading.get("code", "unknown"))
            form_count = _integer(leading.get("forms"))
            examples = leading.get("examples")
            example = (
                str(examples[0])
                if isinstance(examples, list) and examples
                else "no example message"
            )
            message += (
                f"; leading blocker {code} affected {form_count} form(s): "
                f"{_single_line(example, limit=240)}"
            )
    return Diagnostic(
        code="qt.preview-summary",
        severity=Severity.WARNING if failed else Severity.INFO,
        message=message,
    )


def _count(forms: list[dict[str, object]], field: str) -> int:
    return sum(bool(form.get(field)) for form in forms)


def _preview_failure_groups(
    forms: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for form in forms:
        blockers = _preview_blockers(form)
        if not blockers:
            blockers = [
                {
                    "code": "qt.preview-missing",
                    "severity": "warning",
                    "message": "preview was not saved and no blocker was reported",
                }
            ]
        for blocker in blockers:
            code = str(blocker.get("code", "qt.preview-missing"))
            severity = str(blocker.get("severity", "warning"))
            key = (severity, code)
            group = grouped.setdefault(
                key,
                {
                    "code": code,
                    "severity": severity,
                    "occurrences": 0,
                    "form_paths": set(),
                    "examples": [],
                },
            )
            group["occurrences"] += 1
            group["form_paths"].add(str(form.get("path", "")))
            message = _single_line(str(blocker.get("message", "")))
            if message and message not in group["examples"]:
                if len(group["examples"]) < 3:
                    group["examples"].append(message)

    result = [
        {
            "code": group["code"],
            "severity": group["severity"],
            "occurrences": group["occurrences"],
            "forms": len(group["form_paths"]),
            "examples": group["examples"],
        }
        for group in grouped.values()
    ]
    result.sort(
        key=lambda group: (
            -_integer(group["forms"]),
            -_integer(group["occurrences"]),
            str(group["code"]),
        )
    )
    return result


def _preview_blockers(form: dict[str, object]) -> list[dict[str, object]]:
    raw_diagnostics = form.get("diagnostics")
    if not isinstance(raw_diagnostics, list):
        return []
    diagnostics = [
        item for item in raw_diagnostics if isinstance(item, dict)
    ]
    for code in _PREVIEW_BLOCKER_PRIORITY:
        matching = [item for item in diagnostics if item.get("code") == code]
        if matching:
            return matching
    return []


def _integer(value: object) -> int:
    return int(value) if isinstance(value, (bool, int)) else 0


def _single_line(value: str, *, limit: int | None = None) -> str:
    compact = " ".join(value.split())
    if limit is not None and len(compact) > limit:
        return compact[: limit - 3] + "..."
    return compact

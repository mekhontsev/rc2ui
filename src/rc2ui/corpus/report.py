from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Mapping, Sequence

from rc2ui.corpus.model import CorpusCase, CorpusCaseResult, CorpusRunResult


def write_discovery_report(cases: Sequence[CorpusCase], path: Path) -> Path:
    counts = Counter(item.kind.value for item in cases)
    payload = {
        "summary": {
            "sources": len(cases),
            "runnable": sum(item.runnable for item in cases),
            "kinds": dict(sorted(counts.items())),
        },
        "cases": [_case_payload(item) for item in cases],
    }
    _write_json(path, payload)
    return path


def write_run_report(
    output_dir: Path,
    results: Sequence[CorpusCaseResult],
    *,
    compiler: str,
) -> CorpusRunResult:
    report_path = output_dir / "corpus-report.json"
    markdown_path = output_dir / "corpus-report.md"
    statuses = Counter(item.status.value for item in results)
    diagnostic_codes = Counter(
        (item.severity, item.code)
        for result in results
        for item in result.diagnostics
    )
    issue_codes = Counter(
        code for result in results for code in result.issue_codes
    )
    payload = {
        "summary": {
            "cases": len(results),
            "forms": sum(item.forms for item in results),
            "errors": sum(item.errors for item in results),
            "warnings": sum(item.warnings for item in results),
            "duration_seconds": round(
                sum(item.duration_seconds for item in results), 3
            ),
            "statuses": dict(sorted(statuses.items())),
        },
        "compiler": compiler,
        "projects": _project_summaries(results),
        "diagnostic_codes": [
            {"severity": severity, "code": code, "count": count}
            for (severity, code), count in sorted(
                diagnostic_codes.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        "issue_codes": [
            {"code": code, "count": count}
            for code, count in sorted(
                issue_codes.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        "cases": [_result_payload(item, output_dir) for item in results],
        "representatives": _representatives(results, output_dir),
    }
    _write_json(report_path, payload)
    write_markdown_report(payload, markdown_path)
    return CorpusRunResult(
        output_dir=output_dir,
        cases=tuple(results),
        report_path=report_path,
        markdown_path=markdown_path,
    )


def rebuild_markdown_report(report_path: Path, output: Path | None = None) -> Path:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    markdown_path = output or report_path.with_suffix(".md")
    write_markdown_report(payload, markdown_path)
    return markdown_path


def write_markdown_report(payload: Mapping[str, object], path: Path) -> None:
    summary = _mapping(payload.get("summary"))
    statuses = _mapping(summary.get("statuses"))
    lines = [
        "# rc2ui corpus report",
        "",
        f"- Cases: {summary.get('cases', 0)}",
        f"- Generated forms: {summary.get('forms', 0)}",
        f"- Errors: {summary.get('errors', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        f"- Aggregate case time: {summary.get('duration_seconds', 0)} s",
        "",
        "## Statuses",
        "",
        "| Status | Cases |",
        "| --- | ---: |",
    ]
    lines.extend(f"| `{name}` | {count} |" for name, count in statuses.items())
    lines.extend(
        [
            "",
            "## Projects",
            "",
            "| Project | Cases | Passed | Compile failed | Convert failed | "
            "No forms | Forms | Warnings |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in _project_rows(payload):
        lines.append(
            f"| `{_markdown(item.get('project', ''))}` | "
            f"{item.get('cases', 0)} | {item.get('passed', 0)} | "
            f"{item.get('compile_failed', 0)} | "
            f"{item.get('convert_failed', 0)} | "
            f"{item.get('no_forms', 0)} | {item.get('forms', 0)} | "
            f"{item.get('warnings', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Most frequent diagnostics",
            "",
            "| Severity | Code | Count |",
            "| --- | --- | ---: |",
        ]
    )
    diagnostics = payload.get("diagnostic_codes", [])
    if isinstance(diagnostics, list):
        for item in diagnostics[:30]:
            row = _mapping(item)
            lines.append(
                f"| {row.get('severity', '')} | `{row.get('code', '')}` | "
                f"{row.get('count', 0)} |"
            )
    lines.extend(
        [
            "",
            "## Smallest representative cases",
            "",
            "One smallest source is retained for every issue code. These are "
            "triage candidates, not automatically reduced or copied fixtures.",
            "",
            "| Issue | Status | Bytes | Source | Case artifacts |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    representatives = payload.get("representatives", [])
    if isinstance(representatives, list):
        for item in representatives:
            row = _mapping(item)
            lines.append(
                f"| `{row.get('issue_code', '')}` | `{row.get('status', '')}` | "
                f"{row.get('byte_size', 0)} | `{_markdown(row.get('source', ''))}` | "
                f"`{_markdown(row.get('case_dir', ''))}` |"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def _case_payload(case: CorpusCase) -> dict[str, object]:
    return {
        "case_id": case.case_id,
        "project_root": str(case.project_root),
        "source": str(case.source),
        "relative_source": case.relative_source.as_posix(),
        "kind": case.kind.value,
        "runnable": case.runnable,
        "byte_size": case.byte_size,
        "direct_dialogs": case.direct_dialogs,
        "reachable_dialogs": case.reachable_dialogs,
        "includes": [item.as_posix() for item in case.includes],
        "included_by": [item.as_posix() for item in case.included_by],
        "languages": list(case.languages),
        "read_error": case.read_error,
        "preferred_language": case.preferred_language,
        "compiler_codepage": case.compiler_codepage,
    }


def _result_payload(item: CorpusCaseResult, output_dir: Path) -> dict[str, object]:
    return {
        **_case_payload(item.case),
        "status": item.status.value,
        "duration_seconds": round(item.duration_seconds, 3),
        "forms": item.forms,
        "errors": item.errors,
        "warnings": item.warnings,
        "diagnostics": [asdict(diagnostic) for diagnostic in item.diagnostics],
        "issue_codes": list(item.issue_codes),
        "case_dir": _relative_or_absolute(item.case_dir, output_dir),
        "compile_command": list(item.compile_command),
        "convert_command": list(item.convert_command),
        "compile_returncode": item.compile_returncode,
        "convert_returncode": item.convert_returncode,
        "message": item.message,
    }


def _representatives(
    results: Sequence[CorpusCaseResult], output_dir: Path
) -> list[dict[str, object]]:
    candidates: dict[str, CorpusCaseResult] = {}
    for result in results:
        for code in result.issue_codes:
            current = candidates.get(code)
            if current is None or (
                result.case.byte_size,
                result.case.relative_source.as_posix(),
            ) < (
                current.case.byte_size,
                current.case.relative_source.as_posix(),
            ):
                candidates[code] = result
    return [
        {
            "issue_code": code,
            "status": result.status.value,
            "byte_size": result.case.byte_size,
            "source": str(result.case.source),
            "case_dir": _relative_or_absolute(result.case_dir, output_dir),
        }
        for code, result in sorted(candidates.items())
    ]


def _project_summaries(
    results: Sequence[CorpusCaseResult],
) -> list[dict[str, object]]:
    counters: dict[str, Counter[str]] = {}
    for result in results:
        name = result.case.project_root.name
        counter = counters.setdefault(name, Counter())
        counter["cases"] += 1
        counter[result.status.value.replace("-", "_")] += 1
        counter["forms"] += result.forms
        counter["errors"] += result.errors
        counter["warnings"] += result.warnings
    return [
        {"project": name, **dict(sorted(counter.items()))}
        for name, counter in sorted(counters.items())
    ]


def _project_rows(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    projects = payload.get("projects")
    if isinstance(projects, list):
        return [_mapping(item) for item in projects]
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return []
    counters: dict[str, Counter[str]] = {}
    for raw_case in cases:
        case = _mapping(raw_case)
        root = Path(str(case.get("project_root", "unknown")))
        counter = counters.setdefault(root.name or "unknown", Counter())
        counter["cases"] += 1
        counter[str(case.get("status", "unknown")).replace("-", "_")] += 1
        for name in ("forms", "errors", "warnings"):
            value = case.get(name)
            if isinstance(value, int):
                counter[name] += value
    return [
        {"project": name, **dict(sorted(counter.items()))}
        for name, counter in sorted(counters.items())
    ]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _markdown(value: object) -> str:
    return str(value).replace("|", "\\|").replace("`", "\\`")

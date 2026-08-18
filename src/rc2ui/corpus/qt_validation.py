from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from rc2ui.qtcheck.model import FormGeometryReference
from rc2ui.qtcheck.runner import read_geometry_report, run_qt_checks
from rc2ui.corpus.output_lock import exclusive_output_lock


@dataclass(frozen=True, slots=True)
class CorpusQtValidationResult:
    output_dir: Path
    total_forms: int
    checked_forms: int
    total_batches: int
    completed_batches: int
    errors: int
    warnings: int
    report_path: Path
    markdown_path: Path

    @property
    def pending_batches(self) -> int:
        return self.total_batches - self.completed_batches


@dataclass(frozen=True, slots=True)
class _Form:
    path: Path
    reference: FormGeometryReference
    fingerprint: str


def validate_corpus_qt(
    corpus_run: Path,
    output_dir: Path,
    *,
    batch_size: int = 20,
    resume: bool = False,
    max_new_batches: int | None = None,
    worker_module: str = "rc2ui.qtcheck.worker",
    on_batch: Callable[[int, int, int, int], None] | None = None,
) -> CorpusQtValidationResult:
    """Validate every generated corpus form in resumable isolated Qt shards."""

    run = corpus_run.resolve()
    forms = _discover_forms(run)
    if not forms:
        raise ValueError(f"corpus run contains no generated forms: {run}")
    output = output_dir.resolve()
    batches = tuple(
        forms[index : index + batch_size]
        for index in range(0, len(forms), batch_size)
    )
    manifest = _manifest(run, forms, batch_size, len(batches))
    _prepare_output(output, manifest, resume=resume)

    with exclusive_output_lock(
        output,
        filename=".rc2ui-qt.lock",
        description="Qt corpus output",
    ):
        completed = 0
        started = 0
        for batch_index, batch in enumerate(batches, 1):
            report_path = output / "batches" / f"batch-{batch_index:04d}.json"
            checkpoint_path = (
                output / "batches" / f"batch-{batch_index:04d}.done.json"
            )
            expected = _batch_identity(batch)
            if _valid_checkpoint(checkpoint_path, report_path, expected):
                completed += 1
                continue
            if max_new_batches is not None and started >= max_new_batches:
                continue
            started += 1
            result = run_qt_checks(
                tuple(item.path for item in batch),
                report_path=report_path,
                required=True,
                ui_root=run,
                geometry_references={item.path: item.reference for item in batch},
                worker_module=worker_module,
            )
            _write_json(
                checkpoint_path,
                {
                    "forms": list(expected),
                    "checked_forms": result.checked_forms,
                    "available": result.available,
                },
            )
            completed += 1
            errors, warnings = _batch_counts(report_path)
            if on_batch is not None:
                on_batch(completed, len(batches), errors, warnings)

        payload = _aggregate(output, manifest, batches)
        report_path = output / "qt-corpus-report.json"
        markdown_path = output / "qt-corpus-report.md"
        _write_json(report_path, payload)
        _write_markdown(markdown_path, payload)
    summary = payload["summary"]
    assert isinstance(summary, dict)
    return CorpusQtValidationResult(
        output_dir=output,
        total_forms=int(summary["forms"]),
        checked_forms=int(summary["checked_forms"]),
        total_batches=int(summary["batches"]),
        completed_batches=int(summary["completed_batches"]),
        errors=int(summary["errors"]),
        warnings=int(summary["warnings"]),
        report_path=report_path,
        markdown_path=markdown_path,
    )


def _discover_forms(run: Path) -> tuple[_Form, ...]:
    result: dict[Path, _Form] = {}
    cases = run / "cases"
    for report in sorted(cases.glob("*/generated/rc2ui-report.json"), key=str):
        for path, reference in read_geometry_report(report).items():
            if path.is_file():
                resolved = path.resolve()
                result[resolved] = _Form(
                    resolved,
                    reference,
                    _form_fingerprint(resolved, reference),
                )
    return tuple(
        result[path]
        for path in sorted(result, key=lambda item: str(item).casefold())
    )


def _form_fingerprint(path: Path, reference: FormGeometryReference) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    digest.update(b"\0")
    digest.update(
        json.dumps(asdict(reference), sort_keys=True, separators=(",", ":")).encode()
    )
    return digest.hexdigest()


def _batch_identity(batch: tuple[_Form, ...]) -> tuple[dict[str, str], ...]:
    return tuple(
        {"path": str(item.path), "fingerprint": item.fingerprint}
        for item in batch
    )


def _manifest(
    run: Path,
    forms: tuple[_Form, ...],
    batch_size: int,
    batches: int,
) -> dict[str, object]:
    paths = tuple(str(item.path) for item in forms)
    digest = hashlib.sha256("\n".join(paths).encode()).hexdigest()
    return {
        "corpus_run": str(run),
        "batch_size": batch_size,
        "forms": len(forms),
        "batches": batches,
        "form_set_sha256": digest,
    }


def _prepare_output(
    output: Path,
    manifest: dict[str, object],
    *,
    resume: bool,
) -> None:
    manifest_path = output / "qt-corpus-manifest.json"
    if resume:
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"cannot resume Qt corpus output without a valid manifest: {output}"
            ) from error
        if existing != manifest:
            raise ValueError(
                "Qt corpus inputs or --batch-size differ from the existing run"
            )
        (output / "batches").mkdir(exist_ok=True)
        return
    if output.exists() and any(output.iterdir()):
        raise ValueError(
            f"Qt corpus output directory is not empty: {output}; choose a new directory"
        )
    (output / "batches").mkdir(parents=True, exist_ok=True)
    _write_json(manifest_path, manifest)


def _valid_checkpoint(
    checkpoint: Path,
    report: Path,
    expected: tuple[dict[str, str], ...],
) -> bool:
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        return payload.get("forms") == list(expected) and report.is_file()
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def _aggregate(
    output: Path,
    manifest: dict[str, object],
    batches: tuple[tuple[_Form, ...], ...],
) -> dict[str, object]:
    diagnostics: list[dict[str, object]] = []
    issue_counts: Counter[tuple[str, str]] = Counter()
    checked = 0
    compiled = 0
    source_checked = 0
    completed = 0
    forms_with_errors: set[str] = set()
    forms_with_warnings: set[str] = set()
    binding = None
    binding_version = None
    qt_version = None
    for batch_index, batch in enumerate(batches, 1):
        report = output / "batches" / f"batch-{batch_index:04d}.json"
        checkpoint = output / "batches" / f"batch-{batch_index:04d}.done.json"
        expected = _batch_identity(batch)
        if not _valid_checkpoint(checkpoint, report, expected):
            continue
        completed += 1
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        binding = binding or payload.get("binding")
        binding_version = binding_version or payload.get("binding_version")
        qt_version = qt_version or payload.get("qt_version")
        raw_forms = payload.get("forms", [])
        form_diagnostic_count = 0
        if isinstance(raw_forms, list):
            for form in raw_forms:
                if not isinstance(form, dict):
                    continue
                path = str(form.get("path", ""))
                checked += int(bool(form.get("loaded")))
                compiled += int(bool(form.get("compiled")))
                source_checked += int(bool(form.get("source_geometry_checked")))
                raw_diagnostics = form.get("diagnostics", [])
                if not isinstance(raw_diagnostics, list):
                    continue
                form_diagnostic_count += len(raw_diagnostics)
                for diagnostic in raw_diagnostics:
                    if not isinstance(diagnostic, dict):
                        continue
                    severity = str(diagnostic.get("severity", "unknown"))
                    code = str(diagnostic.get("code", "unknown"))
                    issue_counts[(severity, code)] += 1
                    diagnostics.append(diagnostic)
                    if severity == "error":
                        forms_with_errors.add(path)
                    elif severity == "warning":
                        forms_with_warnings.add(path)
        if form_diagnostic_count == 0:
            raw_diagnostics = payload.get("diagnostics", [])
            if isinstance(raw_diagnostics, list):
                for diagnostic in raw_diagnostics:
                    if not isinstance(diagnostic, dict):
                        continue
                    severity = str(diagnostic.get("severity", "unknown"))
                    code = str(diagnostic.get("code", "unknown"))
                    issue_counts[(severity, code)] += 1
                    diagnostics.append(diagnostic)

    errors = sum(
        count for (severity, _), count in issue_counts.items() if severity == "error"
    )
    warnings = sum(
        count for (severity, _), count in issue_counts.items() if severity == "warning"
    )
    return {
        "summary": {
            **manifest,
            "completed_batches": completed,
            "pending_batches": int(manifest["batches"]) - completed,
            "checked_forms": checked,
            "compiled_forms": compiled,
            "source_geometry_checked_forms": source_checked,
            "errors": errors,
            "warnings": warnings,
            "forms_with_errors": len(forms_with_errors),
            "forms_with_warnings": len(forms_with_warnings),
            "binding": binding,
            "binding_version": binding_version,
            "qt_version": qt_version,
        },
        "issues": [
            {"severity": severity, "code": code, "count": count}
            for (severity, code), count in sorted(
                issue_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "representatives": diagnostics[:200],
    }


def _batch_counts(report: Path) -> tuple[int, int]:
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1, 0
    errors = 0
    warnings = 0
    for form in payload.get("forms", []):
        if not isinstance(form, dict):
            continue
        for item in form.get("diagnostics", []):
            if not isinstance(item, dict):
                continue
            errors += item.get("severity") == "error"
            warnings += item.get("severity") == "warning"
    return errors, warnings


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _write_markdown(path: Path, payload: dict[str, object]) -> None:
    summary = payload["summary"]
    assert isinstance(summary, dict)
    issues = payload["issues"]
    assert isinstance(issues, list)
    lines = [
        "# rc2ui corpus Qt validation",
        "",
        f"- Forms: {summary['checked_forms']} / {summary['forms']}",
        f"- Batches: {summary['completed_batches']} / {summary['batches']}",
        f"- Source geometry checked: {summary['source_geometry_checked_forms']}",
        f"- Error occurrences: {summary['errors']} in {summary['forms_with_errors']} form(s)",
        f"- Warning occurrences: {summary['warnings']} in {summary['forms_with_warnings']} form(s)",
        f"- Runtime: {summary.get('binding') or 'unknown'} {summary.get('binding_version') or ''}; Qt {summary.get('qt_version') or 'unknown'}",
        "",
        "## Issues",
        "",
        "| Severity | Code | Count |",
        "| --- | --- | ---: |",
    ]
    for issue in issues:
        if isinstance(issue, dict):
            lines.append(
                f"| {issue['severity']} | `{issue['code']}` | {issue['count']} |"
            )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")

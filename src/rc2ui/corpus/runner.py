from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable, Sequence

from rc2ui.application.batch import BatchConverter
from rc2ui.application.models import ConversionRequest, InputGroup
from rc2ui.corpus.assets import prepare_asset_overlay
from rc2ui.corpus.compiler import compile_resource
from rc2ui.corpus.model import (
    CorpusCase,
    CorpusCaseResult,
    CorpusCaseStatus,
    CorpusDiagnosticCount,
    CorpusRunResult,
)
from rc2ui.corpus.output_lock import exclusive_output_lock
from rc2ui.corpus.report import write_run_report
from rc2ui.qtcheck.model import QtCheckMode


def run_corpus(
    cases: Sequence[CorpusCase],
    output_dir: Path,
    *,
    compiler: str,
    jobs: int = 1,
    timeout_seconds: float = 60.0,
    include_paths: tuple[Path, ...] = (),
    defines: tuple[tuple[str, int], ...] = (),
    source_encoding: str = "cp1251",
    compiler_codepage: int | None = None,
    default_language: int | None = None,
    qt_check: str = "off",
    resume: bool = False,
    retry_failed: bool = False,
    max_new_cases: int | None = None,
    converter_mode: str = "subprocess",
    on_result: Callable[[int, int, CorpusCaseResult], None] | None = None,
) -> CorpusRunResult:
    output = output_dir.resolve()
    _prepare_output(output, resume=resume)
    with exclusive_output_lock(
        output,
        filename=".rc2ui-corpus.lock",
        description="corpus output",
    ):
        return _run_corpus_prepared(
            cases,
            output,
            compiler=compiler,
            jobs=jobs,
            timeout_seconds=timeout_seconds,
            include_paths=include_paths,
            defines=defines,
            source_encoding=source_encoding,
            compiler_codepage=compiler_codepage,
            default_language=default_language,
            qt_check=qt_check,
            resume=resume,
            retry_failed=retry_failed,
            max_new_cases=max_new_cases,
            converter_mode=converter_mode,
            on_result=on_result,
        )


def _run_corpus_prepared(
    cases: Sequence[CorpusCase],
    output: Path,
    *,
    compiler: str,
    jobs: int,
    timeout_seconds: float,
    include_paths: tuple[Path, ...],
    defines: tuple[tuple[str, int], ...],
    source_encoding: str,
    compiler_codepage: int | None,
    default_language: int | None,
    qt_check: str,
    resume: bool,
    retry_failed: bool,
    max_new_cases: int | None,
    converter_mode: str,
    on_result: Callable[[int, int, CorpusCaseResult], None] | None,
) -> CorpusRunResult:
    runnable = tuple(item for item in cases if item.runnable)
    completed: list[CorpusCaseResult] = []
    pending: list[CorpusCase] = []
    indexed = _read_checkpoint_index(output) if resume else {}
    existing_case_dirs = (
        {
            item.name
            for item in (output / "cases").iterdir()
            if item.is_dir()
        }
        if resume
        else set()
    )
    for case in runnable:
        payload = indexed.get(case.case_id)
        if payload is not None:
            restored = _result_from_payload(
                case,
                output / "cases" / case.case_id,
                payload,
            )
        elif resume and case.case_id in existing_case_dirs:
            restored = _restore_case(case, output)
        else:
            restored = None
        if (
            restored is not None
            and retry_failed
            and restored.status is not CorpusCaseStatus.PASSED
        ):
            _archive_case_directory(
                output / "cases" / case.case_id,
                output / "retried",
            )
            restored = None
        if restored is None:
            pending.append(case)
        else:
            completed.append(restored)
    if max_new_cases is not None:
        pending = pending[:max_new_cases]
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
        iterator = iter(pending)
        futures: dict[Future[CorpusCaseResult], CorpusCase] = {}

        def submit_next() -> bool:
            case = next(iterator, None)
            if case is None:
                return False
            future = executor.submit(
                _run_case,
                case,
                output,
                compiler=compiler,
                timeout_seconds=timeout_seconds,
                include_paths=include_paths,
                defines=defines,
                source_encoding=source_encoding,
                compiler_codepage=compiler_codepage,
                default_language=default_language,
                qt_check=qt_check,
                converter_mode=converter_mode,
            )
            futures[future] = case
            return True

        for _ in range(max(1, jobs)):
            submit_next()
        while futures:
            done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
            for future in done:
                case = futures.pop(future)
                try:
                    result = future.result()
                except Exception as error:  # Keep a large corpus running.
                    case_dir = output / "cases" / case.case_id
                    case_dir.mkdir(parents=True, exist_ok=True)
                    result = CorpusCaseResult(
                        case=case,
                        status=CorpusCaseStatus.INTERNAL_ERROR,
                        duration_seconds=0.0,
                        forms=0,
                        errors=1,
                        warnings=0,
                        diagnostics=(),
                        issue_codes=("runner.internal-error",),
                        case_dir=case_dir,
                        compile_command=(),
                        message=f"{type(error).__name__}: {error}",
                    )
                completed.append(result)
                _write_case_checkpoint(result)
                if on_result is not None:
                    on_result(
                        len(completed),
                        len(runnable),
                        result,
                    )
                submit_next()
    results = tuple(
        sorted(
            completed,
            key=lambda item: (
                item.case.project_root.name.casefold(),
                item.case.relative_source.as_posix().casefold(),
            ),
        )
    )
    return write_run_report(output, results, compiler=compiler)


def _run_case(
    case: CorpusCase,
    output_dir: Path,
    *,
    compiler: str,
    timeout_seconds: float,
    include_paths: tuple[Path, ...],
    defines: tuple[tuple[str, int], ...],
    source_encoding: str,
    compiler_codepage: int | None,
    default_language: int | None,
    qt_check: str,
    converter_mode: str,
) -> CorpusCaseResult:
    started = time.monotonic()
    case_dir = output_dir / "cases" / case.case_id
    case_dir.mkdir(parents=True, exist_ok=False)
    resource = case_dir / "compiled.res"
    compile_paths = tuple(
        dict.fromkeys(
            (
                case.source.parent,
                case.project_root,
                *(path.resolve() for path in include_paths),
            )
        )
    )
    compile_working_directory = prepare_asset_overlay(
        case.source,
        case.project_root,
        case_dir / "compile-tree",
        fallback_encoding=source_encoding,
    )
    compile_paths = tuple(
        dict.fromkeys((compile_working_directory, *compile_paths))
    )
    try:
        compiled = compile_resource(
            compiler,
            case.source,
            resource,
            include_paths=compile_paths,
            defines=defines,
            codepage=(
                compiler_codepage
                if compiler_codepage is not None
                else case.compiler_codepage
            ),
            timeout_seconds=timeout_seconds,
            working_directory=compile_working_directory,
        )
    except subprocess.TimeoutExpired as error:
        _write_text(case_dir / "compile.stdout.txt", _timeout_stream(error.stdout))
        _write_text(case_dir / "compile.stderr.txt", _timeout_stream(error.stderr))
        return _case_result(
            case,
            CorpusCaseStatus.TIMED_OUT,
            started,
            case_dir,
            compile_command=tuple(str(item) for item in error.cmd),
            errors=1,
            issue_codes=("compiler.timeout",),
            message=f"resource compiler exceeded {timeout_seconds:g} seconds",
        )
    _write_text(case_dir / "compile.stdout.txt", compiled.stdout)
    _write_text(case_dir / "compile.stderr.txt", compiled.stderr)
    if compiled.returncode != 0:
        issue = _compiler_issue(compiled.stderr or compiled.stdout)
        return _case_result(
            case,
            CorpusCaseStatus.COMPILE_FAILED,
            started,
            case_dir,
            compile_command=compiled.command,
            compile_returncode=compiled.returncode,
            errors=1,
            issue_codes=(issue,),
            message=_last_nonempty_line(compiled.stderr or compiled.stdout),
        )

    generated = case_dir / "generated"
    effective_language = (
        default_language
        if default_language is not None
        else case.preferred_language
        if case.preferred_language is not None
        else 1033
    )
    command = [
        sys.executable,
        "-m",
        "rc2ui",
        "convert",
        "--project-root",
        str(case.project_root),
        "--output",
        str(generated),
        "--rc-encoding",
        source_encoding,
        "--default-language",
        str(effective_language),
        "--qt-check",
        qt_check,
    ]
    for path in compile_paths:
        command.extend(("--include", str(path)))
    for name, value in defines:
        command.extend(("--define", f"{name}={value}"))
    command.extend((str(case.source), str(resource)))
    if converter_mode == "in-process":
        in_process = BatchConverter().convert(
            ConversionRequest(
                project_root=case.project_root,
                output_dir=generated,
                input_groups=(
                    InputGroup(
                        rc_files=(case.source,),
                        resource_files=(resource,),
                    ),
                ),
                include_paths=compile_paths,
                defines=defines,
                rc_encoding=source_encoding,
                default_language=effective_language,
                qt_check=QtCheckMode.OFF,
            )
        )
        convert_command = ("in-process:rc2ui.application.BatchConverter",)
        convert_returncode = int(bool(in_process.error_count))
        convert_stdout = ""
        convert_stderr = "\n".join(
            f"{item.severity}: {item.message} [{item.code}]"
            for item in in_process.diagnostics
        )
    else:
        try:
            converted = subprocess.run(
                command,
                cwd=case.project_root,
                check=False,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout_seconds,
                env=_subprocess_environment(),
            )
        except subprocess.TimeoutExpired as error:
            _write_text(case_dir / "convert.stdout.txt", _timeout_stream(error.stdout))
            _write_text(case_dir / "convert.stderr.txt", _timeout_stream(error.stderr))
            return _case_result(
                case,
                CorpusCaseStatus.TIMED_OUT,
                started,
                case_dir,
                compile_command=compiled.command,
                convert_command=tuple(command),
                compile_returncode=compiled.returncode,
                errors=1,
                issue_codes=("converter.timeout",),
                message=f"converter exceeded {timeout_seconds:g} seconds",
            )
        convert_command = tuple(command)
        convert_returncode = converted.returncode
        convert_stdout = converted.stdout
        convert_stderr = converted.stderr
    _write_text(case_dir / "convert.stdout.txt", convert_stdout)
    _write_text(case_dir / "convert.stderr.txt", convert_stderr)
    report = _read_conversion_report(generated / "rc2ui-report.json")
    diagnostics = tuple(
        CorpusDiagnosticCount(
            severity=str(item.get("severity", "unknown")),
            code=str(item.get("code", "unknown")),
        )
        for item in report.get("diagnostics", [])
        if isinstance(item, dict)
    )
    summary = report.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    forms = _integer(summary.get("forms"))
    errors = _integer(summary.get("errors"))
    warnings = _integer(summary.get("warnings"))
    codes = tuple(
        sorted(
            {
                f"rc2ui.{item.code}"
                for item in diagnostics
                if item.severity in {"error", "warning"}
            }
        )
    )
    if convert_returncode != 0 or errors:
        status = CorpusCaseStatus.CONVERT_FAILED
        issue_codes = codes or ("converter.failed",)
    elif not forms:
        status = CorpusCaseStatus.NO_FORMS
        issue_codes = codes or ("converter.no-forms",)
    else:
        status = CorpusCaseStatus.PASSED
        issue_codes = codes
    return _case_result(
        case,
        status,
        started,
        case_dir,
        compile_command=compiled.command,
        convert_command=convert_command,
        compile_returncode=compiled.returncode,
        convert_returncode=convert_returncode,
        forms=forms,
        errors=errors,
        warnings=warnings,
        diagnostics=diagnostics,
        issue_codes=issue_codes,
        message=(
            _last_nonempty_line(convert_stderr or convert_stdout)
            if status is not CorpusCaseStatus.PASSED
            else None
        ),
    )


def _case_result(
    case: CorpusCase,
    status: CorpusCaseStatus,
    started: float,
    case_dir: Path,
    *,
    compile_command: tuple[str, ...],
    convert_command: tuple[str, ...] = (),
    compile_returncode: int | None = None,
    convert_returncode: int | None = None,
    forms: int = 0,
    errors: int = 0,
    warnings: int = 0,
    diagnostics: tuple[CorpusDiagnosticCount, ...] = (),
    issue_codes: tuple[str, ...] = (),
    message: str | None = None,
) -> CorpusCaseResult:
    return CorpusCaseResult(
        case=case,
        status=status,
        duration_seconds=round(time.monotonic() - started, 3),
        forms=forms,
        errors=errors,
        warnings=warnings,
        diagnostics=diagnostics,
        issue_codes=issue_codes,
        case_dir=case_dir,
        compile_command=compile_command,
        convert_command=convert_command,
        compile_returncode=compile_returncode,
        convert_returncode=convert_returncode,
        message=message,
    )


def _prepare_output(path: Path, *, resume: bool = False) -> None:
    if resume:
        if not (path / "cases").is_dir():
            raise ValueError(
                f"cannot resume corpus output without a cases directory: {path}"
            )
        return
    if path.exists() and any(path.iterdir()):
        raise ValueError(
            f"corpus output directory is not empty: {path}; choose a new directory"
        )
    path.mkdir(parents=True, exist_ok=True)
    (path / "cases").mkdir()


def _write_case_checkpoint(result: CorpusCaseResult) -> None:
    payload = _checkpoint_payload(result)
    path = result.case_dir / "case-result.json"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)
    index_path = result.case_dir.parent.parent / "case-results.jsonl"
    with index_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(
                {"case_id": result.case.case_id, **payload},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )


def _checkpoint_payload(result: CorpusCaseResult) -> dict[str, object]:
    return {
        "status": result.status.value,
        "duration_seconds": result.duration_seconds,
        "forms": result.forms,
        "errors": result.errors,
        "warnings": result.warnings,
        "diagnostics": [
            {"severity": item.severity, "code": item.code}
            for item in result.diagnostics
        ],
        "issue_codes": list(result.issue_codes),
        "compile_command": list(result.compile_command),
        "convert_command": list(result.convert_command),
        "compile_returncode": result.compile_returncode,
        "convert_returncode": result.convert_returncode,
        "message": result.message,
    }


def _read_checkpoint_index(output: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    path = output / "case-results.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return result
    except (OSError, UnicodeError):
        return result
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        case_id = payload.pop("case_id", None)
        if isinstance(case_id, str):
            result[case_id] = payload
    return result


def _restore_case(case: CorpusCase, output: Path) -> CorpusCaseResult | None:
    case_dir = output / "cases" / case.case_id
    if not case_dir.exists():
        return None
    checkpoint = _read_case_checkpoint(case, case_dir)
    if checkpoint is not None:
        _write_case_checkpoint(checkpoint)
        return checkpoint
    recovered = _recover_case_artifacts(case, case_dir)
    if recovered is not None:
        _write_case_checkpoint(recovered)
        return recovered
    _archive_case_directory(case_dir, output / "incomplete")
    return None


def _archive_case_directory(case_dir: Path, archive_root: Path) -> None:
    if not case_dir.exists():
        return
    archive_root.mkdir(exist_ok=True)
    destination = archive_root / case_dir.name
    counter = 2
    while destination.exists():
        destination = archive_root / f"{case_dir.name}-{counter}"
        counter += 1
    shutil.move(str(case_dir), str(destination))


def _read_case_checkpoint(
    case: CorpusCase,
    case_dir: Path,
) -> CorpusCaseResult | None:
    try:
        payload = json.loads((case_dir / "case-result.json").read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return _result_from_payload(case, case_dir, payload)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _result_from_payload(
    case: CorpusCase,
    case_dir: Path,
    payload: dict[str, object],
) -> CorpusCaseResult | None:
    try:
        status = CorpusCaseStatus(payload["status"])
        raw_diagnostics = payload.get("diagnostics", [])
        if not isinstance(raw_diagnostics, list):
            return None
        diagnostics = tuple(
            CorpusDiagnosticCount(str(item["severity"]), str(item["code"]))
            for item in raw_diagnostics
            if isinstance(item, dict)
        )
        return CorpusCaseResult(
            case=case,
            status=status,
            duration_seconds=float(payload.get("duration_seconds", 0.0)),
            forms=_integer(payload.get("forms")),
            errors=_integer(payload.get("errors")),
            warnings=_integer(payload.get("warnings")),
            diagnostics=diagnostics,
            issue_codes=tuple(str(item) for item in payload.get("issue_codes", [])),
            case_dir=case_dir,
            compile_command=tuple(str(item) for item in payload.get("compile_command", [])),
            convert_command=tuple(str(item) for item in payload.get("convert_command", [])),
            compile_returncode=payload.get("compile_returncode"),
            convert_returncode=payload.get("convert_returncode"),
            message=payload.get("message") if isinstance(payload.get("message"), str) else None,
        )
    except (ValueError, TypeError, KeyError):
        return None


def _recover_case_artifacts(
    case: CorpusCase,
    case_dir: Path,
) -> CorpusCaseResult | None:
    resource = case_dir / "compiled.res"
    report_path = case_dir / "generated" / "rc2ui-report.json"
    if resource.is_file() and report_path.is_file():
        report = _read_conversion_report(report_path)
        summary = report.get("summary", {})
        if not isinstance(summary, dict):
            return None
        diagnostics = tuple(
            CorpusDiagnosticCount(
                severity=str(item.get("severity", "unknown")),
                code=str(item.get("code", "unknown")),
            )
            for item in report.get("diagnostics", [])
            if isinstance(item, dict)
        )
        forms = _integer(summary.get("forms"))
        errors = _integer(summary.get("errors"))
        warnings = _integer(summary.get("warnings"))
        codes = tuple(
            sorted(
                f"rc2ui.{item.code}"
                for item in diagnostics
                if item.severity in {"error", "warning"}
            )
        )
        status = (
            CorpusCaseStatus.CONVERT_FAILED
            if errors
            else CorpusCaseStatus.PASSED
            if forms
            else CorpusCaseStatus.NO_FORMS
        )
        return _case_result(
            case,
            status,
            time.monotonic(),
            case_dir,
            compile_command=(),
            convert_command=(),
            compile_returncode=0,
            convert_returncode=1 if errors else 0,
            forms=forms,
            errors=errors,
            warnings=warnings,
            diagnostics=diagnostics,
            issue_codes=codes or (() if forms else ("converter.no-forms",)),
        )
    stderr_path = case_dir / "compile.stderr.txt"
    if not resource.exists() and stderr_path.is_file():
        message = stderr_path.read_text(encoding="utf-8", errors="replace")
        if message.strip():
            return _case_result(
                case,
                CorpusCaseStatus.COMPILE_FAILED,
                time.monotonic(),
                case_dir,
                compile_command=(),
                compile_returncode=1,
                errors=1,
                issue_codes=(_compiler_issue(message),),
                message=_last_nonempty_line(message),
            )
    return None


def _compiler_issue(message: str) -> str:
    lowered = message.casefold()
    patterns = (
        (
            "compiler.include-not-found",
            ("file not found", "no such file", "could not find include"),
        ),
        ("compiler.syntax-error", ("syntax error", "parse error", "unexpected token")),
        ("compiler.invalid-codepage", ("codepage", "invalid encoding")),
        ("compiler.preprocessor-error", ("preprocessor", "macro")),
    )
    for code, needles in patterns:
        if any(needle in lowered for needle in needles):
            return code
    return "compiler.failed"


def _read_conversion_report(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    package_root = str(Path(__file__).resolve().parents[2])
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        package_root + os.pathsep + existing if existing else package_root
    )
    return environment


def _integer(value: object) -> int:
    return value if isinstance(value, int) else 0


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _last_nonempty_line(text: str) -> str | None:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return next((line for line in reversed(lines) if line), None)

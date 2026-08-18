from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath


class CorpusCaseKind(StrEnum):
    ROOT = "root"
    DIALOG_FRAGMENT = "dialog-fragment"
    LANGUAGE_FRAGMENT = "language-fragment"
    NON_DIALOG = "non-dialog"
    UNREADABLE = "unreadable"


class CorpusCaseStatus(StrEnum):
    PASSED = "passed"
    COMPILE_FAILED = "compile-failed"
    CONVERT_FAILED = "convert-failed"
    NO_FORMS = "no-forms"
    TIMED_OUT = "timed-out"
    INTERNAL_ERROR = "internal-error"


@dataclass(frozen=True, slots=True)
class CorpusCase:
    case_id: str
    project_root: Path
    source: Path
    relative_source: PurePosixPath
    kind: CorpusCaseKind
    byte_size: int
    direct_dialogs: int
    reachable_dialogs: int
    includes: tuple[PurePosixPath, ...] = ()
    included_by: tuple[PurePosixPath, ...] = ()
    languages: tuple[str, ...] = ()
    read_error: str | None = None
    preferred_language: int | None = None
    compiler_codepage: int | None = None

    @property
    def runnable(self) -> bool:
        return self.kind is CorpusCaseKind.ROOT


@dataclass(frozen=True, slots=True)
class CorpusDiagnosticCount:
    severity: str
    code: str


@dataclass(frozen=True, slots=True)
class CorpusCaseResult:
    case: CorpusCase
    status: CorpusCaseStatus
    duration_seconds: float
    forms: int
    errors: int
    warnings: int
    diagnostics: tuple[CorpusDiagnosticCount, ...]
    issue_codes: tuple[str, ...]
    case_dir: Path
    compile_command: tuple[str, ...]
    convert_command: tuple[str, ...] = ()
    compile_returncode: int | None = None
    convert_returncode: int | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class CorpusRunResult:
    output_dir: Path
    cases: tuple[CorpusCaseResult, ...]
    report_path: Path
    markdown_path: Path

    @property
    def failed(self) -> int:
        return sum(item.status is not CorpusCaseStatus.PASSED for item in self.cases)

from __future__ import annotations

import argparse
import codecs
import os
import re
from collections import Counter
from pathlib import Path

from rc2ui.corpus.compiler import (
    ResourceCompilerUnavailable,
    find_resource_compiler,
)
from rc2ui.corpus.discovery import discover_corpus
from rc2ui.corpus.report import rebuild_markdown_report, write_discovery_report
from rc2ui.corpus.qt_validation import validate_corpus_qt
from rc2ui.corpus.runner import run_corpus
from rc2ui.corpus.source_extractor import extract_source_corpus


def add_corpus_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    corpus = commands.add_parser(
        "corpus",
        help="discover and run external RC regression corpora",
    )
    actions = corpus.add_subparsers(dest="corpus_command")

    discover = actions.add_parser(
        "discover",
        help="classify top-level RC files and included fragments",
    )
    discover.add_argument("roots", nargs="+", type=Path, metavar="ROOT")
    discover.add_argument(
        "--report",
        type=Path,
        default=Path("corpus-discovery.json"),
    )
    discover.add_argument("--rc-encoding", default="cp1251", type=_encoding)

    extract = actions.add_parser(
        "extract",
        help="materialize every direct RC dialog as an isolated source case",
    )
    extract.add_argument("roots", nargs="+", type=Path, metavar="ROOT")
    extract.add_argument(
        "--output",
        type=Path,
        default=Path("source-corpus"),
    )
    extract.add_argument("--rc-encoding", default="cp1251", type=_encoding)

    run = actions.add_parser(
        "run",
        help="compile and convert every discovered top-level dialog RC",
    )
    run.add_argument("roots", nargs="+", type=Path, metavar="ROOT")
    run.add_argument("--output", type=Path, default=Path("corpus-results"))
    run.add_argument(
        "--compiler",
        default="auto",
        help="resource compiler executable (default: auto)",
    )
    run.add_argument(
        "--jobs",
        type=_positive_integer,
        default=min(4, os.cpu_count() or 1),
    )
    run.add_argument("--timeout", type=_positive_float, default=60.0)
    run.add_argument(
        "--resume",
        action="store_true",
        help="resume a previous output directory from per-case checkpoints",
    )
    run.add_argument(
        "--retry-failed",
        action="store_true",
        help="rerun non-passing checkpoints while resuming",
    )
    run.add_argument(
        "--max-new-cases",
        type=_positive_integer,
        help="process at most this many unfinished cases in one resumable batch",
    )
    run.add_argument(
        "--converter-mode",
        choices=("subprocess", "in-process"),
        default="subprocess",
        help=(
            "converter isolation mode; in-process reduces child processes and "
            "requires --qt-check off"
        ),
    )
    run.add_argument(
        "--match",
        dest="match_pattern",
        help="run sources whose relative path matches this regex",
    )
    run.add_argument(
        "--limit",
        type=_positive_integer,
        help="run at most this many cases after sorting and filtering",
    )
    run.add_argument("--include", type=Path, action="append", default=[])
    run.add_argument("--define", action="append", default=[], metavar="NAME[=VALUE]")
    run.add_argument("--rc-encoding", default="cp1251", type=_encoding)
    run.add_argument(
        "--compiler-codepage",
        type=_integer,
        help="codepage passed to the resource compiler; omitted by default",
    )
    run.add_argument(
        "--default-language",
        type=_integer,
        help=(
            "default Win32 LANGID; extracted cases use their language hint, "
            "ordinary cases default to 1033"
        ),
    )
    run.add_argument(
        "--qt-check",
        choices=("auto", "required", "off"),
        default="off",
        help="Qt runtime validation mode (default: off for throughput)",
    )

    report = actions.add_parser(
        "report",
        help="rebuild a Markdown summary from corpus-report.json",
    )
    report.add_argument("path", type=Path)
    report.add_argument("--output", type=Path)

    qt_check = actions.add_parser(
        "qt-check",
        help="run resumable Qt runtime and resize checks over a corpus run",
    )
    qt_check.add_argument("path", type=Path, metavar="CORPUS_RUN")
    qt_check.add_argument("--output", type=Path)
    qt_check.add_argument("--batch-size", type=_positive_integer, default=20)
    qt_check.add_argument("--max-new-batches", type=_positive_integer)
    qt_check.add_argument("--resume", action="store_true")


def run_corpus_command(args: argparse.Namespace) -> int:
    if args.corpus_command is None:
        raise ValueError("corpus requires discover, extract, run, qt-check, or report")
    if args.corpus_command == "discover":
        cases = discover_corpus(args.roots, fallback_encoding=args.rc_encoding)
        report = write_discovery_report(cases, args.report.resolve())
        counts = Counter(item.kind.value for item in cases)
        print(
            f"discovered {len(cases)} resource source(s); "
            f"{sum(item.runnable for item in cases)} runnable root(s)"
        )
        print(
            "classification: "
            + ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
        )
        print(f"discovery report: {report}")
        return 0
    if args.corpus_command == "extract":
        result = extract_source_corpus(
            args.roots,
            args.output,
            fallback_encoding=args.rc_encoding,
            on_source=lambda completed, total, case, count: (
                print(
                    f"[{completed}/{total}] {case.relative_source.as_posix()}: "
                    f"{count} dialog variant(s)",
                    flush=True,
                )
                if completed == total or completed % 100 == 0
                else None
            ),
        )
        print(
            f"extracted {result.extracted_variants} variant(s) from "
            f"{result.declared_dialogs} declaration(s) into "
            f"{len(result.cases)} independent case(s)"
        )
        print(
            f"removed {result.exact_duplicates} exact duplicate(s); "
            f"{result.malformed_dialogs} malformed/incomplete block(s)"
        )
        print(f"source corpus report: {result.report_path}")
        print(f"Markdown report: {result.markdown_path}")
        print(f"next: rc2ui corpus run \"{result.output_dir}\"")
        return 0
    if args.corpus_command == "report":
        output = rebuild_markdown_report(
            args.path.resolve(),
            args.output.resolve() if args.output else None,
        )
        print(f"Markdown report: {output}")
        return 0
    if args.corpus_command == "qt-check":
        result = validate_corpus_qt(
            args.path,
            args.output or args.path / "qt-validation",
            batch_size=args.batch_size,
            resume=args.resume,
            max_new_batches=args.max_new_batches,
            on_batch=lambda completed, total, errors, warnings: print(
                f"[{completed}/{total}] Qt batch: "
                f"{errors} error(s), {warnings} warning(s)",
                flush=True,
            ),
        )
        print(
            f"checked {result.checked_forms}/{result.total_forms} form(s); "
            f"{result.errors} error(s), {result.warnings} warning(s); "
            f"{result.pending_batches} batch(es) pending"
        )
        print(f"Qt corpus report: {result.report_path}")
        print(f"Markdown report: {result.markdown_path}")
        return int(bool(result.errors))

    cases = discover_corpus(args.roots, fallback_encoding=args.rc_encoding)
    if args.converter_mode == "in-process" and args.qt_check != "off":
        raise ValueError("--converter-mode in-process requires --qt-check off")
    if args.retry_failed and not args.resume:
        raise ValueError("--retry-failed requires --resume")
    selected = tuple(item for item in cases if item.runnable)
    if args.match_pattern:
        try:
            pattern = re.compile(args.match_pattern, flags=re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"invalid --match regex: {error}") from error
        selected = tuple(
            item
            for item in selected
            if pattern.search(item.relative_source.as_posix())
            or pattern.search(str(item.source))
        )
    if args.limit is not None:
        selected = selected[: args.limit]
    if not selected:
        raise ValueError("corpus selection contains no runnable RC roots")
    try:
        compiler = find_resource_compiler(args.compiler)
    except ResourceCompilerUnavailable as error:
        raise ValueError(str(error)) from error
    defines = tuple(_define(value) for value in args.define)
    result = run_corpus(
        selected,
        args.output,
        compiler=compiler,
        jobs=args.jobs,
        timeout_seconds=args.timeout,
        include_paths=tuple(args.include),
        defines=defines,
        source_encoding=args.rc_encoding,
        compiler_codepage=args.compiler_codepage,
        default_language=args.default_language,
        qt_check=args.qt_check,
        resume=args.resume,
        retry_failed=args.retry_failed,
        max_new_cases=args.max_new_cases,
        converter_mode=args.converter_mode,
        on_result=lambda completed, total, item: print(
            f"[{completed}/{total}] {item.status.value}: "
            f"{item.case.relative_source.as_posix()} "
            f"({item.forms} form(s), {item.duration_seconds:.2f}s)",
            flush=True,
        ),
    )
    statuses = Counter(item.status.value for item in result.cases)
    print(
        f"ran {len(result.cases)} case(s); "
        f"generated {sum(item.forms for item in result.cases)} form(s); "
        f"{result.failed} non-passing case(s)"
    )
    print(
        "statuses: "
        + ", ".join(f"{name}={count}" for name, count in sorted(statuses.items()))
    )
    print(f"corpus report: {result.report_path}")
    print(f"Markdown report: {result.markdown_path}")
    return int(bool(result.failed))


def _encoding(value: str) -> str:
    try:
        codecs.lookup(value)
    except LookupError as error:
        raise argparse.ArgumentTypeError(
            f"unknown text encoding: {value!r}"
        ) from error
    return value


def _positive_integer(value: str) -> int:
    result = _integer(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return result


def _positive_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid number: {value!r}") from error
    if result <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return result


def _integer(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from error


def _define(value: str) -> tuple[str, int]:
    name, separator, raw_value = value.partition("=")
    if not re.fullmatch(r"[A-Za-z_]\w*", name):
        raise ValueError(f"invalid preprocessor name: {name!r}")
    if not separator:
        return name, 1
    try:
        return name, int(raw_value, 0)
    except ValueError as error:
        raise ValueError(f"invalid value in --define {value!r}") from error

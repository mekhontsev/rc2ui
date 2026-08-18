from __future__ import annotations

import argparse
import codecs
import math
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from rc2ui import __version__
from rc2ui.application.batch import BatchConverter
from rc2ui.application.manifest import ManifestError, load_manifest
from rc2ui.application.models import ConversionRequest, InputGroup
from rc2ui.corpus.cli import add_corpus_parser, run_corpus_command
from rc2ui.domain.diagnostics import Diagnostic
from rc2ui.layout.mode import LayoutMode
from rc2ui.qtcheck.model import QtCheckMode
from rc2ui.qtcheck.runner import find_ui_files, run_qt_checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rc2ui",
        description="Convert Win32 dialog resources to Qt 6 .ui files.",
    )
    parser.add_argument("--version", action="version", version=__version__)

    commands = parser.add_subparsers(dest="command")
    validate_config = commands.add_parser(
        "validate-config",
        help="validate a unified rc2ui TOML configuration",
    )
    validate_config.add_argument("path", type=Path)

    convert = commands.add_parser(
        "convert",
        help="convert dialogs from RC sources and compiled resources",
    )
    convert.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        metavar="INPUT",
        help=(
            "input files for one group: .rc files are sources; .res and all "
            "other files are compiled-resource containers"
        ),
    )
    convert.add_argument(
        "--manifest",
        type=Path,
        help="TOML manifest; cannot be combined with direct input options",
    )
    convert.add_argument("--project-root", type=Path, default=Path.cwd())
    convert.add_argument("--output", type=Path, default=Path("generated-ui"))
    convert.add_argument("--include", type=Path, action="append", default=[])
    convert.add_argument(
        "--define",
        action="append",
        default=[],
        metavar="NAME[=VALUE]",
        help="preprocessor definition used while reading RC headers",
    )
    convert.add_argument(
        "--rc-encoding",
        type=_encoding,
        default="cp1251",
        help="fallback encoding for non-Unicode RC files (default: cp1251)",
    )
    convert.add_argument(
        "--default-language",
        "--language",
        dest="default_language",
        type=_integer,
        help=(
            "default Win32 LANGID written into .ui, decimal or 0x-prefixed "
            "(default: 1033)"
        ),
    )
    convert.add_argument(
        "--strict",
        action="store_true",
        help="return failure for warnings as well as errors",
    )
    convert.add_argument(
        "--layout-mode",
        choices=tuple(mode.value for mode in LayoutMode),
        help="layout strategy: faithful (default) or simplified",
    )
    convert.add_argument(
        "--ui-comments",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "include source and translation comments in generated .ui files "
            "(default: enabled)"
        ),
    )
    convert.add_argument(
        "--qt-check",
        choices=tuple(mode.value for mode in QtCheckMode),
        help="optional PyQt6/PySide6 validation: auto, required, or off",
    )
    convert.add_argument(
        "--qt-preview",
        type=Path,
        help="write Qt 6 PNG previews and an HTML gallery",
    )
    convert.add_argument(
        "--qt-font-scale",
        type=_positive_float,
        help=(
            "scale the QApplication font for Qt validation and previews "
            "(default: 1.0)"
        ),
    )

    qt_check = commands.add_parser(
        "qt-check",
        help="validate existing Qt Designer forms with PyQt6 or PySide6",
    )
    qt_check.add_argument("paths", nargs="+", type=Path, metavar="UI_OR_DIR")
    qt_check.add_argument("--report", type=Path)
    qt_check.add_argument("--preview", type=Path)
    qt_check.add_argument(
        "--font-scale",
        type=_positive_float,
        default=1.0,
        help="scale the QApplication font before loading forms (default: 1.0)",
    )
    qt_check.add_argument(
        "--strict",
        action="store_true",
        help="return failure for warnings as well as errors",
    )
    add_corpus_parser(commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "validate-config":
        try:
            request = load_manifest(args.path)
        except ManifestError as error:
            parser.error(str(error))
        print(
            f"valid configuration: {len(request.input_groups)} input group(s), "
            f"{len(request.rules.naming.rules)} naming rule(s), "
            f"{len(request.rules.controls.rules)} control rule(s), "
            f"{len(request.rules.controls.compounds)} control compound(s), "
            f"{len(request.rules.semantics.rules)} semantic rule(s)"
        )
        return 0

    if args.command == "convert":
        request = _conversion_request(parser, args)
        result = BatchConverter().convert(request)
        _print_diagnostics(result.diagnostics)
        print(
            f"generated {len(result.forms)} form(s); "
            f"{result.error_count} error(s), {result.warning_count} warning(s)"
        )
        print(f"report: {result.report_path}")
        print(f"name suggestions: {result.suggestions_path}")
        if result.qt_report_path:
            print(f"Qt report: {result.qt_report_path}")
        if result.qt_preview_index:
            print(f"Qt previews: {result.qt_preview_index}")
        for translation_path in result.translation_paths:
            print(f"translation: {translation_path}")
        if result.error_count or (request.strict and result.warning_count):
            return 1
        return 0

    if args.command == "qt-check":
        ui_paths = find_ui_files(tuple(args.paths))
        if not ui_paths:
            parser.error("qt-check did not find any .ui files")
        report = args.report or _default_qt_report(tuple(args.paths))
        run = run_qt_checks(
            ui_paths,
            report_path=report,
            required=True,
            preview_dir=args.preview,
            font_scale=args.font_scale,
        )
        _print_diagnostics(run.diagnostics)
        errors = sum(item.severity == "error" for item in run.diagnostics)
        warnings = sum(item.severity == "warning" for item in run.diagnostics)
        print(
            f"checked {run.checked_forms} form(s); "
            f"{errors} error(s), {warnings} warning(s)"
        )
        if run.report_path:
            print(f"Qt report: {run.report_path}")
        if run.preview_index:
            print(f"Qt previews: {run.preview_index}")
        return int(bool(errors or (args.strict and warnings)))

    if args.command == "corpus":
        try:
            return run_corpus_command(args)
        except (OSError, ValueError) as error:
            parser.error(str(error))

    parser.error(f"unknown command: {args.command}")


def _conversion_request(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> ConversionRequest:
    if args.manifest:
        direct_options = (
            args.inputs
            or args.include
            or args.define
            or args.rc_encoding != "cp1251"
        )
        if direct_options:
            parser.error(
                "--manifest cannot be combined with positional inputs, "
                "--include, --define, or --rc-encoding"
            )
        try:
            request = load_manifest(args.manifest)
        except ManifestError as error:
            parser.error(str(error))
        updates: dict[str, object] = {}
        if args.strict and not request.strict:
            updates["strict"] = True
        if args.qt_check:
            updates["qt_check"] = QtCheckMode(args.qt_check)
        if args.layout_mode:
            updates["layout_mode"] = LayoutMode(args.layout_mode)
        if args.ui_comments is not None:
            updates["ui_comments"] = args.ui_comments
        if args.qt_preview:
            updates["qt_preview_dir"] = args.qt_preview
        if args.qt_font_scale is not None:
            updates["qt_font_scale"] = args.qt_font_scale
        if args.default_language is not None:
            updates["default_language"] = args.default_language
        if updates:
            request = replace(request, **updates)
        return request

    if not args.inputs:
        parser.error(
            "convert requires RC and compiled-resource input files or --manifest"
        )
    rc_files = tuple(path for path in args.inputs if _is_rc_source(path))
    resource_files = tuple(path for path in args.inputs if not _is_rc_source(path))
    if not rc_files:
        parser.error("direct conversion requires at least one .rc input")
    if not resource_files:
        parser.error(
            "direct conversion requires at least one .res or PE binary input"
        )
    try:
        defines = tuple(_define(value) for value in args.define)
    except ValueError as error:
        parser.error(str(error))
    return ConversionRequest(
        project_root=args.project_root,
        output_dir=args.output,
        input_groups=(
            InputGroup(
                rc_files=rc_files,
                resource_files=resource_files,
            ),
        ),
        include_paths=tuple(args.include),
        defines=defines,
        rc_encoding=args.rc_encoding,
        default_language=(
            args.default_language
            if args.default_language is not None
            else 1033
        ),
        strict=args.strict,
        layout_mode=LayoutMode(args.layout_mode or LayoutMode.FAITHFUL.value),
        ui_comments=(
            args.ui_comments if args.ui_comments is not None else True
        ),
        qt_check=QtCheckMode(args.qt_check or QtCheckMode.AUTO.value),
        qt_preview_dir=args.qt_preview,
        qt_font_scale=args.qt_font_scale or 1.0,
    )


def _print_diagnostics(diagnostics: Sequence[Diagnostic]) -> None:
    for diagnostic in diagnostics:
        location = f"{diagnostic.location}: " if diagnostic.location else ""
        print(
            f"{diagnostic.severity}: {location}{diagnostic.message} "
            f"[{diagnostic.code}]",
            file=sys.stderr,
        )


def _default_qt_report(inputs: tuple[Path, ...]) -> Path:
    if len(inputs) == 1:
        base = inputs[0] if inputs[0].is_dir() else inputs[0].parent
        return base / "rc2ui-qt-report.json"
    return Path.cwd() / "rc2ui-qt-report.json"


def _integer(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from error


def _encoding(value: str) -> str:
    try:
        codecs.lookup(value)
    except LookupError as error:
        raise argparse.ArgumentTypeError(
            f"unknown text encoding: {value!r}"
        ) from error
    return value


def _positive_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "value must be a positive finite number"
        ) from error
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError(
            "value must be a positive finite number"
        )
    return result


def _is_rc_source(path: Path) -> bool:
    return path.suffix.casefold() == ".rc"


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

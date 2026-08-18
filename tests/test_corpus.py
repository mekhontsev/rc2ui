from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rc2ui.adapters.resources.source import read_dialog_resources
from rc2ui.corpus.assets import prepare_asset_overlay
from rc2ui.corpus.compiler import (
    CompileResult,
    compile_resource,
    find_resource_compiler,
)
from rc2ui.corpus.discovery import discover_corpus
from rc2ui.corpus.model import CorpusCaseKind, CorpusCaseStatus
from rc2ui.corpus.qt_validation import validate_corpus_qt
from rc2ui.corpus.runner import run_corpus
from rc2ui.corpus.source_extractor import extract_source_corpus
from rc2ui.qtcheck.discovery import QtBindingAvailability


class CorpusDiscoveryTests(unittest.TestCase):
    def test_classifies_roots_language_fragments_and_non_dialog_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name, "project")
            language_dir = root / "lang"
            language_dir.mkdir(parents=True)
            (root / "main.rc").write_text(
                '#include "lang/en-US.rc"\n', encoding="utf-8"
            )
            (language_dir / "en-US.rc").write_text(
                "LANGUAGE 9, 1\n"
                "IDD_MAIN DIALOGEX 0, 0, 100, 50\n"
                "BEGIN\nEND\n",
                encoding="utf-8",
            )
            (root / "orphan.rc2").write_text(
                "IDD_ORPHAN DIALOG 0, 0, 80, 40\nBEGIN\nEND\n",
                encoding="utf-8",
            )
            (root / "strings.rc").write_text(
                "STRINGTABLE\nBEGIN\n  1 \"Text\"\nEND\n",
                encoding="utf-8",
            )

            cases = discover_corpus((root,), fallback_encoding="cp1251")

        by_path = {item.relative_source.as_posix(): item for item in cases}
        self.assertEqual(by_path["main.rc"].kind, CorpusCaseKind.ROOT)
        self.assertEqual(by_path["main.rc"].direct_dialogs, 0)
        self.assertEqual(by_path["main.rc"].reachable_dialogs, 1)
        self.assertEqual(
            by_path["lang/en-US.rc"].kind,
            CorpusCaseKind.LANGUAGE_FRAGMENT,
        )
        self.assertEqual(
            by_path["orphan.rc2"].kind,
            CorpusCaseKind.DIALOG_FRAGMENT,
        )
        self.assertEqual(
            by_path["strings.rc"].kind,
            CorpusCaseKind.NON_DIALOG,
        )

    def test_expands_a_directory_containing_git_projects(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            parent = Path(directory_name)
            for name in ("one", "two"):
                project = parent / name
                (project / ".git").mkdir(parents=True)
                (project / "main.rc").write_text(
                    "IDD_MAIN DIALOG 0, 0, 10, 10\nBEGIN\nEND\n",
                    encoding="utf-8",
                )

            cases = discover_corpus((parent,))

        self.assertEqual(len(cases), 2)
        self.assertEqual({item.project_root.name for item in cases}, {"one", "two"})
        self.assertNotEqual(cases[0].case_id, cases[1].case_id)

    def test_case_id_is_stable_when_checkout_moves(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            parent = Path(directory_name)
            case_ids: list[str] = []
            for container in ("first", "second"):
                root = parent / container / "project"
                root.mkdir(parents=True)
                (root / "main.rc").write_text(
                    "IDD_MAIN DIALOG 0, 0, 10, 10\nBEGIN\nEND\n",
                    encoding="utf-8",
                )
                case_ids.append(discover_corpus((root,))[0].case_id)

        self.assertEqual(case_ids[0], case_ids[1])

    def test_reads_execution_metadata_from_extracted_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            (root / "dialog.rc").write_text(
                "// rc2ui-preferred-language: 1049\n"
                "// rc2ui-compiler-codepage: 65001\n"
                "100 DIALOG 0, 0, 10, 10\nBEGIN\nEND\n",
                encoding="utf-8",
            )

            [case] = discover_corpus((root,))

        self.assertEqual(case.preferred_language, 1049)
        self.assertEqual(case.compiler_codepage, 65001)


class SourceCorpusExtractionTests(unittest.TestCase):
    def test_groups_language_fragments_and_materializes_resolved_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name, "project")
            language_dir = root / "lang"
            language_dir.mkdir(parents=True)
            (root / "resource.h").write_text(
                "#define IDD_MAIN 100\n#define IDC_NAME 101\n",
                encoding="utf-8",
            )
            english = (
                '#include "../resource.h"\n'
                "LANGUAGE LANG_ENGLISH, SUBLANG_ENGLISH_US\n"
                "IDD_MAIN DIALOGEX 0, 0, 100, 50\n"
                'CAPTION "English"\n'
                "BEGIN\n"
                'LTEXT "Name", IDC_NAME, 5, 5, 30, 8\n'
                "END\n"
            )
            russian = (
                '#include "../resource.h"\n'
                "LANGUAGE LANG_RUSSIAN, SUBLANG_RUSSIAN_RUSSIA\n"
                "IDD_MAIN DIALOGEX 0, 0, 100, 50\n"
                'CAPTION "Русский"\n'
                "BEGIN\n"
                'LTEXT "Имя", IDC_NAME, 5, 6, 30, 8\n'
                "END\n"
            )
            (language_dir / "en-US.rc").write_text(english, encoding="utf-8")
            (language_dir / "ru-RU.rc").write_bytes(russian.encode("cp1251"))
            output = Path(directory_name, "extracted")

            result = extract_source_corpus((root,), output)

            self.assertEqual(result.declared_dialogs, 2)
            self.assertEqual(result.extracted_variants, 2)
            self.assertEqual(len(result.cases), 1)
            extracted = result.cases[0]
            self.assertEqual(extracted.preferred_language, 1033)
            self.assertEqual(
                {item.language for item in extracted.variants},
                {1033, 1049},
            )
            text = extracted.source.read_text(encoding="utf-8")
            self.assertIn("#define IDD_MAIN 100", text)
            self.assertIn("#define IDC_NAME 101", text)
            self.assertIn("LANGUAGE 9, 1", text)
            self.assertIn("LANGUAGE 25, 1", text)
            [case] = discover_corpus((output,))
            self.assertEqual(case.preferred_language, 1033)
            self.assertEqual(case.compiler_codepage, 65001)

    def test_repairs_unambiguous_caption_and_falls_back_for_build_macros(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name, "project")
            root.mkdir()
            (root / "custom.rc").write_text(
                "LANGUAGE LANG_FANTASY, SUBLANG_DEFAULT\n"
                "IDD_MAIN DIALOGEX 65535, 65506, 100, 50\n"
                "BEGIN\n"
                '  CONTROL "", IDC_LIST, "SysListView32", LVCHECKSTYLES, '
                "5, 5, 90, 20\n"
                '  PUSHBUTTON &Broken caption", IDC_OK, 5, 30, 50, 12\n'
                '  CTEXT "two " "parts"\n'
                '    IDC_TEXT, 5, 42, 50, 8\n'
                '  CONTROL "", IDC_CHECK "button", SS_INMOTION, 60, 30, 30, 8\n'
                '  CONTROL "", DID_CUSTOM, CUSTOM_CLASSW, 0, 60, 40, 30, 8 '
                "{ 1, 2, 3 }\n"
                "  ICON ICO_MAIN, DID_ICON, 5, 5\n"
                '  ICON "NAMED_ICON"-1, 5, 5, 10, 10\n'
                '  CTEXT "Missing comma" -1, 5, 5, 30, 8\n'
                "  COMBOBOX IDC_COMBO, 5, 5, 30, 40, CBS_DROPDOWNLIST\n"
                "END\n",
                encoding="utf-8",
            )
            output = Path(directory_name, "extracted")

            result = extract_source_corpus((root,), output)

            text = result.cases[0].source.read_text(encoding="utf-8")
            self.assertIn("LANGUAGE 0, 0", text)
            self.assertIn("#define LVCHECKSTYLES 0", text)
            self.assertIn('PUSHBUTTON "&Broken caption",', text)
            self.assertIn("DIALOGEX -1, -30, 100, 50", text)
            self.assertIn('CTEXT "two parts",', text)
            self.assertIn('IDC_CHECK, "button"', text)
            self.assertIn('#define SS_INMOTION 0', text)
            self.assertIn('DID_CUSTOM, "CUSTOM_CLASSW"', text)
            self.assertNotIn("{ 1, 2, 3 }", text)
            self.assertIn("ICON ICO_MAIN, DID_ICON, 5, 5, 0, 0", text)
            self.assertIn('ICON "NAMED_ICON", -1, 5, 5, 10, 10', text)
            self.assertIn('CTEXT "Missing comma", -1, 5, 5, 30, 8', text)
            self.assertIn("#define ICO_MAIN", text)
            self.assertIn("#define DID_ICON", text)
            self.assertNotIn("#define COMBOBOX", text)


class CorpusRunnerTests(unittest.TestCase):
    def test_runs_converter_in_isolation_and_aggregates_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name, "project")
            root.mkdir()
            source = root / "main.rc"
            source.write_text(
                "IDD_MAIN DIALOG 0, 0, 100, 50\nBEGIN\nEND\n",
                encoding="utf-8",
            )
            case = discover_corpus((root,))[0]
            output = Path(directory_name, "results")

            def convert(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                generated = Path(command[command.index("--output") + 1])
                generated.mkdir(parents=True)
                (generated / "rc2ui-report.json").write_text(
                    json.dumps(
                        {
                            "summary": {"forms": 1, "errors": 0, "warnings": 1},
                            "diagnostics": [
                                {
                                    "severity": "warning",
                                    "code": "layout.test-warning",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "generated\n", "")

            compiled = CompileResult(
                command=("fake-windres",),
                returncode=0,
                stdout="",
                stderr="",
            )
            with patch("rc2ui.corpus.runner.compile_resource", return_value=compiled), patch(
                "rc2ui.corpus.runner.subprocess.run", side_effect=convert
            ):
                result = run_corpus(
                    (case,),
                    output,
                    compiler="fake-windres",
                )

            self.assertEqual(result.cases[0].status, CorpusCaseStatus.PASSED)
            self.assertEqual(result.cases[0].forms, 1)
            self.assertEqual(
                result.cases[0].issue_codes,
                ("rc2ui.layout.test-warning",),
            )
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["forms"], 1)
            self.assertEqual(
                report["representatives"][0]["issue_code"],
                "rc2ui.layout.test-warning",
            )
            self.assertTrue(result.markdown_path.is_file())

    def test_does_not_reuse_a_nonempty_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name, "project")
            root.mkdir()
            source = root / "main.rc"
            source.write_text(
                "IDD_MAIN DIALOG 0, 0, 10, 10\nBEGIN\nEND\n",
                encoding="utf-8",
            )
            case = discover_corpus((root,))[0]
            output = Path(directory_name, "results")
            output.mkdir()
            (output / "keep.txt").write_text("user data", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "not empty"):
                run_corpus((case,), output, compiler="fake")
            self.assertEqual(
                (output / "keep.txt").read_text(encoding="utf-8"),
                "user data",
            )

    def test_resume_can_retry_failed_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name, "project")
            root.mkdir()
            source = root / "main.rc"
            source.write_text(
                "IDD_MAIN DIALOG 0, 0, 10, 10\nBEGIN\nEND\n",
                encoding="utf-8",
            )
            case = discover_corpus((root,))[0]
            output = Path(directory_name, "results")
            failed = CompileResult(("fake",), 1, "", "syntax error")
            passed = CompileResult(("fake",), 0, "", "")

            def convert(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                generated = Path(command[command.index("--output") + 1])
                generated.mkdir(parents=True)
                (generated / "rc2ui-report.json").write_text(
                    json.dumps({"summary": {"forms": 1, "errors": 0, "warnings": 0}}),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch(
                "rc2ui.corpus.runner.compile_resource",
                side_effect=(failed, passed),
            ), patch("rc2ui.corpus.runner.subprocess.run", side_effect=convert):
                first = run_corpus((case,), output, compiler="fake")
                second = run_corpus(
                    (case,),
                    output,
                    compiler="fake",
                    resume=True,
                    retry_failed=True,
                )

            self.assertEqual(first.cases[0].status, CorpusCaseStatus.COMPILE_FAILED)
            self.assertEqual(second.cases[0].status, CorpusCaseStatus.PASSED)
            self.assertTrue((output / "retried" / case.case_id).is_dir())

    def test_refuses_a_concurrent_run_in_the_same_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name, "project")
            root.mkdir()
            (root / "main.rc").write_text(
                "IDD_MAIN DIALOG 0, 0, 10, 10\nBEGIN\nEND\n",
                encoding="utf-8",
            )
            case = discover_corpus((root,))[0]
            output = Path(directory_name, "results")
            (output / "cases").mkdir(parents=True)
            (output / ".rc2ui-corpus.lock").write_text(
                f"{os.getpid()} active\n",
                encoding="ascii",
            )

            with self.assertRaisesRegex(ValueError, "already in use"):
                run_corpus((case,), output, compiler="fake", resume=True)


class CorpusQtValidationTests(unittest.TestCase):
    def test_resumes_sharded_qt_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            run = Path(directory_name, "run")
            for index in (1, 2):
                generated = run / "cases" / f"case-{index}" / "generated"
                generated.mkdir(parents=True)
                ui = generated / f"FORM_{index}.ui"
                ui.write_text("<ui/>", encoding="utf-8")
                (generated / "rc2ui-report.json").write_text(
                    json.dumps(
                        {
                            "forms": [
                                {
                                    "output": str(ui),
                                    "layout_rect_dlu": [0, 0, 100, 50],
                                    "controls": [],
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
            output = Path(directory_name, "qt-validation")

            with patch(
                "rc2ui.qtcheck.runner.discover_qt_binding",
                return_value=QtBindingAvailability(True),
            ):
                first = validate_corpus_qt(
                    run,
                    output,
                    batch_size=1,
                    max_new_batches=1,
                    worker_module="tests.fake_qt_worker",
                )
                second = validate_corpus_qt(
                    run,
                    output,
                    batch_size=1,
                    resume=True,
                    worker_module="tests.fake_qt_worker",
                )

            self.assertEqual(first.checked_forms, 1)
            self.assertEqual(first.pending_batches, 1)
            self.assertEqual(second.checked_forms, 2)
            self.assertEqual(second.pending_batches, 0)
            self.assertEqual(second.warnings, 2)
            self.assertTrue(second.markdown_path.is_file())

    def test_changed_ui_invalidates_only_its_completed_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            run = Path(directory_name, "run")
            generated = run / "cases" / "case-1" / "generated"
            generated.mkdir(parents=True)
            ui = generated / "FORM.ui"
            ui.write_text("<ui version='4.0'/>", encoding="utf-8")
            (generated / "rc2ui-report.json").write_text(
                json.dumps(
                    {
                        "forms": [
                            {
                                "output": str(ui),
                                "layout_rect_dlu": [0, 0, 100, 50],
                                "controls": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = Path(directory_name, "qt-validation")

            with patch(
                "rc2ui.qtcheck.runner.discover_qt_binding",
                return_value=QtBindingAvailability(True),
            ):
                validate_corpus_qt(
                    run,
                    output,
                    batch_size=1,
                    worker_module="tests.fake_qt_worker",
                )
                first_checkpoint = json.loads(
                    (output / "batches" / "batch-0001.done.json").read_text(
                        encoding="utf-8"
                    )
                )
                ui.write_text("<ui version='4.0'><class>New</class></ui>", encoding="utf-8")
                validate_corpus_qt(
                    run,
                    output,
                    batch_size=1,
                    resume=True,
                    worker_module="tests.fake_qt_worker",
                )

            second_checkpoint = json.loads(
                (output / "batches" / "batch-0001.done.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotEqual(
                first_checkpoint["forms"][0]["fingerprint"],
                second_checkpoint["forms"][0]["fingerprint"],
            )

    def test_refuses_a_concurrent_qt_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            run = Path(directory_name, "run")
            generated = run / "cases" / "case-1" / "generated"
            generated.mkdir(parents=True)
            ui = generated / "FORM.ui"
            ui.write_text("<ui/>", encoding="utf-8")
            (generated / "rc2ui-report.json").write_text(
                json.dumps(
                    {
                        "forms": [
                            {
                                "output": str(ui),
                                "layout_rect_dlu": [0, 0, 100, 50],
                                "controls": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = Path(directory_name, "qt-validation")
            (output / "batches").mkdir(parents=True)
            (output / "qt-corpus-manifest.json").write_text(
                json.dumps(
                    {
                        "corpus_run": str(run.resolve()),
                        "batch_size": 20,
                        "forms": 1,
                        "batches": 1,
                        "form_set_sha256": hashlib.sha256(
                            str(ui.resolve()).encode()
                        ).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            (output / ".rc2ui-qt.lock").write_text(
                f"{os.getpid()} active\n",
                encoding="ascii",
            )

            with self.assertRaisesRegex(ValueError, "Qt corpus output is already"):
                validate_corpus_qt(run, output, resume=True)


class CorpusCompilerTests(unittest.TestCase):
    def test_asset_overlay_resolves_windows_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name, "project")
            source_dir = root / "resources"
            source_dir.mkdir(parents=True)
            source = source_dir / "main.rc"
            source.write_text(
                'IDI_MAIN ICON "MAINICON.ICO"\n',
                encoding="utf-8",
            )
            (source_dir / "MainIcon.ico").write_bytes(b"icon")
            overlay = Path(directory_name, "overlay")

            working = prepare_asset_overlay(
                source,
                root,
                overlay,
                fallback_encoding="cp1251",
            )

            self.assertEqual((working / "MAINICON.ICO").read_bytes(), b"icon")
            self.assertFalse((source_dir / "MAINICON.ICO").exists())

    def test_asset_overlay_follows_resource_script_includes(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name, "project")
            source_dir = root / "resources"
            source_dir.mkdir(parents=True)
            source = source_dir / "main.rc"
            source.write_text('#include "dialogs.rc2"\n', encoding="utf-8")
            (source_dir / "dialogs.rc2").write_text(
                'IDI_MAIN ICON "TOOLBAR.BMP"\n',
                encoding="utf-8",
            )
            (source_dir / "Toolbar.bmp").write_bytes(b"bitmap")
            overlay = Path(directory_name, "overlay")

            working = prepare_asset_overlay(
                source,
                root,
                overlay,
                fallback_encoding="cp1251",
            )

            self.assertEqual((working / "TOOLBAR.BMP").read_bytes(), b"bitmap")

    def test_asset_overlay_reconstructs_project_include_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name, "project")
            source_dir = root / "app"
            include_dir = root / "sdk" / "include" / "Project"
            source_dir.mkdir(parents=True)
            include_dir.mkdir(parents=True)
            source = source_dir / "main.rc"
            source.write_text(
                "#include <project/version.rc>\n",
                encoding="utf-8",
            )
            (include_dir / "Version.rc").write_text(
                '#include "BuildNo.h"\nIDI_MAIN ICON "Logo.ICO"\n',
                encoding="utf-8",
            )
            (include_dir / "buildno.H").write_text(
                "#define BUILD_NO 42\n",
                encoding="utf-8",
            )
            (include_dir / "logo.ico").write_bytes(b"icon")

            working = prepare_asset_overlay(
                source,
                root,
                Path(directory_name, "overlay"),
                fallback_encoding="cp1251",
            )

            mirrored = working / "project"
            self.assertTrue((mirrored / "version.rc").is_file())
            self.assertTrue((mirrored / "BuildNo.h").is_file())
            self.assertEqual((mirrored / "Logo.ICO").read_bytes(), b"icon")

    @unittest.skipUnless(
        shutil.which("x86_64-w64-mingw32-windres") or shutil.which("windres"),
        "windres is not installed",
    )
    def test_compiles_a_real_dialog_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source = root / "main.rc"
            output = root / "main.res"
            source.write_text(
                "#define IDD_MAIN 100\n"
                "#define IDC_STATIC -1\n"
                "IDD_MAIN DIALOG 0, 0, 100, 40\n"
                "BEGIN\n"
                '  LTEXT "Corpus", IDC_STATIC, 5, 5, 50, 8\n'
                "END\n",
                encoding="utf-8",
            )
            compiler = find_resource_compiler()

            result = compile_resource(
                compiler,
                source,
                output,
                include_paths=(root,),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(read_dialog_resources(output)), 1)

    @unittest.skipUnless(
        shutil.which("x86_64-w64-mingw32-windres") or shutil.which("windres"),
        "windres is not installed",
    )
    def test_extracted_dialect_normalization_compiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name, "project")
            root.mkdir()
            (root / "dialog.rc").write_text(
                "IDD_MAIN DIALOGEX 65535, 65506, 100, 60\n"
                "BEGIN\n"
                '  CTEXT "two " "parts"\n'
                "    IDC_TEXT, 5, 5, 40, 8\n"
                '  LTEXT "A \\"quoted\\" link", IDC_QUOTE, 5, 13, 40, 8\n'
                '  CONTROL "", IDC_CUSTOM CUSTOM_CLASSW, SS_PROJECT, '
                "5, 15, 40, 20 { 1, 2 }\n"
                "  ICON ICO_MAIN, DID_ICON, 5, 40\n"
                "  COMBOBOX IDC_COMBO, 50, 5, 40, 40, CBS_DROPDOWNLIST\n"
                "END\n",
                encoding="utf-8",
            )
            extracted = extract_source_corpus(
                (root,),
                Path(directory_name, "extracted"),
            ).cases[0].source
            output = Path(directory_name, "dialog.res")

            result = compile_resource(
                find_resource_compiler(),
                extracted,
                output,
                include_paths=(extracted.parent,),
                codepage=65001,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(read_dialog_resources(output)), 1)


if __name__ == "__main__":
    unittest.main()

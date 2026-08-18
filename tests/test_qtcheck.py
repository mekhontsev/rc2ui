from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from rc2ui.domain.diagnostics import Diagnostic, Severity
from rc2ui.domain.dialog import DialogFont
from rc2ui.domain.geometry import RectDlu
from rc2ui.layout.infer import LayoutBuilder
from rc2ui.mapping.controls import ControlMapper
from rc2ui.naming.resolver import NameResolver
from rc2ui.qt.emitter import emit_ui
from rc2ui.qtcheck.discovery import QtBindingAvailability, discover_qt_binding
from rc2ui.qtcheck.font_scaling import analyze_font_change
from rc2ui.qtcheck.inspector import inspect_form
from rc2ui.qtcheck.model import (
    ControlGeometryReference,
    FormGeometryReference,
)
from rc2ui.qtcheck.runner import (
    _preview_name,
    _worker_environment,
    find_ui_files,
    run_qt_checks,
)
from rc2ui.qtcheck.runtime import RuntimeInspector
from rc2ui.qtcheck.runtime_analysis import analyze_stretch
from rc2ui.qtcheck.source_geometry import analyze_source_geometry
from rc2ui.qtcheck.summary import (
    build_report_summary,
    preview_summary_diagnostic,
    summarize_diagnostics,
)
from rc2ui.qtcheck.ui_transform import prepare_ui_xml
from tests.test_layout_and_emitter import dense_multiline_dialog, make_dialog
from tests.test_mapping_and_naming import sample_dialog


class QtCheckTests(unittest.TestCase):
    def test_application_font_scale_is_applied_before_forms_load(self) -> None:
        class Font:
            def __init__(self) -> None:
                self.point_size = 10.0

            def pointSizeF(self) -> float:
                return self.point_size

            def pixelSize(self) -> int:
                return -1

            def setPointSizeF(self, value: float) -> None:
                self.point_size = value

        class Application:
            def __init__(self) -> None:
                self.current_font = Font()
                self.processed = 0

            def font(self) -> Font:
                return self.current_font

            def setFont(self, font: Font) -> None:
                self.current_font = font

            def processEvents(self) -> None:
                self.processed += 1

        application = Application()
        runtime = RuntimeInspector(application, None, None)

        runtime.scale_application_font(1.5)

        self.assertEqual(application.current_font.point_size, 15.0)
        self.assertEqual(application.processed, 1)

    def test_explicit_ui_fonts_are_scaled_after_form_load(self) -> None:
        class Font:
            def __init__(self, point_size: float) -> None:
                self.point_size = point_size

            def pointSizeF(self) -> float:
                return self.point_size

            def pixelSize(self) -> int:
                return -1

            def setPointSizeF(self, value: float) -> None:
                self.point_size = value

        class Metrics:
            @staticmethod
            def height() -> int:
                return 16

        class WidgetBase:
            pass

        class Widget(WidgetBase):
            def __init__(self, size: float, *, explicit: bool) -> None:
                self.current_font = Font(size)
                self.explicit = explicit
                self.children: list[Widget] = []
                self.set_calls = 0

            def findChildren(self, widget_type: object) -> list[Widget]:
                return self.children

            def testAttribute(self, attribute: object) -> bool:
                return self.explicit

            def font(self) -> Font:
                return self.current_font

            def fontMetrics(self) -> Metrics:
                return Metrics()

            def setFont(self, font: Font) -> None:
                self.current_font = font
                self.set_calls += 1

        class WidgetAttribute:
            WA_SetFont = object()

        class QtNamespace:
            pass

        class Core:
            pass

        class Widgets:
            pass

        QtNamespace.WidgetAttribute = WidgetAttribute
        Core.Qt = QtNamespace
        Widgets.QWidget = WidgetBase

        class Application:
            def __init__(self) -> None:
                self.processed = 0

            def processEvents(self) -> None:
                self.processed += 1

        root = Widget(10.0, explicit=True)
        explicit_child = Widget(8.0, explicit=True)
        inherited_child = Widget(10.0, explicit=False)
        root.children = [explicit_child, inherited_child]
        application = Application()
        runtime = RuntimeInspector(application, Core, Widgets)

        runtime.scale_explicit_widget_fonts(root, 1.5)

        self.assertEqual(root.current_font.point_size, 15.0)
        self.assertEqual(explicit_child.current_font.point_size, 12.0)
        self.assertEqual(inherited_child.current_font.point_size, 10.0)
        self.assertEqual(inherited_child.set_calls, 0)
        self.assertEqual(application.processed, 1)

    def test_preview_names_preserve_ui_names_and_relative_directories(self) -> None:
        root = Path("forms")
        used: set[str] = set()

        first = _preview_name(root / "dialogs" / "SETTINGS.ui", root, used)
        second = _preview_name(root / "advanced" / "SETTINGS.ui", root, used)

        self.assertEqual(first, Path("dialogs/SETTINGS.png"))
        self.assertEqual(second, Path("advanced/SETTINGS.png"))

    def test_preview_names_disambiguate_case_insensitive_collisions(self) -> None:
        root = Path("forms")
        used: set[str] = set()

        first = _preview_name(root / "DIALOG.ui", root, used)
        second = _preview_name(root / "dialog.ui", root, used)

        self.assertEqual(first, Path("DIALOG.png"))
        self.assertEqual(second, Path("dialog_2.png"))

    def test_preview_uses_native_windows_qt_platform(self) -> None:
        request = {"forms": [{"preview_path": "preview.png"}]}

        with patch.dict(os.environ, {}, clear=True), patch(
            "rc2ui.qtcheck.runner.sys.platform",
            "win32",
        ):
            environment = _worker_environment(request)

        self.assertNotIn("QT_QPA_PLATFORM", environment)

    def test_validation_without_preview_uses_offscreen_platform(self) -> None:
        request = {"forms": [{"preview_path": None}]}

        with patch.dict(os.environ, {}, clear=True), patch(
            "rc2ui.qtcheck.runner.sys.platform",
            "win32",
        ):
            environment = _worker_environment(request)

        self.assertEqual(environment["QT_QPA_PLATFORM"], "offscreen")

    def test_explicit_qt_platform_is_preserved_for_preview(self) -> None:
        request = {"forms": [{"preview_path": "preview.png"}]}

        with patch.dict(
            os.environ,
            {"QT_QPA_PLATFORM": "minimal"},
            clear=True,
        ):
            environment = _worker_environment(request)

        self.assertEqual(environment["QT_QPA_PLATFORM"], "minimal")

    def test_headless_linux_preview_uses_offscreen_platform(self) -> None:
        request = {"forms": [{"preview_path": "preview.png"}]}

        with patch.dict(os.environ, {}, clear=True), patch(
            "rc2ui.qtcheck.runner.sys.platform",
            "linux",
        ):
            environment = _worker_environment(request)

        self.assertEqual(environment["QT_QPA_PLATFORM"], "offscreen")

    def test_discovers_pyside_when_pyqt_is_unavailable(self) -> None:
        def find_spec(name: str) -> object | None:
            return object() if name in {"PySide6", "PySide6.QtUiTools"} else None

        with patch(
            "rc2ui.qtcheck.discovery.importlib.util.find_spec",
            side_effect=find_spec,
        ):
            availability = discover_qt_binding()

        self.assertTrue(availability.available)
        self.assertEqual(availability.binding, "PySide6")

    def test_substitutes_chained_custom_widgets_and_collects_buddies(self) -> None:
        prepared = prepare_ui_xml(
            """<?xml version="1.0"?>
<ui version="4.0">
 <class>SampleDialog</class>
 <widget class="QDialog" name="sampleDialog">
  <widget class="QLabel" name="nameLabel">
   <property name="buddy"><cstring>customTree</cstring></property>
  </widget>
  <widget class="CompanyTree" name="customTree"/>
 </widget>
 <customwidgets>
  <customwidget>
   <class>CompanyBase</class><extends>QTreeWidget</extends><header>base.h</header>
  </customwidget>
  <customwidget>
   <class>CompanyTree</class><extends>CompanyBase</extends><header>tree.h</header>
  </customwidget>
 </customwidgets>
</ui>
"""
        )

        self.assertNotIn("customwidgets", prepared.text)
        self.assertIn('class="QTreeWidget" name="customTree"', prepared.text)
        self.assertEqual(prepared.substitutions[0].custom_class, "CompanyTree")
        self.assertEqual(prepared.substitutions[0].base_class, "QTreeWidget")
        self.assertEqual(prepared.buddies[0].buddy_name, "customTree")
        self.assertEqual(
            prepared.widget_names,
            ("sampleDialog", "nameLabel", "customTree"),
        )
        self.assertIsNone(prepared.serialized_size)

    def test_reads_serialized_designer_canvas_size(self) -> None:
        prepared = prepare_ui_xml(
            """<?xml version="1.0"?>
<ui version="4.0">
 <class>SampleDialog</class>
 <widget class="QDialog" name="sampleDialog">
  <property name="geometry"><rect><x>0</x><y>0</y><width>321</width><height>123</height></rect></property>
 </widget>
</ui>
"""
        )

        self.assertEqual(prepared.serialized_size, (321, 123))

    def test_auto_mode_is_silent_when_pyqt_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            report = Path(directory_name, "report.json")
            with patch(
                "rc2ui.qtcheck.runner.discover_qt_binding",
                return_value=QtBindingAvailability(False, "not installed"),
            ):
                result = run_qt_checks(
                    (),
                    report_path=report,
                    required=False,
                )

        self.assertFalse(result.available)
        self.assertEqual(result.diagnostics, ())
        self.assertIsNone(result.report_path)

    def test_required_mode_reports_missing_pyqt(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            report = Path(directory_name, "report.json")
            with patch(
                "rc2ui.qtcheck.runner.discover_qt_binding",
                return_value=QtBindingAvailability(False, "not installed"),
            ):
                result = run_qt_checks(
                    (),
                    report_path=report,
                    required=True,
                )
            payload = json.loads(report.read_text(encoding="utf-8"))

        self.assertFalse(result.available)
        self.assertEqual(result.diagnostics[0].code, "qt.unavailable")
        self.assertEqual(result.diagnostics[0].severity, "error")
        self.assertFalse(payload["available"])

    def test_rejects_nonpositive_runtime_font_scale(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            report = Path(directory_name, "report.json")
            with self.assertRaisesRegex(ValueError, "positive finite"):
                run_qt_checks(
                    (),
                    report_path=report,
                    required=False,
                    font_scale=0,
                )

    def test_invalid_worker_response_becomes_a_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            report = Path(directory_name, "report.json")
            with patch(
                "rc2ui.qtcheck.runner.discover_qt_binding",
                return_value=QtBindingAvailability(True),
            ), patch(
                "rc2ui.qtcheck.runner._run_worker",
                return_value={"diagnostics": "invalid", "forms": []},
            ):
                result = run_qt_checks(
                    (),
                    report_path=report,
                    required=True,
                )
            payload = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(result.diagnostics[0].code, "qt.worker-error")
        self.assertEqual(payload["diagnostics"][0]["code"], "qt.worker-error")

    def test_runtime_failure_is_isolated_to_one_form(self) -> None:
        events: list[str] = []

        class Root:
            def close(self) -> None:
                pass

            def deleteLater(self) -> None:
                pass

        class Uic:
            @staticmethod
            def compileUi(path: str, stream: object) -> None:
                pass

            @staticmethod
            def loadUi(path: str) -> Root:
                return Root()

        class Runtime:
            def capture_preview(self, *args: object, **kwargs: object) -> None:
                events.append("preview")

            def inspect(self, *args: object, **kwargs: object) -> None:
                events.append("inspect")
                raise RuntimeError("broken widget")

            def process_events(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            ui = root / "sample.ui"
            ui.write_text(
                '<ui version="4.0"><widget class="QDialog" '
                'name="sampleDialog"/></ui>',
                encoding="utf-8",
            )
            result = inspect_form(
                {
                    "path": str(ui),
                    "preview_path": str(root / "sample.png"),
                    "geometry_reference": {
                        "rect_dlu": [0, 0, 100, 50],
                        "controls": [],
                    },
                },
                index=0,
                temporary_dir=root,
                factors=(1.0,),
                font_factor=2.0,
                runtime=Runtime(),
                uic=Uic(),
            )

        self.assertTrue(result["loaded"])
        self.assertTrue(result["preview_attempted"])
        self.assertEqual(result["preview"], str(root / "sample.png"))
        self.assertEqual(events, ["preview", "inspect"])
        self.assertTrue(result["source_geometry_checked"])
        self.assertEqual(result["diagnostics"][0]["code"], "qt.runtime-error")

    def test_preview_failure_does_not_prevent_runtime_checks(self) -> None:
        events: list[str] = []

        class Root:
            def close(self) -> None:
                pass

            def deleteLater(self) -> None:
                pass

        class Uic:
            @staticmethod
            def compileUi(path: str, stream: object) -> None:
                pass

            @staticmethod
            def loadUi(path: str) -> Root:
                return Root()

        class Runtime:
            def capture_preview(self, *args: object, **kwargs: object) -> None:
                events.append("preview")
                raise OSError("cannot save image")

            def inspect(self, *args: object, **kwargs: object) -> None:
                events.append("inspect")

            def process_events(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            ui = root / "sample.ui"
            ui.write_text(
                '<ui version="4.0"><widget class="QDialog" '
                'name="sampleDialog"/></ui>',
                encoding="utf-8",
            )
            result = inspect_form(
                {
                    "path": str(ui),
                    "preview_path": str(root / "sample.png"),
                },
                index=0,
                temporary_dir=root,
                factors=(1.0,),
                font_factor=2.0,
                runtime=Runtime(),
                uic=Uic(),
            )

        self.assertTrue(result["loaded"])
        self.assertTrue(result["preview_attempted"])
        self.assertIsNone(result["preview"])
        self.assertEqual(events, ["preview", "inspect"])
        self.assertEqual(result["diagnostics"][0]["code"], "qt.preview-error")

    def test_compile_failure_does_not_prevent_preview_attempt(self) -> None:
        events: list[str] = []

        class Root:
            def close(self) -> None:
                pass

            def deleteLater(self) -> None:
                pass

        class Uic:
            @staticmethod
            def compileUi(path: str, stream: object) -> None:
                raise ValueError("cannot generate Python")

            @staticmethod
            def loadUi(path: str) -> Root:
                events.append("load")
                return Root()

        class Runtime:
            def capture_preview(self, *args: object, **kwargs: object) -> None:
                events.append("preview")

            def inspect(self, *args: object, **kwargs: object) -> None:
                events.append("inspect")

            def process_events(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            ui = root / "sample.ui"
            ui.write_text(
                '<ui version="4.0"><widget class="QDialog" '
                'name="sampleDialog"/></ui>',
                encoding="utf-8",
            )
            preview = root / "sample.png"
            result = inspect_form(
                {
                    "path": str(ui),
                    "preview_path": str(preview),
                },
                index=0,
                temporary_dir=root,
                factors=(1.0,),
                font_factor=2.0,
                runtime=Runtime(),
                uic=Uic(),
            )

        self.assertFalse(result["compiled"])
        self.assertTrue(result["loaded"])
        self.assertTrue(result["preview_attempted"])
        self.assertEqual(result["preview"], str(preview))
        self.assertEqual(events, ["load", "preview", "inspect"])
        self.assertEqual(result["diagnostics"][0]["code"], "qt.compile-error")

    def test_isolated_worker_writes_metrics_report_and_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            forms = root / "forms"
            forms.mkdir()
            ui = forms / "sample.ui"
            ui.write_text("<ui/>", encoding="utf-8")
            preview = root / "previews"
            report = root / "qt-report.json"
            with patch(
                "rc2ui.qtcheck.runner.discover_qt_binding",
                return_value=QtBindingAvailability(True),
            ):
                result = run_qt_checks(
                    (ui,),
                    report_path=report,
                    required=True,
                    preview_dir=preview,
                    ui_root=forms,
                    font_scale=1.25,
                    worker_module="tests.fake_qt_worker",
                )
            payload = json.loads(report.read_text(encoding="utf-8"))
            gallery = result.preview_index.read_text(encoding="utf-8")

        self.assertTrue(result.available)
        self.assertEqual(result.checked_forms, 1)
        self.assertEqual(result.binding_version, "6.fake")
        self.assertEqual(payload["font_scale"], 1.25)
        self.assertEqual(result.diagnostics[0].code, "qt.fake-warning")
        self.assertEqual(payload["summary"]["forms"], 1)
        self.assertEqual(payload["summary"]["loaded"], 1)
        self.assertEqual(
            payload["summary"]["preview"],
            {
                "requested": 1,
                "attempted": 1,
                "saved": 1,
                "failed": 0,
                "failure_diagnostics": [],
            },
        )
        self.assertEqual(result.diagnostics[-1].code, "qt.preview-summary")
        self.assertEqual(
            payload["forms"][0]["metrics"]["sampleDialog"]["size_hint"],
            [320, 200],
        )
        self.assertIn("sample.ui", gallery)

    def test_report_summary_explains_preview_blockers(self) -> None:
        forms = [
            {
                "path": "one.ui",
                "prepared": True,
                "compiled": False,
                "loaded": False,
                "preview_requested": True,
                "preview_attempted": False,
                "preview": None,
                "diagnostics": [
                    {
                        "code": "qt.compile-error",
                        "severity": "error",
                        "message": "compile rejected the enum",
                    },
                    {
                        "code": "qt.load-error",
                        "severity": "error",
                        "message": "loader rejected the enum",
                    },
                ],
            },
            {
                "path": "two.ui",
                "prepared": True,
                "compiled": True,
                "loaded": True,
                "preview_requested": True,
                "preview_attempted": True,
                "preview": None,
                "diagnostics": [
                    {
                        "code": "qt.preview-error",
                        "severity": "warning",
                        "message": "Qt returned false while saving PNG",
                    }
                ],
            },
        ]

        summary = build_report_summary(forms)
        diagnostic = preview_summary_diagnostic(summary)

        self.assertEqual(summary["forms"], 2)
        self.assertEqual(summary["prepared"], 2)
        self.assertEqual(summary["compiled"], 1)
        self.assertEqual(summary["loaded"], 1)
        preview = summary["preview"]
        self.assertEqual(preview["requested"], 2)
        self.assertEqual(preview["attempted"], 1)
        self.assertEqual(preview["saved"], 0)
        self.assertEqual(preview["failed"], 2)
        groups = preview["failure_diagnostics"]
        self.assertEqual(groups[0]["code"], "qt.load-error")
        self.assertEqual(groups[0]["forms"], 1)
        self.assertEqual(groups[1]["code"], "qt.preview-error")
        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic.code, "qt.preview-summary")
        self.assertEqual(diagnostic.severity, "warning")
        self.assertIn("requested for 2", diagnostic.message)

    @unittest.skipUnless(
        discover_qt_binding().available,
        "Qt 6 binding is not installed",
    )
    def test_real_qt_binding_loads_a_designer_form(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            ui = root / "sample.ui"
            report = root / "report.json"
            ui.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<ui version="4.0">
 <class>sampleDialog</class>
 <widget class="QDialog" name="sampleDialog">
  <property name="geometry">
   <rect><x>0</x><y>0</y><width>200</width><height>80</height></rect>
  </property>
  <layout class="QVBoxLayout" name="verticalLayout">
   <item>
    <widget class="QLabel" name="sampleLabel">
     <property name="text"><string>Sample</string></property>
    </widget>
   </item>
  </layout>
 </widget>
 <resources/><connections/>
</ui>
""",
                encoding="utf-8",
            )

            result = run_qt_checks(
                (ui,),
                report_path=report,
                required=True,
            )
            payload = json.loads(report.read_text(encoding="utf-8"))

        self.assertTrue(result.available)
        self.assertEqual(result.checked_forms, 1)
        self.assertIn(payload["binding"], {"PyQt6", "PySide6"})

    @unittest.skipUnless(
        discover_qt_binding().available,
        "Qt 6 binding is not installed",
    )
    def test_generated_form_survives_dynamic_font_change(self) -> None:
        dialog = sample_dialog()
        mapped = tuple(
            ControlMapper().map(control) for control in dialog.controls
        )
        naming = NameResolver().resolve(dialog, mapped)
        generated = LayoutBuilder().build(dialog, mapped, naming)

        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            ui = root / "sample.ui"
            report = root / "report.json"
            ui.write_text(
                emit_ui(generated.root_widget),
                encoding="utf-8",
            )

            result = run_qt_checks(
                (ui,),
                report_path=report,
                required=True,
            )
            payload = json.loads(report.read_text(encoding="utf-8"))

        self.assertFalse(
            any(
                diagnostic.severity is Severity.ERROR
                for diagnostic in result.diagnostics
            )
        )
        self.assertTrue(payload["forms"][0]["font_test"]["passed"])
        self.assertGreater(
            payload["forms"][0]["font_test"]["font_point_size_after"],
            payload["forms"][0]["font_test"]["font_point_size_before"],
        )
        self.assertGreater(
            payload["forms"][0]["font_test"]["form_size_after"][0],
            payload["forms"][0]["font_test"]["form_size_before"][0],
        )
        self.assertGreater(
            payload["forms"][0]["font_test"]["form_size_after"][1],
            payload["forms"][0]["font_test"]["form_size_before"][1],
        )

    @unittest.skipUnless(
        discover_qt_binding().available,
        "Qt 6 binding is not installed",
    )
    def test_dense_multiline_checkbox_renders_without_clipped_text(self) -> None:
        dialog = dense_multiline_dialog()
        mapped = tuple(
            ControlMapper().map(control) for control in dialog.controls
        )
        naming = NameResolver().resolve(dialog, mapped)
        generated = LayoutBuilder().build(dialog, mapped, naming)

        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            ui = root / "sample.ui"
            report = root / "report.json"
            ui.write_text(emit_ui(generated.root_widget), encoding="utf-8")

            run_qt_checks(
                (ui,),
                report_path=report,
                required=True,
            )
            form = json.loads(report.read_text(encoding="utf-8"))["forms"][0]

        self.assertTrue(form["font_test"]["passed"])
        self.assertFalse(
            any(
                diagnostic["code"] == "qt.clipped-text"
                and "preserveItems" in diagnostic["message"]
                for diagnostic in form["diagnostics"]
            )
        )

    @unittest.skipUnless(
        discover_qt_binding().available,
        "Qt 6 binding is not installed",
    )
    def test_distant_guides_cannot_reverse_runtime_gap_affinity(self) -> None:
        specs = [
            ("Edit", "", 0, RectDlu(55, 10, 106, 12)),
            ("Edit", "", 0, RectDlu(55, 25, 106, 12)),
            ("msctls_updown32", "Spin", 0xB0, RectDlu(93, 43, 10, 12)),
            ("msctls_updown32", "Spin", 0xB0, RectDlu(93, 58, 11, 12)),
            ("Button", "Option", 9, RectDlu(95, 73, 55, 12)),
            ("Static", "Maximum", 1, RectDlu(5, 87, 45, 12)),
            ("Button", "10000", 0x200, RectDlu(53, 87, 37, 12)),
            ("Static", "unit.", 1, RectDlu(95, 87, 21, 12)),
            ("Button", "10000", 0x200, RectDlu(119, 87, 37, 12)),
        ]
        dialog = replace(
            make_dialog(specs),
            rect=RectDlu(0, 0, 180, 110),
            font=DialogFont(9, "Arial"),
        )
        mapped = tuple(
            ControlMapper().map(control) for control in dialog.controls
        )
        naming = NameResolver().resolve(dialog, mapped)
        generated = LayoutBuilder().build(dialog, mapped, naming)

        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            ui = root / "sample.ui"
            report = root / "report.json"
            ui.write_text(emit_ui(generated.root_widget), encoding="utf-8")
            reference = FormGeometryReference(
                rect_dlu=(0, 0, 180, 110),
                layout_rect_dlu=(
                    generated.layout_bounds.x,
                    generated.layout_bounds.y,
                    generated.layout_bounds.width,
                    generated.layout_bounds.height,
                ),
                controls=tuple(
                    ControlGeometryReference(
                        object_name=naming.for_order(control.order).object_name,
                        rect_dlu=(
                            control.rect.x,
                            control.rect.y,
                            control.rect.width,
                            control.rect.height,
                        ),
                        layout_rect_dlu=(
                            generated.rect_for(control.order).x,
                            generated.rect_for(control.order).y,
                            generated.rect_for(control.order).width,
                            generated.rect_for(control.order).height,
                        ),
                        qt_class=mapped[control.order].qt_class,
                        horizontal_anchor=(
                            generated.anchors_for(control.order)[0]
                        ),
                        vertical_anchor=(
                            generated.anchors_for(control.order)[1]
                        ),
                    )
                    for control in dialog.controls
                ),
            )

            run_qt_checks(
                (ui,),
                report_path=report,
                required=True,
                geometry_references={ui: reference},
            )
            form = json.loads(report.read_text(encoding="utf-8"))["forms"][0]

        self.assertFalse(
            any(
                item["code"] == "qt.source-gap-affinity-changed"
                for item in form["diagnostics"]
            )
        )

    def test_runner_passes_source_geometry_to_worker(self) -> None:
        captured: dict[str, object] = {}

        def worker(
            request: dict[str, object],
            *,
            worker_module: str,
        ) -> dict[str, object]:
            captured.update(request)
            return {"available": True, "forms": [], "diagnostics": []}

        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            ui = root / "sample.ui"
            ui.write_text("<ui/>", encoding="utf-8")
            reference = FormGeometryReference(
                rect_dlu=(0, 0, 200, 100),
                controls=(
                    ControlGeometryReference("nameEdit", (60, 10, 120, 14)),
                ),
            )
            with patch(
                "rc2ui.qtcheck.runner.discover_qt_binding",
                return_value=QtBindingAvailability(True),
            ), patch(
                "rc2ui.qtcheck.runner._run_worker",
                side_effect=worker,
            ):
                run_qt_checks(
                    (ui,),
                    report_path=root / "report.json",
                    required=True,
                    font_scale=1.25,
                    geometry_references={ui: reference},
                )

        forms = captured["forms"]
        self.assertEqual(captured["font_factor"], 2.0)
        self.assertEqual(captured["font_scale"], 1.25)
        self.assertEqual(
            forms[0]["geometry_reference"]["controls"][0]["object_name"],
            "nameEdit",
        )

    def test_runner_loads_source_geometry_from_conversion_report(self) -> None:
        captured: dict[str, object] = {}

        def worker(
            request: dict[str, object],
            *,
            worker_module: str,
        ) -> dict[str, object]:
            captured.update(request)
            return {"available": True, "forms": [], "diagnostics": []}

        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            ui = root / "sample.ui"
            ui.write_text("<ui/>", encoding="utf-8")
            (root / "rc2ui-report.json").write_text(
                json.dumps(
                    {
                        "forms": [
                            {
                                "output": str(ui),
                                "default_rect_dlu": [0, 0, 180, 80],
                                "layout_rect_dlu": [0, 0, 200, 100],
                                "controls": [
                                    {
                                        "object_name": "divider",
                                        "rect_dlu": [89, 5, 1, 70],
                                        "layout_rect_dlu": [99, 5, 1, 90],
                                        "separator_orientation": "vertical",
                                        "qt_class": "QFrame",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "rc2ui.qtcheck.runner.discover_qt_binding",
                return_value=QtBindingAvailability(True),
            ), patch(
                "rc2ui.qtcheck.runner._run_worker",
                side_effect=worker,
            ):
                run_qt_checks(
                    (ui,),
                    report_path=root / "qt-report.json",
                    required=True,
                )

        forms = captured["forms"]
        reference = forms[0]["geometry_reference"]
        self.assertEqual(reference["rect_dlu"], (0, 0, 180, 80))
        self.assertEqual(reference["layout_rect_dlu"], (0, 0, 200, 100))
        self.assertEqual(
            reference["controls"][0]["rect_dlu"],
            (89, 5, 1, 70),
        )
        self.assertEqual(
            reference["controls"][0]["layout_rect_dlu"],
            (99, 5, 1, 90),
        )
        self.assertEqual(
            reference["controls"][0]["separator_orientation"],
            "vertical",
        )
        self.assertEqual(reference["controls"][0]["qt_class"], "QFrame")

    def test_finds_ui_files_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            nested = root / "nested"
            nested.mkdir()
            first = root / "one.ui"
            second = nested / "two.UI"
            first.write_text("", encoding="utf-8")
            second.write_text("", encoding="utf-8")

            found = find_ui_files((root,))

        self.assertEqual(set(found), {first.resolve(), second.resolve()})

    def test_stretch_analysis_reports_only_non_growing_expanding_axis(self) -> None:
        def item(
            width: int,
            height: int,
            *,
            horizontal: str,
            vertical: str,
        ) -> dict[str, object]:
            return {
                "geometry": [0, 0, width, height],
                "parent_size": [
                    400 if width > 100 else 200,
                    300 if height > 30 else 100,
                ],
                "horizontal_policy": horizontal,
                "vertical_policy": vertical,
                "visible": True,
            }

        snapshots = [
            {
                "form_size": [200, 100],
                "widgets": {
                    "stuckEdit": item(
                        100,
                        30,
                        horizontal="Expanding",
                        vertical="Fixed",
                    ),
                    "growingList": item(
                        100,
                        30,
                        horizontal="Expanding",
                        vertical="Expanding",
                    ),
                },
            },
            {
                "form_size": [400, 300],
                "widgets": {
                    "stuckEdit": {
                        **item(
                            100,
                            30,
                            horizontal="Expanding",
                            vertical="Fixed",
                        ),
                        "parent_size": [400, 300],
                    },
                    "growingList": {
                        **item(
                            260,
                            180,
                            horizontal="Expanding",
                            vertical="Expanding",
                        ),
                        "parent_size": [400, 300],
                    },
                },
            },
        ]

        diagnostics = analyze_stretch(snapshots, path=Path("sample.ui"))

        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["code"], "qt.no-horizontal-growth")
        self.assertIn("stuckEdit", diagnostics[0]["message"])

    def test_font_change_reports_new_vertical_clipping(self) -> None:
        baseline = _font_snapshot(
            {
                "nameEdit": ([10, 10, 100, 24], [80, 24], 20, 18),
            }
        )
        scaled = _font_snapshot(
            {
                "nameEdit": ([10, 10, 100, 24], [80, 40], 20, 34),
            }
        )

        diagnostics = analyze_font_change(
            baseline,
            scaled,
            path=Path("sample.ui"),
            expected_overlaps=set(),
        )

        [clipping] = [
            item
            for item in diagnostics
            if item["code"] == "qt.font-height-clipped"
        ]
        self.assertEqual(clipping["severity"], "error")
        self.assertIn("nameEdit", clipping["message"])

    def test_font_order_ignores_unrelated_disjoint_panes(self) -> None:
        baseline = _font_snapshot(
            {
                "leftGroup": ([0, 60, 80, 30], [20, 20], 30, 12),
                "rightEdit": ([100, 20, 80, 24], [60, 24], 24, 12),
            }
        )
        scaled = _font_snapshot(
            {
                "leftGroup": ([0, 40, 80, 60], [20, 40], 60, 24),
                "rightEdit": ([100, 20, 80, 40], [60, 40], 40, 24),
            }
        )

        diagnostics = analyze_font_change(
            baseline,
            scaled,
            path=Path("sample.ui"),
            expected_overlaps=set(),
        )

        self.assertFalse(
            any(item["code"] == "qt.font-order-changed" for item in diagnostics)
        )

    def test_font_order_tolerates_three_pixel_shared_edge_rounding(self) -> None:
        baseline = _font_snapshot(
            {
                "icon": ([0, 0, 20, 20], [10, 10], 20, 10),
                "label": ([0, 20, 50, 10], [20, 10], 10, 10),
            }
        )
        scaled = _font_snapshot(
            {
                "icon": ([0, 0, 40, 39], [20, 20], 39, 20),
                "label": ([0, 36, 100, 20], [40, 20], 20, 20),
            }
        )

        diagnostics = analyze_font_change(
            baseline,
            scaled,
            path=Path("sample.ui"),
            expected_overlaps=set(),
        )

        self.assertFalse(
            any(item["code"] == "qt.font-order-changed" for item in diagnostics)
        )

    def test_font_order_reports_a_clear_crossing(self) -> None:
        baseline = _font_snapshot(
            {
                "icon": ([0, 0, 20, 20], [10, 10], 20, 10),
                "label": ([0, 20, 50, 10], [20, 10], 10, 10),
            }
        )
        scaled = _font_snapshot(
            {
                "icon": ([0, 0, 40, 42], [20, 20], 42, 20),
                "label": ([0, 36, 100, 20], [40, 20], 20, 20),
            }
        )

        diagnostics = analyze_font_change(
            baseline,
            scaled,
            path=Path("sample.ui"),
            expected_overlaps=set(),
        )

        self.assertTrue(
            any(item["code"] == "qt.font-order-changed" for item in diagnostics)
        )

    def test_source_geometry_reports_radically_moved_widget(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _snapshot(
                    {
                        "runawayEdit": [10, 155, 60, 24],
                    }
                )
            ],
            _reference(
                (
                    {
                        "object_name": "runawayEdit",
                        "rect_dlu": [140, 8, 30, 14],
                    },
                )
            ),
            path=Path("sample.ui"),
        )

        [drift] = [
            item
            for item in diagnostics
            if item["code"] == "qt.source-geometry-drift"
        ]
        self.assertEqual(drift["severity"], "error")
        self.assertIn("runawayEdit", drift["message"])

    def test_source_geometry_reports_broken_long_range_anchor(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _snapshot(
                    {
                        "firstEdit": [20, 20, 100, 24],
                        "secondEdit": [20, 80, 100, 24],
                        "thirdEdit": [20, 140, 100, 24],
                    }
                ),
                _snapshot(
                    {
                        "firstEdit": [20, 20, 100, 24],
                        "secondEdit": [22, 80, 100, 24],
                        "thirdEdit": [55, 140, 100, 24],
                    },
                    baseline=False,
                )
            ],
            _reference(
                (
                    {"object_name": "firstEdit", "rect_dlu": [10, 10, 50, 14]},
                    {"object_name": "secondEdit", "rect_dlu": [10, 40, 50, 14]},
                    {"object_name": "thirdEdit", "rect_dlu": [10, 70, 50, 14]},
                )
            ),
            path=Path("sample.ui"),
        )

        anchors = [
            item
            for item in diagnostics
            if item["code"] == "qt.source-anchor-drift"
        ]
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["severity"], "error")
        self.assertIn("horizontal start", anchors[0]["message"])

    def test_source_geometry_enforces_two_control_alignment(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _snapshot(
                    {
                        "firstEdit": [20, 20, 100, 24],
                        "secondEdit": [70, 100, 100, 24],
                    }
                )
            ],
            _reference(
                (
                    {"object_name": "firstEdit", "rect_dlu": [10, 10, 50, 14]},
                    {"object_name": "secondEdit", "rect_dlu": [11, 50, 50, 14]},
                )
            ),
            path=Path("sample.ui"),
        )

        [anchors] = [
            item
            for item in diagnostics
            if item["code"] == "qt.source-anchor-drift"
        ]
        self.assertEqual(anchors["severity"], "error")
        self.assertIn("horizontal start", anchors["message"])

    def test_source_geometry_enforces_declared_anchor_across_groups(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _snapshot(
                    {
                        "leftGroup": [10, 10, 170, 100],
                        "leftEdit": [25, 45, 130, 24],
                        "rightGroup": [200, 10, 170, 100],
                        "rightCombo": [215, 55, 130, 24],
                    }
                )
            ],
            _reference(
                (
                    {
                        "object_name": "leftGroup",
                        "rect_dlu": [5, 5, 85, 50],
                        "qt_class": "QGroupBox",
                    },
                    {
                        "object_name": "leftEdit",
                        "rect_dlu": [12, 20, 65, 14],
                        "qt_class": "QLineEdit",
                        "vertical_anchor": ["start", 40],
                    },
                    {
                        "object_name": "rightGroup",
                        "rect_dlu": [100, 5, 85, 50],
                        "qt_class": "QGroupBox",
                    },
                    {
                        "object_name": "rightCombo",
                        "rect_dlu": [107, 20, 65, 14],
                        "qt_class": "QComboBox",
                        "vertical_anchor": ["start", 40],
                    },
                )
            ),
            path=Path("sample.ui"),
        )

        [anchors] = [
            item
            for item in diagnostics
            if item["code"] == "qt.source-anchor-drift"
        ]
        self.assertIn("vertical start", anchors["message"])
        self.assertIn("leftEdit", anchors["message"])
        self.assertIn("rightCombo", anchors["message"])

    def test_source_geometry_prefers_exact_preserved_edge_over_fuzzy_center(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _snapshot(
                    {
                        "wideLabel": [20, 20, 120, 24],
                        "button": [20, 100, 80, 24],
                    }
                )
            ],
            _reference(
                (
                    {"object_name": "wideLabel", "rect_dlu": [10, 10, 60, 14]},
                    {"object_name": "button", "rect_dlu": [10, 50, 55, 14]},
                )
            ),
            path=Path("sample.ui"),
        )

        self.assertFalse(
            any(item["code"] == "qt.source-anchor-drift" for item in diagnostics)
        )

    def test_source_geometry_enforces_non_overlapping_partial_order(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _snapshot(
                    {
                        "leftButton": [220, 20, 80, 24],
                        "rightButton": [100, 20, 80, 24],
                    }
                )
            ],
            _reference(
                (
                    {"object_name": "leftButton", "rect_dlu": [10, 10, 40, 14]},
                    {"object_name": "rightButton", "rect_dlu": [80, 10, 40, 14]},
                )
            ),
            path=Path("sample.ui"),
        )

        [ordering] = [
            item
            for item in diagnostics
            if item["code"] == "qt.source-order-changed"
        ]
        self.assertIn("left-to-right", ordering["message"])

    def test_source_order_uses_raw_rc_before_layout_corrections(self) -> None:
        reference = _reference(
            (
                {
                    "object_name": "rowAEdit",
                    "rect_dlu": [50, 10, 80, 14],
                    "layout_rect_dlu": [50, 20, 80, 14],
                },
                {
                    "object_name": "rowBEdit",
                    "rect_dlu": [50, 30, 80, 14],
                    "layout_rect_dlu": [50, 20, 80, 14],
                },
            )
        )
        reference["layout_rect_dlu"] = [0, 0, 200, 100]

        diagnostics = analyze_source_geometry(
            [
                _snapshot(
                    {
                        "rowAEdit": [100, 40, 160, 24],
                        "rowBEdit": [100, 40, 160, 24],
                    }
                )
            ],
            reference,
            path=Path("sample.ui"),
        )

        [ordering] = [
            item
            for item in diagnostics
            if item["code"] == "qt.source-order-changed"
        ]
        self.assertIn("top-to-bottom", ordering["message"])

    def test_source_geometry_does_not_order_overlapping_layers_by_center(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _snapshot(
                    {
                        "firstLayer": [20, 32, 160, 30],
                        "secondLayer": [20, 20, 160, 30],
                    }
                )
            ],
            _reference(
                (
                    {"object_name": "firstLayer", "rect_dlu": [10, 10, 80, 20]},
                    {"object_name": "secondLayer", "rect_dlu": [10, 12, 80, 20]},
                )
            ),
            path=Path("sample.ui"),
        )

        self.assertFalse(
            any(item["code"] == "qt.source-order-changed" for item in diagnostics)
        )

    def test_source_geometry_reports_control_leaving_group_box(self) -> None:
        snapshot = _snapshot(
            {
                "settingsGroupBox": [10, 10, 300, 120],
                "nameEdit": [30, 40, 200, 24],
            }
        )
        snapshot["widgets"]["settingsGroupBox"]["parent_name"] = "sampleDialog"
        snapshot["widgets"]["nameEdit"]["parent_name"] = "sampleDialog"

        diagnostics = analyze_source_geometry(
            [snapshot],
            _reference(
                (
                    {
                        "object_name": "settingsGroupBox",
                        "rect_dlu": [5, 5, 150, 60],
                        "qt_class": "QGroupBox",
                    },
                    {
                        "object_name": "nameEdit",
                        "rect_dlu": [15, 20, 100, 14],
                        "qt_class": "QLineEdit",
                    },
                )
            ),
            path=Path("sample.ui"),
        )

        [parent] = [
            item
            for item in diagnostics
            if item["code"] == "qt.source-parent-changed"
        ]
        self.assertEqual(parent["severity"], "error")
        self.assertIn("nameEdit", parent["message"])

    def test_source_geometry_requires_gaps_to_participate_in_resize(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _resized_snapshot(
                    [400, 200],
                    {
                        "leftButton": [20, 20, 60, 24],
                        "rightButton": [160, 20, 60, 24],
                    },
                ),
                _resized_snapshot(
                    [800, 400],
                    {
                        "leftButton": [40, 40, 60, 24],
                        "rightButton": [180, 40, 60, 24],
                    },
                    baseline=False,
                ),
            ],
            _reference(
                (
                    {"object_name": "leftButton", "rect_dlu": [10, 10, 30, 14]},
                    {"object_name": "rightButton", "rect_dlu": [80, 10, 30, 14]},
                )
            ),
            path=Path("sample.ui"),
        )

        [gap] = [
            item for item in diagnostics if item["code"] == "qt.source-gap-static"
        ]
        self.assertEqual(gap["severity"], "error")

    def test_source_geometry_accepts_growing_gap(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _resized_snapshot(
                    [400, 200],
                    {
                        "leftButton": [20, 20, 60, 24],
                        "rightButton": [160, 20, 60, 24],
                    },
                ),
                _resized_snapshot(
                    [800, 400],
                    {
                        "leftButton": [40, 40, 60, 24],
                        "rightButton": [280, 40, 60, 24],
                    },
                    baseline=False,
                ),
            ],
            _reference(
                (
                    {"object_name": "leftButton", "rect_dlu": [10, 10, 30, 14]},
                    {"object_name": "rightButton", "rect_dlu": [80, 10, 30, 14]},
                )
            ),
            path=Path("sample.ui"),
        )

        self.assertFalse(
            any(item["code"].startswith("qt.source-gap-") for item in diagnostics)
        )

    def test_source_geometry_accepts_one_pixel_of_rounded_gap_growth(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _resized_snapshot(
                    [400, 200],
                    {
                        "leftButton": [20, 20, 60, 24],
                        "rightButton": [160, 20, 60, 24],
                    },
                ),
                _resized_snapshot(
                    [800, 400],
                    {
                        "leftButton": [40, 40, 60, 24],
                        "rightButton": [181, 40, 60, 24],
                    },
                    baseline=False,
                ),
            ],
            _reference(
                (
                    {"object_name": "leftButton", "rect_dlu": [10, 10, 30, 14]},
                    {"object_name": "rightButton", "rect_dlu": [80, 10, 30, 14]},
                )
            ),
            path=Path("sample.ui"),
        )

        self.assertFalse(
            any(item["code"].startswith("qt.source-gap-") for item in diagnostics)
        )

    def test_source_geometry_reports_reversed_local_gap_affinity(self) -> None:
        reference = _reference(
            (
                {"object_name": "firstValue", "rect_dlu": [53, 40, 37, 12]},
                {"object_name": "unitLabel", "rect_dlu": [95, 40, 21, 12]},
                {"object_name": "secondValue", "rect_dlu": [119, 40, 37, 12]},
            )
        )
        diagnostics = analyze_source_geometry(
            [
                _snapshot(
                    {
                        "firstValue": [106, 80, 74, 24],
                        "unitLabel": [184, 80, 42, 24],
                        "secondValue": [238, 80, 74, 24],
                    }
                )
            ],
            reference,
            path=Path("sample.ui"),
        )

        [affinity] = [
            item
            for item in diagnostics
            if item["code"] == "qt.source-gap-affinity-changed"
        ]
        self.assertEqual(affinity["severity"], "error")
        self.assertIn("firstValue", affinity["message"])
        self.assertIn("RC gaps 5/3", affinity["message"])
        self.assertIn("runtime gaps 4/12", affinity["message"])

    def test_source_geometry_accepts_preserved_local_gap_affinity(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _snapshot(
                    {
                        "firstValue": [106, 80, 74, 24],
                        "unitLabel": [190, 80, 42, 24],
                        "secondValue": [238, 80, 74, 24],
                    }
                )
            ],
            _reference(
                (
                    {
                        "object_name": "firstValue",
                        "rect_dlu": [53, 40, 37, 12],
                    },
                    {
                        "object_name": "unitLabel",
                        "rect_dlu": [95, 40, 21, 12],
                    },
                    {
                        "object_name": "secondValue",
                        "rect_dlu": [119, 40, 37, 12],
                    },
                )
            ),
            path=Path("sample.ui"),
        )

        self.assertFalse(
            any(
                item["code"] == "qt.source-gap-affinity-changed"
                for item in diagnostics
            )
        )

    def test_source_geometry_does_not_compare_different_runtime_layers(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _resized_snapshot(
                    [400, 200],
                    {
                        "firstMode": [20, 20, 100, 24],
                        "secondMode": [20, 20, 100, 24],
                    },
                ),
                _resized_snapshot(
                    [800, 400],
                    {
                        "firstMode": [40, 40, 100, 24],
                        "secondMode": [40, 40, 100, 24],
                    },
                    baseline=False,
                ),
            ],
            _reference(
                (
                    {
                        "object_name": "firstMode",
                        "rect_dlu": [10, 10, 40, 14],
                        "alternative_states": [[0, 0]],
                    },
                    {
                        "object_name": "secondMode",
                        "rect_dlu": [80, 10, 40, 14],
                        "alternative_states": [[0, 1]],
                    },
                )
            ),
            path=Path("sample.ui"),
        )

        self.assertFalse(
            any(
                item["code"] in {
                    "qt.source-order-changed",
                    "qt.source-gap-shrunk",
                    "qt.source-gap-static",
                }
                for item in diagnostics
            )
        )

    def test_source_geometry_compares_layers_from_independent_groups(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _resized_snapshot(
                    [400, 200],
                    {
                        "firstMode": [20, 20, 100, 24],
                        "secondMode": [200, 20, 100, 24],
                    },
                ),
                _resized_snapshot(
                    [800, 400],
                    {
                        "firstMode": [40, 40, 100, 24],
                        "secondMode": [220, 40, 100, 24],
                    },
                    baseline=False,
                ),
            ],
            _reference(
                (
                    {
                        "object_name": "firstMode",
                        "rect_dlu": [10, 10, 40, 14],
                        "alternative_states": [[0, 0]],
                    },
                    {
                        "object_name": "secondMode",
                        "rect_dlu": [80, 10, 40, 14],
                        "alternative_states": [[1, 0]],
                    },
                )
            ),
            path=Path("sample.ui"),
        )

        self.assertTrue(
            any(item["code"] == "qt.source-gap-static" for item in diagnostics)
        )

    def test_source_geometry_reports_control_crossing_separator(self) -> None:
        diagnostics = analyze_source_geometry(
            [
                _snapshot(
                    {
                        "leftList": [260, 20, 100, 160],
                        "separator": [198, 10, 4, 180],
                        "rightEdit": [240, 40, 120, 24],
                    }
                )
            ],
            _reference(
                (
                    {"object_name": "leftList", "rect_dlu": [10, 10, 70, 80]},
                    {
                        "object_name": "separator",
                        "rect_dlu": [99, 5, 1, 90],
                        "separator_orientation": "vertical",
                    },
                    {"object_name": "rightEdit", "rect_dlu": [120, 20, 60, 14]},
                )
            ),
            path=Path("sample.ui"),
        )

        violations = [
            item
            for item in diagnostics
            if item["code"] == "qt.separator-region-violation"
        ]
        self.assertEqual(len(violations), 1)
        self.assertIn("leftList", violations[0]["message"])

    def test_console_diagnostics_are_aggregated_by_code(self) -> None:
        diagnostics = tuple(
            Diagnostic(
                "qt.clipped-text",
                Severity.WARNING,
                f"clipped {index}",
                f"form-{index}.ui",
            )
            for index in range(3)
        )

        summary = summarize_diagnostics(diagnostics)

        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0].code, "qt.clipped-text")
        self.assertIn("3 occurrence(s) across 3 form(s)", summary[0].message)
        self.assertIsNone(summary[0].location)


def _snapshot(
    widgets: dict[str, list[int]],
    *,
    baseline: bool = True,
) -> dict[str, object]:
    return {
        "form_size": [400, 200],
        "baseline": baseline,
        "widgets": {
            name: {
                "root_geometry": geometry,
                "visible": True,
            }
            for name, geometry in widgets.items()
        },
    }


def _reference(controls: tuple[dict[str, object], ...]) -> dict[str, object]:
    return {
        "rect_dlu": [0, 0, 200, 100],
        "controls": list(controls),
    }


def _resized_snapshot(
    form_size: list[int],
    widgets: dict[str, list[int]],
    *,
    baseline: bool = True,
) -> dict[str, object]:
    snapshot = _snapshot(widgets, baseline=baseline)
    snapshot["form_size"] = form_size
    return snapshot


def _font_snapshot(
    widgets: dict[
        str,
        tuple[list[int], list[int], int, int],
    ],
) -> dict[str, object]:
    return {
        "form_size": [300, 180],
        "widgets": {
            name: {
                "geometry": geometry,
                "minimum_size_hint": minimum_hint,
                "contents_height": contents_height,
                "text_required_height": text_height,
                "parent_name": "sampleDialog",
                "visible": True,
            }
            for name, (
                geometry,
                minimum_hint,
                contents_height,
                text_height,
            ) in widgets.items()
        },
    }


if __name__ == "__main__":
    unittest.main()

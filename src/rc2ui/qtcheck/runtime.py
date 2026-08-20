from __future__ import annotations

from pathlib import Path
from typing import Any

from rc2ui.qtcheck.font_scaling import analyze_font_change
from rc2ui.qtcheck.protocol import diagnostic
from rc2ui.qtcheck.runtime_analysis import analyze_stretch
from rc2ui.qtcheck.source_geometry import analyze_source_geometry
from rc2ui.qtcheck.ui_transform import PreparedUiXml


class RuntimeInspector:
    def __init__(self, application: Any, qt_core: Any, qt_widgets: Any) -> None:
        self.application = application
        self.QtCore = qt_core
        self.QtWidgets = qt_widgets

    def process_events(self) -> None:
        self.application.processEvents()

    def scale_application_font(self, factor: float) -> None:
        if abs(factor - 1.0) < 1e-9:
            return
        font = self.application.font()
        fallback_height = None
        if font.pointSizeF() <= 0 and font.pixelSize() <= 0:
            probe = self.QtWidgets.QWidget()
            try:
                probe.setFont(font)
                fallback_height = probe.fontMetrics().height()
            finally:
                probe.deleteLater()
        font = _scaled_font(
            font,
            factor=factor,
            fallback_height=fallback_height,
        )
        self.application.setFont(font)
        self.process_events()

    def scale_explicit_widget_fonts(self, root: Any, factor: float) -> None:
        """Scale .ui font overrides that do not inherit QApplication.font."""

        if abs(factor - 1.0) < 1e-9:
            return
        font_attribute = self.QtCore.Qt.WidgetAttribute.WA_SetFont
        widgets = (root, *root.findChildren(self.QtWidgets.QWidget))
        for widget in widgets:
            if not widget.testAttribute(font_attribute):
                continue
            widget.setFont(
                _scaled_font(
                    widget.font(),
                    factor=factor,
                    fallback_height=widget.fontMetrics().height(),
                )
            )
        self.process_events()

    def environment_metrics(self) -> dict[str, object]:
        screen = self.application.primaryScreen()
        font = self.application.font()
        return {
            "platform": self.application.platformName(),
            "style": self.application.style().objectName(),
            "font_family": font.family(),
            "font_point_size": font.pointSizeF(),
            "logical_dpi": (
                screen.logicalDotsPerInch() if screen is not None else None
            ),
            "device_pixel_ratio": (
                screen.devicePixelRatio() if screen is not None else None
            ),
        }

    def inspect(
        self,
        root: Any,
        prepared: PreparedUiXml,
        *,
        path: Path,
        factors: tuple[float, ...],
        font_factor: float,
        font_factors: tuple[float, ...] | None = None,
        geometry_reference: object,
        result: dict[str, object],
        diagnostics: list[dict[str, str]],
    ) -> None:
        self._show_without_screen(root)
        self._check_designer_size(root, prepared, path, diagnostics)
        if root.layout() is None:
            diagnostics.append(
                diagnostic(
                    "qt.missing-root-layout",
                    "error",
                    "loaded form has no root layout",
                    path,
                )
            )

        widgets, missing_names = self._declared_widgets(
            root,
            prepared.widget_names,
        )
        for name in missing_names:
            diagnostics.append(
                diagnostic(
                    "qt.missing-widget",
                    "error",
                    f"declared widget {name!r} was not created by the Qt UI loader",
                    path,
                )
            )
        self._check_buddies(root, prepared, path, diagnostics)
        expected_overlaps = _source_overlap_pairs(geometry_reference)
        seen: set[tuple[str, ...]] = set()
        tested_sizes: list[list[int]] = []
        snapshots: list[dict[str, object]] = []
        baseline_size: tuple[int, int] | None = None
        for width, height, baseline in self._target_sizes(root, factors):
            root.resize(width, height)
            if root.layout() is not None:
                root.layout().activate()
            self.process_events()
            actual = root.size()
            tested_sizes.append([actual.width(), actual.height()])
            snapshots.append(
                self._runtime_snapshot(
                    widgets,
                    root,
                    baseline=baseline,
                )
            )
            if baseline:
                baseline_size = (actual.width(), actual.height())
            self._check_geometry(
                widgets,
                root=root,
                path=path,
                tested_size=(actual.width(), actual.height()),
                diagnostics=diagnostics,
                seen=seen,
                expected_overlaps=expected_overlaps,
            )
            if baseline:
                self._check_text_clipping(
                    widgets,
                    path=path,
                    diagnostics=diagnostics,
                    seen=seen,
                )
                result["metrics"] = self._widget_metrics(widgets)
        result["tested_sizes"] = tested_sizes
        result["runtime_snapshots"] = snapshots
        diagnostics.extend(analyze_stretch(snapshots, path=path))
        diagnostics.extend(
            analyze_source_geometry(
                snapshots,
                geometry_reference,
                path=path,
            )
        )
        requested_font_factors = font_factors or (font_factor,)
        font_tests: list[dict[str, object]] = []
        if baseline_size is not None:
            for factor in requested_font_factors:
                if abs(factor - 1.0) < 1e-9:
                    continue
                font_tests.append(
                    self._check_dynamic_font(
                        root,
                        widgets,
                        path=path,
                        baseline_size=baseline_size,
                        factor=factor,
                        expected_overlaps=expected_overlaps,
                        diagnostics=diagnostics,
                    )
                )
        result["font_tests"] = font_tests
        if font_tests:
            result["font_test"] = max(
                font_tests,
                key=lambda item: float(item["factor"]),
            )

    def capture_preview(self, root: Any, preview_path: Path) -> None:
        """Render a loaded form before optional runtime checks can fail."""

        self._show_without_screen(root)
        width, height, _baseline = self._target_sizes(root, (1.0,))[0]
        root.resize(width, height)
        if root.layout() is not None:
            root.layout().activate()
        self.process_events()
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        if not root.grab().save(str(preview_path), "PNG"):
            raise OSError("Qt returned false while saving PNG")

    def _show_without_screen(self, root: Any) -> None:
        root.setAttribute(
            self.QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen,
            True,
        )
        root.ensurePolished()
        root.show()
        self.process_events()

    def _check_dynamic_font(
        self,
        root: Any,
        widgets: tuple[Any, ...],
        *,
        path: Path,
        baseline_size: tuple[int, int],
        factor: float,
        expected_overlaps: set[tuple[str, str]],
        diagnostics: list[dict[str, str]],
    ) -> dict[str, object]:
        root.resize(*baseline_size)
        if root.layout() is not None:
            root.layout().activate()
        self.process_events()
        before = self._runtime_snapshot(
            widgets,
            root,
            baseline=True,
            include_font_requirements=True,
        )
        original_font = root.font()
        try:
            scaled_font = _scaled_font(
                root.font(),
                factor=factor,
                fallback_height=root.fontMetrics().height(),
            )
            root.setFont(scaled_font)
            root.ensurePolished()
            self.process_events()
            after = self._runtime_snapshot(
                widgets,
                root,
                baseline=False,
                include_font_requirements=True,
            )
            font_diagnostics = analyze_font_change(
                before,
                after,
                path=path,
                expected_overlaps=expected_overlaps,
            )
            diagnostics.extend(font_diagnostics)
            return {
                "factor": factor,
                "font_point_size_before": original_font.pointSizeF(),
                "font_point_size_after": root.font().pointSizeF(),
                "form_size_before": before["form_size"],
                "form_size_after": after["form_size"],
                "passed": not font_diagnostics,
            }
        finally:
            root.setFont(original_font)
            root.ensurePolished()
            self.process_events()

    def _declared_widgets(
        self,
        root: Any,
        names: tuple[str, ...],
    ) -> tuple[tuple[Any, ...], tuple[str, ...]]:
        by_name = {root.objectName(): root}
        for widget in root.findChildren(self.QtWidgets.QWidget):
            by_name.setdefault(widget.objectName(), widget)
        widgets = tuple(
            by_name[name]
            for name in names
            if (
                name in by_name
                and by_name[name].window() is root
                and not bool(by_name[name].property("rc2uiInternal"))
            )
        )
        found = {
            name
            for name in names
            if name in by_name and by_name[name].window() is root
        }
        return widgets, tuple(name for name in names if name not in found)

    def _check_buddies(
        self,
        root: Any,
        prepared: PreparedUiXml,
        path: Path,
        diagnostics: list[dict[str, str]],
    ) -> None:
        for binding in prepared.buddies:
            label = root.findChild(self.QtWidgets.QLabel, binding.label_name)
            target = root.findChild(self.QtWidgets.QWidget, binding.buddy_name)
            if label is None or target is None or label.buddy() is not target:
                diagnostics.append(
                    diagnostic(
                        "qt.invalid-buddy",
                        "error",
                        (
                            f"buddy {binding.label_name!r}->"
                            f"{binding.buddy_name!r} was not loaded"
                        ),
                        path,
                    )
                )

    def _check_designer_size(
        self,
        root: Any,
        prepared: PreparedUiXml,
        path: Path,
        diagnostics: list[dict[str, str]],
    ) -> None:
        if prepared.serialized_size is None:
            return
        hint = root.sizeHint().expandedTo(root.minimumSizeHint())
        width, height = prepared.serialized_size
        missing_width = max(0, hint.width() - width)
        missing_height = max(0, hint.height() - height)
        if missing_width <= 4 and missing_height <= 4:
            return
        diagnostics.append(
            diagnostic(
                "qt.designer-size-too-small",
                "warning",
                (
                    f"serialized Designer canvas {width}x{height} is smaller "
                    f"than Qt layout hint {hint.width()}x{hint.height()} "
                    f"(short by {missing_width}px horizontally and "
                    f"{missing_height}px vertically)"
                ),
                path,
            )
        )

    def _target_sizes(
        self,
        root: Any,
        factors: tuple[float, ...],
    ) -> tuple[tuple[int, int, bool], ...]:
        hint = root.sizeHint()
        minimum = root.minimumSizeHint()
        # Validate the form at the size emitted from its DLU client rectangle.
        # A synthetic 100x60 floor radically changes tiny embedded dialog
        # templates and reports validator-induced movement as converter drift.
        base_width = max(1, hint.width(), minimum.width(), root.width())
        base_height = max(1, hint.height(), minimum.height(), root.height())
        targets: list[tuple[int, int, bool]] = []
        seen: set[tuple[int, int]] = set()
        for factor in factors:
            width = max(1, minimum.width(), round(base_width * factor))
            height = max(1, minimum.height(), round(base_height * factor))
            size = (width, height)
            if size in seen:
                continue
            seen.add(size)
            targets.append((width, height, abs(factor - 1.0) < 0.001))
        if not any(item[2] for item in targets):
            targets.append((base_width, base_height, True))
        return tuple(targets)

    def _check_geometry(
        self,
        widgets: tuple[Any, ...],
        *,
        root: Any,
        path: Path,
        tested_size: tuple[int, int],
        diagnostics: list[dict[str, str]],
        seen: set[tuple[str, ...]],
        expected_overlaps: set[tuple[str, str]],
    ) -> None:
        declared = set(widgets)
        for widget in widgets:
            if widget is root or widget.isHidden():
                continue
            geometry = widget.geometry()
            if geometry.width() <= 0 or geometry.height() <= 0:
                _append_once(
                    diagnostics,
                    seen,
                    ("zero", widget.objectName()),
                    diagnostic(
                        "qt.zero-size",
                        "warning",
                        f"widget {widget.objectName()!r} has zero runtime size",
                        path,
                    ),
                )
            minimum_hint = widget.minimumSizeHint()
            if (
                minimum_hint.width() > 0
                and geometry.width() + 2 < minimum_hint.width()
            ) or (
                minimum_hint.height() > 0
                and geometry.height() + 2 < minimum_hint.height()
            ):
                _append_once(
                    diagnostics,
                    seen,
                    ("minimum-hint", widget.objectName()),
                    diagnostic(
                        "qt.below-minimum-size-hint",
                        "warning",
                        (
                            f"widget {widget.objectName()!r} is smaller than "
                            "its minimumSizeHint"
                        ),
                        path,
                    ),
                )
            parent = widget.parentWidget()
            if parent is not None and parent in declared:
                bounds = parent.rect().adjusted(-1, -1, 1, 1)
                if not bounds.contains(geometry):
                    _append_once(
                        diagnostics,
                        seen,
                        ("bounds", widget.objectName()),
                        diagnostic(
                            "qt.out-of-bounds",
                            "warning",
                            (
                                f"widget {widget.objectName()!r} exceeds parent "
                                f"at {tested_size[0]}x{tested_size[1]}"
                            ),
                            path,
                        ),
                    )

        parents = {
            widget.parentWidget() for widget in widgets if widget is not root
        }
        for parent in parents:
            if (
                parent is None
                or parent.objectName().startswith("runtimeAlternatives")
            ):
                continue
            siblings = [
                widget
                for widget in widgets
                if widget.parentWidget() is parent and not widget.isHidden()
            ]
            for index, left in enumerate(siblings):
                for right in siblings[index + 1 :]:
                    intersection = left.geometry().intersected(right.geometry())
                    overlap = intersection.width() * intersection.height()
                    smaller = min(
                        left.width() * left.height(),
                        right.width() * right.height(),
                    )
                    if smaller <= 0 or overlap / smaller < 0.2:
                        continue
                    names = tuple(
                        sorted((left.objectName(), right.objectName()))
                    )
                    if names in expected_overlaps:
                        continue
                    _append_once(
                        diagnostics,
                        seen,
                        ("overlap", *names),
                        diagnostic(
                            "qt.unexpected-overlap",
                            "warning",
                            (
                                f"runtime widgets {names[0]!r} and "
                                f"{names[1]!r} overlap"
                            ),
                            path,
                        ),
                    )

    def _check_text_clipping(
        self,
        widgets: tuple[Any, ...],
        *,
        path: Path,
        diagnostics: list[dict[str, str]],
        seen: set[tuple[str, ...]],
    ) -> None:
        for widget in widgets:
            text = self._widget_text(widget)
            if not text or widget.isHidden():
                continue
            contents = widget.contentsRect()
            metrics = widget.fontMetrics()
            if isinstance(widget, self.QtWidgets.QLabel):
                flags = self.QtCore.Qt.TextFlag.TextShowMnemonic
                if widget.wordWrap():
                    flags |= self.QtCore.Qt.TextFlag.TextWordWrap
                required = metrics.boundingRect(
                    0,
                    0,
                    max(1, contents.width()) if widget.wordWrap() else 100000,
                    100000,
                    flags,
                    text,
                )
            elif isinstance(widget, self.QtWidgets.QAbstractButton):
                required = widget.minimumSizeHint()
                contents = widget.rect()
            else:
                continue
            if (
                required.width() <= contents.width() + 4
                and required.height() <= contents.height() + 4
            ):
                continue
            _append_once(
                diagnostics,
                seen,
                ("text", widget.objectName()),
                diagnostic(
                    "qt.clipped-text",
                    "warning",
                    (
                        f"text may be clipped in widget {widget.objectName()!r}: "
                        f"contents {contents.width()}x{contents.height()}, "
                        f"required {required.width()}x{required.height()}, "
                        f"short by {max(0, required.width() - contents.width())}px "
                        f"horizontally and "
                        f"{max(0, required.height() - contents.height())}px "
                        "vertically"
                    ),
                    path,
                ),
            )

    def _widget_metrics(self, widgets: tuple[Any, ...]) -> dict[str, object]:
        result: dict[str, object] = {}
        for widget in widgets:
            policy = widget.sizePolicy()
            font = widget.font()
            font_metrics = widget.fontMetrics()
            text = self._widget_text(widget)
            result[widget.objectName()] = {
                "qt_class": widget.metaObject().className(),
                "size": _size(widget.size()),
                "size_hint": _size(widget.sizeHint()),
                "minimum_size_hint": _size(widget.minimumSizeHint()),
                "minimum_size": _size(widget.minimumSize()),
                "maximum_size": _size(widget.maximumSize()),
                "horizontal_policy": policy.horizontalPolicy().name,
                "vertical_policy": policy.verticalPolicy().name,
                "font": {
                    "family": font.family(),
                    "point_size": font.pointSizeF(),
                    "pixel_size": font.pixelSize(),
                    "height": font_metrics.height(),
                    "ascent": font_metrics.ascent(),
                    "average_char_width": font_metrics.averageCharWidth(),
                },
                "text_advance": (
                    font_metrics.horizontalAdvance(text) if text else None
                ),
            }
        return result

    def _runtime_snapshot(
        self,
        widgets: tuple[Any, ...],
        root: Any,
        *,
        baseline: bool,
        include_font_requirements: bool = False,
    ) -> dict[str, object]:
        widgets_snapshot: dict[str, dict[str, object]] = {
            widget.objectName(): {
                "geometry": [
                    widget.x(),
                    widget.y(),
                    widget.width(),
                    widget.height(),
                ],
                "root_geometry": self._root_geometry(widget, root),
                "parent_name": (
                    widget.parentWidget().objectName()
                    if widget.parentWidget() is not None
                    else None
                ),
                "parent_size": (
                    _size(widget.parentWidget().size())
                    if widget.parentWidget() is not None
                    else None
                ),
                "horizontal_policy": (
                    widget.sizePolicy().horizontalPolicy().name
                ),
                "vertical_policy": widget.sizePolicy().verticalPolicy().name,
                "visible": not widget.isHidden(),
            }
            for widget in widgets
        }
        if include_font_requirements:
            for widget in widgets:
                item = widgets_snapshot[widget.objectName()]
                text_required_height = self._text_required_height(widget)
                text_required_width = self._text_required_width(widget)
                item["font_height_sensitive"] = (
                    text_required_height is not None
                    or isinstance(
                        widget,
                        (
                            self.QtWidgets.QLineEdit,
                            self.QtWidgets.QComboBox,
                            self.QtWidgets.QAbstractSpinBox,
                        ),
                    )
                )
                item["font_width_sensitive"] = text_required_width is not None
                item["minimum_size_hint"] = _size(widget.minimumSizeHint())
                item["contents_height"] = widget.contentsRect().height()
                item["text_required_height"] = text_required_height
                item["contents_width"] = self._text_available_width(widget)
                item["text_required_width"] = text_required_width
        return {
            "form_size": _size(root.size()),
            "baseline": baseline,
            "widgets": widgets_snapshot,
        }

    def _text_required_height(self, widget: Any) -> int | None:
        text = self._widget_text(widget)
        if not text:
            return None
        if isinstance(widget, self.QtWidgets.QLabel):
            flags = self.QtCore.Qt.TextFlag.TextShowMnemonic
            if widget.wordWrap():
                flags |= self.QtCore.Qt.TextFlag.TextWordWrap
            return widget.fontMetrics().boundingRect(
                0,
                0,
                max(1, widget.contentsRect().width())
                if widget.wordWrap()
                else 100000,
                100000,
                flags,
                text,
            ).height()
        if isinstance(widget, self.QtWidgets.QAbstractButton):
            return widget.minimumSizeHint().height()
        if isinstance(widget, self.QtWidgets.QGroupBox):
            return widget.fontMetrics().height()
        return None

    def _text_required_width(self, widget: Any) -> int | None:
        text = self._widget_text(widget)
        if not text:
            return None
        if isinstance(widget, self.QtWidgets.QLabel):
            if widget.wordWrap():
                return None
            return widget.fontMetrics().boundingRect(
                0,
                0,
                100000,
                100000,
                self.QtCore.Qt.TextFlag.TextShowMnemonic,
                text,
            ).width()
        if isinstance(widget, self.QtWidgets.QAbstractButton):
            return widget.minimumSizeHint().width()
        if isinstance(widget, self.QtWidgets.QGroupBox):
            # QGroupBox.sizeHint() is commonly governed by its child layout
            # and can omit a growing title.  The frame/title clearance is
            # deliberately small; the validator is looking for clipping, not
            # enforcing a particular style's preferred padding.
            return (
                widget.fontMetrics()
                .boundingRect(
                    0,
                    0,
                    100000,
                    100000,
                    self.QtCore.Qt.TextFlag.TextShowMnemonic,
                    text,
                )
                .width()
                + 16
            )
        return None

    def _text_available_width(self, widget: Any) -> int:
        if isinstance(
            widget,
            (self.QtWidgets.QAbstractButton, self.QtWidgets.QGroupBox),
        ):
            return widget.width()
        return widget.contentsRect().width()

    def _root_geometry(self, widget: Any, root: Any) -> list[int]:
        origin = widget.mapTo(root, self.QtCore.QPoint(0, 0))
        return [origin.x(), origin.y(), widget.width(), widget.height()]

    def _widget_text(self, widget: Any) -> str:
        if isinstance(
            widget,
            (self.QtWidgets.QLabel, self.QtWidgets.QAbstractButton),
        ):
            return widget.text()
        if isinstance(widget, self.QtWidgets.QGroupBox):
            return widget.title()
        return ""

def _size(value: Any) -> list[int]:
    return [value.width(), value.height()]


def _scaled_font(
    font: Any,
    *,
    factor: float,
    fallback_height: int | None,
) -> Any:
    point_size = font.pointSizeF()
    pixel_size = font.pixelSize()
    if point_size > 0:
        font.setPointSizeF(point_size * factor)
    elif pixel_size > 0:
        font.setPixelSize(max(1, round(pixel_size * factor)))
    else:
        assert fallback_height is not None
        font.setPixelSize(max(1, round(fallback_height * factor)))
    return font


def _append_once(
    diagnostics: list[dict[str, str]],
    seen: set[tuple[str, ...]],
    key: tuple[str, ...],
    item: dict[str, str],
) -> None:
    if key not in seen:
        seen.add(key)
        diagnostics.append(item)


def _source_overlap_pairs(reference: object) -> set[tuple[str, str]]:
    """Return overlaps already present in RC, including intentional layers."""

    if not isinstance(reference, dict):
        return set()
    raw_controls = reference.get("controls")
    if not isinstance(raw_controls, list):
        return set()
    controls: list[tuple[str, tuple[float, float, float, float]]] = []
    for raw in raw_controls:
        name = raw.get("object_name") if isinstance(raw, dict) else None
        rect = raw.get("rect_dlu") if isinstance(raw, dict) else None
        if (
            not isinstance(name, str)
            or not isinstance(rect, (list, tuple))
            or len(rect) != 4
            or not all(isinstance(value, (int, float)) for value in rect)
        ):
            continue
        controls.append((name, tuple(float(value) for value in rect)))

    result: set[tuple[str, str]] = set()
    for index, (left_name, left) in enumerate(controls):
        for right_name, right in controls[index + 1 :]:
            overlap_width = max(
                0.0,
                min(left[0] + left[2], right[0] + right[2])
                - max(left[0], right[0]),
            )
            overlap_height = max(
                0.0,
                min(left[1] + left[3], right[1] + right[3])
                - max(left[1], right[1]),
            )
            smaller = min(left[2] * left[3], right[2] * right[3])
            if smaller > 0 and overlap_width * overlap_height / smaller >= 0.2:
                result.add(tuple(sorted((left_name, right_name))))
    return result

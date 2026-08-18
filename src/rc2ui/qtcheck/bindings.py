from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class QtBinding:
    name: str
    version: str
    qt_version: str
    QtCore: Any
    QtWidgets: Any
    uic: Any


def load_qt_binding() -> QtBinding:
    try:
        from PyQt6 import QtCore, QtWidgets, uic
    except ImportError as pyqt_error:
        try:
            import PySide6
            from PySide6 import QtCore, QtUiTools, QtWidgets
        except ImportError as pyside_error:
            raise ImportError(
                f"PyQt6 unavailable ({pyqt_error}); "
                f"PySide6 unavailable ({pyside_error})"
            ) from pyside_error
        return QtBinding(
            name="PySide6",
            version=PySide6.__version__,
            qt_version=QtCore.qVersion(),
            QtCore=QtCore,
            QtWidgets=QtWidgets,
            uic=_PySideUic(QtUiTools, QtWidgets),
        )
    return QtBinding(
        name="PyQt6",
        version=QtCore.PYQT_VERSION_STR,
        qt_version=QtCore.QT_VERSION_STR,
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        uic=uic,
    )


class _PySideUic:
    """Expose the two PyQt uic operations used by the generic inspector."""

    def __init__(self, qt_ui_tools: Any, qt_widgets: Any) -> None:
        self._QtUiTools = qt_ui_tools
        self._QtWidgets = qt_widgets

    def compileUi(self, path: str, _stream: object) -> None:
        widget = self._load(path)
        widget.close()
        widget.deleteLater()

    def loadUi(self, path: str) -> Any:
        return self._load(path)

    def _load(self, path: str) -> Any:
        loader = self._QtUiTools.QUiLoader()
        widget = loader.load(str(Path(path)))
        if widget is None or not isinstance(widget, self._QtWidgets.QWidget):
            reason = loader.errorString() or "QUiLoader returned no widget"
            raise ValueError(reason)
        return widget

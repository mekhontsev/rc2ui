from __future__ import annotations

import importlib.util
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QtBindingAvailability:
    available: bool
    reason: str | None = None
    binding: str | None = None


def discover_qt_binding() -> QtBindingAvailability:
    """Find a supported Qt binding without importing Qt or starting a GUI."""

    errors: list[str] = []
    for binding, required_module in (
        ("PyQt6", "PyQt6.uic"),
        ("PySide6", "PySide6.QtUiTools"),
    ):
        try:
            package = importlib.util.find_spec(binding)
            module = (
                importlib.util.find_spec(required_module)
                if package is not None
                else None
            )
        except (ImportError, ModuleNotFoundError, ValueError) as error:
            errors.append(f"{binding}: {error}")
            continue
        if package is not None and module is not None:
            return QtBindingAvailability(True, binding=binding)
    return QtBindingAvailability(
        False,
        "; ".join(errors) or "neither PyQt6 nor PySide6 is installed",
    )

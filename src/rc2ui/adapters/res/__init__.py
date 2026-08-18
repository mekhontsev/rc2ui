"""Readers for compiled Win32 `.res` resources."""

from rc2ui.adapters.res.dialog_template import DialogTemplateError, parse_dialog
from rc2ui.adapters.res.reader import ResEntry, ResFormatError, read_res

__all__ = [
    "DialogTemplateError",
    "ResEntry",
    "ResFormatError",
    "parse_dialog",
    "read_res",
]

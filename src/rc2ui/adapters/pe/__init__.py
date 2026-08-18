"""Read-only parser for resources embedded in Windows PE images."""

from rc2ui.adapters.pe.reader import (
    PeFormatError,
    PeResourceEntry,
    parse_pe,
    read_pe,
)

__all__ = ["PeFormatError", "PeResourceEntry", "parse_pe", "read_pe"]

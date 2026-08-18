from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResourceId:
    """A compiled resource identifier with optional source-level symbols."""

    ordinal: int | None = None
    name: str | None = None
    symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.ordinal is None) == (self.name is None):
            raise ValueError("a resource ID must have exactly one ordinal or name")
        if self.name is not None and not self.name:
            raise ValueError("a named resource ID cannot be empty")
        if any(not symbol for symbol in self.symbols):
            raise ValueError("resource ID symbols cannot be empty")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("resource ID symbols must be unique")

    @classmethod
    def from_ordinal(cls, value: int, *symbols: str) -> ResourceId:
        return cls(ordinal=value, symbols=tuple(symbols))

    @classmethod
    def from_name(cls, value: str) -> ResourceId:
        return cls(name=value)

    @property
    def display_name(self) -> str:
        if self.symbols:
            return self.symbols[0]
        if self.ordinal is not None:
            return f"#{self.ordinal}"
        assert self.name is not None
        return self.name

from __future__ import annotations

import struct


class BinaryFormatError(ValueError):
    pass


class BinaryReader:
    """Small bounded little-endian reader with explicit alignment."""

    def __init__(self, data: bytes, *, context: str = "binary data") -> None:
        self.data = data
        self.offset = 0
        self.context = context

    @property
    def remaining(self) -> int:
        return len(self.data) - self.offset

    def seek(self, offset: int) -> None:
        if not 0 <= offset <= len(self.data):
            raise BinaryFormatError(
                f"{self.context}: offset {offset} is outside the data"
            )
        self.offset = offset

    def align(self, boundary: int) -> None:
        if boundary <= 0 or boundary & (boundary - 1):
            raise ValueError("alignment must be a positive power of two")
        self.seek((self.offset + boundary - 1) & -boundary)

    def read(self, size: int) -> bytes:
        if size < 0:
            raise ValueError("read size cannot be negative")
        end = self.offset + size
        if end > len(self.data):
            raise BinaryFormatError(
                f"{self.context}: need {size} byte(s) at offset {self.offset}, "
                f"only {self.remaining} remain"
            )
        value = self.data[self.offset : end]
        self.offset = end
        return value

    def u8(self) -> int:
        return self._unpack("<B", 1)

    def u16(self) -> int:
        return self._unpack("<H", 2)

    def i16(self) -> int:
        return self._unpack("<h", 2)

    def u32(self) -> int:
        return self._unpack("<I", 4)

    def utf16z(self, *, first: int | None = None) -> str:
        units: list[int] = []
        if first not in (None, 0):
            units.append(first)
        while first != 0:
            first = self.u16()
            if first:
                units.append(first)
        raw = b"".join(unit.to_bytes(2, "little") for unit in units)
        try:
            return raw.decode("utf-16-le")
        except UnicodeDecodeError as error:
            raise BinaryFormatError(
                f"{self.context}: invalid UTF-16 string near offset {self.offset}"
            ) from error

    def _unpack(self, format_: str, size: int) -> int:
        return int(struct.unpack(format_, self.read(size))[0])

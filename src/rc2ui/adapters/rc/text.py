from __future__ import annotations

from pathlib import Path


def read_rc_text(path: Path, *, fallback_encoding: str = "cp1251") -> str:
    """Read the encodings commonly emitted by Windows resource editors."""

    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Legacy Visual C++ projects commonly use a Windows code page without
        # a BOM. The caller can select the project code page explicitly.
        return data.decode(fallback_encoding)


def strip_rc_comments(text: str) -> str:
    """Remove C/C++ comments without treating markers inside strings as comments."""

    result: list[str] = []
    index = 0
    quoted = False
    while index < len(text):
        current = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if quoted:
            result.append(current)
            if current == '"':
                if following == '"':
                    result.append(following)
                    index += 2
                    continue
                quoted = False
            elif current == "\\" and following:
                result.append(following)
                index += 2
                continue
            index += 1
            continue
        if current == '"':
            quoted = True
            result.append(current)
            index += 1
            continue
        if current == "/" and following == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if current == "/" and following == "*":
            index += 2
            while index < len(text):
                if text[index] == "*" and index + 1 < len(text) and text[index + 1] == "/":
                    index += 2
                    break
                if text[index] in "\r\n":
                    result.append(text[index])
                index += 1
            continue
        result.append(current)
        index += 1
    return "".join(result)

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def exclusive_output_lock(
    output: Path,
    *,
    filename: str,
    description: str,
) -> Iterator[None]:
    """Prevent two resumable jobs from mutating one output directory."""

    path = output / filename
    token = f"{os.getpid()} {uuid.uuid4().hex}\n"
    for attempt in range(2):
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if attempt or _lock_owner_is_alive(path):
                owner = _read_lock_owner(path)
                detail = f" (PID {owner})" if owner is not None else ""
                raise ValueError(
                    f"{description} is already in use{detail}: {output}"
                ) from None
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(token)
        break
    try:
        yield
    finally:
        try:
            if path.read_text(encoding="ascii") == token:
                path.unlink()
        except (FileNotFoundError, OSError, UnicodeError):
            pass


def _read_lock_owner(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="ascii").split(None, 1)[0]
        return int(raw)
    except (FileNotFoundError, OSError, UnicodeError, ValueError, IndexError):
        return None


def _lock_owner_is_alive(path: Path) -> bool:
    owner = _read_lock_owner(path)
    if owner is None:
        return True
    try:
        os.kill(owner, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True

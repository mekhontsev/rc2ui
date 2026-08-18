from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ResourceCompilerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CompileResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def find_resource_compiler(requested: str = "auto") -> str:
    if requested != "auto":
        resolved = shutil.which(requested)
        if resolved is None:
            raise ResourceCompilerUnavailable(
                f"resource compiler not found: {requested}"
            )
        return resolved
    candidates = (
        ("rc", "llvm-rc", "windres")
        if os.name == "nt"
        else (
            "x86_64-w64-mingw32-windres",
            "i686-w64-mingw32-windres",
            "windres",
            "llvm-rc",
        )
    )
    for candidate in candidates:
        if resolved := shutil.which(candidate):
            return resolved
    raise ResourceCompilerUnavailable(
        "no resource compiler found; install llvm-rc or GNU windres"
    )


def compile_resource(
    compiler: str,
    source: Path,
    output: Path,
    *,
    include_paths: tuple[Path, ...],
    defines: tuple[tuple[str, int], ...] = (),
    codepage: int | None = None,
    timeout_seconds: float = 60.0,
    working_directory: Path | None = None,
) -> CompileResult:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = _compile_command(
        compiler,
        source,
        output,
        include_paths=include_paths,
        defines=defines,
        codepage=codepage,
    )
    completed = subprocess.run(
        command,
        cwd=working_directory or source.parent,
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout_seconds,
    )
    return CompileResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _compile_command(
    compiler: str,
    source: Path,
    output: Path,
    *,
    include_paths: tuple[Path, ...],
    defines: tuple[tuple[str, int], ...],
    codepage: int | None,
) -> tuple[str, ...]:
    if Path(compiler).name.casefold() in {
        "llvm-rc",
        "llvm-rc.exe",
        "rc",
        "rc.exe",
    }:
        return _llvm_rc_command(
            compiler,
            source,
            output,
            include_paths=include_paths,
            defines=defines,
            codepage=codepage,
        )
    command = [
        compiler,
        "--input",
        str(source),
        "--output",
        str(output),
        "--output-format=res",
    ]
    if codepage is not None:
        command.append(f"--codepage={codepage}")
    for path in _unique_paths((source.parent, *include_paths)):
        command.append(f"--include-dir={path}")
    for name, value in defines:
        command.append(f"--define={name}={value}")
    return tuple(command)


def _llvm_rc_command(
    compiler: str,
    source: Path,
    output: Path,
    *,
    include_paths: tuple[Path, ...],
    defines: tuple[tuple[str, int], ...],
    codepage: int | None,
) -> tuple[str, ...]:
    command = [compiler, "/NOLOGO", "/FO", str(output)]
    if codepage is not None:
        command.extend(("/C", str(codepage)))
    for path in _unique_paths((source.parent, *include_paths)):
        command.extend(("/I", str(path)))
    for name, value in defines:
        command.append(f"/D{name}={value}")
    command.append(str(source))
    return tuple(command)


def _unique_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return tuple(result)

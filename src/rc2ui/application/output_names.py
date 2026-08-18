from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from rc2ui.domain.resource_id import ResourceId


@dataclass(frozen=True, slots=True)
class OutputOwner:
    source: PurePosixPath
    resource_id: ResourceId
    object_name: str
    explicit: bool

    @property
    def display_name(self) -> str:
        return f"{self.source}:{_resource_label(self.resource_id)}"

    @property
    def identity(self) -> tuple[PurePosixPath, int | None, str | None]:
        return self.source, self.resource_id.ordinal, self.resource_id.name


@dataclass(frozen=True, slots=True)
class OutputAllocation:
    object_name: str
    output: Path
    conflicting_owner: OutputOwner | None = None
    object_key: tuple[str, str] | None = None

    @property
    def was_disambiguated(self) -> bool:
        return self.conflicting_owner is not None


class OutputNameCollision(ValueError):
    def __init__(
        self,
        *,
        output: Path,
        current: OutputOwner,
        existing: OutputOwner,
        duplicate_input: bool,
    ) -> None:
        self.output = output
        self.current = current
        self.existing = existing
        self.duplicate_input = duplicate_input
        if duplicate_input:
            message = (
                f"duplicate dialog input {current.display_name}; the same "
                "dialog was already processed"
            )
        else:
            message = (
                f"explicit root object name {current.object_name!r} for "
                f"{current.display_name} is already used by "
                f"{existing.display_name} and cannot be changed automatically"
            )
        super().__init__(message)


class OutputNameAllocator:
    """Reserve deterministic per-RC output and root object names."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self._object_owners: dict[tuple[str, str], OutputOwner] = {}
        self._file_owners: dict[str, OutputOwner] = {}

    def allocate(
        self,
        *,
        source: PurePosixPath,
        resource_id: ResourceId,
        requested_name: str,
        explicit: bool,
    ) -> OutputAllocation:
        owner = OutputOwner(source, resource_id, requested_name, explicit)
        requested_key = self._object_key(source, requested_name)
        existing = self._object_owners.get(requested_key)
        if existing is None:
            unique_name = requested_name
            conflicting_owner = None
        else:
            if existing.identity == owner.identity or explicit:
                raise OutputNameCollision(
                    output=self._output_path(
                        source,
                        requested_name,
                    ),
                    current=owner,
                    existing=existing,
                    duplicate_input=existing.identity == owner.identity,
                )
            conflicting_owner = existing
            stem = f"{requested_name}_{_resource_token(resource_id)}"
            unique_name = stem
            counter = 2
            while self._object_key(source, unique_name) in self._object_owners:
                unique_name = f"{stem}_{counter}"
                counter += 1

        allocated_owner = OutputOwner(
            source,
            resource_id,
            unique_name,
            explicit=explicit,
        )
        object_key = self._object_key(source, unique_name)
        output = self._allocate_file(source, allocated_owner)
        self._object_owners[object_key] = allocated_owner
        return OutputAllocation(
            unique_name,
            output,
            conflicting_owner,
            object_key,
        )

    def release(self, allocation: OutputAllocation) -> None:
        """Release a reservation when form validation or writing fails."""

        if allocation.object_key is not None:
            self._object_owners.pop(allocation.object_key, None)
        self._file_owners.pop(self._file_key(allocation.output), None)

    def _allocate_file(
        self,
        source: PurePosixPath,
        owner: OutputOwner,
    ) -> Path:
        output = self._output_path(source, owner.object_name)
        existing = self._file_owners.get(self._file_key(output))
        if existing is not None:
            raise OutputNameCollision(
                output=output,
                current=owner,
                existing=existing,
                duplicate_input=existing.identity == owner.identity,
            )
        self._file_owners[self._file_key(output)] = owner
        return output

    def _output_path(self, source: PurePosixPath, file_stem: str) -> Path:
        relative_dir = Path(source.as_posix()).with_suffix("")
        return self.output_dir / relative_dir / f"{file_stem}.ui"

    @staticmethod
    def _object_key(
        source: PurePosixPath,
        object_name: str,
    ) -> tuple[str, str]:
        return (
            source.with_suffix("").as_posix().casefold(),
            object_name.casefold(),
        )

    @staticmethod
    def _file_key(output: Path) -> str:
        return output.resolve().as_posix().casefold()


def _resource_label(resource_id: ResourceId) -> str:
    if symbol := _preferred_symbol(resource_id):
        return symbol
    if resource_id.ordinal is not None:
        return f"#{resource_id.ordinal}"
    assert resource_id.name is not None
    return resource_id.name


def _resource_token(resource_id: ResourceId) -> str:
    if symbol := _preferred_symbol(resource_id):
        return symbol
    if resource_id.ordinal is not None:
        if resource_id.ordinal >= 0:
            return f"id{resource_id.ordinal}"
        return f"idMinus{abs(resource_id.ordinal)}"

    assert resource_id.name is not None
    token = re.sub(r"[^A-Za-z0-9_]+", "_", resource_id.name).strip("_")
    if token:
        return f"id_{token}" if token[0].isdigit() else token
    digest = hashlib.blake2s(
        resource_id.name.encode("utf-8"),
        digest_size=4,
    ).hexdigest()
    return f"resource_{digest}"


def _preferred_symbol(resource_id: ResourceId) -> str | None:
    if not resource_id.symbols:
        return None
    return min(
        resource_id.symbols,
        key=lambda symbol: (not symbol.startswith("IDD_"), symbol),
    )

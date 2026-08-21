"""Immutable local CSV snapshot and panel-shape validation."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import stat
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.models.artifact import ArtifactRef
from envresearch.storage.research_artifacts import ResearchArtifactStore

STRICT_FROZEN = ConfigDict(
    extra="forbid", frozen=True, strict=True, validate_default=True
)


class LocalDataInvalid(ValueError):
    """The selected local file or its declared panel structure is invalid."""

    def __init__(self, message: str, *, code: str = "LOCAL_DATA_INVALID") -> None:
        super().__init__(message)
        self.code = code


class LocalDataChanged(LocalDataInvalid):
    """The selected local file changed while it was being snapshotted."""


class MissingValueCount(BaseModel):
    """Missing-cell count for one source column."""

    model_config = STRICT_FROZEN

    column: str
    count: int = Field(ge=0)


class LocalDataSnapshot(BaseModel):
    """Content-addressed owned bytes plus validated panel metadata."""

    model_config = STRICT_FROZEN

    reference: ArtifactRef
    relative_path: Path
    sha256: str
    size_bytes: int = Field(gt=0)
    row_count: int = Field(gt=0)
    columns: tuple[str, ...]
    missing_values: tuple[MissingValueCount, ...]

    def missing_count(self, column: str) -> int:
        """Return the recorded missing count for an exact column."""
        for item in self.missing_values:
            if item.column == column:
                return item.count
        raise KeyError(column)


class LocalDataValidation(BaseModel):
    """Read-only validation result before any artifact is persisted."""

    model_config = STRICT_FROZEN

    sha256: str
    size_bytes: int = Field(gt=0)
    row_count: int = Field(gt=0)
    columns: tuple[str, ...]
    missing_values: tuple[MissingValueCount, ...]


class _CsvInspection(BaseModel):
    """Private validated shape summary before durable publication."""

    model_config = STRICT_FROZEN

    row_count: int = Field(gt=0)
    columns: tuple[str, ...]
    missing_values: tuple[MissingValueCount, ...]


def snapshot_metadata_matches(
    data: bytes, spec: AnalysisSpec, snapshot: LocalDataSnapshot
) -> bool:
    """Independently recompute and compare persisted CSV panel metadata."""
    inspection = _inspect_csv(data, spec)
    return (
        len(data) == snapshot.size_bytes
        and inspection.row_count == snapshot.row_count
        and inspection.columns == snapshot.columns
        and inspection.missing_values == snapshot.missing_values
    )


def snapshot_csv(spec: AnalysisSpec, store: ResearchArtifactStore) -> LocalDataSnapshot:
    """Validate and persist exact CSV bytes without modifying the source."""
    source = spec.data_path
    limit = spec.budget.max_workspace_bytes
    descriptor, identity = _open_source(source, limit)
    try:
        data = _read_all(descriptor, limit)
    finally:
        os.close(descriptor)
    snapshot = snapshot_csv_bytes(spec, data, store)
    digest = snapshot.sha256
    _require_source_unchanged(source, identity, digest, limit)
    return snapshot


def snapshot_csv_bytes(
    spec: AnalysisSpec, data: bytes, store: ResearchArtifactStore
) -> LocalDataSnapshot:
    """Validate already-authenticated CSV bytes and persist their exact snapshot."""
    if not data:
        raise LocalDataInvalid("local CSV must not be empty")
    if len(data) > spec.budget.max_workspace_bytes:
        raise LocalDataInvalid("local data exceeds the workspace budget")
    digest = hashlib.sha256(data).hexdigest()
    inspection = _inspect_csv(data, spec)
    relative = Path("artifacts/econometrics/data") / f"{digest}.csv"
    _persist_snapshot(store, relative, data)
    return LocalDataSnapshot(
        reference=ArtifactRef(
            artifact_id=f"local-data-{digest[:16]}",
            artifact_version=1,
            content_hash=digest,
        ),
        relative_path=relative,
        sha256=digest,
        size_bytes=len(data),
        row_count=inspection.row_count,
        columns=inspection.columns,
        missing_values=inspection.missing_values,
    )


def _open_source(path: Path, max_bytes: int) -> tuple[int, tuple[int, int, int]]:
    """Open one regular non-symlink file and return stable identity fields."""
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise LocalDataInvalid("local data must be a regular non-symlink file")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except (FileNotFoundError, OSError) as error:
        raise LocalDataInvalid(
            "local data must be a regular non-symlink file"
        ) from error
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or (
        opened.st_dev,
        opened.st_ino,
    ) != (metadata.st_dev, metadata.st_ino):
        os.close(descriptor)
        raise LocalDataInvalid("local data must be a regular non-symlink file")
    if opened.st_size > max_bytes:
        os.close(descriptor)
        raise LocalDataInvalid("local data exceeds the workspace budget")
    return descriptor, (opened.st_dev, opened.st_ino, opened.st_size)


def _read_all(descriptor: int, max_bytes: int) -> bytes:
    """Read exact bytes from an already authenticated descriptor."""
    chunks: list[bytes] = []
    total = 0
    while chunk := os.read(descriptor, min(1024 * 1024, max_bytes - total + 1)):
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise LocalDataInvalid("local data exceeds the workspace budget")
    data = b"".join(chunks)
    if not data:
        raise LocalDataInvalid("local CSV must not be empty")
    return data


def _inspect_csv(data: bytes, spec: AnalysisSpec) -> _CsvInspection:
    """Validate declared columns, numeric roles, missingness, and panel keys."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LocalDataInvalid("local CSV must be UTF-8") from error
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = tuple(next(reader))
    except StopIteration as error:
        raise LocalDataInvalid("local CSV must contain a header") from error
    if not header or any(not name for name in header):
        raise LocalDataInvalid("CSV columns must be nonempty")
    if len(set(header)) != len(header):
        raise LocalDataInvalid("CSV contains duplicate columns")
    missing_required = tuple(
        name for name in spec.required_columns() if name not in header
    )
    if missing_required:
        raise LocalDataInvalid(f"CSV is missing required columns: {missing_required}")
    missing = {name: 0 for name in header}
    rows: list[tuple[str, ...]] = []
    for line_number, row in enumerate(reader, start=2):
        if len(row) != len(header):
            raise LocalDataInvalid(f"CSV row {line_number} has the wrong width")
        for name, value in zip(header, row, strict=True):
            if value == "":
                missing[name] += 1
        rows.append(tuple(row))
    from envresearch.econometrics._causal_csv import validate_rows

    validate_rows(header, tuple(rows), spec)
    return _CsvInspection(
        row_count=len(rows),
        columns=header,
        missing_values=tuple(
            MissingValueCount(column=name, count=missing[name]) for name in header
        ),
    )


def _persist_snapshot(
    store: ResearchArtifactStore, relative: Path, data: bytes
) -> Path:
    """Publish bytes relative to an authenticated store directory descriptor."""
    parent_descriptor, identity = _open_store_parent(store, relative.parent)
    try:
        _publish_snapshot(parent_descriptor, relative.name, data)
    finally:
        os.close(parent_descriptor)
    verification_descriptor, current_identity = _open_store_parent(
        store, relative.parent
    )
    try:
        if current_identity != identity:
            raise LocalDataChanged("store hierarchy changed during snapshot")
        if (
            _read_snapshot_leaf(verification_descriptor, relative.name, len(data))
            != data
        ):
            raise LocalDataChanged("persisted snapshot does not match source bytes")
    finally:
        os.close(verification_descriptor)
    return store.root / relative


def _open_store_parent(
    store: ResearchArtifactStore, relative: Path
) -> tuple[int, tuple[int, int]]:
    """Walk or create a directory hierarchy without following symlinks."""
    if relative.is_absolute() or ".." in relative.parts:
        raise LocalDataInvalid("snapshot destination must stay inside the store")
    store.root.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        current = os.open(store.root, flags)
    except OSError as error:
        raise LocalDataInvalid(
            "snapshot requires an authenticated store hierarchy"
        ) from error
    try:
        for part in relative.parts:
            try:
                following = os.open(part, flags, dir_fd=current)
            except FileNotFoundError:
                os.mkdir(part, mode=0o700, dir_fd=current)
                following = os.open(part, flags, dir_fd=current)
            except OSError as error:
                raise LocalDataInvalid(
                    "snapshot requires an authenticated store hierarchy"
                ) from error
            os.close(current)
            current = following
        metadata = os.fstat(current)
        return current, (metadata.st_dev, metadata.st_ino)
    except Exception:
        os.close(current)
        raise


def _read_snapshot_leaf(parent_descriptor: int, name: str, max_bytes: int) -> bytes:
    """Authenticate, bound, seal, and read one descriptor-relative leaf."""
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_descriptor,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
                raise LocalDataInvalid(
                    "snapshot destination must be an owned regular file"
                )
            os.fchmod(descriptor, 0o444)
            return _read_all(descriptor, max_bytes)
        finally:
            os.close(descriptor)
    except (LocalDataInvalid, OSError) as error:
        raise LocalDataInvalid(
            "snapshot destination must be an owned regular file"
        ) from error


def _publish_snapshot(parent_descriptor: int, name: str, data: bytes) -> None:
    """Atomically replace one leaf inside an already authenticated directory."""
    try:
        existing = _read_snapshot_leaf(parent_descriptor, name, len(data))
    except LocalDataInvalid as error:
        try:
            os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        else:
            raise error
    if existing is not None:
        if existing != data:
            raise LocalDataChanged("content-addressed snapshot collision")
        return
    temporary = f".{name}.{uuid.uuid4().hex}.stage"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        view = memoryview(data)
        written = 0
        while written < len(view):
            written += os.write(descriptor, view[written:])
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary,
            name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass


def _require_source_unchanged(
    source: Path, identity: tuple[int, int, int], digest: str, max_bytes: int
) -> None:
    """Reject replacement or mutation after the authenticated read."""
    try:
        descriptor, current_identity = _open_source(source, max_bytes)
        try:
            current = _read_all(descriptor, max_bytes)
        finally:
            os.close(descriptor)
    except (LocalDataInvalid, OSError) as error:
        raise LocalDataChanged("source changed during snapshot") from error
    if current_identity != identity or hashlib.sha256(current).hexdigest() != digest:
        raise LocalDataChanged("source changed during snapshot")

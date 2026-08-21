"""Durability and portability regressions for storage primitives."""

from pathlib import Path

import pytest

from envresearch.storage import atomic
from envresearch.storage.artifacts import ArtifactStore
from envresearch.storage.paths import safe_join


def test_atomic_replace_syncs_parent_directory_after_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful rename is not crash-durable until its directory is synced."""
    target = tmp_path / "result.bin"
    operations: list[str] = []
    original_replace = atomic.os.replace

    def track_replace(source: Path, destination: Path) -> None:
        operations.append("replace")
        original_replace(source, destination)

    def track_sync(directory: Path) -> None:
        assert target.read_bytes() == b"new"
        operations.append(f"sync:{directory.name}")

    monkeypatch.setattr(atomic.os, "replace", track_replace)
    monkeypatch.setattr(atomic, "_sync_parent_directory", track_sync, raising=False)

    atomic.atomic_write_bytes(target, b"new")

    assert operations == ["replace", f"sync:{tmp_path.name}"]


def test_directory_sync_failure_reports_error_after_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed durability barrier must surface even though replacement is atomic."""
    target = tmp_path / "result.bin"
    target.write_bytes(b"old")

    def fail_sync(_directory: Path) -> None:
        raise OSError("directory sync failed")

    monkeypatch.setattr(atomic, "_sync_parent_directory", fail_sync, raising=False)

    with pytest.raises(OSError, match="directory sync failed"):
        atomic.atomic_write_bytes(target, b"new")

    assert target.read_bytes() == b"new"
    assert list(tmp_path.glob(".result.bin.*")) == []


def test_parent_directory_sync_opens_fsyncs_and_closes_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The durability helper must fsync the directory descriptor it opens."""
    operations: list[str] = []

    def open_directory(path: Path, _flags: int) -> int:
        assert path == tmp_path
        operations.append("open")
        return 41

    def fsync_directory(descriptor: int) -> None:
        assert descriptor == 41
        operations.append("fsync")

    def close_directory(descriptor: int) -> None:
        assert descriptor == 41
        operations.append("close")

    monkeypatch.setattr(atomic.os, "open", open_directory)
    monkeypatch.setattr(atomic.os, "fsync", fsync_directory)
    monkeypatch.setattr(atomic.os, "close", close_directory)

    atomic._sync_parent_directory(tmp_path)

    assert operations == ["open", "fsync", "close"]


def test_safe_join_rejects_absolute_input_even_inside_root(tmp_path: Path) -> None:
    """The public relative-path contract must not depend on containment alone."""
    inside = tmp_path / "inside.json"

    with pytest.raises(ValueError, match="relative"):
        safe_join(tmp_path, inside)


@pytest.mark.parametrize(
    "relative",
    [
        Path("RAW/source.json"),
        Path("Raw/source.json"),
        Path("rAw/source.json"),
        Path("reports/../RAW/source.json"),
    ],
)
def test_artifact_store_reserves_raw_segment_case_insensitively(
    tmp_path: Path, relative: Path
) -> None:
    """Case-insensitive filesystems must not alias a writable name to raw/."""
    with pytest.raises(PermissionError, match="immutable raw directory"):
        ArtifactStore(tmp_path).write_json(relative, {"unsafe": True})

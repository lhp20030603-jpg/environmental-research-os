"""Tests for workspace-confined, immutable artifact persistence."""

from datetime import timedelta
from pathlib import Path

import pytest

from envresearch.storage.artifacts import ArtifactStore
from envresearch.storage.atomic import atomic_write_bytes


def test_store_rejects_path_traversal(tmp_path: Path) -> None:
    """Traversal could otherwise overwrite files outside a run workspace."""
    store = ArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="escapes workspace"):
        store.write_json(Path("../outside.json"), {"unsafe": True})


def test_store_rejects_symlink_resolution_escape(tmp_path: Path) -> None:
    """A symlink within the workspace must not bypass path confinement."""
    outside = tmp_path.parent / "outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    store = ArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="escapes workspace"):
        store.write_json(Path("linked/escape.json"), {"unsafe": True})


def test_store_rejects_raw_directory_write(tmp_path: Path) -> None:
    """Raw source inputs must remain immutable through the artifact API."""
    store = ArtifactStore(tmp_path)

    with pytest.raises(PermissionError, match="immutable raw directory"):
        store.write_json(Path("raw/source.json"), {"unsafe": True})


def test_store_rejects_raw_symlink_into_workspace(tmp_path: Path) -> None:
    """A raw symlink must not redirect writes into a mutable workspace directory."""
    derived = tmp_path / "derived"
    derived.mkdir()
    (tmp_path / "raw").symlink_to(derived, target_is_directory=True)
    store = ArtifactStore(tmp_path)

    with pytest.raises(PermissionError, match="immutable raw directory"):
        store.write_json(Path("raw/source.json"), {"unsafe": True})


def test_store_rejects_normalized_path_into_raw_directory(tmp_path: Path) -> None:
    """Dot segments must not evade the immutable raw directory restriction."""
    store = ArtifactStore(tmp_path)

    with pytest.raises(PermissionError, match="immutable raw directory"):
        store.write_json(Path("reports/../raw/source.json"), {"unsafe": True})


def test_store_writes_json_and_records_content_metadata(tmp_path: Path) -> None:
    """Written JSON needs retrievable contents and integrity metadata."""
    store = ArtifactStore(tmp_path)

    record = store.write_json(Path("derived/result.json"), {"score": 0.9})

    assert store.read_json(Path("derived/result.json")) == {"score": 0.9}
    assert record.relative_path == Path("derived/result.json")
    assert record.sha256 == (
        "6dad1d80cba034d6b55d18c08de9ceddd2a516b4c9cca683508cbf49edb33c13"
    )
    assert record.size_bytes == 13
    assert record.written_at.tzinfo is not None
    assert record.written_at.utcoffset() == timedelta(0)


def test_atomic_write_preserves_old_file_and_cleans_temp_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed replacement must leave durable old bytes and no temp artifacts."""
    target = tmp_path / "result.bin"
    target.write_bytes(b"old-bytes")

    def fail_replace(source: str, destination: str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("envresearch.storage.atomic.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_bytes(target, b"new-bytes")

    assert target.read_bytes() == b"old-bytes"
    assert list(tmp_path.glob(".result.bin.*")) == []

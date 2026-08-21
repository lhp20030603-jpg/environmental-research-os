"""Tests for persistence of typed, content-addressed research artifacts."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from envresearch.models.artifact import (
    ArtifactEnvelope,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.storage import research_artifacts
from envresearch.storage.research_artifacts import ResearchArtifactStore


def _envelope(artifact_id: str) -> ArtifactEnvelope:
    return ArtifactEnvelope(
        artifact_id=artifact_id,
        artifact_version=1,
        run_id="run-001",
        created_at=datetime(2026, 8, 5, tzinfo=UTC),
        producer=ProducerIdentity(component="research-intake", version="0.2.0"),
    )


def sealed_brief_artifact() -> ResearchArtifact[dict[str, str]]:
    """Build one valid structured artifact for persistence tests."""
    return seal_artifact(
        ResearchArtifact(envelope=_envelope("research-brief"), payload={"topic": "air"})
    )


def evidence_envelope() -> ArtifactEnvelope:
    """Build metadata for an evidence-matrix CSV."""
    return _envelope("evidence-matrix")


def identification_envelope() -> ArtifactEnvelope:
    """Build metadata for an identification memo."""
    return _envelope("identification-memo")


def test_yaml_round_trip_requires_valid_content_hash(tmp_path: Path) -> None:
    store = ResearchArtifactStore(tmp_path)
    artifact = sealed_brief_artifact()
    store.write_structured(Path("artifacts/research-brief.yaml"), artifact)

    loaded = store.read_structured(
        Path("artifacts/research-brief.yaml"),
        TypeAdapter(ResearchArtifact[dict[str, str]]),
    )

    assert loaded == artifact


def test_json_write_uses_canonical_compact_utf8_bytes(tmp_path: Path) -> None:
    store = ResearchArtifactStore(tmp_path)
    artifact = sealed_brief_artifact()
    relative = Path("artifacts/research-brief.json")

    store.write_structured(relative, artifact)

    assert (tmp_path / relative).read_bytes() == json.dumps(
        artifact.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def test_csv_sidecar_hashes_exact_csv_bytes(tmp_path: Path) -> None:
    store = ResearchArtifactStore(tmp_path)
    csv_record, metadata_record = store.write_csv(
        Path("artifacts/evidence-matrix.csv"),
        ("source_id", "finding"),
        ({"source_id": "p1", "finding": "lower emissions"},),
        evidence_envelope(),
    )

    assert csv_record.sha256 == json.loads(
        (tmp_path / metadata_record.relative_path).read_text(encoding="utf-8")
    )["content_hash"]
    assert (tmp_path / csv_record.relative_path).read_bytes() == (
        b"source_id,finding\np1,lower emissions\n"
    )


def test_markdown_round_trip_uses_structured_front_matter(tmp_path: Path) -> None:
    store = ResearchArtifactStore(tmp_path)
    store.write_markdown(
        Path("artifacts/identification-memo.md"),
        identification_envelope(),
        "# Identification\n\nParallel trends is the central assumption.\n",
    )
    envelope, body = store.read_markdown(Path("artifacts/identification-memo.md"))

    assert envelope.artifact_id == "identification-memo"
    assert body.startswith("# Identification")


@pytest.mark.parametrize(
    "relative",
    (
        Path("raw/brief.json"),
        Path("derived/brief.json"),
        Path("artifacts/../brief.json"),
        Path("artifacts/brief.txt"),
        Path("/tmp/brief.json"),
    ),
)
def test_structured_writes_require_authoritative_safe_paths(
    tmp_path: Path, relative: Path
) -> None:
    store = ResearchArtifactStore(tmp_path)

    with pytest.raises((PermissionError, ValueError)):
        store.write_structured(relative, sealed_brief_artifact())


def test_structured_write_rejects_unsealed_artifact(tmp_path: Path) -> None:
    store = ResearchArtifactStore(tmp_path)
    unsealed = ResearchArtifact(
        envelope=_envelope("research-brief"), payload={"topic": "air"}
    )

    with pytest.raises(ValueError, match="unsealed"):
        store.write_structured(Path("artifacts/research-brief.json"), unsealed)


@pytest.mark.parametrize("existing_pair", (False, True))
def test_csv_write_rolls_back_pair_when_metadata_publish_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_pair: bool
) -> None:
    store = ResearchArtifactStore(tmp_path)
    relative = Path("artifacts/evidence-matrix.csv")
    metadata = tmp_path / relative.with_suffix(".meta.json")
    csv_path = tmp_path / relative
    if existing_pair:
        store.write_csv(
            relative,
            ("source_id", "finding"),
            ({"source_id": "old", "finding": "old finding"},),
            evidence_envelope(),
        )
    old_csv = csv_path.read_bytes() if csv_path.exists() else None
    old_metadata = metadata.read_bytes() if metadata.exists() else None
    real_replace = os.replace

    def fail_metadata_publish(source: Path | str, destination: Path | str) -> None:
        if Path(destination) == metadata:
            raise OSError("metadata publish failed")
        real_replace(source, destination)

    monkeypatch.setattr("os.replace", fail_metadata_publish)

    with pytest.raises(OSError, match="metadata publish failed"):
        store.write_csv(
            relative,
            ("source_id", "finding"),
            ({"source_id": "new", "finding": "new finding"},),
            evidence_envelope(),
        )

    assert (
        csv_path.read_bytes() == old_csv if old_csv is not None else not csv_path.exists()
    )
    assert (
        metadata.read_bytes() == old_metadata
        if old_metadata is not None
        else not metadata.exists()
    )


def test_csv_pair_publication_and_rollback_sync_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchArtifactStore(tmp_path)
    relative = Path("artifacts/evidence-matrix.csv")
    metadata = tmp_path / relative.with_suffix(".meta.json")
    syncs: list[Path] = []

    def record_sync(directory: Path) -> None:
        syncs.append(directory)

    monkeypatch.setattr(research_artifacts, "_sync_parent_directory", record_sync)
    store.write_csv(
        relative,
        ("source_id",),
        ({"source_id": "p1"},),
        evidence_envelope(),
    )

    assert syncs == [metadata.parent, metadata.parent]
    syncs.clear()
    real_replace = os.replace

    def fail_metadata_publish(source: Path | str, destination: Path | str) -> None:
        if Path(destination) == metadata:
            raise OSError("metadata publish failed")
        real_replace(source, destination)

    monkeypatch.setattr("os.replace", fail_metadata_publish)
    with pytest.raises(OSError, match="metadata publish failed"):
        store.write_csv(
            relative,
            ("source_id",),
            ({"source_id": "p2"},),
            evidence_envelope(),
        )

    assert syncs == [metadata.parent, metadata.parent]


def test_csv_data_sync_failure_removes_new_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchArtifactStore(tmp_path)
    relative = Path("artifacts/evidence-matrix.csv")
    csv_path = tmp_path / relative
    metadata_path = tmp_path / relative.with_suffix(".meta.json")

    def fail_sync(_: Path) -> None:
        raise OSError("data directory sync failed")

    monkeypatch.setattr(research_artifacts, "_sync_parent_directory", fail_sync)

    with pytest.raises(OSError, match="data directory sync failed"):
        store.write_csv(
            relative,
            ("source_id",),
            ({"source_id": "p1"},),
            evidence_envelope(),
        )

    assert not csv_path.exists()
    assert not metadata_path.exists()


def test_csv_data_sync_failure_is_not_masked_by_staged_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchArtifactStore(tmp_path)
    relative = Path("artifacts/evidence-matrix.csv")
    csv_path = tmp_path / relative
    metadata_path = tmp_path / relative.with_suffix(".meta.json")
    real_unlink = Path.unlink

    def fail_sync(_: Path) -> None:
        raise OSError("original data directory sync failed")

    def fail_metadata_stage_cleanup(
        path: Path, missing_ok: bool = False
    ) -> None:
        if path.name.startswith(".evidence-matrix.meta.json."):
            raise OSError("staged cleanup failed")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(research_artifacts, "_sync_parent_directory", fail_sync)
    monkeypatch.setattr(Path, "unlink", fail_metadata_stage_cleanup)

    with pytest.raises(OSError, match="original data directory sync failed"):
        store.write_csv(
            relative,
            ("source_id",),
            ({"source_id": "p1"},),
            evidence_envelope(),
        )

    assert not csv_path.exists()
    assert not metadata_path.exists()


def test_csv_metadata_sync_failure_restores_existing_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchArtifactStore(tmp_path)
    relative = Path("artifacts/evidence-matrix.csv")
    csv_path = tmp_path / relative
    metadata_path = tmp_path / relative.with_suffix(".meta.json")
    store.write_csv(
        relative,
        ("source_id",),
        ({"source_id": "old"},),
        evidence_envelope(),
    )
    old_csv = csv_path.read_bytes()
    old_metadata = metadata_path.read_bytes()
    sync_calls = 0

    def fail_metadata_sync(_: Path) -> None:
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 2:
            raise OSError("metadata directory sync failed")

    monkeypatch.setattr(research_artifacts, "_sync_parent_directory", fail_metadata_sync)

    with pytest.raises(OSError, match="metadata directory sync failed"):
        store.write_csv(
            relative,
            ("source_id",),
            ({"source_id": "new"},),
            evidence_envelope(),
        )

    assert csv_path.read_bytes() == old_csv
    assert metadata_path.read_bytes() == old_metadata


@pytest.mark.parametrize(
    "operation",
    ("write_structured", "read_structured", "write_csv", "write_markdown", "read_markdown"),
)
def test_all_public_routes_reject_authoritative_symlink_to_raw(
    tmp_path: Path, operation: str
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    try:
        (tmp_path / "artifacts").symlink_to(raw, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    store = ResearchArtifactStore(tmp_path)

    with pytest.raises(PermissionError, match="authoritative artifact namespaces"):
        if operation == "write_structured":
            store.write_structured(Path("artifacts/brief.json"), sealed_brief_artifact())
        elif operation == "read_structured":
            store.read_structured(
                Path("artifacts/brief.json"),
                TypeAdapter(ResearchArtifact[dict[str, str]]),
            )
        elif operation == "write_csv":
            store.write_csv(
                Path("artifacts/evidence.csv"),
                ("source_id",),
                ({"source_id": "p1"},),
                evidence_envelope(),
            )
        elif operation == "write_markdown":
            store.write_markdown(
                Path("artifacts/memo.md"), identification_envelope(), "# Memo\n"
            )
        else:
            store.read_markdown(Path("artifacts/memo.md"))


def test_markdown_read_rejects_tampered_body(tmp_path: Path) -> None:
    store = ResearchArtifactStore(tmp_path)
    relative = Path("decisions/identification-memo.md")
    store.write_markdown(relative, identification_envelope(), "# Identification\r\n")
    path = tmp_path / relative
    path.write_bytes(path.read_bytes().replace(b"# Identification", b"# Tampered"))

    with pytest.raises(ValueError, match="content hash mismatch"):
        store.read_markdown(relative)


def test_markdown_normalizes_newlines_before_hashing(tmp_path: Path) -> None:
    store = ResearchArtifactStore(tmp_path)
    relative = Path("decisions/identification-memo.md")
    store.write_markdown(relative, identification_envelope(), "# Identification\r\n")

    envelope, body = store.read_markdown(relative)

    assert body == "# Identification\n"
    assert envelope.content_hash == hashlib.sha256(body.encode("utf-8")).hexdigest()

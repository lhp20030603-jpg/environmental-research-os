"""Private immutable raw-output and acquired-archive evidence mechanics."""

from __future__ import annotations

import hashlib
import os
import tarfile
import tempfile
from pathlib import Path

from envresearch.benchmarks.compare import ComparisonStatus, compare_output
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark import ExpectedOutput
from envresearch.replication._ledger_models import OutputResult, ReplicationRun
from envresearch.replication._service_support import persist_payload, read_exact
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    InventoryFile,
    Tier2ExpectedOutput,
)
from envresearch.storage.hashing import sha256_file
from envresearch.storage.research_artifacts import ResearchArtifactStore

_RAW_ID = "tier2-raw-author-output"
_CHUNK = 1024 * 1024


def persist_raw_output(
    store: ResearchArtifactStore,
    path: str,
    source: Path,
    inputs: tuple[ArtifactRef, ...],
    log_ref: ArtifactRef,
    *,
    max_bytes: int,
) -> ArtifactRef:
    """Publish a bounded content-addressed byte copy and its sealed manifest."""
    if source.is_symlink() or not source.is_file():
        raise ValueError("raw author output must be a regular file")
    size = source.stat().st_size
    if size > max_bytes:
        raise ValueError("raw author output exceeds approved storage")
    digest = sha256_file(source)
    relative = Path(f"artifacts/replication/raw-outputs/blobs/{digest}.bin")
    target = store.root / relative
    _publish_blob(source, target, digest, size)
    return persist_payload(
        store,
        _RAW_ID,
        "raw-output-manifests",
        {
            "path": path,
            "sha256": digest,
            "bytes": size,
            "blob": relative.as_posix(),
        },
        (*inputs, log_ref),
        "tier2-replication",
    )


def require_raw_output(
    store: ResearchArtifactStore,
    run: ReplicationRun,
    result: OutputResult,
    declaration: Tier2ExpectedOutput,
    input_root: Path,
) -> None:
    """Rehash both retained raw forms and independently repeat comparison."""
    artifact = read_exact(store, "raw-output-manifests", result.raw_ref)
    if (
        artifact.envelope.artifact_id != _RAW_ID
        or artifact.envelope.producer.component != "tier2-replication"
        or artifact.envelope.input_artifacts
        != (
            run.approved_intake_ref,
            run.acquired_inventory_ref,
            run.runtime_ref,
            result.log_ref,
        )
        or not isinstance(artifact.payload, dict)
    ):
        raise ValueError("raw author output manifest identity is invalid")
    payload = artifact.payload
    expected_blob = f"artifacts/replication/raw-outputs/blobs/{result.sha256}.bin"
    if (
        set(payload) != {"path", "sha256", "bytes", "blob"}
        or payload.get("path") != result.path
        or payload.get("sha256") != result.sha256
        or payload.get("blob") != expected_blob
        or type(payload.get("bytes")) is not int
        or payload["bytes"] < 0
    ):
        raise ValueError("raw author output manifest differs from ledger")
    blob = store.root / expected_blob
    workspace = store.root / run.output_root / "author-reproduction" / result.path
    for path in (blob, workspace):
        if path.is_symlink() or not path.is_file():
            raise ValueError("raw author output bytes are unavailable")
        if (
            path.stat().st_size != payload["bytes"]
            or sha256_file(path) != result.sha256
        ):
            raise ValueError("raw author output bytes differ from sealed digest")
    comparison = compare_output(
        blob.parent,
        input_root,
        ExpectedOutput(
            path=Path(blob.name),
            comparator=declaration.comparator,
            expected_path=Path(declaration.expected_path),
            absolute_tolerance=declaration.absolute_tolerance,
            relative_tolerance=declaration.relative_tolerance,
        ),
    )
    if comparison.status is not ComparisonStatus.MATCHED:
        raise ValueError("raw author output no longer matches approved expectation")


def require_acquired_bytes(
    store: ResearchArtifactStore, inventory: AcquiredPackageInventory
) -> Path:
    """Rehash the raw archive and its run-root materialization without writes."""
    archive_path = (
        store.root / f"artifacts/replication/raw/{inventory.archive_sha256}.tar.gz"
    )
    if (
        archive_path.is_symlink()
        or archive_path.stat().st_size != inventory.archive_bytes
        or sha256_file(archive_path) != inventory.archive_sha256
    ):
        raise ValueError("acquired raw archive differs from inventory")
    members = {item.path.as_posix(): item for item in inventory.files}
    with tarfile.open(archive_path, "r:gz") as archive:
        observed = {member.name: member for member in archive.getmembers()}
        if set(observed) != set(members):
            raise ValueError("acquired archive members differ from inventory")
        for name, expected in members.items():
            _require_member(archive, observed[name], expected)
    root = (
        store.root
        / "artifacts/replication/acquired"
        / inventory.archive_sha256
        / inventory.approved_intake_ref.content_hash
    )
    _require_materialized(root, members)
    return root.resolve()


def _require_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo, expected: InventoryFile
) -> None:
    source = archive.extractfile(member)
    if source is None or not member.isreg() or member.size != expected.bytes:
        raise ValueError("acquired archive member is not the inventoried file")
    digest = hashlib.sha256()
    observed = 0
    while chunk := source.read(min(_CHUNK, expected.bytes - observed + 1)):
        observed += len(chunk)
        if observed > expected.bytes:
            raise ValueError("acquired archive member exceeds inventoried size")
        digest.update(chunk)
    if observed != expected.bytes or digest.hexdigest() != expected.sha256:
        raise ValueError("acquired archive member differs from inventory")


def _require_materialized(root: Path, members: dict[str, InventoryFile]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("materialized acquired input is unavailable")
    paths = tuple(root.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("materialized acquired input contains a symlink")
    files = {
        path.relative_to(root).as_posix()
        for path in paths
        if path.is_file() and ".generated" not in path.relative_to(root).parts
    }
    if files != set(members):
        raise ValueError("materialized acquired inputs differ from inventory")
    for name, expected in members.items():
        path = root / name
        if (
            path.stat().st_size != expected.bytes
            or sha256_file(path) != expected.sha256
        ):
            raise ValueError("materialized acquired input hash mismatch")


def _publish_blob(source: Path, target: Path, digest: str, size: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.stat().st_size != size or sha256_file(target) != digest:
            raise ValueError("content-addressed raw output blob differs")
        return
    descriptor, staged_name = tempfile.mkstemp(prefix=".raw-", dir=target.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "wb") as destination, source.open("rb") as origin:
            while chunk := origin.read(_CHUNK):
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if staged.stat().st_size != size or sha256_file(staged) != digest:
            raise ValueError("raw output changed during immutable publication")
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)

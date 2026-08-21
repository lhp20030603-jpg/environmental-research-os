"""Private authenticated snapshots for resumable attempt workspaces."""

from __future__ import annotations

from pathlib import Path

from envresearch.models.artifact import ArtifactRef
from envresearch.replication._service_support import persist_payload, read_exact
from envresearch.replication.ledger import ReplicationRun
from envresearch.storage.hashing import sha256_file
from envresearch.storage.research_artifacts import ResearchArtifactStore

_CHECKPOINT_ID = "tier2-workspace-checkpoint"
_MAX_FILES = 10_000


def persist_workspace_checkpoint(
    store: ResearchArtifactStore,
    run_ref: ArtifactRef,
    run: ReplicationRun,
    root: Path,
    *,
    max_bytes: int,
) -> ArtifactRef:
    """Seal the exact bounded file set retained for a later resume."""
    files = _snapshot(store, run, root, max_bytes=max_bytes)
    return persist_payload(
        store,
        _CHECKPOINT_ID,
        "checkpoints",
        {"output_root": run.output_root, "files": files},
        (run_ref, run.attempt_ref),
        "replication-service",
    )


def require_workspace_checkpoint(
    store: ResearchArtifactStore,
    paused_ref: ArtifactRef,
    run: ReplicationRun,
    root: Path,
    *,
    max_bytes: int,
) -> None:
    """Reopen the referenced snapshot and match every retained file exactly."""
    if run.exception is None:
        raise ValueError("paused run lacks a typed checkpoint reason")
    references = tuple(
        ref for ref in run.exception.evidence_refs if ref.artifact_id == _CHECKPOINT_ID
    )
    if not references:
        if any(root.rglob("*")):
            raise ValueError("paused output root has no authenticated checkpoint")
        return
    if len(references) != 1:
        raise ValueError("paused run must reference one workspace checkpoint")
    reference = references[0]
    artifact = read_exact(store, "checkpoints", reference)
    if (
        artifact.envelope.artifact_id != _CHECKPOINT_ID
        or artifact.envelope.producer.component != "replication-service"
        or len(artifact.envelope.input_artifacts) != 2
        or artifact.envelope.input_artifacts[1] != run.attempt_ref
    ):
        raise ValueError("workspace checkpoint identity is invalid")
    predecessor = artifact.envelope.input_artifacts[0]
    if (
        predecessor.artifact_id != "replication-ledger"
        or predecessor.artifact_version != paused_ref.artifact_version - 1
    ):
        raise ValueError("workspace checkpoint does not bind paused predecessor")
    if not isinstance(artifact.payload, dict):
        raise TypeError("workspace checkpoint payload must be an object")
    if artifact.payload.get("output_root") != run.output_root:
        raise ValueError("workspace checkpoint output root differs from ledger")
    expected = artifact.payload.get("files")
    observed = _snapshot(store, run, root, max_bytes=max_bytes)
    if expected != observed:
        raise ValueError("paused workspace differs from authenticated checkpoint")


def _snapshot(
    store: ResearchArtifactStore,
    run: ReplicationRun,
    root: Path,
    *,
    max_bytes: int,
) -> list[dict[str, object]]:
    expected = (store.root / run.output_root).resolve()
    if root.is_symlink() or root.resolve() != expected or not root.is_dir():
        raise ValueError("workspace root differs from attempt authority")
    paths = sorted(root.rglob("*"))
    if len(paths) > _MAX_FILES or any(path.is_symlink() for path in paths):
        raise ValueError("workspace checkpoint has unsafe or excessive entries")
    files: list[dict[str, object]] = []
    total = 0
    for path in paths:
        if not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        if total > max_bytes:
            raise ValueError("workspace checkpoint exceeds approved storage")
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": size,
                "sha256": sha256_file(path),
            }
        )
    return files

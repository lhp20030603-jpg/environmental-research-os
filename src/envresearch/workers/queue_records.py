"""Small immutable record constructor shared by the filesystem queue."""

from datetime import UTC, datetime
from pathlib import Path

from envresearch.storage.artifacts import ArtifactRecord

MANAGED_SOURCE_NAMESPACES = frozenset(
    {
        ".locks",
        "artifacts",
        "decisions",
        "node-checkpoints",
        "raw",
        "work-orders",
        "worker-submissions",
    }
)


def artifact_record(relative: Path, digest: str, size: int) -> ArtifactRecord:
    """Describe one just-published queue artifact."""
    return ArtifactRecord(
        relative_path=relative,
        sha256=digest,
        size_bytes=size,
        written_at=datetime.now(UTC),
    )


def order_path(order_id: str) -> Path:
    """Return the canonical public work-order path."""
    return Path("work-orders") / f"{order_id}.json"


def candidate_path(order_id: str, filename: str) -> Path:
    """Return the canonical published candidate path."""
    return (
        Path("worker-submissions")
        / order_id
        / "transactions"
        / f"{filename}.submission"
        / filename
    )

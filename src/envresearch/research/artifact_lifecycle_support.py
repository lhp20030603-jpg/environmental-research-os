"""Small identity helpers shared by research artifact lifecycle operations."""

from __future__ import annotations

from pathlib import Path

from envresearch.models.artifact import ArtifactEnvelope, ArtifactRef, ProducerIdentity


def artifact_ref(envelope: ArtifactEnvelope) -> ArtifactRef:
    if envelope.content_hash is None:
        raise ValueError("input artifact is unsealed")
    return ArtifactRef(
        artifact_id=envelope.artifact_id,
        artifact_version=envelope.artifact_version,
        content_hash=envelope.content_hash,
    )


def history_path(path: Path, version: int) -> Path:
    namespace, *parts = path.parts
    relative = Path(*parts)
    root = Path(namespace) / ".versions" / relative.parent / relative.name
    return root / f"{version:04d}.json"


def normalize_body(body: str) -> str:
    return body.replace("\r\n", "\n").replace("\r", "\n")


def producer_identity(value: str | ProducerIdentity) -> ProducerIdentity:
    if isinstance(value, ProducerIdentity):
        return ProducerIdentity.model_validate(dict(value.__dict__))
    return ProducerIdentity(component=value, version="0.2.0")

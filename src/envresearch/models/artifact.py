"""Immutable, content-addressed research artifact contracts."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from envresearch.kernel.task_identity import payload_hash
from envresearch.models.enums import ArtifactLifecycle

T = TypeVar("T")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProducerIdentity(BaseModel):
    """Versioned identity of the component that produced an artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    component: str
    version: str
    model: str | None = None
    runtime: str | None = None
    context_id: str | None = None


class ArtifactRef(BaseModel):
    """Content-addressed reference to an immutable input artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    artifact_version: int = Field(ge=1)
    content_hash: str

    @field_validator("content_hash")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        """Require canonical digests for provenance references."""
        if not _SHA256.fullmatch(value):
            raise ValueError("content hash must be a 64-character lowercase SHA-256")
        return value


class ArtifactEnvelope(BaseModel):
    """Shared immutable metadata and provenance for research artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1.0"
    artifact_id: str
    artifact_version: int = Field(ge=1)
    run_id: str
    created_at: datetime
    producer: ProducerIdentity
    input_artifacts: tuple[ArtifactRef, ...] = ()
    provenance: dict[str, object] = Field(default_factory=dict)
    validation_status: ArtifactLifecycle = ArtifactLifecycle.PRODUCED
    content_hash: str | None = None

    @field_validator("created_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Reject ambiguous or noncanonical artifact timestamps."""
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("timestamps must be UTC-aware")
        return value


class ResearchArtifact(BaseModel, Generic[T]):
    """A typed payload paired with its immutable metadata envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope: ArtifactEnvelope
    payload: T


def seal_artifact(artifact: ResearchArtifact[T]) -> ResearchArtifact[T]:
    """Return an artifact copy sealed by its canonical JSON content digest."""
    canonical = artifact.model_dump(mode="json")
    canonical["envelope"]["content_hash"] = None
    digest = payload_hash(canonical)
    envelope = artifact.envelope.model_copy(update={"content_hash": digest})
    return artifact.model_copy(update={"envelope": envelope})


def verify_artifact(artifact: ResearchArtifact[object]) -> None:
    """Raise when an artifact is unsealed, malformed, or has been changed."""
    digest = artifact.envelope.content_hash
    if digest is None:
        raise ValueError("artifact is unsealed")
    if not _SHA256.fullmatch(digest):
        raise ValueError("content hash must be a 64-character lowercase SHA-256")

    canonical = artifact.model_dump(mode="json")
    canonical["envelope"]["content_hash"] = None
    if payload_hash(canonical) != digest:
        raise ValueError("content hash mismatch")

"""Exact immutable identity rules for resumable research artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from envresearch.models.artifact import (
    ArtifactEnvelope,
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
)
from envresearch.models.enums import ArtifactLifecycle


def utc_now() -> datetime:
    """Return the production timestamp for a new artifact envelope."""
    return datetime.now(UTC)


def json_value(payload: object) -> object:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    return json.loads(json.dumps(payload, default=str))


def base_provenance(path: Path, component: str) -> dict[str, object]:
    return {"node": component, "artifact_path": path.as_posix()}


def approved_provenance(
    path: Path,
    component: str,
    predecessor_ref: ArtifactRef,
    gate_context_hash: str,
) -> dict[str, object]:
    return {
        **base_provenance(path, component),
        "predecessor_ref": predecessor_ref.model_dump(mode="json"),
        "gate_context_hash": gate_context_hash,
    }


def make_envelope(
    *,
    path: Path,
    run_id: str,
    producer: ProducerIdentity,
    inputs: tuple[ArtifactRef, ...],
    version: int,
    status: ArtifactLifecycle,
    created_at: datetime,
    digest: str | None = None,
    provenance: dict[str, object] | None = None,
) -> ArtifactEnvelope:
    return ArtifactEnvelope(
        artifact_id=path.stem,
        artifact_version=version,
        run_id=run_id,
        created_at=created_at,
        producer=producer,
        input_artifacts=inputs,
        provenance=provenance
        if provenance is not None
        else base_provenance(path, producer.component),
        validation_status=status,
        content_hash=digest,
    )


def require_identity(
    artifact: ResearchArtifact[object],
    *,
    path: Path,
    payload: object,
    run_id: str,
    producer: ProducerIdentity,
    inputs: tuple[ArtifactRef, ...],
    version: int,
    status: ArtifactLifecycle,
    provenance: dict[str, object] | None = None,
) -> None:
    envelope = artifact.envelope
    expected = envelope.model_copy(
        update={
            "schema_version": "1.0",
            "artifact_id": path.stem,
            "artifact_version": version,
            "run_id": run_id,
            "producer": producer,
            "input_artifacts": inputs,
            "provenance": (
                provenance
                if provenance is not None
                else base_provenance(path, producer.component)
            ),
            "validation_status": status,
        }
    )
    if artifact.payload != json_value(payload) or envelope != expected:
        raise FileExistsError("conflicting immutable artifact version")

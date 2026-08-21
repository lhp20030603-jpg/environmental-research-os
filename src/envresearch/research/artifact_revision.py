"""Revision-aware structured artifact version transitions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from envresearch.models.artifact import (
    ArtifactEnvelope,
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.research.artifact_identity import json_value
from envresearch.research.artifact_lifecycle_support import (
    artifact_ref,
    producer_identity,
)

if TYPE_CHECKING:
    from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle


def persist_structured(
    lifecycle: ResearchArtifactLifecycle,
    path: Path,
    payload: object,
    component: str | ProducerIdentity,
    inputs: tuple[ArtifactRef, ...],
    final: ArtifactLifecycle,
) -> ResearchArtifact[object]:
    """Publish produced/validated versions after any superseded generation."""
    producer = producer_identity(component)
    normalized = json_value(payload)
    first_version = _next_produced_version(lifecycle, path)
    lifecycle._history(
        path,
        normalized,
        producer,
        inputs,
        first_version,
        ArtifactLifecycle.PRODUCED,
    )
    promoted = lifecycle._history(
        path, normalized, producer, inputs, first_version + 1, final
    )
    lifecycle._publish_structured(path, promoted)
    return promoted


def supersede(
    lifecycle: ResearchArtifactLifecycle,
    path: Path,
    *,
    revision_id: str,
    reason: str,
    actor: str,
) -> ResearchArtifact[object]:
    """Append an immutable superseded version and retain the prior payload."""
    if path.suffix in {".csv", ".md"}:
        from envresearch.research.artifact_special import supersede_special

        supersede_special(
            lifecycle,
            path,
            revision_id=revision_id,
            reason=reason,
            actor=actor,
        )
        return lifecycle.read_history(
            path, lifecycle.artifact_ref(path).artifact_version
        )
    current = lifecycle.read_artifact(path)
    provenance = dict(current.envelope.provenance)
    if current.envelope.validation_status is ArtifactLifecycle.SUPERSEDED:
        if provenance.get("revision_id") != revision_id:
            raise RuntimeError("artifact belongs to a conflicting revision")
        return lifecycle._require_history_matches_current(path, current)
    provenance.update(
        {
            "revision_id": revision_id,
            "revision_reason": reason,
            "revision_actor": actor,
            "supersedes_ref": artifact_ref(current.envelope).model_dump(mode="json"),
        }
    )
    superseded = lifecycle._history(
        path,
        current.payload,
        producer_identity(actor),
        current.envelope.input_artifacts,
        current.envelope.artifact_version + 1,
        ArtifactLifecycle.SUPERSEDED,
        provenance=provenance,
    )
    lifecycle._publish_upgrade(path, current, superseded)
    return superseded


def validated_history_ref(
    lifecycle: ResearchArtifactLifecycle, path: Path
) -> ArtifactRef:
    """Bind current validated data or an approved artifact's reviewed predecessor."""
    current = lifecycle.read_artifact(path)
    if current.envelope.validation_status is ArtifactLifecycle.APPROVED:
        predecessor = current.envelope.provenance.get("predecessor_ref")
        if not isinstance(predecessor, dict):
            raise ValueError("approved artifact lacks a reviewed predecessor")
        return ArtifactRef.model_validate(predecessor)
    if current.envelope.validation_status is not ArtifactLifecycle.VALIDATED:
        raise ValueError("artifact is not currently validated or approved")
    return lifecycle.history_ref(path, current.envelope.artifact_version)


def publish_structured(
    lifecycle: ResearchArtifactLifecycle,
    path: Path,
    artifact: ResearchArtifact[object],
) -> None:
    """Publish or verify current structured state across revision generations."""
    if (lifecycle.workspace / path).exists():
        current = lifecycle.read_artifact(path)
        if current.envelope.validation_status is ArtifactLifecycle.SUPERSEDED:
            publish_upgrade(lifecycle, path, current, artifact)
            return
        if current != artifact:
            raise FileExistsError("conflicting authoritative artifact exists")
        return
    lifecycle.store.write_structured(path, artifact)


def publish_upgrade(
    lifecycle: ResearchArtifactLifecycle,
    path: Path,
    previous: ResearchArtifact[object],
    promoted: ResearchArtifact[object],
) -> None:
    """Replace current only when it remains the exact expected predecessor."""
    durable = lifecycle.read_artifact(path)
    if durable == promoted:
        return
    if durable != previous:
        raise FileExistsError("current artifact changed during lifecycle promotion")
    lifecycle.store.write_structured(path, promoted)


def current_artifact_ref(
    lifecycle: ResearchArtifactLifecycle, path: Path
) -> ArtifactRef:
    """Resolve exact current identity for structured, CSV, or Markdown output."""
    if path.suffix == ".csv":
        envelope = ArtifactEnvelope.model_validate(
            lifecycle.raw.read_json(path.with_suffix(".meta.json"))
        )
    elif path.suffix == ".md":
        envelope, _ = lifecycle.store.read_markdown(path)
    else:
        envelope = lifecycle.read_artifact(path).envelope
    if envelope.provenance.get("artifact_path") != path.as_posix():
        raise FileExistsError("artifact path identity mismatch")
    return artifact_ref(envelope)


def current_envelope(
    lifecycle: ResearchArtifactLifecycle, path: Path
) -> ArtifactEnvelope:
    """Read the authoritative envelope without assuming an output format."""
    if path.suffix == ".csv":
        return ArtifactEnvelope.model_validate(
            lifecycle.raw.read_json(path.with_suffix(".meta.json"))
        )
    if path.suffix == ".md":
        envelope, _ = lifecycle.store.read_markdown(path)
        return envelope
    return lifecycle.read_artifact(path).envelope


def _next_produced_version(lifecycle: ResearchArtifactLifecycle, path: Path) -> int:
    if not (lifecycle.workspace / path).exists():
        return 1
    current = lifecycle.read_artifact(path)
    if current.envelope.validation_status is ArtifactLifecycle.SUPERSEDED:
        return current.envelope.artifact_version + 1
    return max(1, current.envelope.artifact_version - 1)

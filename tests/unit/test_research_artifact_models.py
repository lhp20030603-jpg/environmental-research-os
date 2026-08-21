"""Tests for immutable, content-addressed research artifacts."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from envresearch import __version__
from envresearch.models.artifact import (
    ArtifactEnvelope,
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
    verify_artifact,
)
from envresearch.models.enums import ArtifactLifecycle


def envelope() -> ArtifactEnvelope:
    """Build a deterministic envelope for hashing tests."""
    return ArtifactEnvelope(
        artifact_id="brief-001",
        artifact_version=1,
        run_id="run-001",
        created_at=datetime(2026, 8, 5, tzinfo=UTC),
        producer=ProducerIdentity(
            component="research-intake",
            version="0.2.0",
            model="gpt-5",
            runtime="codex",
        ),
        provenance={"source": "user-brief"},
    )


def test_sealed_artifact_verifies_and_detects_payload_change() -> None:
    """A hash must bind the complete artifact payload and its envelope."""
    sealed = seal_artifact(
        ResearchArtifact(envelope=envelope(), payload={"topic": "air"})
    )

    verify_artifact(sealed)
    with pytest.raises(ValueError, match="content hash mismatch"):
        verify_artifact(sealed.model_copy(update={"payload": {"topic": "water"}}))


def test_verify_artifact_rejects_unsealed_and_malformed_hashes() -> None:
    """Only sealed artifacts carrying lowercase SHA-256 digests are valid."""
    artifact = ResearchArtifact(envelope=envelope(), payload={"topic": "air"})

    with pytest.raises(ValueError, match="unsealed"):
        verify_artifact(artifact)

    malformed = artifact.model_copy(
        update={"envelope": artifact.envelope.model_copy(update={"content_hash": "A" * 64})}
    )
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        verify_artifact(malformed)


def test_envelope_records_complete_metadata_and_rejects_invalid_inputs() -> None:
    """Provenance links must be immutable, complete, and unambiguous."""
    input_ref = ArtifactRef(
        artifact_id="charter-001",
        artifact_version=2,
        content_hash="a" * 64,
    )
    artifact = ResearchArtifact(
        envelope=envelope().model_copy(
            update={
                "input_artifacts": (input_ref,),
                "validation_status": ArtifactLifecycle.VALIDATED,
            }
        ),
        payload={"topic": "air"},
    )

    assert artifact.envelope.input_artifacts == (input_ref,)
    assert artifact.envelope.validation_status is ArtifactLifecycle.VALIDATED
    with pytest.raises(ValidationError, match="Extra inputs"):
        ArtifactEnvelope.model_validate({**envelope().model_dump(), "unexpected": True})
    with pytest.raises(ValidationError, match="UTC"):
        ArtifactEnvelope(
            artifact_id="brief-002",
            artifact_version=1,
            run_id="run-001",
            created_at=datetime(
                2026, 8, 5, tzinfo=timezone(timedelta(hours=8))
            ),
            producer=ProducerIdentity(component="research-intake", version="0.2.0"),
        )


def test_package_exports_v02_version() -> None:
    """The public package version must match the V0.2 artifact contract."""
    assert __version__ == "0.2.0"

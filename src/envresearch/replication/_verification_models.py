"""Private sealed report values re-exported by the read-only verifier."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    TypeAdapter,
    field_validator,
    model_validator,
)

from envresearch.models.artifact import ArtifactRef, ResearchArtifact, verify_artifact
from envresearch.models.enums import ArtifactLifecycle
from envresearch.replication._service_support import seal_payload

_FROZEN = ConfigDict(extra="forbid", frozen=True, strict=True)
Model = TypeVar("Model", bound=BaseModel)


class VerificationFinding(BaseModel):
    """One stable fail-closed finding from independent evidence reopening."""

    model_config = _FROZEN

    code: str
    message: str
    evidence: tuple[str, ...] = ()

    @field_validator("code", "message")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("verification finding text must be canonical and nonblank")
        return value


class VerificationPayload(BaseModel):
    """Exact run and evidence references covered by a verifier result."""

    model_config = _FROZEN

    run_ref: ArtifactRef
    verified_refs: tuple[ArtifactRef, ...]
    findings: tuple[VerificationFinding, ...]


class VerificationReport(BaseModel):
    """Sealed independent verification report; zero findings means passed."""

    model_config = _FROZEN

    artifact: ResearchArtifact[VerificationPayload]

    @model_validator(mode="after")
    def require_verifier_seal(self) -> VerificationReport:
        verify_artifact(cast(ResearchArtifact[object], self.artifact))
        envelope = self.artifact.envelope
        payload = self.artifact.payload
        if envelope.artifact_id != "tier2-replication-verification":
            raise ValueError("verification artifact ID is invalid")
        if envelope.producer.component != "replication-verifier":
            raise ValueError("verification artifact producer is invalid")
        if envelope.validation_status is not ArtifactLifecycle.VALIDATED:
            raise ValueError("verification artifact is not validated")
        if envelope.input_artifacts != payload.verified_refs:
            raise ValueError("verification artifact inputs differ from verified refs")
        if not payload.verified_refs or payload.verified_refs[-1] != payload.run_ref:
            raise ValueError("verification report does not bind its run reference")
        return self

    @property
    def run_ref(self) -> ArtifactRef:
        return self.artifact.payload.run_ref

    @property
    def verified_refs(self) -> tuple[ArtifactRef, ...]:
        return self.artifact.payload.verified_refs

    @property
    def findings(self) -> tuple[VerificationFinding, ...]:
        return self.artifact.payload.findings

    @property
    def passed(self) -> bool:
        return not self.findings


def seal_verification(
    run_ref: ArtifactRef,
    verified_refs: tuple[ArtifactRef, ...],
    findings: list[VerificationFinding],
) -> VerificationReport:
    payload = VerificationPayload(
        run_ref=run_ref, verified_refs=verified_refs, findings=tuple(findings)
    )
    artifact = seal_payload(
        "tier2-replication-verification",
        payload,
        verified_refs,
        "replication-verifier",
    )
    typed = TypeAdapter(ResearchArtifact[VerificationPayload]).validate_python(artifact)
    return VerificationReport(artifact=typed)


def restore(model: type[Model], payload: object) -> Model:
    return model.model_validate_json(json.dumps(payload))


def finding(code: str, path: Path, error: Exception) -> VerificationFinding:
    return VerificationFinding(
        code=code, message=str(error), evidence=(path.as_posix(),)
    )

"""Frozen evidence and result contracts for blind benchmark scoring."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from envresearch.models.artifact import ArtifactRef, ResearchArtifact, verify_artifact
from envresearch.models.benchmark_evaluation import (
    AdjudicationVerdict,
    ExpertDimension,
    ExpertScoreSheet,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.principal import PrincipalAssignment, PrincipalKind


class StrictScoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SealedScoreArtifact(StrictScoringModel):
    case_id: str
    score_sheet_ref: ArtifactRef
    artifact: ResearchArtifact[ExpertScoreSheet]
    current_ref: ArtifactRef
    validated_history_ref: ArtifactRef
    principal_assignment: PrincipalAssignment
    queue_order_id: str
    queue_input_artifacts: tuple[ArtifactRef, ...]
    signed_evidence_ref: ArtifactRef

    @field_validator("case_id", "queue_order_id")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("score provenance text must be canonical and nonblank")
        return value

    @model_validator(mode="after")
    def require_current_authenticated_score(self) -> SealedScoreArtifact:
        verify_artifact(cast(ResearchArtifact[object], self.artifact))
        if self.artifact.envelope.validation_status is not ArtifactLifecycle.VALIDATED:
            raise ValueError("score artifact must be currently validated")
        sealed_ref = artifact_ref(cast(ResearchArtifact[object], self.artifact))
        if (
            self.score_sheet_ref != sealed_ref
            or self.current_ref != sealed_ref
            or self.validated_history_ref != sealed_ref
        ):
            raise ValueError("score refs must match the current validated artifact")
        if self.artifact.payload.recommendation_ref not in self.queue_input_artifacts:
            raise ValueError("score order must bind the evaluated recommendation")
        if self.artifact.envelope.producer != self.principal_assignment.producer:
            raise ValueError("score producer does not match authenticated assignment")
        if self.signed_evidence_ref not in self.artifact.envelope.input_artifacts:
            raise ValueError("score artifact must bind its signed evidence")
        if (
            self.artifact.payload.scorer_principal
            != self.principal_assignment.principal_id
        ):
            raise ValueError("score principal does not match authenticated assignment")
        return self

    @property
    def score_sheet(self) -> ExpertScoreSheet:
        return self.artifact.payload

    @property
    def recommendation_ref(self) -> ArtifactRef:
        return self.score_sheet.recommendation_ref


class LockedThirdScore(StrictScoringModel):
    score: SealedScoreArtifact

    @model_validator(mode="after")
    def require_adjudicator_score_order(self) -> LockedThirdScore:
        if self.score.principal_assignment.kind is not PrincipalKind.ADJUDICATOR:
            raise ValueError("third score requires an adjudicator assignment")
        if self.score.queue_order_id != "adjudicator-score":
            raise ValueError("third score must be locked by adjudicator-score order")
        return self


class AdjudicationRecord(StrictScoringModel):
    third_score: LockedThirdScore
    final_order_inputs: tuple[ArtifactRef, ...]
    verdict_ref: ArtifactRef
    signed_verdict_evidence_ref: ArtifactRef
    verdict: AdjudicationVerdict


class DimensionMean(StrictScoringModel):
    dimension: ExpertDimension
    score: Decimal


class CaseEvaluation(StrictScoringModel):
    recommendation_ref: ArtifactRef
    original_score_artifacts: tuple[SealedScoreArtifact, ...]
    dimension_scores: tuple[DimensionMean, ...]
    weighted_score: Decimal
    passed: bool
    requires_adjudication: bool
    adjudication: AdjudicationRecord | None = None

    @property
    def original_score_sheets(self) -> tuple[ExpertScoreSheet, ...]:
        return tuple(item.score_sheet for item in self.original_score_artifacts)

    @property
    def dimension_means(self) -> dict[ExpertDimension, Decimal]:
        return {item.dimension: item.score for item in self.dimension_scores}


def artifact_ref(artifact: ResearchArtifact[object]) -> ArtifactRef:
    envelope = artifact.envelope
    if envelope.content_hash is None:
        raise ValueError("score artifact is unsealed")
    return ArtifactRef(
        artifact_id=envelope.artifact_id,
        artifact_version=envelope.artifact_version,
        content_hash=envelope.content_hash,
    )

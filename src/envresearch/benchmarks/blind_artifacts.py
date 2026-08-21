"""Typed persistence and exact lineage for one blind benchmark case."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import TypeVar, cast

from pydantic import BaseModel

from envresearch.benchmarks.blind_artifact_support import (
    BlindLineageValidator,
    artifact_ref,
    decode_payload,
    require_case,
    require_payload_principal,
    require_refs,
    revalidate_model,
)
from envresearch.benchmarks.blind_evidence_artifacts import require_signed_evidence
from envresearch.benchmarks.blind_paths import BlindArtifactPaths
from envresearch.benchmarks.blind_principal_auth import PrincipalAuthenticator
from envresearch.benchmarks.claim_integrity import CitationIntegrityReport
from envresearch.benchmarks.claim_report import report_input_refs, report_payload
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_blinding import BlindedBrief, LeakageReport
from envresearch.models.benchmark_claims import ClaimFactMap, CuratorSourceSheet
from envresearch.models.benchmark_evaluation import (
    AdjudicationVerdict,
    ExpertScoreSheet,
    MethodRecommendationPayload,
    PosthocComparison,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.principal import PrincipalAssignment, PrincipalKind
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.principal_registry import PrincipalRegistry

__all__ = ["BlindArtifactLifecycle", "BlindArtifactPaths"]
ModelT = TypeVar("ModelT", bound=BaseModel)
LockedMethod = TypeVar("LockedMethod", bound=Callable[..., ArtifactRef])


def _case_locked(method: LockedMethod) -> LockedMethod:
    @wraps(method)
    def locked(
        self: BlindArtifactLifecycle,
        case_id: str,
        *args: object,
        **kwargs: object,
    ) -> ArtifactRef:
        with self.principals.registry.control.transaction_lock("blind-case", case_id):
            from envresearch.benchmarks.blind_enrollment_marker import (
                require_frozen_enrollment,
            )
            require_frozen_enrollment(self.principals.registry, case_id)
            return method(self, case_id, *args, **kwargs)

    return cast(LockedMethod, locked)


class BlindArtifactLifecycle:
    def __init__(self, workspace: Path, run_id: str, principals: PrincipalRegistry) -> None:
        self.lifecycle = ResearchArtifactLifecycle(workspace, run_id)
        self.principals = PrincipalAuthenticator(principals)
        self.lineage = BlindLineageValidator(
            self.lifecycle,
            self.paths,
            self.principals.require_producer,
            principals.control,
        )

    def paths(self, case_id: str) -> BlindArtifactPaths:
        return BlindArtifactPaths.for_case(case_id)

    def ref(self, case_id: str, artifact: str) -> ArtifactRef:
        paths = self.paths(case_id)
        if artifact not in BlindArtifactPaths.__dataclass_fields__:
            raise ValueError("unknown blind benchmark artifact")
        return self.lifecycle.artifact_ref(getattr(paths, artifact))

    @_case_locked
    def publish_source(
        self,
        case_id: str,
        payload: CuratorSourceSheet,
        principal: PrincipalAssignment,
    ) -> ArtifactRef:
        source = revalidate_model(payload, CuratorSourceSheet)
        require_case(case_id, source.case_id)
        return self._persist_locked(
            case_id,
            self.paths(case_id).source_sheet,
            source,
            principal,
            PrincipalKind.CURATOR,
            (),
        )

    @_case_locked
    def publish_brief(
        self,
        case_id: str,
        payload: BlindedBrief,
        principal: PrincipalAssignment,
    ) -> ArtifactRef:
        brief = revalidate_model(payload, BlindedBrief)
        require_case(case_id, brief.case_id)
        source = self._validated_ref(self.paths(case_id).source_sheet)
        require_refs((brief.source_sheet_ref,), (source,))
        require_payload_principal(brief.masker_principal, principal)
        return self._persist_locked(
            case_id,
            self.paths(case_id).blinded_brief,
            brief,
            principal,
            PrincipalKind.MASKER,
            (source,),
        )

    @_case_locked
    def publish_fact_map(
        self,
        case_id: str,
        payload: ClaimFactMap,
        principal: PrincipalAssignment,
    ) -> ArtifactRef:
        mapping = revalidate_model(payload, ClaimFactMap)
        require_case(case_id, mapping.case_id)
        inputs = self._source_brief_refs(case_id)
        require_refs((mapping.source_sheet_ref, mapping.blinded_brief_ref), inputs)
        require_payload_principal(mapping.mapper_principal, principal)
        return self._persist_locked(
            case_id,
            self.paths(case_id).claim_fact_map,
            mapping,
            principal,
            PrincipalKind.MASKER,
            inputs,
        )

    @_case_locked
    def publish_leakage(
        self,
        case_id: str,
        payload: LeakageReport,
        principal: PrincipalAssignment,
    ) -> ArtifactRef:
        report = revalidate_model(payload, LeakageReport)
        inputs = self._source_brief_refs(case_id)
        require_refs((report.source_sheet_ref, report.blinded_brief_ref), inputs)
        require_payload_principal(report.validator_principal, principal)
        return self._persist_locked(
            case_id,
            self.paths(case_id).leakage_report,
            report,
            principal,
            PrincipalKind.LEAKAGE_VALIDATOR,
            inputs,
        )

    @_case_locked
    def publish_recommendation(
        self,
        case_id: str,
        payload: MethodRecommendationPayload,
        principal: PrincipalAssignment,
    ) -> ArtifactRef:
        recommendation = revalidate_model(payload, MethodRecommendationPayload)
        paths = self.paths(case_id)
        inputs = (
            self._validated_ref(paths.blinded_brief),
            self._validated_ref(paths.leakage_report),
        )
        require_refs(
            (recommendation.blinded_brief_ref, recommendation.leakage_report_ref),
            inputs,
        )
        if self._payload(paths.leakage_report, LeakageReport).verdict != "pass":
            raise ValueError("passing leakage report is required")
        require_payload_principal(recommendation.recommender_principal, principal)
        return self._persist_locked(
            case_id,
            paths.recommendation,
            recommendation,
            principal,
            PrincipalKind.RECOMMENDER,
            inputs,
        )

    @_case_locked
    def publish_expert_score(
        self,
        case_id: str,
        payload: ExpertScoreSheet,
        principal: PrincipalAssignment,
        *,
        slot: int,
    ) -> ArtifactRef:
        score = revalidate_model(payload, ExpertScoreSheet)
        paths = self.paths(case_id)
        inputs = (
            self._validated_ref(paths.blinded_brief),
            self._validated_ref(paths.recommendation),
        )
        evidence = require_signed_evidence(
            self, case_id, principal, PrincipalKind.EXPERT, slot, score,
            "envresearch.ExpertScoreSheet",
        )
        require_refs((score.recommendation_ref,), (inputs[1],))
        require_payload_principal(score.scorer_principal, principal)
        return self._persist_locked(
            case_id,
            paths.expert_score(slot),
            score,
            principal,
            PrincipalKind.EXPERT,
            (*inputs, evidence),
            slot=slot,
        )

    @_case_locked
    def publish_third_score(
        self, case_id: str, payload: ExpertScoreSheet, principal: PrincipalAssignment
    ) -> ArtifactRef:
        score = revalidate_model(payload, ExpertScoreSheet)
        paths = self.paths(case_id)
        inputs = (self._validated_ref(paths.blinded_brief),
                  self._validated_ref(paths.recommendation),
                  self._validated_ref(paths.source_sheet.parent / "expert-rubric.json"))
        evidence = require_signed_evidence(
            self, case_id, principal, PrincipalKind.ADJUDICATOR, 1, score,
            "envresearch.ExpertScoreSheet",
        )
        require_refs((score.recommendation_ref,), (inputs[1],))
        require_payload_principal(score.scorer_principal, principal)
        return self._persist_locked(case_id, paths.third_score, score, principal,
                                    PrincipalKind.ADJUDICATOR, (*inputs, evidence), slot=1)

    @_case_locked
    def publish_adjudication(
        self,
        case_id: str,
        payload: AdjudicationVerdict,
        principal: PrincipalAssignment,
        *,
        slot: int = 1,
    ) -> ArtifactRef:
        if slot != 1:
            raise ValueError("canonical adjudicator slot is one")
        verdict = revalidate_model(payload, AdjudicationVerdict)
        evidence = require_signed_evidence(
            self, case_id, principal, PrincipalKind.ADJUDICATOR, slot, verdict,
            "envresearch.AdjudicationVerdict",
        )
        recommendation, expert_one, expert_two = self.lineage.locked_score_refs(case_id)
        third = self.lineage.locked_third_score_ref(case_id)
        if verdict.score_sheet_ref not in (expert_one, expert_two):
            raise ValueError("case lineage mismatch")
        require_payload_principal(verdict.adjudicator_principal, principal)
        return self._persist_locked(
            case_id,
            self.paths(case_id).adjudication,
            verdict,
            principal,
            PrincipalKind.ADJUDICATOR,
            (recommendation, expert_one, expert_two, third, evidence),
            slot=slot,
        )

    @_case_locked
    def publish_posthoc(
        self,
        case_id: str,
        payload: PosthocComparison,
        principal: PrincipalAssignment,
        *,
        slot: int = 1,
    ) -> ArtifactRef:
        if slot != 1:
            raise ValueError("canonical adjudicator slot is one")
        comparison = revalidate_model(payload, PosthocComparison)
        try:
            recommendation, expert_one, expert_two = self.lineage.locked_score_refs(
                case_id
            )
        except (FileNotFoundError, FileExistsError, TypeError, ValueError) as error:
            raise ValueError(
                "blind scores must be locked before posthoc comparison"
            ) from error
        require_refs((comparison.recommendation_ref,), (recommendation,))
        require_payload_principal(comparison.analyst_principal, principal)
        paths = self.paths(case_id)
        inputs: tuple[ArtifactRef, ...] = (
            self._validated_ref(paths.source_sheet),
            recommendation,
            expert_one,
            expert_two,
        )
        if (self.lifecycle.workspace / paths.adjudication).exists() and (
            self.lifecycle.current_envelope(paths.adjudication).validation_status
            is ArtifactLifecycle.VALIDATED
        ):
            inputs = (*inputs, self._validated_ref(paths.adjudication))
        return self._persist_locked(
            case_id,
            paths.posthoc_comparison,
            comparison,
            principal,
            PrincipalKind.ADJUDICATOR,
            inputs,
            slot=slot,
        )

    @_case_locked
    def publish_citation_report(
        self,
        case_id: str,
        report: CitationIntegrityReport,
        principal: PrincipalAssignment,
    ) -> ArtifactRef:
        if report != self.lineage.recompute_citation_report(case_id, report):
            raise ValueError("citation integrity report is not current")
        return self._persist_locked(
            case_id,
            self.paths(case_id).citation_report,
            report_payload(report),
            principal,
            PrincipalKind.LEAKAGE_VALIDATOR,
            report_input_refs(report),
        )

    def require_current_chain(self, case_id: str) -> tuple[ArtifactRef, ...]:
        try:
            return self.lineage.require_current_chain(case_id)
        except (FileNotFoundError, FileExistsError, TypeError, ValueError) as error:
            raise ValueError("blind benchmark lineage is stale") from error

    @_case_locked
    def revise_source(
        self,
        case_id: str,
        payload: CuratorSourceSheet,
        *,
        revision_id: str,
        reason: str,
        actor: str,
        curator: PrincipalAssignment,
    ) -> ArtifactRef:
        source = revalidate_model(payload, CuratorSourceSheet)
        require_case(case_id, source.case_id)
        assignment = self.principals.require_assignment(
            case_id, curator, PrincipalKind.CURATOR, None
        )
        return self.lineage.revise_source(
            case_id,
            source,
            revision_id=revision_id,
            reason=reason,
            actor=actor,
            curator=assignment,
        )

    def _persist_locked(
        self,
        case_id: str,
        path: Path,
        payload: object,
        principal: PrincipalAssignment,
        kind: PrincipalKind,
        inputs: tuple[ArtifactRef, ...],
        *,
        slot: int | None = None,
    ) -> ArtifactRef:
        assignment = self.principals.require_assignment(case_id, principal, kind, slot)
        return artifact_ref(
            self.lifecycle.persist_structured(
                path, payload, assignment.producer, inputs
            )
        )

    def _validated_ref(self, path: Path) -> ArtifactRef:
        current = self.lifecycle.artifact_ref(path)
        if self.lifecycle.validated_history_ref(path) != current:
            raise FileExistsError("current artifact does not match validated history")
        return current

    def _source_brief_refs(self, case_id: str) -> tuple[ArtifactRef, ArtifactRef]:
        paths = self.paths(case_id)
        return (
            self._validated_ref(paths.source_sheet),
            self._validated_ref(paths.blinded_brief),
        )

    def _payload(self, path: Path, model: type[ModelT]) -> ModelT:
        return decode_payload(self.lifecycle.read_artifact(path).payload, model)

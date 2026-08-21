"""Internal authentication and lineage checks for blind benchmark artifacts."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from pydantic import BaseModel, JsonValue

from envresearch.benchmarks.claim_integrity import (
    CitationIntegrityReport,
    CitationIntegrityValidator,
)
from envresearch.benchmarks.claim_report import (
    payload_leaf_hashes,
    report_binding_is_valid,
    report_from_payload,
    report_input_refs,
)
from envresearch.models.artifact import ArtifactRef, ProducerIdentity, ResearchArtifact
from envresearch.models.benchmark_blinding import BlindedBrief, LeakageReport
from envresearch.models.benchmark_claims import ClaimFactMap, CuratorSourceSheet
from envresearch.models.benchmark_evaluation import (
    AcceptedArtifactClaims,
    AdjudicationVerdict,
    ExpertScoreSheet,
    MethodRecommendationPayload,
    PosthocComparison,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.principal import PrincipalAssignment, PrincipalKind
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle

if TYPE_CHECKING:
    from envresearch.benchmarks.blind_paths import BlindArtifactPaths
    from envresearch.workers.control import QueueControl

ModelT = TypeVar("ModelT", bound=BaseModel)
PathResolver = Callable[[str], "BlindArtifactPaths"]
ProducerVerifier = Callable[
    [str, ProducerIdentity, PrincipalKind, int | None], PrincipalAssignment
]


def revalidate_model(payload: ModelT, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(payload.model_dump_json())


def decode_payload(payload: object, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(json.dumps(payload))


def artifact_ref(artifact: ResearchArtifact[object]) -> ArtifactRef:
    envelope = artifact.envelope
    if envelope.content_hash is None:
        raise ValueError("blind benchmark artifact is unsealed")
    return ArtifactRef(
        artifact_id=envelope.artifact_id,
        artifact_version=envelope.artifact_version,
        content_hash=envelope.content_hash,
    )


def require_case(expected: str, actual: str) -> None:
    if expected != actual:
        raise ValueError("case lineage mismatch")


def require_refs(
    actual: tuple[ArtifactRef, ...], expected: tuple[ArtifactRef, ...]
) -> None:
    if actual != expected:
        raise ValueError("case lineage mismatch")


def require_payload_principal(actual: str, assignment: PrincipalAssignment) -> None:
    if actual != assignment.principal_id:
        raise ValueError("payload principal does not match authenticated producer")


class BlindLineageValidator:
    def __init__(
        self,
        lifecycle: ResearchArtifactLifecycle,
        paths: PathResolver,
        require_producer: ProducerVerifier,
        revision_control: QueueControl,
    ) -> None:
        self.lifecycle = lifecycle
        self.paths = paths
        self.require_producer = require_producer
        self.revision_control = revision_control

    def locked_score_refs(
        self, case_id: str
    ) -> tuple[ArtifactRef, ArtifactRef, ArtifactRef]:
        paths = self.paths(case_id)
        brief = self._validated_ref(paths.blinded_brief)
        recommendation = self._validated_ref(paths.recommendation)
        refs: list[ArtifactRef] = []
        pairs = (
            (1, paths.expert_one_evidence, paths.expert_one),
            (2, paths.expert_two_evidence, paths.expert_two),
        )
        for slot, evidence_path, path in pairs:
            evidence = self._validated_ref(evidence_path)
            artifact, assignment = self._current(
                case_id,
                path,
                PrincipalKind.EXPERT,
                (brief, recommendation, evidence),
                slot,
            )
            score = decode_payload(artifact.payload, ExpertScoreSheet)
            require_refs((score.recommendation_ref,), (recommendation,))
            require_payload_principal(score.scorer_principal, assignment)
            refs.append(artifact_ref(artifact))
        return recommendation, refs[0], refs[1]

    def locked_third_score_ref(self, case_id: str) -> ArtifactRef:
        """Authenticate the independently sealed adjudicator score."""
        paths = self.paths(case_id)
        recommendation = self._validated_ref(paths.recommendation)
        inputs = (
            self._validated_ref(paths.blinded_brief),
            recommendation,
            self._validated_ref(paths.source_sheet.parent / "expert-rubric.json"),
            self._validated_ref(paths.third_score_evidence),
        )
        artifact, assignment = self._current(
            case_id, paths.third_score, PrincipalKind.ADJUDICATOR, inputs, 1
        )
        score = decode_payload(artifact.payload, ExpertScoreSheet)
        require_refs((score.recommendation_ref,), (recommendation,))
        require_payload_principal(score.scorer_principal, assignment)
        return artifact_ref(artifact)

    def require_current_chain(self, case_id: str) -> tuple[ArtifactRef, ...]:
        paths = self.paths(case_id)
        refs: list[ArtifactRef] = []
        source, assignment = self._current(
            case_id, paths.source_sheet, PrincipalKind.CURATOR, (), None
        )
        source_payload = decode_payload(source.payload, CuratorSourceSheet)
        require_case(case_id, source_payload.case_id)
        source_ref = artifact_ref(source)
        refs.append(source_ref)

        brief, assignment = self._current(
            case_id, paths.blinded_brief, PrincipalKind.MASKER, (source_ref,), None
        )
        brief_payload = decode_payload(brief.payload, BlindedBrief)
        require_case(case_id, brief_payload.case_id)
        require_refs((brief_payload.source_sheet_ref,), (source_ref,))
        require_payload_principal(brief_payload.masker_principal, assignment)
        brief_ref = artifact_ref(brief)
        refs.append(brief_ref)

        mapping, assignment = self._current(
            case_id,
            paths.claim_fact_map,
            PrincipalKind.MASKER,
            (source_ref, brief_ref),
            None,
        )
        map_payload = decode_payload(mapping.payload, ClaimFactMap)
        require_case(case_id, map_payload.case_id)
        require_refs(
            (map_payload.source_sheet_ref, map_payload.blinded_brief_ref),
            (source_ref, brief_ref),
        )
        require_payload_principal(map_payload.mapper_principal, assignment)
        map_ref = artifact_ref(mapping)
        refs.append(map_ref)

        leakage, assignment = self._current(
            case_id,
            paths.leakage_report,
            PrincipalKind.LEAKAGE_VALIDATOR,
            (source_ref, brief_ref),
            None,
        )
        leakage_payload = decode_payload(leakage.payload, LeakageReport)
        require_refs(
            (
                leakage_payload.source_sheet_ref,
                leakage_payload.blinded_brief_ref,
            ),
            (source_ref, brief_ref),
        )
        require_payload_principal(leakage_payload.validator_principal, assignment)
        leakage_ref = artifact_ref(leakage)
        refs.append(leakage_ref)

        recommendation, assignment = self._current(
            case_id,
            paths.recommendation,
            PrincipalKind.RECOMMENDER,
            (brief_ref, leakage_ref),
            None,
        )
        recommendation_payload = decode_payload(
            recommendation.payload, MethodRecommendationPayload
        )
        require_refs(
            (
                recommendation_payload.blinded_brief_ref,
                recommendation_payload.leakage_report_ref,
            ),
            (brief_ref, leakage_ref),
        )
        require_payload_principal(
            recommendation_payload.recommender_principal, assignment
        )
        recommendation_ref = artifact_ref(recommendation)
        refs.append(recommendation_ref)

        locked_recommendation, expert_one, expert_two = self.locked_score_refs(case_id)
        require_refs((locked_recommendation,), (recommendation_ref,))
        refs.extend((expert_one, expert_two))
        posthoc_inputs: tuple[ArtifactRef, ...] = (
            source_ref,
            recommendation_ref,
            expert_one,
            expert_two,
        )
        third_ref: ArtifactRef | None = None
        if (self.lifecycle.workspace / paths.third_score).exists():
            third_ref = self.locked_third_score_ref(case_id)
            refs.append(third_ref)
        if (self.lifecycle.workspace / paths.adjudication).exists() and (
            self.lifecycle.current_envelope(paths.adjudication).validation_status
            is ArtifactLifecycle.VALIDATED
        ):
            if third_ref is None:
                raise ValueError("adjudication requires a current third score")
            adjudication, assignment = self._current(
                case_id,
                paths.adjudication,
                PrincipalKind.ADJUDICATOR,
                (
                    recommendation_ref,
                    expert_one,
                    expert_two,
                    third_ref,
                    self._validated_ref(paths.adjudication_evidence),
                ),
                1,
            )
            verdict = decode_payload(adjudication.payload, AdjudicationVerdict)
            if verdict.score_sheet_ref not in (expert_one, expert_two):
                raise ValueError("case lineage mismatch")
            require_payload_principal(verdict.adjudicator_principal, assignment)
            adjudication_ref = artifact_ref(adjudication)
            refs.append(adjudication_ref)
            posthoc_inputs = (*posthoc_inputs, adjudication_ref)

        posthoc, assignment = self._current(
            case_id,
            paths.posthoc_comparison,
            PrincipalKind.ADJUDICATOR,
            posthoc_inputs,
            1,
        )
        comparison = decode_payload(posthoc.payload, PosthocComparison)
        require_refs((comparison.recommendation_ref,), (recommendation_ref,))
        require_payload_principal(comparison.analyst_principal, assignment)
        refs.append(artifact_ref(posthoc))

        raw_report = self.lifecycle.read_artifact(paths.citation_report)
        report = report_from_payload(raw_report.payload)
        if report != self.recompute_citation_report(case_id, report):
            raise ValueError("citation integrity report is not current")
        citation, _ = self._current(
            case_id,
            paths.citation_report,
            PrincipalKind.LEAKAGE_VALIDATOR,
            report_input_refs(report),
            None,
        )
        refs.append(artifact_ref(citation))
        return tuple(refs)

    def recompute_citation_report(
        self, case_id: str, report: CitationIntegrityReport
    ) -> CitationIntegrityReport:
        paths = self.paths(case_id)
        source_ref = self._validated_ref(paths.source_sheet)
        brief_ref = self._validated_ref(paths.blinded_brief)
        map_ref = self._validated_ref(paths.claim_fact_map)
        recommendation_ref = self._validated_ref(paths.recommendation)
        if (
            not report.passed
            or report.findings
            or not report_binding_is_valid(report)
            or report.source_sheet_refs != (source_ref,)
            or report.claim_fact_map_refs != (map_ref,)
            or report.blinded_brief_refs != (brief_ref,)
            or report.accepted_artifact_refs != (recommendation_ref,)
            or len(report.accepted_artifact_bindings) != 1
        ):
            raise ValueError("citation integrity report is not current")
        recommendation = self.lifecycle.read_artifact(paths.recommendation).payload
        binding = report.accepted_artifact_bindings[0]
        if binding.payload_leaf_hashes != payload_leaf_hashes(recommendation):
            raise ValueError("citation integrity report payload binding is not current")
        accepted = AcceptedArtifactClaims(
            artifact_ref=recommendation_ref,
            payload=cast(JsonValue, recommendation),
            usages=binding.usages,
        )
        return CitationIntegrityValidator().validate(
            source_sheets=(self._payload(paths.source_sheet, CuratorSourceSheet),),
            fact_maps=(self._payload(paths.claim_fact_map, ClaimFactMap),),
            artifacts=(accepted,),
            source_sheet_refs=(source_ref,),
            claim_fact_map_refs=(map_ref,),
            blinded_brief_refs=(brief_ref,),
        )

    def revise_source(
        self,
        case_id: str,
        source: CuratorSourceSheet,
        *,
        revision_id: str,
        reason: str,
        actor: str,
        curator: PrincipalAssignment,
    ) -> ArtifactRef:
        paths = self.paths(case_id)
        current_artifact, current_curator = self._current(
            case_id, paths.source_sheet, PrincipalKind.CURATOR, (), None
        )
        if current_curator != curator:
            raise ValueError("replacement curator is not the current source producer")
        current = decode_payload(current_artifact.payload, CuratorSourceSheet)
        from envresearch.benchmarks.blind_revision import BlindRevisionTransaction

        transaction = BlindRevisionTransaction(
            self.lifecycle, self.revision_control, paths
        )
        if source.source_generation == current.source_generation:
            recovered = transaction.recover_committed(
                source,
                revision_id=revision_id,
                reason=reason,
                actor=actor,
                curator=curator,
            )
            if recovered is not None:
                return recovered
            raise ValueError("source generation must advance")
        if source.source_generation < current.source_generation:
            raise ValueError("source generation must advance")
        return transaction.execute(
            source,
            revision_id=revision_id,
            reason=reason,
            actor=actor,
            curator=curator,
        )

    def _current(
        self,
        case_id: str,
        path: Path,
        kind: PrincipalKind,
        inputs: tuple[ArtifactRef, ...],
        slot: int | None,
    ) -> tuple[ResearchArtifact[object], PrincipalAssignment]:
        producer = self.lifecycle.current_envelope(path).producer
        assignment = self.require_producer(case_id, producer, kind, slot)
        artifact = self.lifecycle.require_validated(
            path, producer=producer, inputs=inputs
        )
        return artifact, assignment

    def _validated_ref(self, path: Path) -> ArtifactRef:
        current = self.lifecycle.artifact_ref(path)
        if self.lifecycle.validated_history_ref(path) != current:
            raise FileExistsError("current artifact does not match validated history")
        return current

    def _payload(self, path: Path, model: type[ModelT]) -> ModelT:
        return decode_payload(self.lifecycle.read_artifact(path).payload, model)

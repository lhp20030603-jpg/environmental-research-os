"""Authenticated lifecycle and queue evidence for blind case scoring."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from pydantic import BaseModel

from envresearch.benchmarks.blind_authority import verify_human_evidence
from envresearch.benchmarks.blind_evidence_artifacts import evidence_path
from envresearch.benchmarks.blind_scoring_contracts import (
    AdjudicationRecord,
    LockedThirdScore,
    SealedScoreArtifact,
)
from envresearch.benchmarks.blind_signed_submission import read_signed_evidence
from envresearch.models.artifact import ArtifactRef, ResearchArtifact
from envresearch.models.benchmark_claims import CuratorSourceSheet
from envresearch.models.benchmark_evaluation import (
    AdjudicationVerdict,
    ExpertScoreSheet,
)
from envresearch.models.principal import PrincipalAssignment, PrincipalKind
from envresearch.research.order_policy import (
    blind_order_constraints,
    canonical_blind_json,
)
from envresearch.workers.contracts import WorkerRole, WorkerSubmission, WorkOrder
from envresearch.workers.queue import FilesystemWorkerQueue

if TYPE_CHECKING:
    from envresearch.benchmarks.blind_artifacts import BlindArtifactLifecycle
    from envresearch.research.order_issuance import BlindControllerInfrastructure

ModelT = TypeVar("ModelT", bound=BaseModel)


class BlindScoringEvidenceReader:
    """Reconstruct score evidence from authoritative state, never caller refs."""

    def __init__(self, artifacts: BlindArtifactLifecycle, case_id: str) -> None:
        self.artifacts = artifacts
        self.case_id = case_id
        self.root = artifacts.lifecycle.workspace

    def expert_scores(self) -> tuple[SealedScoreArtifact, SealedScoreArtifact]:
        paths = self.artifacts.paths(self.case_id)
        return (
            self._score(1, paths.expert_one, PrincipalKind.EXPERT),
            self._score(2, paths.expert_two, PrincipalKind.EXPERT),
        )

    def has_final_state(self) -> bool:
        path = self.artifacts.paths(self.case_id).adjudication
        exchange, control = self._queue_paths(PrincipalKind.ADJUDICATOR, 1)
        if (self.root / path).exists():
            return True
        if not exchange.exists() or not control.exists():
            return False
        queue = self._queue(PrincipalKind.ADJUDICATOR, 1)
        try:
            return queue.has_generation("adjudicate")
        finally:
            queue.close()

    def adjudication(
        self, first: SealedScoreArtifact, second: SealedScoreArtifact
    ) -> AdjudicationRecord:
        paths = self.artifacts.paths(self.case_id)
        third = self._score(1, paths.third_score, PrincipalKind.ADJUDICATOR)
        queue = self._queue(PrincipalKind.ADJUDICATOR, 1)
        expected = (
            first.recommendation_ref,
            first.score_sheet_ref,
            second.score_sheet_ref,
            third.score_sheet_ref,
        )
        try:
            order, submission, data, signed_ref = self._queue_evidence(
                queue,
                "adjudicate",
                WorkerRole.BENCHMARK_ADJUDICATOR,
                expected,
                "envresearch.AdjudicationVerdict",
                "adjudication-verdict.yaml",
                PrincipalKind.ADJUDICATOR,
                1,
            )
        finally:
            queue.close()
        verdict = self._canonical_candidate(data, AdjudicationVerdict)
        assignment = cast(PrincipalAssignment, order.principal_assignment)
        artifact, verdict_ref = self._artifact(
            paths.adjudication, verdict, assignment, (*expected, signed_ref)
        )
        _require_submission_payload(submission, verdict, artifact)
        if verdict.adjudicator_principal != assignment.principal_id:
            raise ValueError("final verdict principal does not match capability")
        return AdjudicationRecord(
            third_score=LockedThirdScore(score=third),
            final_order_inputs=order.input_artifacts,
            verdict_ref=verdict_ref,
            signed_verdict_evidence_ref=signed_ref,
            verdict=verdict,
        )

    def _score(
        self, slot: int, path: Path, kind: PrincipalKind
    ) -> SealedScoreArtifact:
        order_id = f"expert-score-{slot}" if kind is PrincipalKind.EXPERT else "adjudicator-score"
        role = (WorkerRole.BENCHMARK_EXPERT if kind is PrincipalKind.EXPERT
                else WorkerRole.BENCHMARK_ADJUDICATOR)
        queue = self._queue(kind, slot)
        paths = self.artifacts.paths(self.case_id)
        brief = self.artifacts.lifecycle.artifact_ref(paths.blinded_brief)
        recommendation = self.artifacts.lifecycle.artifact_ref(paths.recommendation)
        rubric = self.artifacts.lifecycle.artifact_ref(
            paths.source_sheet.parent / "expert-rubric.json"
        )
        queue_inputs = (brief, recommendation, rubric)
        try:
            order, submission, data, signed_ref = self._queue_evidence(
                queue, order_id, role, queue_inputs, "envresearch.ExpertScoreSheet",
                "expert-score.yaml" if kind is PrincipalKind.EXPERT else "adjudicator-score.yaml",
                kind, slot,
            )
        finally:
            queue.close()
        score = self._canonical_candidate(data, ExpertScoreSheet)
        assignment = cast(PrincipalAssignment, order.principal_assignment)
        lifecycle_inputs = (
            (brief, recommendation, signed_ref)
            if kind is PrincipalKind.EXPERT
            else (*queue_inputs, signed_ref)
        )
        artifact, score_ref = self._artifact(path, score, assignment, lifecycle_inputs)
        _require_submission_payload(submission, score, artifact)
        if score.recommendation_ref != recommendation:
            raise ValueError("score recommendation is not current")
        if score.scorer_principal != assignment.principal_id:
            raise ValueError("score principal does not match capability")
        typed = ResearchArtifact[ExpertScoreSheet](
            envelope=artifact.envelope, payload=score
        )
        return SealedScoreArtifact(
            case_id=self.case_id,
            score_sheet_ref=score_ref,
            artifact=typed,
            current_ref=self.artifacts.lifecycle.artifact_ref(path),
            validated_history_ref=self.artifacts.lifecycle.validated_history_ref(path),
            principal_assignment=assignment,
            queue_order_id=order.order_id,
            queue_input_artifacts=order.input_artifacts,
            signed_evidence_ref=signed_ref,
        )

    def _queue_evidence(
        self,
        queue: FilesystemWorkerQueue,
        order_id: str,
        role: WorkerRole,
        inputs: tuple[ArtifactRef, ...],
        schema: str,
        filename: str,
        kind: PrincipalKind,
        slot: int,
    ) -> tuple[WorkOrder, WorkerSubmission, bytes, ArtifactRef]:
        if not queue.has_generation(order_id):
            raise ValueError("current authenticated work-order generation is required")
        order = queue.read_order(order_id)
        assignment = order.principal_assignment
        if assignment is None:
            raise ValueError("work order lacks an authenticated principal")
        durable = self.artifacts.principals.require_assignment(
            self.case_id, assignment, kind, slot
        )
        expected_version = f"generation-{self._source_generation()}"
        if (
            order.order_id != order_id
            or order.node_id != order_id
            or order.node_version != expected_version
            or order.role is not role
            or order.input_artifacts != inputs
            or order.expected_output_schema != schema
            or order.expected_output_filenames != (filename,)
            or order.policy_constraints != blind_order_constraints(role)
            or order.evidence_requirements != ("Retain opaque fact references",)
            or assignment != durable
        ):
            raise ValueError("work order does not match the exact blind contract")
        submissions = queue.collect(order_id)
        if len(submissions) != 1:
            raise ValueError("work order requires exactly one authenticated receipt")
        submission = submissions[0]
        if submission.principal_assignment != durable or submission.producer != durable.producer:
            raise ValueError("submission producer does not match capability")
        data = queue.exchange.read_file(
            submission.candidate_relative_paths[0], description="blind score evidence"
        )
        signed = read_signed_evidence(queue, order_id)
        participant = self.artifacts.principals.registry.enrolled_human_key(
            self.case_id, kind, slot
        )
        signed_data = verify_human_evidence(
            signed,
            participant,
            case_id=self.case_id,
            role=kind,
            slot=slot,
            source_generation=self._source_generation(),
            assignment_id=durable.assignment_id,
            order_hash=cast(str, order.order_hash),
            candidate_schema=schema,
        )
        if signed_data != data:
            raise ValueError("queue candidate does not match signed human evidence")
        signed_path = evidence_path(self.artifacts, self.case_id, signed)
        signed_artifact = self.artifacts.lifecycle.require_validated(
            signed_path, producer=durable.producer, inputs=order.input_artifacts
        )
        if signed_artifact.payload != signed.model_dump(mode="json"):
            raise ValueError("signed lifecycle evidence does not match transport evidence")
        signed_ref = self.artifacts.lifecycle.artifact_ref(signed_path)
        if self.artifacts.lifecycle.validated_history_ref(signed_path) != signed_ref:
            raise ValueError("signed lifecycle evidence is not current")
        return order, submission, data, signed_ref

    def _artifact(
        self,
        path: Path,
        payload: ModelT,
        assignment: PrincipalAssignment,
        inputs: tuple[ArtifactRef, ...],
    ) -> tuple[ResearchArtifact[object], ArtifactRef]:
        artifact = self.artifacts.lifecycle.require_validated(
            path, producer=assignment.producer, inputs=inputs
        )
        if artifact.payload != payload.model_dump(mode="json"):
            raise ValueError("queue candidate does not match the sealed artifact")
        current = self.artifacts.lifecycle.artifact_ref(path)
        if self.artifacts.lifecycle.validated_history_ref(path) != current:
            raise ValueError("artifact is not current in validated history")
        return artifact, current

    @staticmethod
    def _canonical_candidate(data: bytes, model: type[ModelT]) -> ModelT:
        payload = model.model_validate_json(data)
        if data != canonical_blind_json(payload.model_dump(mode="json")):
            raise ValueError("queue candidate is not canonical")
        return payload

    def _source_generation(self) -> int:
        source = self.artifacts.lifecycle.read_payload(
            self.artifacts.paths(self.case_id).source_sheet, CuratorSourceSheet
        )
        return cast(int, source.source_generation)

    def _queue(self, kind: PrincipalKind, slot: int) -> FilesystemWorkerQueue:
        exchange, control = self._queue_paths(kind, slot)
        if not exchange.exists() or not control.exists():
            raise ValueError("current authenticated queue state is required")
        return FilesystemWorkerQueue(
            exchange, control_root=control, require_producer_context=True
        )

    def _queue_paths(self, kind: PrincipalKind, slot: int) -> tuple[Path, Path]:
        if kind is PrincipalKind.EXPERT:
            exchange = self.root / "exchanges/expert" / self.case_id / str(slot)
            control = self.root / "control/queues/expert" / self.case_id / str(slot)
        else:
            exchange = self.root / "exchanges/adjudicator" / self.case_id
            control = self.root / "control/queues/adjudicator" / self.case_id
        return exchange, control


def promote_locked_third_score(
    infrastructure: BlindControllerInfrastructure,
    queue: FilesystemWorkerQueue,
    adjudicator: PrincipalAssignment,
) -> tuple[ExpertScoreSheet, ArtifactRef]:
    """Promote exactly the authenticated blind-score receipt into Task 8."""
    reader = BlindScoringEvidenceReader(
        infrastructure.artifacts, infrastructure.case_id
    )
    order, _submission, data, signed_ref = reader._queue_evidence(
        queue,
        "adjudicator-score",
        WorkerRole.BENCHMARK_ADJUDICATOR,
        (
            infrastructure.artifacts.ref(infrastructure.case_id, "blinded_brief"),
            infrastructure.artifacts.ref(infrastructure.case_id, "recommendation"),
            infrastructure.artifacts.lifecycle.artifact_ref(infrastructure._rubric_path()),
        ),
        "envresearch.ExpertScoreSheet",
        "adjudicator-score.yaml",
        PrincipalKind.ADJUDICATOR,
        1,
    )
    assignment = cast(PrincipalAssignment, order.principal_assignment)
    if assignment != adjudicator:
        raise ValueError("adjudicator capability does not match the work order")
    score = reader._canonical_candidate(data, ExpertScoreSheet)
    ref = infrastructure.artifacts.publish_third_score(
        infrastructure.case_id, score, assignment
    )
    if infrastructure.artifacts.lifecycle.current_envelope(
        infrastructure.artifacts.paths(infrastructure.case_id).third_score
    ).input_artifacts[-1] != signed_ref:
        raise ValueError("third score does not bind signed evidence")
    return score, ref


def _require_submission_payload(
    submission: WorkerSubmission,
    payload: BaseModel,
    artifact: ResearchArtifact[object],
) -> None:
    if artifact.payload != payload.model_dump(mode="json"):
        raise ValueError("submission payload does not match sealed artifact")
    if submission.candidate_sha256[0] != _sha256(
        canonical_blind_json(payload.model_dump(mode="json"))
    ):
        raise ValueError("submission receipt does not bind the sealed payload")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

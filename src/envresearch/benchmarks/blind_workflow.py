"""Internal offline composition service for one blinded benchmark case."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import JsonValue

from envresearch.benchmarks.blind_authority import SignedHumanEvidence
from envresearch.benchmarks.blind_registry import LoadedBlindCase
from envresearch.benchmarks.blind_signed_submission import import_signed_submission
from envresearch.benchmarks.leakage import LeakageScanner
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_blinding import BlindedBrief, LeakageReport
from envresearch.models.benchmark_claims import ClaimFactMap, CuratorSourceSheet
from envresearch.models.benchmark_evaluation import (
    AdjudicationVerdict,
    ExpertScoreSheet,
    MethodRecommendationPayload,
)
from envresearch.models.principal import PrincipalAssignment, PrincipalKind
from envresearch.research.isolated_order_publication import (
    issue_isolated_extension,
    issue_isolated_order,
)
from envresearch.research.order_issuance import BlindControllerInfrastructure
from envresearch.research.order_policy import (
    BlindWorkflowStatus,
    blind_claim_usages,
    blind_expert_rubric,
    blind_scores_require_adjudication,
    blind_workflow_status,
    build_blind_graph,
)
from envresearch.workers import FilesystemWorkerQueue, WorkerRole


class BlindEvaluationController(BlindControllerInfrastructure):
    """Compose registries, exact lineage, and isolated authenticated queues."""

    def __init__(self, loaded: LoadedBlindCase, run_root: Path) -> None:
        super().__init__(loaded, run_root)
        self.graph = build_blind_graph(self.case_id)

    @classmethod
    def from_case(cls, case_root: Path, run_root: Path) -> BlindEvaluationController:
        """Load exactly one inert Tier-1 case without constructing a connector."""
        from envresearch.benchmarks.blind_controller_factory import controller_from_case
        return controller_from_case(cls, case_root, run_root)

    def replay_calibration(self) -> BlindWorkflowStatus:
        """Persist, scan, freeze, and issue the offline recommendation order."""
        self._require_enrollment()
        paths = self.artifacts.paths(self.case_id)
        curator = self._worker(PrincipalKind.CURATOR)
        masker = self._worker(PrincipalKind.MASKER)
        validator = self._worker(PrincipalKind.LEAKAGE_VALIDATOR)
        if not (self.run_root / paths.source_sheet).exists():
            self.artifacts.publish_source(self.case_id, self.loaded.source_sheet, curator)
        source = self.artifacts.lifecycle.read_payload(paths.source_sheet, CuratorSourceSheet)
        source_ref = self.source_ref()
        if not self._validated(paths.blinded_brief):
            brief = self.loaded.blinded_brief.model_copy(
                update={"source_sheet_ref": source_ref, "masker_principal": masker.principal_id}
            )
            self.artifacts.publish_brief(self.case_id, brief, masker)
        brief_ref = self.artifacts.ref(self.case_id, "blinded_brief")
        if not self._validated(paths.claim_fact_map):
            mapping = self.loaded.claim_fact_map.model_copy(
                update={
                    "source_sheet_ref": source_ref,
                    "blinded_brief_ref": brief_ref,
                    "mapper_principal": masker.principal_id,
                }
            )
            self.artifacts.publish_fact_map(self.case_id, mapping, masker)
        if not self._validated(paths.leakage_report):
            brief = self.artifacts.lifecycle.read_payload(paths.blinded_brief, BlindedBrief)
            report = LeakageScanner().scan(
                source, brief, source_ref, brief_ref, validator.principal_id
            )
            self.artifacts.publish_leakage(self.case_id, report, validator)
        self._ensure_support_artifacts()
        self.issue_ready()
        return self.status()

    def issue_ready(self) -> None:
        """Issue recommendation only for a current passing leakage generation."""
        paths = self.artifacts.paths(self.case_id)
        required = (
            paths.source_sheet, paths.blinded_brief,
            paths.claim_fact_map, paths.leakage_report,
        )
        if not all(self._validated(path) for path in required):
            raise ValueError("current validated blind inputs are required")
        report = self.artifacts.lifecycle.read_payload(paths.leakage_report, LeakageReport)
        if report.verdict != "pass":
            raise ValueError("passing leakage report is required")
        assignment = self._worker(PrincipalKind.RECOMMENDER)
        order = self._order(
            "recommend-method",
            WorkerRole.BENCHMARK_RECOMMENDER,
            self.allowed_recommendation_refs(),
            "envresearch.MethodRecommendationPayload",
            "method-recommendation.yaml",
            assignment,
        )
        if self.queue.has_generation(order.order_id):
            if self.queue.read_order(order.order_id) != order:
                raise ValueError("stale recommendation work order")
            return
        issue_isolated_order(
            self.queue,
            self.recommender_workspace,
            order,
            (
                ("blinded-brief.yaml", self._public_brief()),
                ("leakage-report.yaml", self._public_leakage()),
                ("method-profiles.json", self._profile_payload()),
            ),
            enrollment_registry=self.registry, case_id=self.case_id,
        )

    def accept_recommendation(self, payload: MethodRecommendationPayload) -> ArtifactRef:
        """Queue and accept one exact three-input offline recommendation."""
        candidate = MethodRecommendationPayload.model_validate_json(payload.model_dump_json())
        allowed = self.allowed_recommendation_refs()
        profile = self._profile_payload()
        if (
            (candidate.blinded_brief_ref, candidate.leakage_report_ref) != allowed[:2]
            or candidate.method_profile_registry_sha256 != profile["registry_sha256"]
        ):
            raise ValueError("prohibited recommendation input")
        mapping = self.artifacts.lifecycle.read_payload(
            self.artifacts.paths(self.case_id).claim_fact_map, ClaimFactMap
        )
        blind_claim_usages(
            cast(JsonValue, candidate.model_dump(mode="json")), mapping
        )
        self._submit_payload(self.queue, "recommend-method", candidate)
        return self.accept_submission("recommend-method")

    def accept_submission(self, order_id: str) -> ArtifactRef:
        """Promote one authenticated candidate from its role-specific queue."""
        if order_id == "recommend-method":
            recommendation = self._collected(
                self.queue, order_id, MethodRecommendationPayload
            )
            return self._promote_recommendation(recommendation)
        if order_id in {"expert-score-1", "expert-score-2"}:
            slot = int(order_id[-1])
            score = self._collected(
                self.expert_queues[slot], order_id, ExpertScoreSheet
            )
            return self._promote_expert(slot, score)
        raise ValueError("unknown blind work order")

    def issue_expert_orders(self) -> None:
        """Create two separate expert roots with no score-sheet visibility."""
        paths = self.artifacts.paths(self.case_id)
        required = (paths.blinded_brief, paths.recommendation, paths.citation_report)
        if not all(self._validated(path) for path in required):
            raise ValueError("current validated recommendation lineage is required")
        recommendation = self.artifacts.ref(self.case_id, "recommendation")
        citation = self.artifacts.lifecycle.read_artifact(paths.citation_report).payload
        if not isinstance(citation, dict) or citation.get("passed") is not True:
            raise ValueError("passing citation report is required")
        inputs = (
            self.artifacts.ref(self.case_id, "blinded_brief"),
            recommendation,
            self.artifacts.lifecycle.artifact_ref(self._rubric_path()),
        )
        for slot in (1, 2):
            queue = self.expert_queues.setdefault(slot, self._queue("expert", f"{self.case_id}/{slot}"))
            assignment = self._human(PrincipalKind.EXPERT, slot)
            order = self._order(
                f"expert-score-{slot}",
                WorkerRole.BENCHMARK_EXPERT,
                inputs,
                "envresearch.ExpertScoreSheet",
                "expert-score.yaml",
                assignment,
            )
            if queue.has_generation(order.order_id):
                if queue.read_order(order.order_id) != order:
                    raise ValueError("stale expert work order")
                continue
            issue_isolated_order(
                queue,
                self.expert_workspaces[slot],
                order,
                (
                    ("blinded-brief.yaml", self._public_brief()),
                    ("method-recommendation.yaml", self._recommendation_payload()),
                    ("expert-rubric.yaml", blind_expert_rubric()),
                ),
                enrollment_registry=self.registry, case_id=self.case_id,
            )

    def accept_expert_score(
        self, slot: int, payload: SignedHumanEvidence | None = None
    ) -> ArtifactRef:
        from envresearch.benchmarks.blind_signed_submission import accept_expert_score
        return accept_expert_score(self, slot, payload)

    def issue_adjudication_order(self, trigger: bool | None = None) -> None:
        """Create a blind adjudicator root only for an authenticated score trigger."""
        first, second = self._expert_payloads()
        required = blind_scores_require_adjudication(first, second)
        if trigger is False or not required:
            raise ValueError("adjudication trigger is required")
        self.adjudicator_queue = self._queue("adjudicator", self.case_id)
        self._adjudicator = self._human(PrincipalKind.ADJUDICATOR, 1)
        inputs = (
            self.artifacts.ref(self.case_id, "blinded_brief"),
            self.artifacts.ref(self.case_id, "recommendation"),
            self.artifacts.lifecycle.artifact_ref(self._rubric_path()),
        )
        order = self._order(
            "adjudicator-score",
            WorkerRole.BENCHMARK_ADJUDICATOR,
            inputs,
            "envresearch.ExpertScoreSheet",
            "adjudicator-score.yaml",
            self._adjudicator,
        )
        if self.adjudicator_queue.has_generation(order.order_id):
            if self.adjudicator_queue.read_order(order.order_id) != order:
                raise ValueError("stale adjudication work order")
            return
        issue_isolated_order(
            self.adjudicator_queue,
            self.adjudicator_workspace,
            order,
            (
                ("blinded-brief.yaml", self._public_brief()),
                ("method-recommendation.yaml", self._recommendation_payload()),
                ("expert-rubric.yaml", blind_expert_rubric()),
            ),
            enrollment_registry=self.registry, case_id=self.case_id,
        )

    def accept_adjudication(
        self, payload: SignedHumanEvidence | None = None
    ) -> ArtifactRef:
        queue, adjudicator = self._restore_adjudicator()
        if payload is not None and not isinstance(payload, SignedHumanEvidence):
            raise ValueError("externally signed human evidence is required")
        if payload is not None and payload.candidate_schema == "envresearch.AdjudicationVerdict":
            third_ref = self._locked_third_score(queue, adjudicator)[1]
            self._issue_final_adjudication(queue, adjudicator, third_ref)
            import_signed_submission(
                self, queue, "adjudicate", payload, AdjudicationVerdict,
                PrincipalKind.ADJUDICATOR, 1,
            )
            verdict = self._collected(queue, "adjudicate", AdjudicationVerdict)
            return self.artifacts.publish_adjudication(
                self.case_id, verdict, adjudicator
            )
        if payload is not None:
            import_signed_submission(
                self, queue, "adjudicator-score", payload, ExpertScoreSheet,
                PrincipalKind.ADJUDICATOR, 1,
            )
        score, self._third_score_ref = self._locked_third_score(queue, adjudicator)
        self._write_visible(self.adjudicator_workspace, "adjudicator-score.yaml", score)
        first, second = self._expert_payloads()
        self._write_visible(
            self.adjudicator_workspace,
            "disagreement-rationales.json",
            {
                "expert_one": [item.rationale for item in first.scores],
                "expert_two": [item.rationale for item in second.scores],
            },
        )
        self._issue_final_adjudication(queue, adjudicator, self._third_score_ref)
        return self._third_score_ref

    def source_ref(self) -> ArtifactRef:
        return self.artifacts.ref(self.case_id, "source_sheet")

    def allowed_recommendation_refs(self) -> tuple[ArtifactRef, ...]:
        paths = self.artifacts.paths(self.case_id)
        return (
            self.artifacts.ref(self.case_id, "blinded_brief"),
            self.artifacts.ref(self.case_id, "leakage_report"),
            self.artifacts.lifecycle.artifact_ref(paths.source_sheet.parent / "method-profiles.json"),
        )

    def recommender_workspace_files(self) -> tuple[str, ...]:
        return self._workspace_files(self.recommender_workspace)

    def expert_workspace_files(self, slot: int) -> tuple[str, ...]:
        if slot not in (1, 2):
            raise ValueError("expert slot must be one or two")
        return self._workspace_files(self.expert_workspaces[slot])

    def adjudicator_workspace_files(self) -> tuple[str, ...]:
        return self._workspace_files(self.adjudicator_workspace)

    def revise_source(
        self,
        source: CuratorSourceSheet,
        *,
        revision_id: str,
        reason: str = "Advance the blind case source generation",
        actor: str = "blind-revision-controller",
    ) -> ArtifactRef:
        """Use Task 8's locked transaction, then revoke every issued order."""
        envelope = self.artifacts.lifecycle.current_envelope(
            self.artifacts.paths(self.case_id).source_sheet
        )
        curator = self.artifacts.principals.require_producer(
            self.case_id, envelope.producer, PrincipalKind.CURATOR, None
        )
        result = self.artifacts.revise_source(
            self.case_id,
            source,
            revision_id=revision_id,
            reason=reason,
            actor=actor,
            curator=curator,
        )
        for queue, order_id in self._issued_queues():
            if queue.has_generation(order_id):
                queue.archive_generation(order_id, revision_id, allow_cancellation=True)
        self._archive_isolated(revision_id)
        self.expert_queues.clear()
        self.adjudicator_queue = None
        self._adjudicator = None
        self._third_score_ref = None
        return result

    def recompute_ready(self) -> BlindWorkflowStatus:
        """Re-mask and reissue only descendants invalidated by source revision."""
        self.replay_calibration()
        return self.status()

    def status(self) -> BlindWorkflowStatus:
        third_score_locked = False
        try:
            queue, adjudicator = self._restore_adjudicator()
            self._locked_third_score(queue, adjudicator)
            third_score_locked = True
        except (FileNotFoundError, ValueError):
            pass
        return blind_workflow_status(
            self.graph, self.artifacts, self.case_id,
            third_score_locked=third_score_locked,
        )

    def _promote_recommendation(self, payload: MethodRecommendationPayload) -> ArtifactRef:
        order = self.queue.read_order("recommend-method")
        if order.input_artifacts != self.allowed_recommendation_refs():
            raise ValueError("sealed work order does not match current blind inputs")
        assignment = cast(PrincipalAssignment, order.principal_assignment)
        result = self.artifacts.publish_recommendation(self.case_id, payload, assignment)
        self._publish_citations(result, payload)
        return result

    def _promote_expert(self, slot: int, payload: ExpertScoreSheet) -> ArtifactRef:
        queue = self.expert_queues[slot]
        order = queue.read_order(f"expert-score-{slot}")
        assignment = cast(PrincipalAssignment, order.principal_assignment)
        result = self.artifacts.publish_expert_score(
            self.case_id, payload, assignment, slot=slot
        )
        self._write_visible(self.expert_workspaces[slot], "expert-score.yaml", payload)
        return result

    def _issue_final_adjudication(
        self, queue: FilesystemWorkerQueue, adjudicator: PrincipalAssignment, third_ref: ArtifactRef,
    ) -> None:
        recommendation, first, second = self.artifacts.lineage.locked_score_refs(
            self.case_id
        )
        order = self._order(
            "adjudicate", WorkerRole.BENCHMARK_ADJUDICATOR,
            (recommendation, first, second, third_ref),
            "envresearch.AdjudicationVerdict", "adjudication-verdict.yaml", adjudicator,
        )
        score = self._locked_third_score(queue, adjudicator)[0]
        expert_one, expert_two = self._expert_payloads()
        issue_isolated_extension(
            queue, self.adjudicator_workspace, order,
            "adjudication-work-order.json",
            (
                ("blinded-brief.yaml", self._public_brief()),
                ("method-recommendation.yaml", self._recommendation_payload()),
                ("expert-rubric.yaml", blind_expert_rubric()),
                ("work-order.json", queue.read_order("adjudicator-score")),
                ("adjudicator-score.yaml", score),
                ("disagreement-rationales.json", {
                    "expert_one": [item.rationale for item in expert_one.scores],
                    "expert_two": [item.rationale for item in expert_two.scores],
                }),
            ),
            enrollment_registry=self.registry, case_id=self.case_id,
        )

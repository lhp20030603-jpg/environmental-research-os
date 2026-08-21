"""Phase-driven orchestration for the bounded V0.2 research workflow."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Self

from envresearch.connectors.contracts import ConnectorCoverage
from envresearch.kernel.events import EventLog
from envresearch.kernel.gates import GateDecision, GateRequest, GateStore
from envresearch.kernel.node_checkpoints import NodeCheckpointStore
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_evaluation import AcceptedArtifactClaims
from envresearch.models.design import DesignReviewPayload
from envresearch.models.enums import ArtifactLifecycle, GateStatus
from envresearch.models.intake import (
    CandidateChartersPayload,
    ResearchBriefPayload,
    ResearchCharterPayload,
    ResearchIntakeMode,
)
from envresearch.research.acceptance_transaction import accept_node_transaction
from envresearch.research.artifact_identity import utc_now
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.audit_state import ResearchAuditState
from envresearch.research.citation_attestations import ProtectedCitationAttestations
from envresearch.research.citation_gate import record_citation_integrity
from envresearch.research.config_publication import initialization_transaction
from envresearch.research.coverage_api import bind_coverage
from envresearch.research.final_approval import apply_final_gate
from envresearch.research.final_integrity import require_complete_final
from envresearch.research.gate_api import decide_gate
from envresearch.research.gate_context import BoundGateManager
from envresearch.research.gate_policy import GATE_NAMES, GATE_ORDER
from envresearch.research.node_inputs import (
    adopt_literature_coverage_state,
    refresh_literature_coverage_input,
)
from envresearch.research.orchestrator_state import current_data_risk_reasons
from envresearch.research.orchestrator_summary import summarize
from envresearch.research.order_issuance import issue_ready
from envresearch.research.principal_policy import require_gate_principal
from envresearch.research.principal_registry import PrincipalRegistry
from envresearch.research.review_policy import ReviewPolicy
from envresearch.research.revision_api import request_revision
from envresearch.research.revisions import RevisionIntent, RevisionTransaction
from envresearch.research.run_binding import prepare_run
from envresearch.research.semantic_validation import SemanticSubmissionValidator
from envresearch.research.workflow import (
    ResearchRunConfig,
    ResearchRunPhase,  # noqa: F401 - retained as a public compatibility export
    ResearchRunSummary,
    build_research_graph,
)
from envresearch.storage.artifacts import ArtifactStore
from envresearch.workers.queue import FilesystemWorkerQueue


class ResearchOrchestrator:
    """Compose existing stores without executing research or external acquisition."""

    def __init__(self, clock: Callable[[], datetime] = utc_now) -> None:
        self._closed = True
        self._clock = clock

    def initialize(
        self,
        config: ResearchRunConfig,
        brief: ResearchBriefPayload,
        explicit_config: bytes | None = None,
    ) -> ResearchRunSummary:
        """Bind or recover a run, persist its intake, and issue its entry order."""
        try:
            durable_config, durable_brief = prepare_run(config, brief)
            with initialization_transaction(durable_config, explicit_config):
                return self._initialize_bound(durable_config, durable_brief)
        except BaseException:
            self.close()
            raise

    def _initialize_bound(
        self, durable_config: ResearchRunConfig, durable_brief: ResearchBriefPayload
    ) -> ResearchRunSummary:
        self.close()
        self._closed = False
        self.config = durable_config
        self.brief = durable_brief
        self.workspace = durable_config.workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.graph = refresh_literature_coverage_input(
            build_research_graph(
                durable_config.input_mode,
                require_claim_verified_citations=(
                    durable_config.require_claim_verified_citations
                ),
            ),
            self.workspace,
        )
        self._nodes = {node.node_id: node for node in self.graph.nodes}
        self.raw_store = ArtifactStore(self.workspace)
        self.lifecycle = ResearchArtifactLifecycle(
            self.workspace, durable_config.run_id, self._clock
        )
        path = (
            Path("artifacts/research-brief.yaml")
            if durable_config.input_mode is ResearchIntakeMode.BROAD_TOPIC
            else Path("artifacts/intake-brief.yaml")
        )
        self.lifecycle.persist_structured(
            path,
            durable_brief,
            "research-intake",
            (),
            final=ArtifactLifecycle.VALIDATED,
        )
        self.queue = FilesystemWorkerQueue(
            self.workspace, require_producer_context=True
        )
        self.principals = PrincipalRegistry(self.queue.control, self.config.run_id)
        self.citation_attestations = ProtectedCitationAttestations(self.queue)
        self.semantics = SemanticSubmissionValidator(
            self.lifecycle,
            self._nodes,
            require_claim_verified_citations=(
                durable_config.require_claim_verified_citations
            ),
            citation_attestations=self.citation_attestations,
        )
        self.audit = ResearchAuditState(self.workspace, self.lifecycle)
        self.audit.initialize(
            self.config, self.lifecycle.artifact_ref(path), self.semantics.registry
        )
        self.gates = GateStore(
            self.raw_store, EventLog(self.workspace / "events.jsonl")
        )
        self.bound_gates = BoundGateManager(
            self.workspace, self.gates, durable_config.requested_by, self._clock
        )
        self.checkpoints = NodeCheckpointStore.for_workspace(
            self.workspace, self._clock
        )
        self.revisions = RevisionTransaction(
            workspace=self.workspace,
            run_id=self.config.run_id,
            graph=self.graph,
            nodes=self._nodes,
            lifecycle=self.lifecycle,
            checkpoints=self.checkpoints,
            queue=self.queue,
            audit=self.audit,
            gates=self.bound_gates,
        )
        with self.queue.control.transaction_lock("mutation"):
            self.revisions.recover_pending()
            self._issue_ready()
        return self._summarize()

    def __enter__(self) -> Self:
        self._require_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True
        for name in ("checkpoints", "revisions", "queue", "audit"):
            if (resource := getattr(self, name, None)) is not None:
                resource.close()

    def advance(self) -> ResearchRunSummary:
        """Reconcile durable gates/checkpoints and issue every newly ready worker."""
        self._require_open()
        with self.queue.control.transaction_lock("mutation"):
            adopt_literature_coverage_state(self)
            self._request_gates()
            self._apply_gate_one()
            self._request_gates()
            self._apply_final_gate()
            self._issue_ready()
            return self._summarize()

    def accept_submission(self, order_id: str) -> ResearchRunSummary:
        """Validate one authenticated worker candidate and promote two versions."""
        self._require_open()
        if order_id not in self._nodes or self._nodes[order_id].worker_role is None:
            raise ValueError("unknown or non-worker work order")
        node = self._nodes[order_id]
        with self.queue.control.transaction_lock("mutation"):
            adopt_literature_coverage_state(self)
            node = self._nodes[order_id]
            accept_node_transaction(
                node=node,
                queue=self.queue,
                lifecycle=self.lifecycle,
                semantics=self.semantics,
                checkpoints=self.checkpoints,
                ranking_policy=self.config.ranking_policy,
            )
            return self._summarize()

    def request_revision(
        self, node_id: str, reason: str, actor: str, principal_capability: str
    ) -> RevisionIntent:
        self._require_open()
        return request_revision(self, node_id, reason, actor, principal_capability)

    def bind_literature_coverage(self, coverage: ConnectorCoverage) -> None:
        self._require_open()
        bind_coverage(self, coverage)

    def record_citation_integrity_report(
        self,
        *,
        case_roots: tuple[Path, ...],
        artifacts: tuple[AcceptedArtifactClaims, ...],
    ) -> ArtifactRef:
        """Recompute and seal one exact strict citation-validation generation."""
        self._require_open()
        return record_citation_integrity(
            self, case_roots=case_roots, artifacts=artifacts
        )

    def decide_gate(
        self, gate_id: str, decision: GateDecision, principal_capability: str
    ) -> GateRequest:
        self._require_open()
        return decide_gate(self, gate_id, decision, principal_capability)

    def _request_gates(self) -> None:
        completed = self.checkpoints.completed_nodes(self.graph)
        entry = self.graph.nodes[0].node_id
        if entry in completed and "approve-charter" not in completed:
            self._request_bound("gate-1")
        if "inspect-data" in completed and self._data_risk_reasons():
            self._request_bound("data-gate")
        citation_predecessor = (
            "validate-citations"
            if self.config.require_claim_verified_citations
            else "compose-plan"
        )
        if citation_predecessor in completed:
            review = self.lifecycle.read_payload(
                Path("artifacts/design-review-findings.json"), DesignReviewPayload
            )
            if ReviewPolicy.can_compose(review.findings):
                self._request_bound("final-gate")

    def _request_bound(self, gate_id: str) -> None:
        self.bound_gates.ensure(gate_id, GATE_NAMES[gate_id], self._gate_refs(gate_id))

    def _apply_gate_one(self) -> None:
        if "approve-charter" in self.checkpoints.completed_nodes(self.graph):
            return
        if self.bound_gates.active_context("gate-1") is None:
            return
        context = self.bound_gates.require_approved(
            "gate-1", GATE_NAMES["gate-1"], self._gate_refs("gate-1")
        )
        if context is None:
            return
        gate = self._gate("gate-1")
        assert gate is not None
        assert gate.decision is not None
        require_gate_principal(self, "gate-1", gate)
        selected = gate.decision.conditions.get("selected_candidate_id")
        if not isinstance(selected, str):
            raise TypeError("gate-1 requires a selected_candidate_id condition")
        entry = self.graph.nodes[0]
        if self.config.input_mode is ResearchIntakeMode.BROAD_TOPIC:
            source = self.lifecycle.read_payload(
                entry.output_paths[0], CandidateChartersPayload
            )
            choices = {item.candidate_id: item for item in source.candidates}
            if selected not in choices:
                raise ValueError(
                    "gate-1 selected_candidate_id is not a current candidate"
                )
            chosen = choices[selected]
        else:
            source = self.lifecycle.read_payload(
                entry.output_paths[0], ResearchCharterPayload
            )
            if selected != source.charter.candidate_id:
                raise ValueError("gate-1 selection is not the draft charter")
            chosen = source.charter
        node = self._nodes["approve-charter"]
        inputs = self.lifecycle.input_refs(node)
        self.lifecycle.persist_structured(
            node.output_paths[0],
            chosen,
            "human-gate-1",
            inputs,
            final=ArtifactLifecycle.APPROVED,
        )
        self.checkpoints.publish(node, inputs, node.output_paths)

    def _apply_final_gate(self) -> None:
        apply_final_gate(self)

    def _issue_ready(self) -> None:
        adopt_literature_coverage_state(self)
        issue_ready(self)

    def _approved_gates(self) -> frozenset[str]:
        approved = {
            gate_id
            for gate_id in ("gate-1", "final-gate")
            if self._is_approved(gate_id)
        }
        if not self._data_risk_reasons() or self._is_approved("data-gate"):
            approved.add("data-clearance")
        return frozenset(approved)

    def _data_risk_reasons(self) -> tuple[str, ...]:
        return current_data_risk_reasons(self)

    def _gate(self, gate_id: str) -> GateRequest | None:
        return self.bound_gates.active_gate(gate_id)

    def _is_approved(self, gate_id: str) -> bool:
        if self.bound_gates.active_context(gate_id) is None:
            return False
        active = self._gate(gate_id)
        if active is None or active.status is not GateStatus.APPROVED:
            return False
        try:
            require_gate_principal(self, gate_id, active)
            return (
                self.bound_gates.require_approved(
                    gate_id, GATE_NAMES[gate_id], self._gate_refs(gate_id)
                )
                is not None
            )
        except (OSError, ValueError, FileExistsError) as error:
            if gate_id == "final-gate":
                raise ValueError(
                    "completed final approval is stale or corrupt"
                ) from error
            raise

    def _gate_refs(self, gate_id: str) -> tuple[ArtifactRef, ...]:
        return self.bound_gates.artifact_refs(gate_id, self.lifecycle, self._nodes)

    def _has_open_blocker(self) -> bool:
        path = Path("artifacts/design-review-findings.json")
        if not (self.workspace / path).exists():
            return False
        review = self.lifecycle.read_payload(path, DesignReviewPayload)
        return not ReviewPolicy.can_compose(review.findings)

    def _summarize(self) -> ResearchRunSummary:
        self.audit.sync()
        return summarize(
            run_id=self.config.run_id,
            graph=self.graph,
            workspace=self.workspace,
            lifecycle=self.lifecycle,
            checkpoints=self.checkpoints,
            gate_lookup=self._gate,
            has_open_blocker=self._has_open_blocker,
            require_complete_final=partial(
                require_complete_final,
                lifecycle=self.lifecycle,
                gates=self.bound_gates,
                checkpoints=self.checkpoints,
                nodes=self._nodes,
                semantics=self.semantics,
                audit=self.audit,
            ),
            gate_order=GATE_ORDER,
        )

    def _require_open(self) -> None:
        if getattr(self, "_closed", True):
            raise RuntimeError("research orchestrator is not initialized")

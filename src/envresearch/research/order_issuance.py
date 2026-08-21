"""Ready-node work-order issuance with durable revision binding."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from pydantic import BaseModel, JsonValue

from envresearch.benchmarks.blind_authority import (
    SignedBlindEnrollment,
    VerifiedBlindEnrollment,
)
from envresearch.benchmarks.claim_integrity import CitationIntegrityValidator
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import ClaimFactMap, CuratorSourceSheet
from envresearch.models.benchmark_evaluation import (
    AcceptedArtifactClaims,
    ExpertScoreSheet,
    MethodRecommendationPayload,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.principal import PrincipalAssignment, PrincipalKind
from envresearch.research.order_policy import (
    archive_isolated_roots,
    blind_claim_usages,
    canonical_blind_json,
    work_order_constraints,
    write_blind_visible,
)
from envresearch.research.principal_registry import PrincipalRegistry
from envresearch.workers.contracts import WorkerRole, WorkOrder
from envresearch.workers.filesystem import PinnedRoot
from envresearch.workers.queue import FilesystemWorkerQueue

if TYPE_CHECKING:
    from envresearch.benchmarks.blind_registry import LoadedBlindCase

ModelT = TypeVar("ModelT", bound=BaseModel)
_METHOD_ROOT = Path(__file__).resolve().parents[3] / "packs/methods"


def issue_ready(orchestrator: object) -> None:
    """Issue every ready worker order, binding a pending revision when present."""
    checkpoints = orchestrator.checkpoints  # type: ignore[attr-defined]
    graph = orchestrator.graph  # type: ignore[attr-defined]
    completed = checkpoints.completed_nodes(graph)
    for node in graph.ready(completed, orchestrator._approved_gates()):  # type: ignore[attr-defined]
        if node.worker_role is None:
            continue
        if node.node_id == "compose-plan" and orchestrator._has_open_blocker():  # type: ignore[attr-defined]
            continue
        order_path = orchestrator.workspace / f"work-orders/{node.node_id}.json"  # type: ignore[attr-defined]
        if order_path.exists():
            continue
        revision_id = _revision_binding(orchestrator, node)
        constraints = work_order_constraints(
            orchestrator.config,  # type: ignore[attr-defined]
            entry_order=node.node_id == graph.nodes[0].node_id,
        )
        if revision_id is not None:
            constraints += (f"Revision transaction: {revision_id}",)
        node_version = (node.version or "1" if revision_id is None
                        else f"{node.version or '1'}-{revision_id}")
        orchestrator.queue.issue(  # type: ignore[attr-defined]
            WorkOrder(
                order_id=node.node_id,
                node_id=node.node_id,
                node_version=node_version,
                role=WorkerRole(node.worker_role),
                input_artifacts=orchestrator.lifecycle.input_refs(node),  # type: ignore[attr-defined]
                expected_output_schema=f"envresearch.{node.node_id.replace('-', '_')}",
                expected_output_filenames=(node.output_paths[0].name,),
                policy_constraints=constraints,
                evidence_requirements=("Retain explicit evidence references",),
                principal_assignment=orchestrator.principals.worker(  # type: ignore[attr-defined]
                    node.node_id, node.worker_role, node_version
                ),
            )
        )


def _revision_binding(orchestrator: object, node: object) -> str | None:
    output_paths = getattr(node, "output_paths", ())
    if not output_paths:
        return None
    path = output_paths[0]
    workspace = orchestrator.workspace  # type: ignore[attr-defined]
    if not (workspace / path).exists():
        return None
    envelope = orchestrator.lifecycle.current_envelope(path)  # type: ignore[attr-defined]
    if envelope.validation_status is not ArtifactLifecycle.SUPERSEDED:
        return None
    value = envelope.provenance.get("revision_id")
    return value if isinstance(value, str) else None


class BlindControllerInfrastructure:
    """Shared queue, principal, projection, and citation machinery."""

    def __init__(self, loaded: LoadedBlindCase, run_root: Path) -> None:
        from envresearch.benchmarks.blind_artifacts import BlindArtifactLifecycle

        self.loaded = loaded
        self.case_id = loaded.source_sheet.case_id
        self.run_root = Path(os.path.abspath(run_root))
        self.recommender_workspace = self.run_root / "isolated/recommender" / self.case_id
        self.expert_workspaces = {
            slot: self.run_root / "isolated/expert" / self.case_id / str(slot)
            for slot in (1, 2)
        }
        self.adjudicator_workspace = self.run_root / "isolated/adjudicator" / self.case_id
        self.queue = self._queue("recommender", self.case_id)
        self.registry = PrincipalRegistry(self.queue.control, f"blind-{self.case_id}")
        self.artifacts = BlindArtifactLifecycle(self.run_root, f"blind-{self.case_id}", self.registry)
        self.expert_queues: dict[int, FilesystemWorkerQueue] = {}
        self.adjudicator_queue: FilesystemWorkerQueue | None = None
        self._third_score_ref: ArtifactRef | None = None
        self._adjudicator: PrincipalAssignment | None = None
        self._descriptor_sha256: str | None = None

    def enroll_participants(
        self, signed: SignedBlindEnrollment,
    ) -> VerifiedBlindEnrollment:
        """Import an authority signature and public human keys, never private keys."""
        from envresearch.benchmarks.blind_enrollment_controller import (
            enroll_participants,
        )
        return enroll_participants(self, signed)

    def _ensure_support_artifacts(self) -> None:
        from envresearch.benchmarks.blind_controller_support import ensure_support
        ensure_support(self, _METHOD_ROOT)

    def _profile_payload(self) -> dict[str, object]:
        from envresearch.benchmarks.blind_controller_support import profile_payload
        return profile_payload(self)

    def _public_brief(self) -> dict[str, object]:
        from envresearch.benchmarks.blind_controller_support import public_brief
        return public_brief(self)

    def _public_leakage(self) -> dict[str, object]:
        from envresearch.benchmarks.blind_controller_support import public_leakage
        return public_leakage(self)

    def _recommendation_payload(self) -> object:
        return self.artifacts.lifecycle.read_artifact(
            self.artifacts.paths(self.case_id).recommendation
        ).payload

    def _expert_payloads(self) -> tuple[ExpertScoreSheet, ExpertScoreSheet]:
        paths = self.artifacts.paths(self.case_id)
        try:
            self.artifacts.lineage.locked_score_refs(self.case_id)
            return (self.artifacts.lifecycle.read_payload(paths.expert_one, ExpertScoreSheet),
                    self.artifacts.lifecycle.read_payload(paths.expert_two, ExpertScoreSheet))
        except FileNotFoundError as error:
            raise ValueError("two expert scores are required") from error

    def _order(
        self,
        order_id: str,
        role: WorkerRole,
        inputs: tuple[ArtifactRef, ...],
        schema: str,
        filename: str,
        assignment: PrincipalAssignment,
    ) -> WorkOrder:
        from envresearch.benchmarks.blind_order_factory import build_blind_order
        return build_blind_order(
            self, order_id, role, inputs, schema, filename, assignment
        )

    def _require_enrollment(self) -> None:
        from envresearch.benchmarks.blind_enrollment_marker import (
            require_frozen_enrollment,
        )
        require_frozen_enrollment(self.registry, self.case_id)

    def _worker(self, kind: PrincipalKind) -> PrincipalAssignment:
        self._require_enrollment()
        return self.registry.benchmark_worker(self.case_id, kind, self._source_generation())

    def _human(self, kind: PrincipalKind, slot: int) -> PrincipalAssignment:
        self._require_enrollment()
        return self.registry.benchmark_human(
            self.case_id, kind, slot, self._source_generation()
        )

    def _rubric_sha256(self) -> str:
        from envresearch.benchmarks.blind_enrollment_controller import rubric_sha256

        return rubric_sha256()

    @staticmethod
    def _policy_sha256() -> str:
        from envresearch.benchmarks.blind_enrollment_controller import policy_sha256

        return policy_sha256()

    def _source_generation(self) -> int:
        path = self.artifacts.paths(self.case_id).source_sheet
        if not (self.run_root / path).exists():
            return self.loaded.source_sheet.source_generation
        source = self.artifacts.lifecycle.read_payload(path, CuratorSourceSheet)
        return cast(int, source.source_generation)

    def _queue(self, role: str, identity: str) -> FilesystemWorkerQueue:
        control_root = self.run_root / "control/queues" / role / identity
        if Path(identity).parent != Path("."):
            parent = PinnedRoot(control_root.parent, private=True)
            parent.close()
        return FilesystemWorkerQueue(self.run_root / "exchanges" / role / identity,
            control_root=control_root,
            require_producer_context=True,
        )

    @staticmethod
    def _submit_payload(queue: FilesystemWorkerQueue, order_id: str,
                        payload: BaseModel) -> None:
        order = queue.read_order(order_id)
        assignment = cast(PrincipalAssignment, order.principal_assignment)
        filename = order.expected_output_filenames[0]
        queue.exchange.write_file_noreplace(
            Path(filename), canonical_blind_json(payload.model_dump(mode="json")), mode=0o600
        )
        try:
            queue.submit(
                order_id,
                Path(filename),
                producer=assignment.producer,
                expected_order_hash=order.order_hash,
            )
        finally:
            os.unlink(queue.root / filename)

    @staticmethod
    def _collected(queue: FilesystemWorkerQueue, order_id: str,
                   model: type[ModelT]) -> ModelT:
        submissions = queue.collect(order_id)
        if len(submissions) != 1:
            raise ValueError("work order requires exactly one authenticated candidate")
        data = queue.exchange.read_file(
            submissions[0].candidate_relative_paths[0], description="blind candidate"
        )
        return model.model_validate_json(data)

    @staticmethod
    def _write_visible(root: Path, filename: str, payload: object) -> None:
        write_blind_visible(root, filename, payload)

    def _restore_adjudicator(self) -> tuple[FilesystemWorkerQueue, PrincipalAssignment]:
        root = self.run_root / "exchanges/adjudicator" / self.case_id
        if self.adjudicator_queue is None:
            if not root.exists():
                raise ValueError("adjudication order has not been issued")
            self.adjudicator_queue = self._queue("adjudicator", self.case_id)
        if not self.adjudicator_queue.has_generation("adjudicator-score"):
            raise ValueError("adjudication order has not been issued")
        self._adjudicator = self._human(PrincipalKind.ADJUDICATOR, 1)
        return self.adjudicator_queue, self._adjudicator

    def _locked_third_score(self, queue: FilesystemWorkerQueue,
                            adjudicator: PrincipalAssignment) -> tuple[ExpertScoreSheet, ArtifactRef]:
        from envresearch.benchmarks.blind_scoring_evidence import (
            promote_locked_third_score,
        )

        self.artifacts.lineage.locked_score_refs(self.case_id)
        return promote_locked_third_score(self, queue, adjudicator)
    def _publish_citations(self, recommendation_ref: ArtifactRef,
                           payload: MethodRecommendationPayload) -> None:
        paths = self.artifacts.paths(self.case_id)
        source = self.artifacts.lifecycle.read_payload(paths.source_sheet, CuratorSourceSheet)
        mapping = self.artifacts.lifecycle.read_payload(paths.claim_fact_map, ClaimFactMap)
        raw = cast(JsonValue, payload.model_dump(mode="json"))
        usages = blind_claim_usages(raw, mapping)
        report = CitationIntegrityValidator().validate(
            source_sheets=(source,),
            fact_maps=(mapping,),
            artifacts=(AcceptedArtifactClaims(
                artifact_ref=recommendation_ref, payload=raw, usages=usages
            ),),
            source_sheet_refs=(self.artifacts.ref(self.case_id, "source_sheet"),),
            claim_fact_map_refs=(self.artifacts.ref(self.case_id, "claim_fact_map"),),
            blinded_brief_refs=(self.artifacts.ref(self.case_id, "blinded_brief"),),
        )
        if not report.passed:
            raise ValueError("citation integrity validation failed")
        self.artifacts.publish_citation_report(
            self.case_id, report, self._worker(PrincipalKind.LEAKAGE_VALIDATOR))
    def _issued_queues(self) -> Iterator[tuple[FilesystemWorkerQueue, str]]:
        yield self.queue, "recommend-method"
        for slot in (1, 2):
            queue = self.expert_queues.get(slot)
            roots = (
                self.run_root / "exchanges/expert" / self.case_id / str(slot),
                self.run_root / "control/queues/expert" / self.case_id / str(slot),
            )
            if queue is None and any(root.exists() for root in roots):
                queue = self._queue("expert", f"{self.case_id}/{slot}")
            if queue is not None and queue.has_generation(f"expert-score-{slot}"):
                yield queue, f"expert-score-{slot}"
        roots = (self.run_root / "exchanges/adjudicator" / self.case_id,
                 self.run_root / "control/queues/adjudicator" / self.case_id)
        if self.adjudicator_queue is None and any(root.exists() for root in roots):
            self.adjudicator_queue = self._queue("adjudicator", self.case_id)
        if self.adjudicator_queue is not None:
            for order_id in ("adjudicator-score", "adjudicate"):
                if self.adjudicator_queue.has_generation(order_id):
                    yield self.adjudicator_queue, order_id

    def _archive_isolated(self, revision_id: str) -> None:
        roots = (self.recommender_workspace, *self.expert_workspaces.values(),
                 self.adjudicator_workspace)
        archive_isolated_roots(self.run_root, roots, revision_id)

    def _profile_path(self) -> Path:
        return self.artifacts.paths(self.case_id).source_sheet.parent / "method-profiles.json"

    def _rubric_path(self) -> Path:
        return self.artifacts.paths(self.case_id).source_sheet.parent / "expert-rubric.json"

    def _validated(self, path: Path) -> bool:
        try:
            return (
                self._state(path) is ArtifactLifecycle.VALIDATED
                and self.artifacts.lifecycle.validated_history_ref(path)
                == self.artifacts.lifecycle.artifact_ref(path)
            )
        except (FileNotFoundError, ValueError):
            return False

    def _state(self, path: Path) -> ArtifactLifecycle | None:
        if not (self.run_root / path).exists():
            return None
        return self.artifacts.lifecycle.current_envelope(path).validation_status

    @staticmethod
    def _workspace_files(root: Path) -> tuple[str, ...]:
        if not root.exists():
            return ()
        children = tuple(root.iterdir())
        if any(not path.is_file() or path.is_symlink() for path in children):
            raise ValueError("isolated workspace contains unexpected entries")
        return tuple(sorted(path.name for path in children))

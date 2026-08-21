"""Global authority order for governed research-factory assembly."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.factory._authority_roots import AuthorityRootManifest
from envresearch.factory.design_resolver import V02ApprovedDesignResolver
from envresearch.factory.errors import FactoryAuthorityInvalid, FactoryError
from envresearch.kernel.events import EventLog, EventRecord
from envresearch.kernel.gates import GateDecision, GateStore
from envresearch.kernel.node_checkpoint_archive import (
    LOCK_NAME,
    InvalidationArchive,
)
from envresearch.kernel.node_checkpoint_events import PinnedNodeEventLog
from envresearch.kernel.node_checkpoints import NodeCheckpointStore
from envresearch.models.artifact import ArtifactRef, ProducerIdentity
from envresearch.models.principal import PrincipalAssignment, PrincipalKind
from envresearch.paper.errors import PaperBuilderError
from envresearch.paper.release import PaperReleaseService
from envresearch.paper.release_contracts import PaperReleaseCandidate
from envresearch.paper.release_revisions import RevisionPair
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.audit_state import ResearchRunManifest
from envresearch.research.citation_attestations import ProtectedCitationAttestations
from envresearch.research.gate_context import BoundGateManager
from envresearch.research.node_inputs import refresh_literature_coverage_input
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.principal_registry import PrincipalRegistry
from envresearch.research.run_binding import method_profile_digests
from envresearch.research.semantic_validation import SemanticSubmissionValidator
from envresearch.research.workflow import ResearchRunConfig, build_research_graph
from envresearch.storage.artifacts import ArtifactStore
from envresearch.workers.filesystem import PinnedRoot
from envresearch.workers.native import locked_regular_at
from envresearch.workers.queue import FilesystemWorkerQueue

if TYPE_CHECKING:
    from envresearch.factory.contracts import ResearchFactoryRun
    from envresearch.factory.promotion_contracts import (
        FactoryPromotionContext,
        FactoryRunPromotion,
    )

FACTORY_RUN_LOCK_SUBJECT = "research-factory-run-publication"


class _ReadOnlyAuditState:
    """Minimum exact manifest authority used by final-state reconstruction."""

    def __init__(self, workspace: Path) -> None:
        self.raw = ArtifactStore(workspace)

    def verify_method_profiles(self, registry: object) -> None:
        manifest = ResearchRunManifest.model_validate(
            self.raw.read_json(Path("research-run-manifest.json"))
        )
        if manifest.method_profile_sha256 != method_profile_digests(registry):  # type: ignore[arg-type]
            raise ValueError("research run method profile content changed")

    def close(self) -> None:
        """Match the orchestrator-owned resource protocol without descriptors."""


def _open_existing_checkpoints(workspace: Path) -> NodeCheckpointStore:
    """Pin an existing checkpoint namespace without repair or creation."""
    store = NodeCheckpointStore.__new__(NodeCheckpointStore)
    store.artifacts = ArtifactStore(workspace)
    store.workspace = workspace
    store._closed = False
    store._checkpoints_fd = -1
    store._root = PinnedRoot(workspace, create=False)
    try:
        root_stat = os.fstat(store._root.fd)
        store._workspace_identity = (root_stat.st_dev, root_stat.st_ino)
        store._checkpoints_fd = store._root.open_directory(
            Path("node-checkpoints"), create=False
        )
        checkpoint_stat = os.fstat(store._checkpoints_fd)
        store._checkpoint_identity = (
            checkpoint_stat.st_dev,
            checkpoint_stat.st_ino,
        )
        names = tuple(os.listdir(store._checkpoints_fd))
        if any(name.startswith(".") and name.endswith(".stage") for name in names):
            raise ValueError("checkpoint recovery residue requires a writer")
        store.events = PinnedNodeEventLog(
            workspace / "events.jsonl", store._root, store._ensure_open
        )
        store.events.validate_file()

        def forbidden(*_args: object, **_kwargs: object) -> None:
            raise ValueError("read-only checkpoint view cannot recover state")

        store._archive = InvalidationArchive(
            store._checkpoints_fd,
            write_once=forbidden,
            move_once=forbidden,
        )
        with locked_regular_at(store._checkpoints_fd, LOCK_NAME, timeout=10):
            pass
        return store
    except BaseException:
        store.close()
        raise


def open_existing_research_authority(workspace: Path) -> ResearchOrchestrator:
    """Open a completed research authority without initialization or recovery."""
    orchestrator = ResearchOrchestrator()
    queue: FilesystemWorkerQueue | None = None
    checkpoints: NodeCheckpointStore | None = None
    try:
        root = PinnedRoot(workspace, create=False)
        try:
            config_data = root.read_file(
                Path("research-run-config.json"),
                description="research run config",
            )
        finally:
            root.close()
        config = ResearchRunConfig.model_validate_json(config_data)
        target = workspace.resolve(strict=True)
        if config.workspace != target:
            raise ValueError("research workspace identity does not match root")
        graph = refresh_literature_coverage_input(
            build_research_graph(
                config.input_mode,
                require_claim_verified_citations=(
                    config.require_claim_verified_citations
                ),
            ),
            target,
        )
        nodes = {node.node_id: node for node in graph.nodes}
        queue = FilesystemWorkerQueue.open_existing(
            target, require_producer_context=True
        )
        attestations = ProtectedCitationAttestations.open_existing(queue)
        lifecycle = ResearchArtifactLifecycle(target, config.run_id)
        checkpoints = _open_existing_checkpoints(target)
        orchestrator._closed = False
        orchestrator.config = config
        orchestrator.workspace = target
        orchestrator.graph = graph
        orchestrator._nodes = nodes
        orchestrator.raw_store = ArtifactStore(target)
        orchestrator.lifecycle = lifecycle
        orchestrator.queue = queue
        orchestrator.principals = PrincipalRegistry.__new__(PrincipalRegistry)
        orchestrator.principals.control = queue.control
        orchestrator.principals.run_id = config.run_id
        orchestrator.citation_attestations = attestations
        orchestrator.semantics = SemanticSubmissionValidator(
            lifecycle,
            nodes,
            require_claim_verified_citations=(config.require_claim_verified_citations),
            citation_attestations=attestations,
        )
        orchestrator.audit = _ReadOnlyAuditState(target)  # type: ignore[assignment]
        gates = GateStore(orchestrator.raw_store, EventLog(target / "events.jsonl"))
        orchestrator.gates = gates
        orchestrator.bound_gates = BoundGateManager(target, gates, config.requested_by)
        orchestrator.checkpoints = checkpoints
        return orchestrator
    except BaseException:
        if checkpoints is not None:
            checkpoints.close()
        if queue is not None:
            queue.close()
        raise


class FactoryAuthority:
    """Acquire research, evidence, paper, handoff, then factory-run authority."""

    def __init__(
        self,
        *,
        design_resolver: V02ApprovedDesignResolver,
        release_service: PaperReleaseService,
    ) -> None:
        self.design_resolver = design_resolver
        self.release_service = release_service
        self.root_manifest = AuthorityRootManifest.derive(
            design_resolver=design_resolver, release_service=release_service
        )

    @contextmanager
    def lease(
        self,
        *,
        design_id: str,
        release_ref: ArtifactRef,
        defer_body_errors: bool = False,
        bootstrap: bool = False,
    ) -> Iterator[tuple[PaperReleaseCandidate, tuple[RevisionPair, ...]]]:
        """Hold every mutation authority in one explicit non-inverting order."""
        deferred: ValueError | None = None
        try:
            self.root_manifest.require_current()
            with (
                self.design_resolver.authority_lease(),
                self.release_service._authority_lease(release_ref) as release_lock,
            ):
                registry = ExitRegistry(
                    self.design_resolver.factory_root, create=bootstrap
                )
                with (
                    registry.lock(self.design_resolver._subject(design_id)),
                    registry.lock(FACTORY_RUN_LOCK_SUBJECT),
                ):
                    if not defer_body_errors:
                        yield release_lock
                    else:
                        try:
                            yield release_lock
                        except ValueError as exc:
                            deferred = exc
                    self.root_manifest.require_current()
        except (FactoryError, PaperBuilderError):
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise FactoryAuthorityInvalid(
                "factory authority lease is unavailable",
                finding_kind="authority",
            ) from exc
        if deferred is not None:
            raise deferred

    def reviewed_producers(
        self, run: ResearchFactoryRun
    ) -> tuple[ProducerIdentity, ...]:
        """Reopen producer identities for every design worker in the exact context."""
        orchestrator = self.design_resolver.orchestrator
        versions = {
            (ref.artifact_id, ref.artifact_version)
            for ref in run.design.final_context.artifact_refs
        }
        return tuple(
            artifact.envelope.producer
            for node in orchestrator.graph.nodes
            for path in node.output_paths[:1]
            for ref in run.design.final_context.artifact_refs
            if path.stem == ref.artifact_id
            for artifact in (
                orchestrator.lifecycle.read_history(path, ref.artifact_version),
            )
            if (ref.artifact_id, ref.artifact_version) in versions
            and node.worker_role is not None
        )

    def require_independent_decision(
        self,
        context: FactoryPromotionContext,
        decision: GateDecision,
        reviewed: tuple[ProducerIdentity, ...],
    ) -> None:
        """Reject the requester, run producer, or any contributing worker."""
        orchestrator = self.design_resolver.orchestrator
        workers = {
            f"principal-{node.node_id}"
            for node in orchestrator.graph.nodes
            if node.worker_role is not None
        }
        forbidden = {
            context.requested_by,
            context.run.producer,
            *workers,
            *(producer.component for producer in reviewed),
        }
        if decision.decided_by in forbidden:
            raise FactoryAuthorityInvalid(
                "promotion requires an independent decision principal",
                finding_kind="promotion-principal-independent",
            )

    def authenticate_promotion_principal(
        self,
        capability: str,
        actor: str,
        reviewed: tuple[ProducerIdentity, ...],
    ) -> PrincipalAssignment:
        """Authenticate the protected gate principal and reviewed separation."""
        principals = self.design_resolver.orchestrator.principals
        try:
            principal = principals.require_existing_capability(
                PrincipalKind.GATE, capability
            )
            principals.require_existing_gate_actor(actor, reviewed)
            return principal
        except ValueError as exc:
            raise FactoryAuthorityInvalid(
                str(exc), finding_kind="promotion-principal-invalid"
            ) from exc

    def require_promotion_principal(
        self, promotion: FactoryRunPromotion, run: ResearchFactoryRun
    ) -> PrincipalAssignment:
        """Require existing protected human authority and its exact sealed identity."""
        principals = self.design_resolver.orchestrator.principals
        try:
            current = principals.require_existing_gate_actor(
                promotion.decision.decided_by, self.reviewed_producers(run)
            )
            if current != promotion.authenticated_principal:
                raise ValueError("gate principal differs from sealed promotion")
            return current
        except ValueError as exc:
            raise FactoryAuthorityInvalid(
                str(exc), finding_kind="promotion-principal-invalid"
            ) from exc

    @staticmethod
    def require_decision_time(decision: GateDecision, event: EventRecord) -> None:
        if decision.decided_at <= event.timestamp:
            raise FactoryAuthorityInvalid(
                "promotion decision must follow the authenticated request",
                finding_kind="promotion-decision-time",
            )


__all__ = ["AuthorityRootManifest", "FactoryAuthority"]

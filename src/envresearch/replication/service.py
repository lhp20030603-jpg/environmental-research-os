"""Autonomous exception-only controller for approved Tier-2 replication."""

from __future__ import annotations

import tarfile
from pathlib import Path

from envresearch.models.artifact import ArtifactRef, ResearchArtifact
from envresearch.replication._attempt_support import AttemptCoordinator
from envresearch.replication._runtime_evidence import (
    engine_authority,
    runtime_payload,
)
from envresearch.replication._service_artifacts import (
    persist_author_outputs,
    persist_derived_output,
)
from envresearch.replication._service_execution import (
    RuntimePause,
    RuntimeTerminal,
    build_derived_plan,
    record_progress_heartbeat,
    require_success,
    run_engine,
    workspace_bytes,
)
from envresearch.replication._service_models import (
    DidReplayConfiguration as _DidReplayConfiguration,
)
from envresearch.replication._service_models import (
    ReplicationFault as _ReplicationFault,
)
from envresearch.replication._service_models import (
    ReplicationReport as _ReplicationReport,
)
from envresearch.replication._service_recovery import (
    pause_interrupted_owner,
    reopen_recovered_execution,
    restart_pending,
)
from envresearch.replication._service_reporting import ServiceReportingMixin
from envresearch.replication._service_resume import resume_run
from envresearch.replication._service_runtime import (
    RuntimeLedgerCallbacks,
    finalize_runtime_pause,
    newest_runtime_ref,
    observe_workspace_start,
)
from envresearch.replication._service_support import (
    materialize_inventory,
    persist_payload,
    read_admission,
    read_inventory,
)
from envresearch.replication.container import ContainerCleanupError, ContainerEngine
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ApprovedTier2Intake,
    ExternalAdmission,
    ReplicationRunState,
    Tier2IntakeProposal,
)
from envresearch.replication.did_r import (
    AuthorReproductionMapping,
    RDidAdapter,
)
from envresearch.replication.intake import Tier2IntakeService
from envresearch.replication.ledger import (
    ReplicationLedger,
    ReplicationRun,
)
from envresearch.replication.verify import ReplicationVerifier
from envresearch.storage.research_artifacts import ResearchArtifactStore

DidReplayConfiguration = _DidReplayConfiguration
ReplicationFault = _ReplicationFault
ReplicationReport = _ReplicationReport


class ReplicationService(ServiceReportingMixin):
    """Autonomously execute one approved replay; only exceptions require review."""

    def __init__(
        self,
        store: ResearchArtifactStore,
        intake: Tier2IntakeService,
        engine: ContainerEngine,
        configuration: DidReplayConfiguration,
        *,
        max_growth_bytes: int = 0,
    ) -> None:
        self._store, self.intake, self.engine = store, intake, engine
        self.configuration = configuration
        self.ledger = ReplicationLedger(store, max_growth_bytes=max_growth_bytes)
        self.verifier = ReplicationVerifier(store)

    def dry_intake(self, proposal: Tier2IntakeProposal) -> Tier2IntakeProposal:
        """Validate an intake value without persisting or acquiring anything."""
        return proposal

    def approve_external_admission(
        self, proposal_ref: ArtifactRef, admission: ExternalAdmission
    ) -> ArtifactRef:
        """Persist the sole human admission required before autonomous replay."""
        return self.intake.approve(proposal_ref, admission)

    def run(self, approved_ref: ArtifactRef) -> ReplicationReport:
        """Run idempotently; concurrent callers observe one immutable attempt."""
        coordinator = AttemptCoordinator(self._store, approved_ref)
        with coordinator.locked():
            return self._run_locked(approved_ref, coordinator)

    def _run_locked(
        self, approved_ref: ArtifactRef, coordinator: AttemptCoordinator
    ) -> ReplicationReport:
        try:
            current = self.ledger.read_current()
        except FileNotFoundError:
            current = None
        except (OSError, ValueError) as error:
            return self._attempt(
                coordinator, ReplicationFault("LEDGER_RECOVERY_FAILED", str(error))
            )
        if current is not None and current[1].approved_intake_ref == approved_ref:
            if current[1].verification_pending:
                return self._finalize(current[0])
            if current[1].state is ReplicationRunState.PENDING:
                return self._recover_pending(*current, coordinator)
            if current[1].state is ReplicationRunState.RUNNING:
                try:
                    paused = pause_interrupted_owner(
                        self._store, self.ledger, self.engine, *current
                    )
                except ContainerCleanupError as error:
                    return self._fail(
                        current[0],
                        ReplicationFault("CONTAINMENT_CLEANUP_FAILED", str(error)),
                    )
                except (OSError, TypeError, ValueError) as error:
                    return self._fail(
                        self.ledger.read_current()[0],
                        ReplicationFault("RECOVERY_EVIDENCE_INVALID", str(error)),
                    )
                return self._report(*self.ledger.read_current(paused))
            return self._authenticated_report(*current)
        if previous := coordinator.read_failure():
            return self._attempt_report(*previous)
        try:
            approved, proposal = read_admission(self._store, approved_ref)
        except (OSError, TypeError, ValueError) as error:
            return self._attempt(
                coordinator,
                ReplicationFault("EXTERNAL_ADMISSION_REQUIRED", str(error)),
            )
        try:
            attempt_ref, claim = coordinator.claim()
            output_root = coordinator.allocate_root(claim)
        except (OSError, ValueError) as error:
            return self._attempt(
                coordinator, ReplicationFault("OUTPUT_NAMESPACE_INVALID", str(error))
            )
        try:
            acquired_ref = self.intake.acquire(
                approved_ref, approved.payload.approval.approved_locator
            )
            inventory = read_inventory(self._store, acquired_ref)
            input_root = materialize_inventory(self._store, inventory)
        except (
            OSError,
            PermissionError,
            tarfile.TarError,
            TypeError,
            ValueError,
        ) as error:
            return self._attempt(
                coordinator, ReplicationFault("ARCHIVE_REJECTED", str(error))
            )
        try:
            selected_engine, executable_sha256, endpoint = engine_authority(self.engine)
            observation = self.engine.preflight(proposal.runtime)
        except (OSError, RuntimeError, ValueError) as error:
            fault = ReplicationFault(
                "NO_CONTAINER_ENGINE", str(error), (approved_ref, acquired_ref)
            )
            return self._attempt(coordinator, fault)
        try:
            observation_payload = runtime_payload(
                observation, selected_engine, executable_sha256, endpoint
            )
        except (TypeError, ValueError) as error:
            return self._attempt(
                coordinator,
                ReplicationFault(
                    "RUNTIME_EVIDENCE_INVALID",
                    str(error),
                    (approved_ref, acquired_ref),
                ),
            )
        runtime_ref: ArtifactRef | None = None
        try:
            runtime_ref = persist_payload(
                self._store,
                "tier2-runtime-observation",
                "runtime",
                observation_payload,
                (approved_ref, acquired_ref),
                "tier2-container",
            )
            run_ref = self.ledger.start(
                approved_ref,
                acquired_ref,
                runtime_ref,
                attempt_ref,
                claim.output_root,
            )
        except (OSError, TypeError, ValueError) as error:
            fault = ReplicationFault(
                "ADMITTED_EVIDENCE_INVALID",
                str(error),
                (
                    approved_ref,
                    acquired_ref,
                    *((runtime_ref,) if runtime_ref is not None else ()),
                ),
            )
            return self._attempt(coordinator, fault)
        return self._execute(run_ref, approved, proposal, inventory, input_root, output_root, selected_engine)  # fmt: skip

    def _recover_pending(
        self,
        run_ref: ArtifactRef,
        run: ReplicationRun,
        coordinator: AttemptCoordinator,
    ) -> ReplicationReport:
        try:
            restarted = restart_pending(self.ledger, run_ref, run)
            context = reopen_recovered_execution(
                self._store, self.engine, run, coordinator
            )
        except (OSError, tarfile.TarError, TypeError, ValueError) as error:
            return self._fail(
                self.ledger.read_current()[0],
                ReplicationFault("RECOVERY_EVIDENCE_INVALID", str(error)),
            )
        return self._execute(
            restarted,
            context.approved,
            context.proposal,
            context.inventory,
            context.input_root,
            context.output_root,
            context.expected_engine,
        )

    def resume(self, run_ref: ArtifactRef) -> ReplicationReport:
        """Resume only an authenticated PAUSED checkpoint with an empty bound root."""
        return resume_run(self, run_ref)

    def _execute(
        self,
        run_ref: ArtifactRef,
        approved: ResearchArtifact[ApprovedTier2Intake],
        proposal: Tier2IntakeProposal,
        inventory: AcquiredPackageInventory,
        input_root: Path,
        output_root: Path,
        expected_engine: str,
    ) -> ReplicationReport:
        active_ref = run_ref
        run = self.ledger.read_current(run_ref)[1]
        adapter = RDidAdapter(proposal.runtime, approved)

        runtime = RuntimeLedgerCallbacks(
            self.ledger, active_ref, proposal, output_root, expected_engine
        )

        try:
            try:
                author_plan = adapter.author_plan(
                    inventory,
                    AuthorReproductionMapping(
                        script_path=self.configuration.author_script,
                        output_mappings=proposal.expected_outputs,
                        input_root=input_root,
                        output_root=output_root,
                        budget=proposal.budget,
                    ),
                )
            except ValueError as error:
                raise ReplicationFault(
                    "PROHIBITED_EXECUTION_PLAN", str(error)
                ) from error
            active_ref = observe_workspace_start(
                self.ledger, active_ref, proposal, output_root
            )
            runtime.active_ref = active_ref
            author_result = run_engine(
                self.engine,
                author_plan,
                expected_engine,
                runtime.progress,
                runtime.started,
                runtime.stopped,
            )
            active_ref = runtime.active_ref
            author_memory, author_storage = require_success(author_result, author_plan)
            active_ref = record_progress_heartbeat(
                self.ledger,
                active_ref,
                proposal,
                at=author_result.finished_at,
                memory_bytes=author_memory,
                storage_bytes=max(author_storage, workspace_bytes(output_root)),
            )
            outputs = persist_author_outputs(
                self._store, run, proposal, author_plan, author_result
            )
            derived_plan = build_derived_plan(
                adapter,
                inventory,
                proposal,
                input_root,
                output_root,
                self.configuration,
            )
            active_ref = observe_workspace_start(
                self.ledger, active_ref, proposal, output_root
            )
            runtime.active_ref = active_ref
            derived_result = run_engine(
                self.engine,
                derived_plan,
                expected_engine,
                runtime.progress,
                runtime.started,
                runtime.stopped,
            )
            active_ref = runtime.active_ref
            derived_memory, derived_storage = require_success(
                derived_result, derived_plan
            )
            active_ref = record_progress_heartbeat(
                self.ledger,
                active_ref,
                proposal,
                at=derived_result.finished_at,
                memory_bytes=derived_memory,
                storage_bytes=max(derived_storage, workspace_bytes(output_root)),
            )
            derived_ref, derived_log_ref = persist_derived_output(
                self._store, run, outputs, derived_plan, derived_result
            )
            active_ref = self.ledger.complete(
                active_ref,
                author_outputs=outputs,
                derived_ref=derived_ref,
                derived_log_ref=derived_log_ref,
            )
            return self._finalize(active_ref)
        except RuntimePause as error:
            active_ref = newest_runtime_ref(runtime.active_ref, error.run_ref)
            try:
                paused = finalize_runtime_pause(
                    self._store,
                    self.ledger,
                    active_ref,
                    proposal,
                    output_root,
                    error,
                )
            except ReplicationFault as fault:
                return self._fail(self.ledger.read_current()[0], fault)
            except (OSError, TypeError, ValueError) as boundary:
                persistence_fault = ReplicationFault(
                    "PERSISTENCE_FAILURE", str(boundary)
                )
                return self._fail(self.ledger.read_current()[0], persistence_fault)
            return self._report(*self.ledger.read_current(paused))
        except RuntimeTerminal as error:
            active_ref = newest_runtime_ref(runtime.active_ref, error.run_ref)
            if error.code is None:
                return self._report(*self.ledger.read_current(active_ref))
            return self._fail(active_ref, ReplicationFault(error.code, str(error)))
        except ReplicationFault as error:
            return self._fail(newest_runtime_ref(runtime.active_ref, active_ref), error)
        except (OSError, ValueError) as error:
            active_ref = newest_runtime_ref(runtime.active_ref, active_ref)
            return self._fail(active_ref, ReplicationFault("PERSISTENCE_FAILURE", str(error)))  # fmt: skip

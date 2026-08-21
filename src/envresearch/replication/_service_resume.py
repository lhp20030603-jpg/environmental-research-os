"""Private authenticated resume flow for the replication controller."""

import tarfile
from pathlib import Path
from typing import Protocol

from envresearch.models.artifact import ArtifactRef, ResearchArtifact
from envresearch.replication._attempt_support import AttemptCoordinator
from envresearch.replication._runtime_evidence import engine_authority
from envresearch.replication._service_models import ReplicationFault, ReplicationReport
from envresearch.replication._service_support import reopen_run_evidence
from envresearch.replication._workspace_checkpoint import require_workspace_checkpoint
from envresearch.replication.container import ContainerEngine
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ApprovedTier2Intake,
    Tier2IntakeProposal,
)
from envresearch.replication.ledger import ReplicationLedger, ReplicationRun
from envresearch.storage.research_artifacts import ResearchArtifactStore


class _ResumeController(Protocol):
    _store: ResearchArtifactStore
    engine: ContainerEngine
    ledger: ReplicationLedger

    def _execute(
        self,
        run_ref: ArtifactRef,
        approved: ResearchArtifact[ApprovedTier2Intake],
        proposal: Tier2IntakeProposal,
        inventory: AcquiredPackageInventory,
        input_root: Path,
        output_root: Path,
        expected_engine: str,
    ) -> ReplicationReport: ...

    def _fail(
        self, run_ref: ArtifactRef, fault: ReplicationFault
    ) -> ReplicationReport: ...


def resume_run(service: _ResumeController, run_ref: ArtifactRef) -> ReplicationReport:
    """Resume only an authenticated PAUSED checkpoint under its attempt lock."""
    run = service.ledger.read_current(run_ref)[1]
    coordinator = AttemptCoordinator(service._store, run.approved_intake_ref)
    with coordinator.locked():
        run = service.ledger.read_current(run_ref)[1]
        return _resume_locked(service, run_ref, run, coordinator)


def _resume_locked(
    service: _ResumeController,
    run_ref: ArtifactRef,
    run: ReplicationRun,
    coordinator: AttemptCoordinator,
) -> ReplicationReport:
    try:
        selected_engine, executable_sha256, endpoint = engine_authority(service.engine)
        evidence = reopen_run_evidence(
            service._store,
            run,
            selected_engine,
            executable_sha256,
            endpoint,
        )
        approved, proposal, inventory, input_root, expected_engine = evidence
        output_root = coordinator.resume_root(run.attempt_ref, run.output_root)
        require_workspace_checkpoint(
            service._store,
            run_ref,
            run,
            output_root,
            max_bytes=proposal.budget.max_storage_bytes,
        )
    except (OSError, tarfile.TarError, TypeError, ValueError) as error:
        return service._fail(
            run_ref, ReplicationFault("RESUME_EVIDENCE_INVALID", str(error))
        )
    resumed = service.ledger.resume(
        run_ref, run.acquired_inventory_ref, run.runtime_ref
    )
    return service._execute(
        resumed,
        approved,
        proposal,
        inventory,
        input_root,
        output_root,
        expected_engine,
    )

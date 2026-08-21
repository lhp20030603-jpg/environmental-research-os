"""Private crash-recovery mechanics for one locked replication attempt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from envresearch.models.artifact import ArtifactRef, ResearchArtifact
from envresearch.replication._attempt_support import AttemptCoordinator
from envresearch.replication._container_validation import workspace_container_names
from envresearch.replication._runtime_evidence import (
    engine_authority,
    restore_runtime_observation,
)
from envresearch.replication._service_support import (
    read_admission,
    read_exact,
    reopen_run_evidence,
)
from envresearch.replication._workspace_checkpoint import persist_workspace_checkpoint
from envresearch.replication.container import ContainerCleanupError, ContainerEngine
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ApprovedTier2Intake,
    ReplicationRunState,
    Tier2IntakeProposal,
)
from envresearch.replication.ledger import ReplicationLedger, ReplicationRun
from envresearch.storage.research_artifacts import ResearchArtifactStore


@dataclass(frozen=True, slots=True)
class RecoveredExecution:
    approved: ResearchArtifact[ApprovedTier2Intake]
    proposal: Tier2IntakeProposal
    inventory: AcquiredPackageInventory
    input_root: Path
    output_root: Path
    expected_engine: str


def restart_pending(
    ledger: ReplicationLedger, run_ref: ArtifactRef, run: ReplicationRun
) -> ArtifactRef:
    """Reauthenticate exact inputs and finish PENDING -> RUNNING publication."""
    if run.state is not ReplicationRunState.PENDING:
        raise ValueError("crash recovery expected a pending ledger")
    return ledger.start(
        run.approved_intake_ref,
        run.acquired_inventory_ref,
        run.runtime_ref,
        run.attempt_ref,
        run.output_root,
    )


def reopen_recovered_execution(
    store: ResearchArtifactStore,
    engine: ContainerEngine,
    run: ReplicationRun,
    coordinator: AttemptCoordinator,
) -> RecoveredExecution:
    """Reopen exact admitted evidence for a safely restarted empty workspace."""
    selected_engine, executable_sha256, endpoint = engine_authority(engine)
    approved, proposal, inventory, input_root, expected_engine = reopen_run_evidence(
        store, run, selected_engine, executable_sha256, endpoint
    )
    output_root = coordinator.resume_root(run.attempt_ref, run.output_root)
    if any(output_root.rglob("*")):
        raise ValueError("pending recovery workspace is not empty")
    return RecoveredExecution(
        approved,
        proposal,
        inventory,
        input_root,
        output_root,
        expected_engine,
    )


def pause_interrupted_owner(
    store: ResearchArtifactStore,
    ledger: ReplicationLedger,
    engine: ContainerEngine,
    run_ref: ArtifactRef,
    run: ReplicationRun,
) -> ArtifactRef:
    """Seal the exact orphaned workspace before publishing resumable PAUSED."""
    if run.state is not ReplicationRunState.RUNNING or run.verification_pending:
        raise ValueError("only an incomplete running generation may be recovered")
    try:
        selected, executable_sha256, endpoint = engine_authority(engine)
        runtime = read_exact(store, "runtime", run.runtime_ref)
        if (
            runtime.envelope.artifact_id != "tier2-runtime-observation"
            or runtime.envelope.producer.component != "tier2-container"
            or runtime.envelope.input_artifacts
            != (run.approved_intake_ref, run.acquired_inventory_ref)
        ):
            raise ValueError("persisted runtime authority is invalid")
        restore_runtime_observation(
            runtime.payload, selected, executable_sha256, endpoint
        )
    except (OSError, TypeError, ValueError) as error:
        raise ContainerCleanupError(
            "persisted runtime authority differs from selected containment control"
        ) from error
    _, proposal = read_admission(store, run.approved_intake_ref)
    coordinator = AttemptCoordinator(store, run.approved_intake_ref)
    root = coordinator.resume_root(run.attempt_ref, run.output_root)
    engine.contain(run.runtime_owner, workspace_container_names(root))
    run_ref = ledger.release_runtime_owner(run_ref)
    run = ledger.read_current(run_ref)[1]
    checkpoint = persist_workspace_checkpoint(
        store,
        run_ref,
        run,
        root,
        max_bytes=proposal.budget.max_storage_bytes,
    )
    return ledger.pause(
        run_ref,
        reason="interrupted-owner",
        evidence_refs=(checkpoint,),
    )

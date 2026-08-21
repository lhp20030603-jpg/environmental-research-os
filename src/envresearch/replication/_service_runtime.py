"""Private heartbeat and resumable workspace orchestration helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from envresearch.models.artifact import ArtifactRef
from envresearch.replication._runtime_owner import (
    RuntimeLaunchIdentity,
    RuntimeOwnership,
)
from envresearch.replication._service_execution import (
    RuntimePause,
    RuntimeTerminal,
    record_progress_heartbeat,
    workspace_bytes,
)
from envresearch.replication._service_models import ReplicationFault
from envresearch.replication._workspace_checkpoint import persist_workspace_checkpoint
from envresearch.replication.contracts import (
    ReplicationException,
    ReplicationRunState,
    Tier2IntakeProposal,
)
from envresearch.replication.ledger import ReplicationLedger
from envresearch.storage.research_artifacts import ResearchArtifactStore


class RuntimeLedgerCallbacks:
    """Bind live runtime ownership and progress to the current sealed ledger."""

    def __init__(
        self,
        ledger: ReplicationLedger,
        run_ref: ArtifactRef,
        proposal: Tier2IntakeProposal,
        output_root: Path,
        expected_engine: str,
    ) -> None:
        self.ledger = ledger
        self.active_ref = run_ref
        self.proposal = proposal
        self.output_root = output_root
        self.expected_engine = expected_engine

    def progress(self, at: datetime, memory_bytes: int, storage_bytes: int) -> None:
        try:
            self.active_ref = record_progress_heartbeat(
                self.ledger,
                self.active_ref,
                self.proposal,
                at=at,
                memory_bytes=memory_bytes,
                storage_bytes=max(storage_bytes, workspace_bytes(self.output_root)),
            )
        except (RuntimePause, RuntimeTerminal) as error:
            if error.run_ref is None:
                raise ValueError(
                    "runtime progress stop lacks an exact ledger"
                ) from error
            self.active_ref = error.run_ref
            raise

    def started(self, identity: RuntimeLaunchIdentity | RuntimeOwnership) -> None:
        """Persist the prepared launch, then its independently bound owner."""
        try:
            if identity.engine != self.expected_engine:
                raise ValueError("runtime owner engine differs from preflight")
            if type(identity) is RuntimeLaunchIdentity:
                self.active_ref = self.ledger.prepare_runtime_launch(
                    self.active_ref, identity
                )
            elif type(identity) is RuntimeOwnership:
                self.active_ref = self.ledger.claim_runtime_owner(
                    self.active_ref, identity
                )
            else:
                raise TypeError("runtime ownership callback type is invalid")
        except (OSError, TypeError, ValueError) as error:
            self._refresh_current()
            raise ReplicationFault("PERSISTENCE_FAILURE", str(error)) from error

    def stopped(self) -> None:
        try:
            self.active_ref = self.ledger.release_runtime_owner(self.active_ref)
        except (OSError, TypeError, ValueError) as error:
            self._refresh_current()
            raise ReplicationFault("CONTAINMENT_CLEANUP_FAILED", str(error)) from error

    def _refresh_current(self) -> None:
        try:
            self.active_ref = self.ledger.read_current()[0]
        except (OSError, TypeError, ValueError):
            pass


def observe_workspace_start(
    ledger: ReplicationLedger,
    run_ref: ArtifactRef,
    proposal: Tier2IntakeProposal,
    output_root: Path,
) -> ArtifactRef:
    return record_progress_heartbeat(
        ledger,
        run_ref,
        proposal,
        at=datetime.now(UTC),
        memory_bytes=0,
        storage_bytes=workspace_bytes(output_root),
    )


def checkpoint_pause(
    store: ResearchArtifactStore,
    ledger: ReplicationLedger,
    run_ref: ArtifactRef,
    proposal: Tier2IntakeProposal,
    output_root: Path,
    *,
    reason: str | None = None,
) -> ArtifactRef:
    run = ledger.read_current(run_ref)[1]
    observed_reason = run.exception.code if run.exception is not None else reason
    if observed_reason not in {"inactivity", "unexpected-growth"}:
        raise ValueError("runtime pause lacks an allowed typed reason")
    checkpoint = persist_workspace_checkpoint(
        store,
        run_ref,
        run,
        output_root,
        max_bytes=proposal.budget.max_storage_bytes,
    )
    return ledger.pause(run_ref, reason=observed_reason, evidence_refs=(checkpoint,))


def finalize_runtime_pause(
    store: ResearchArtifactStore,
    ledger: ReplicationLedger,
    run_ref: ArtifactRef,
    proposal: Tier2IntakeProposal,
    output_root: Path,
    pause: RuntimePause,
) -> ArtifactRef:
    """Measure final progress and bind the exact paused workspace checkpoint."""
    active_ref = newest_runtime_ref(run_ref, pause.run_ref)
    reason = pause.reason
    current = ledger.read_current(active_ref)[1]
    if current.state is ReplicationRunState.EXCEPTION:
        return active_ref
    if pause.run_ref is None and current.state is ReplicationRunState.RUNNING:
        try:
            active_ref = observe_workspace_start(
                ledger, active_ref, proposal, output_root
            )
        except RuntimePause as observed:
            if observed.run_ref is None:
                raise ValueError(
                    "observed runtime pause lacks an exact ledger"
                ) from observed
            active_ref = observed.run_ref
            reason = observed.reason
        except RuntimeTerminal as terminal:
            if terminal.code is None:
                raise ValueError("terminal runtime stop lacks a typed reason")
            return ledger.exception(
                terminal.run_ref,
                ReplicationException(code=terminal.code, message=str(terminal)),
            )
    return checkpoint_pause(
        store,
        ledger,
        active_ref,
        proposal,
        output_root,
        reason=reason,
    )


def newest_runtime_ref(
    callback_ref: ArtifactRef, signal_ref: ArtifactRef | None
) -> ArtifactRef:
    """Select the newest sealed generation produced before containment completed."""
    if signal_ref is None:
        return callback_ref
    if signal_ref.artifact_id != callback_ref.artifact_id:
        raise ValueError("runtime signal reference targets another ledger")
    if signal_ref.artifact_version > callback_ref.artifact_version:
        return signal_ref
    return callback_ref

"""Immutable, resumable ledger for a bounded Tier-2 replication run."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from envresearch.models.artifact import ArtifactRef
from envresearch.replication._heartbeat_compaction import append_observation
from envresearch.replication._ledger_evidence import LedgerEvidenceMixin
from envresearch.replication._ledger_models import (
    OutputResult as _OutputResult,
)
from envresearch.replication._ledger_models import (
    ReplicationRun as _ReplicationRun,
)
from envresearch.replication._ledger_models import (
    ResourceObservation as _ResourceObservation,
)
from envresearch.replication._ledger_models import VerificationPublicationError
from envresearch.replication._runtime_owner import (
    RuntimeLaunchIdentity,
    RuntimeOwnership,
)
from envresearch.replication.contracts import ReplicationException, ReplicationRunState
from envresearch.storage.research_artifacts import ResearchArtifactStore

_LEDGER_PATH = Path("artifacts/replication/replication-ledger.yaml")
OutputResult = _OutputResult
ReplicationRun = _ReplicationRun
ResourceObservation = _ResourceObservation


class ReplicationLedger(LedgerEvidenceMixin):
    """Append-only state transitions backed by sealed authoritative artifacts."""

    def __init__(
        self,
        store: ResearchArtifactStore,
        *,
        max_growth_bytes: int = 0,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        if max_growth_bytes < 0:
            raise ValueError("max growth bytes must be nonnegative")
        self._store = store
        self._max_growth_bytes = max_growth_bytes
        self._failure_injector = failure_injector

    def start(
        self,
        approved_intake_ref: ArtifactRef,
        acquired_inventory_ref: ArtifactRef,
        runtime_ref: ArtifactRef,
        attempt_ref: ArtifactRef,
        output_root: str,
    ) -> ArtifactRef:
        """Record pending admission then make its initial immutable run current."""
        with self._locked():
            self._resolve_admitted_inputs(
                approved_intake_ref, acquired_inventory_ref, runtime_ref
            )
            self._require_attempt(approved_intake_ref, attempt_ref, output_root)
            self._require_acquired_binds_approved(
                acquired_inventory_ref, approved_intake_ref
            )
            declared = self._declared_outputs(approved_intake_ref)
            if self._exists(_LEDGER_PATH):
                current = self._current()
                if (
                    current.payload.approved_intake_ref != approved_intake_ref
                    or current.payload.acquired_inventory_ref != acquired_inventory_ref
                    or current.payload.runtime_ref != runtime_ref
                    or current.payload.attempt_ref != attempt_ref
                    or current.payload.output_root != output_root
                ):
                    raise ValueError(
                        "existing replication ledger has different immutable inputs"
                    )
                if current.payload.state is ReplicationRunState.PENDING:
                    return self._supersede(current, state=ReplicationRunState.RUNNING)
                return self._reference(current)
            pending = ReplicationRun(
                attempt_ref=attempt_ref,
                output_root=output_root,
                approved_intake_ref=approved_intake_ref,
                acquired_inventory_ref=acquired_inventory_ref,
                runtime_ref=runtime_ref,
                declared_outputs=declared,
                max_growth_bytes=self._max_growth_bytes,
                state=ReplicationRunState.PENDING,
            )
            self._publish(pending, version=1)
            return self._supersede(self._current(), state=ReplicationRunState.RUNNING)

    def heartbeat(
        self, run_ref: ArtifactRef, observation: ResourceObservation
    ) -> ArtifactRef:
        """Record a resource heartbeat; elapsed time alone never changes state."""
        with self._locked():
            return self._heartbeat_unlocked(run_ref, observation)

    def observe_progress(
        self, run_ref: ArtifactRef, observation: ResourceObservation
    ) -> ArtifactRef:
        """Append in-flight evidence while deferring state until cleanup finishes."""
        with self._locked():
            current = self._require_current(run_ref, {ReplicationRunState.RUNNING})
            return self._supersede(
                current,
                **append_observation(current.payload, observation),
                state=ReplicationRunState.RUNNING,
            )

    def claim_runtime_owner(
        self, run_ref: ArtifactRef, owner: RuntimeOwnership
    ) -> ArtifactRef:
        """Upgrade one prepared launch to its exact active runtime owner."""
        with self._locked():
            current = self._require_current(run_ref, {ReplicationRunState.RUNNING})
            launch = current.payload.runtime_launch
            if launch is None or not owner.extends(launch):
                raise ValueError("runtime owner differs from its prepared launch")
            if current.payload.runtime_owner is not None:
                if current.payload.runtime_owner == owner:
                    return run_ref
                raise ValueError("replication run already has another runtime owner")
            return self._supersede(current, runtime_owner=owner)

    def prepare_runtime_launch(
        self, run_ref: ArtifactRef, launch: RuntimeLaunchIdentity
    ) -> ArtifactRef:
        """Seal an unpredictable launch identity before spawning its client."""
        with self._locked():
            current = self._require_current(run_ref, {ReplicationRunState.RUNNING})
            if current.payload.runtime_launch is not None:
                if current.payload.runtime_launch == launch:
                    return run_ref
                raise ValueError("replication run already has another runtime launch")
            if current.payload.runtime_owner is not None:
                raise ValueError("runtime owner exists without a prepared launch")
            return self._supersede(current, runtime_launch=launch)

    def release_runtime_owner(self, run_ref: ArtifactRef) -> ArtifactRef:
        """Clear ownership only after process-group and container absence is proven."""
        with self._locked():
            current = self._require_current(run_ref, {ReplicationRunState.RUNNING})
            if (
                current.payload.runtime_owner is None
                and current.payload.runtime_launch is None
            ):
                return run_ref
            return self._supersede(current, runtime_launch=None, runtime_owner=None)

    def _heartbeat_unlocked(
        self, run_ref: ArtifactRef, observation: ResourceObservation
    ) -> ArtifactRef:
        current = self._require_current(run_ref, {ReplicationRunState.RUNNING})
        proposal = self._proposal_from_approved(current.payload.approved_intake_ref)
        if observation.memory_bytes > proposal.budget.max_memory_bytes:
            return self._supersede(
                current,
                state=ReplicationRunState.EXCEPTION,
                **append_observation(current.payload, observation),
                exception=ReplicationException(
                    code="memory-exhaustion", message="observed memory exceeds budget"
                ),
            )
        if observation.storage_bytes > proposal.budget.max_storage_bytes:
            return self._supersede(
                current,
                state=ReplicationRunState.EXCEPTION,
                **append_observation(current.payload, observation),
                exception=ReplicationException(
                    code="storage-exhaustion", message="observed storage exceeds budget"
                ),
            )
        if current.payload.observations:
            previous = current.payload.observations[-1]
            if observation.heartbeat_at - previous.heartbeat_at > timedelta(
                seconds=proposal.budget.inactivity_seconds
            ):
                return self._supersede(
                    current,
                    state=ReplicationRunState.PAUSED,
                    **append_observation(current.payload, observation),
                    exception=ReplicationException(
                        code="inactivity",
                        message="heartbeat gap exceeds approved limit",
                    ),
                )
            if (
                current.payload.max_growth_bytes
                and observation.storage_bytes - previous.storage_bytes
                > current.payload.max_growth_bytes
            ):
                return self._supersede(
                    current,
                    state=ReplicationRunState.PAUSED,
                    **append_observation(current.payload, observation),
                    exception=ReplicationException(
                        code="unexpected-growth", message="storage growth exceeds limit"
                    ),
                )
        return self._supersede(
            current,
            **append_observation(current.payload, observation),
            state=ReplicationRunState.RUNNING,
        )

    def pause(
        self,
        run_ref: ArtifactRef,
        *,
        reason: str,
        evidence_refs: tuple[ArtifactRef, ...] = (),
    ) -> ArtifactRef:
        """Pause a running run with typed evidence for later human resumption."""
        if reason not in {
            "inactivity",
            "unexpected-growth",
            "emergency-stop",
            "interrupted-owner",
        }:
            raise ValueError("pause reason is not an allowed typed exception")
        with self._locked():
            current = self._require_current(
                run_ref, {ReplicationRunState.RUNNING, ReplicationRunState.PAUSED}
            )
            if current.payload.runtime_owner is not None:
                raise ValueError("active runtime owner must be contained before pause")
            if current.payload.state is ReplicationRunState.PAUSED and (
                current.payload.exception is None
                or current.payload.exception.code != reason
            ):
                raise ValueError("paused run reason differs from checkpoint reason")
            return self._supersede(
                current,
                state=ReplicationRunState.PAUSED,
                exception=ReplicationException(
                    code=reason, message=reason, evidence_refs=evidence_refs
                ),
            )

    def resume(
        self,
        run_ref: ArtifactRef,
        acquired_inventory_ref: ArtifactRef,
        runtime_ref: ArtifactRef,
    ) -> ArtifactRef:
        """Resume only the exact admitted archive and runtime identity."""
        with self._locked():
            current = self._require_current(run_ref, {ReplicationRunState.PAUSED})
            if current.payload.acquired_inventory_ref != acquired_inventory_ref:
                raise ValueError("acquired inventory reference differs from paused run")
            if current.payload.runtime_ref != runtime_ref:
                raise ValueError("runtime reference differs from paused run")
            return self._supersede(
                current, state=ReplicationRunState.RUNNING, exception=None
            )

    def complete(
        self,
        run_ref: ArtifactRef,
        *,
        author_outputs: tuple[OutputResult, ...],
        derived_ref: ArtifactRef,
        derived_log_ref: ArtifactRef,
    ) -> ArtifactRef:
        """Seal execution evidence while leaving independent verification pending."""
        with self._locked():
            current = self._require_current(run_ref, {ReplicationRunState.RUNNING})
            if current.payload.runtime_owner is not None:
                raise ValueError(
                    "active runtime owner must be contained before completion"
                )
            expected_outputs = self._proposal_from_approved(
                current.payload.approved_intake_ref
            ).expected_outputs
            self._require_declared_outputs(current, expected_outputs, author_outputs)
            self._require_derived(current, author_outputs, derived_ref, derived_log_ref)
            return self._supersede(
                current,
                author_outputs=author_outputs,
                derived_ref=derived_ref,
                derived_log_ref=derived_log_ref,
                exception=None,
                state=ReplicationRunState.RUNNING,
            )

    def publish_verification(
        self, run_ref: ArtifactRef, report: VerificationReport
    ) -> ArtifactRef:
        """Promote only an exact, sealed, zero-finding independent report."""
        from envresearch.replication._evidence_support import (
            persist_verification_report,
        )
        from envresearch.replication.verify import (
            ReplicationVerifier,
            VerificationReport,
        )

        if not isinstance(report, VerificationReport):
            raise TypeError("verification report must be independently sealed")
        with self._locked():
            current = self._require_current(run_ref, {ReplicationRunState.RUNNING})
            if (
                not current.payload.author_outputs
                or current.payload.derived_ref is None
            ):
                raise ValueError("replication execution evidence is incomplete")
            independent = ReplicationVerifier(self._store).verify(run_ref)
            verification_ref = persist_verification_report(self._store, independent)
            if report.artifact.payload != independent.artifact.payload:
                raise VerificationPublicationError(
                    "report differs from independent verifier result", verification_ref
                )
            expected_refs = self._verification_refs(current)
            if report.run_ref != run_ref or report.verified_refs != expected_refs:
                raise VerificationPublicationError(
                    "verification report does not bind exact current refs",
                    verification_ref,
                )
            if not report.passed:
                raise VerificationPublicationError(
                    "verification report has unresolved findings", verification_ref
                )
            return self._supersede(
                current,
                state=ReplicationRunState.PASSED,
                verification_ref=verification_ref,
                exception=None,
            )

    def exception(
        self, run_ref: ArtifactRef, exception: ReplicationException
    ) -> ArtifactRef:
        """Persist the first typed failure and never replace terminal evidence."""
        with self._locked():
            current = self._current()
            if self._reference(current) != run_ref:
                raise ValueError("replication run reference is not current")
            if current.payload.state is ReplicationRunState.EXCEPTION:
                return run_ref
            if current.payload.state is ReplicationRunState.PASSED:
                raise ValueError("passed replication run cannot become an exception")
            return self._supersede(
                current,
                state=ReplicationRunState.EXCEPTION,
                exception=exception,
            )

    def load_current(self, run_ref: ArtifactRef) -> ArtifactRef:
        """Authenticate and return only the exact currently published generation."""
        self.read_current(run_ref)
        return run_ref

    def read_current(
        self, run_ref: ArtifactRef | None = None
    ) -> tuple[ArtifactRef, ReplicationRun]:
        """Recover and authenticate the exact public ledger generation."""
        with self._locked():
            current = self._current()
            reference = self._reference(current)
            if run_ref is not None and reference != run_ref:
                raise ValueError("replication run reference is not current")
            self._require_public_copies(current)
            self._require_terminal_evidence(current)
            return reference, current.payload


if TYPE_CHECKING:
    from envresearch.replication.verify import VerificationReport

"""Private container-plan boundary helpers for the replication controller."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from envresearch.models.artifact import ArtifactRef
from envresearch.replication._service_models import (
    DidReplayConfiguration,
    ReplicationFault,
)
from envresearch.replication.container import (
    ContainerCleanupError,
    ContainerEngine,
    ContainerInactivityError,
    ContainerPlan,
    ContainerResult,
    ProgressCallback,
    RuntimeStartedCallback,
    RuntimeStoppedCallback,
)
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ReplicationRunState,
    Tier2IntakeProposal,
)
from envresearch.replication.did_r import DidEventStudySpec, RDidAdapter
from envresearch.replication.ledger import (
    ReplicationLedger,
    ResourceObservation,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENGINE_IDENTITY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class RuntimePause(RuntimeError):
    """A typed resumable runtime stop requested by the execution boundary."""

    def __init__(
        self,
        message: str,
        *,
        run_ref: ArtifactRef | None = None,
        reason: str = "inactivity",
    ) -> None:
        super().__init__(message)
        self.run_ref = run_ref
        self.reason = reason


class RuntimeTerminal(RuntimeError):
    """A resource stop requested or already published a terminal generation."""

    def __init__(
        self,
        run_ref: ArtifactRef,
        *,
        code: str | None = None,
        message: str = "runtime heartbeat produced a terminal ledger",
    ) -> None:
        super().__init__(message)
        self.run_ref = run_ref
        self.code = code


def build_derived_plan(
    adapter: RDidAdapter,
    inventory: AcquiredPackageInventory,
    proposal: Tier2IntakeProposal,
    input_root: Path,
    output_root: Path,
    config: DidReplayConfiguration,
) -> ContainerPlan:
    try:
        return adapter.derived_plan(
            inventory,
            DidEventStudySpec(
                data_path=config.data_path,
                unit_column=config.unit_column,
                time_column=config.time_column,
                treatment_column=config.treatment_column,
                cohort_column=config.cohort_column,
                outcome_column=config.outcome_column,
                reference_period=config.reference_period,
                input_root=input_root,
                output_root=output_root,
                budget=proposal.budget,
            ),
        )
    except ValueError as error:
        raise ReplicationFault("PROHIBITED_EXECUTION_PLAN", str(error)) from error


def run_engine(
    engine: ContainerEngine,
    plan: ContainerPlan,
    expected_engine: str,
    on_progress: ProgressCallback | None = None,
    on_started: RuntimeStartedCallback | None = None,
    on_stopped: RuntimeStoppedCallback | None = None,
) -> ContainerResult:
    if type(expected_engine) is not str or not _ENGINE_IDENTITY.fullmatch(
        expected_engine
    ):
        raise ReplicationFault(
            "CONTAINER_EVIDENCE_INVALID", "preflight engine identity is invalid"
        )
    try:
        result = engine.run(
            plan,
            on_progress=on_progress,
            on_started=on_started,
            on_stopped=on_stopped,
        )
    except (ReplicationFault, RuntimePause, RuntimeTerminal):
        raise
    except (ContainerInactivityError, TimeoutError) as error:
        raise RuntimePause(str(error)) from error
    except ContainerCleanupError as error:
        raise ReplicationFault("CONTAINMENT_CLEANUP_FAILED", str(error)) from error
    except ValueError as error:
        raise ReplicationFault("PROHIBITED_EXECUTION_PLAN", str(error)) from error
    except (OSError, RuntimeError) as error:
        exhausted = "exceeded approved" in str(error)
        code = "RESOURCE_EXHAUSTION" if exhausted else "CONTAINER_EXECUTION_FAILED"
        raise ReplicationFault(code, str(error)) from error
    if type(result) is not ContainerResult:
        raise ReplicationFault(
            "CONTAINER_EVIDENCE_INVALID", "container result type is invalid"
        )
    _require_container_result(result, plan, expected_engine)
    return result


def _require_container_result(
    result: ContainerResult, plan: ContainerPlan, expected_engine: str
) -> None:
    if (
        type(result.engine) is not str
        or not _ENGINE_IDENTITY.fullmatch(result.engine)
        or result.engine != expected_engine
    ):
        raise ReplicationFault(
            "CONTAINER_EVIDENCE_INVALID", "container engine identity is invalid"
        )
    if type(result.image_digest) is not str or result.image_digest != plan.image_digest:
        raise ReplicationFault(
            "CONTAINER_EVIDENCE_INVALID", "container image digest is invalid"
        )
    if type(result.exit_status) is not int:
        raise ReplicationFault(
            "CONTAINER_EVIDENCE_INVALID", "container exit status is invalid"
        )
    digests = (result.stdout_sha256, result.stderr_sha256)
    if any(type(value) is not str or not _SHA256.fullmatch(value) for value in digests):
        raise ReplicationFault(
            "CONTAINER_EVIDENCE_INVALID", "container log digest is not canonical"
        )
    if (
        type(result.stdout_truncated) is not bool
        or type(result.stderr_truncated) is not bool
    ):
        raise ReplicationFault(
            "CONTAINER_EVIDENCE_INVALID", "container truncation flags are invalid"
        )
    timestamps = (result.started_at, result.finished_at)
    if (
        any(
            not isinstance(value, datetime)
            or value.utcoffset() != timedelta(0)
            or value.tzname() != "UTC"
            for value in timestamps
        )
        or result.finished_at < result.started_at
    ):
        raise ReplicationFault(
            "CONTAINER_EVIDENCE_INVALID", "container timestamps are invalid"
        )
    resources = (result.peak_memory_bytes, result.storage_bytes)
    if result.resource_status not in {"measured", "unknown"}:
        raise ReplicationFault(
            "CONTAINER_EVIDENCE_INVALID", "container resources are invalid"
        )
    if result.resource_status != "measured":
        raise ReplicationFault(
            "RESOURCE_EXHAUSTION", "container resource measurement is unknown"
        )
    if (
        any(type(value) is not int or value < 0 for value in resources)
        or type(result.oom_killed) is not bool
    ):
        raise ReplicationFault(
            "CONTAINER_EVIDENCE_INVALID", "container resources are invalid"
        )
    if result.oom_killed:
        raise ReplicationFault(
            "RESOURCE_EXHAUSTION", "container runtime reported an OOM kill"
        )


def require_success(result: ContainerResult, plan: ContainerPlan) -> tuple[int, int]:
    if result.image_digest != plan.image_digest or result.exit_status != 0:
        raise ReplicationFault(
            "CONTAINER_EXECUTION_FAILED", "container execution failed"
        )
    if result.peak_memory_bytes is None or result.storage_bytes is None:
        raise ReplicationFault(
            "RESOURCE_EXHAUSTION", "container resource measurement is unknown"
        )
    if (
        result.peak_memory_bytes > plan.budget.max_memory_bytes
        or result.storage_bytes > plan.budget.max_storage_bytes
    ):
        raise ReplicationFault(
            "RESOURCE_EXHAUSTION", "container exceeded resource budget"
        )
    return result.peak_memory_bytes, result.storage_bytes


def record_heartbeat(
    ledger: ReplicationLedger,
    run_ref: ArtifactRef,
    *,
    at: datetime,
    memory_bytes: int,
    storage_bytes: int,
) -> ArtifactRef:
    """Persist one ordered exact resource observation in the sealed ledger."""
    run = ledger.read_current(run_ref)[1]
    previous = run.observations[-1] if run.observations else None
    if previous is not None and at < previous.heartbeat_at:
        raise ReplicationFault(
            "CONTAINER_EVIDENCE_INVALID", "runtime heartbeat timestamps are unordered"
        )
    origin = (
        run.observations[0].heartbeat_at
        - timedelta(seconds=run.observations[0].elapsed_seconds)
        if run.observations
        else at
    )
    updated = ledger.heartbeat(
        run_ref,
        ResourceObservation(
            elapsed_seconds=max(0, int((at - origin).total_seconds())),
            storage_bytes=storage_bytes,
            memory_bytes=memory_bytes,
            heartbeat_at=at.astimezone(UTC),
        ),
    )
    run = ledger.read_current(updated)[1]
    if run.state is ReplicationRunState.PAUSED:
        if run.exception is None:
            raise ValueError("paused heartbeat lacks a typed reason")
        raise RuntimePause(
            run.exception.message,
            run_ref=updated,
            reason=run.exception.code,
        )
    if run.state is ReplicationRunState.EXCEPTION:
        raise RuntimeTerminal(updated)
    return updated


def record_progress_heartbeat(
    ledger: ReplicationLedger,
    run_ref: ArtifactRef,
    proposal: Tier2IntakeProposal,
    *,
    at: datetime,
    memory_bytes: int,
    storage_bytes: int,
) -> ArtifactRef:
    """Persist RUNNING progress and request state change only after containment."""
    run = ledger.read_current(run_ref)[1]
    previous = run.observations[-1] if run.observations else None
    if previous is not None and at < previous.heartbeat_at:
        raise ReplicationFault(
            "CONTAINER_EVIDENCE_INVALID", "runtime heartbeat timestamps are unordered"
        )
    origin = (
        run.observations[0].heartbeat_at
        - timedelta(seconds=run.observations[0].elapsed_seconds)
        if run.observations
        else at
    )
    updated = ledger.observe_progress(
        run_ref,
        ResourceObservation(
            elapsed_seconds=max(0, int((at - origin).total_seconds())),
            storage_bytes=storage_bytes,
            memory_bytes=memory_bytes,
            heartbeat_at=at.astimezone(UTC),
        ),
    )
    if memory_bytes > proposal.budget.max_memory_bytes:
        raise RuntimeTerminal(
            updated,
            code="memory-exhaustion",
            message="observed memory exceeds budget",
        )
    if storage_bytes > proposal.budget.max_storage_bytes:
        raise RuntimeTerminal(
            updated,
            code="storage-exhaustion",
            message="observed storage exceeds budget",
        )
    if previous is not None:
        if (
            at - previous.heartbeat_at
        ).total_seconds() > proposal.budget.inactivity_seconds:
            raise RuntimePause("inactivity", run_ref=updated, reason="inactivity")
        if (
            run.max_growth_bytes
            and storage_bytes - previous.storage_bytes > run.max_growth_bytes
        ):
            raise RuntimePause(
                "unexpected-growth",
                run_ref=updated,
                reason="unexpected-growth",
            )
    return updated


def workspace_bytes(root: Path) -> int:
    """Measure the current confined workspace without following symlink files."""
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ReplicationFault(
                "OUTPUT_NAMESPACE_INVALID", "attempt workspace contains a symlink"
            )
        if path.is_file():
            total += path.stat().st_size
    return total

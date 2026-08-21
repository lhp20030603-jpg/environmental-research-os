"""Runtime, heartbeat, checkpoint, and run-root controller regressions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from replication_runtime_fixtures import (
    BlockingGrowthEngine,
    CleanupFailureEngine,
    CleanupOrderingEngine,
    GrowthEngine,
    HardBudgetInactiveEngine,
    InactiveEngine,
    InvalidRuntimeEngine,
    OwnerCrashEngine,
    ProcessLoss,
    SimulatedCrash,
)
from replication_service_fixtures import (
    FakeEngine,
    ServiceCase,
    approve,
    replay_configuration,
)

from envresearch.replication import _service_runtime as service_runtime
from envresearch.replication.contracts import ReplicationRunState
from envresearch.replication.intake import Tier2IntakeService
from envresearch.replication.ledger import ReplicationLedger
from envresearch.replication.service import ReplicationService


@pytest.mark.parametrize("publication_boundary", ["history", "current", "report"])
def test_pending_publication_crash_recovers_through_ledger_start(
    service_case: ServiceCase,
    publication_boundary: str,
) -> None:
    approved = approve(service_case)

    def crash(boundary: str) -> None:
        if boundary == publication_boundary:
            raise SimulatedCrash(boundary)

    service_case.service.ledger = ReplicationLedger(
        service_case.store, failure_injector=crash
    )
    with pytest.raises(SimulatedCrash, match=publication_boundary):
        service_case.service.run(approved)

    service_case.service.ledger = ReplicationLedger(service_case.store)
    recovered = service_case.service.run(approved)

    assert recovered.state is ReplicationRunState.PASSED
    assert service_case.fetcher.calls == 1


def test_ownerless_running_crash_becomes_exact_resumable_checkpoint(
    service_case: ServiceCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = approve(service_case)
    original = service_case.service._execute

    def crash_after_start(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise SimulatedCrash("after-start")

    monkeypatch.setattr(service_case.service, "_execute", crash_after_start)
    with pytest.raises(SimulatedCrash, match="after-start"):
        service_case.service.run(approved)
    monkeypatch.setattr(service_case.service, "_execute", original)

    paused = service_case.service.run(approved)

    assert paused.state is ReplicationRunState.PAUSED
    assert paused.exception is not None
    assert paused.exception.code == "interrupted-owner"
    assert len(paused.exception.evidence_refs) == 1
    resumed = service_case.service.resume(paused.run_ref)
    assert resumed.state is ReplicationRunState.PASSED
    assert service_case.fetcher.calls == 1


def test_runtime_owner_is_contained_before_interrupted_checkpoint(
    service_case: ServiceCase,
) -> None:
    approved = approve(service_case)
    engine = OwnerCrashEngine()
    service_case.service.engine = engine

    with pytest.raises(ProcessLoss):
        service_case.service.run(approved)

    _, interrupted = service_case.service.ledger.read_current()
    assert interrupted.runtime_owner is not None
    paused = service_case.service.run(approved)

    assert engine.contained
    assert engine.contained[0][0] == interrupted.runtime_owner
    assert paused.state is ReplicationRunState.PAUSED
    assert (
        service_case.service.ledger.read_current(paused.run_ref)[1].runtime_owner
        is None
    )


def test_runtime_observation_is_validated_before_persistence(
    service_case: ServiceCase,
) -> None:
    service_case.service.engine = InvalidRuntimeEngine()

    report = service_case.service.run(approve(service_case))

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "RUNTIME_EVIDENCE_INVALID"
    assert not tuple(
        (service_case.store.root / "artifacts/replication/runtime").glob("*.json")
    )


def test_green_orchestration_persists_resource_heartbeats(
    service_case: ServiceCase,
) -> None:
    report = service_case.service.run(approve(service_case))

    _, run = service_case.service.ledger.read_current(report.run_ref)
    assert len(run.observations) >= 4
    assert all(item.memory_bytes >= 0 for item in run.observations)
    assert all(item.storage_bytes >= 0 for item in run.observations)
    assert tuple(item.heartbeat_at for item in run.observations) == tuple(
        sorted(item.heartbeat_at for item in run.observations)
    )


def test_blocking_runtime_growth_pauses_from_inflight_heartbeat(
    service_case: ServiceCase,
) -> None:
    service = ReplicationService(
        service_case.store,
        service_case.service.intake,
        BlockingGrowthEngine(),
        replay_configuration(),
        max_growth_bytes=2,
    )

    paused = service.run(approve(service_case))

    assert paused.state is ReplicationRunState.PAUSED
    assert paused.exception is not None
    assert paused.exception.code == "unexpected-growth"
    _, run = service.ledger.read_current(paused.run_ref)
    assert run.observations[-1].memory_bytes == 3
    assert run.author_outputs == ()


def test_inactivity_with_hard_storage_exhaustion_returns_terminal_report(
    service_case: ServiceCase,
) -> None:
    service_case.service.engine = HardBudgetInactiveEngine()

    report = service_case.service.run(approve(service_case))

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "storage-exhaustion"
    assert service_case.service.ledger.read_current(report.run_ref)[0] == report.run_ref


def test_checkpoint_failure_never_publishes_pause_without_evidence(
    service_case: ServiceCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_case.service.engine = InactiveEngine()
    started = datetime(2026, 8, 10, tzinfo=UTC)
    moments = iter((started, started, started + timedelta(seconds=11)))
    monkeypatch.setattr(
        service_runtime,
        "datetime",
        SimpleNamespace(now=lambda timezone: next(moments)),
    )

    def fail_checkpoint(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("checkpoint disk full")

    monkeypatch.setattr(
        service_runtime, "persist_workspace_checkpoint", fail_checkpoint
    )

    report = service_case.service.run(approve(service_case))

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "PERSISTENCE_FAILURE"
    assert service_case.service.ledger.read_current(report.run_ref)[0] == report.run_ref


def test_container_cleanup_failure_is_a_durable_typed_exception(
    service_case: ServiceCase,
) -> None:
    service_case.service.engine = CleanupFailureEngine()
    approved = approve(service_case)

    report = service_case.service.run(approved)

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "CONTAINMENT_CLEANUP_FAILED"
    assert service_case.service.run(approved) == report


def test_resource_state_is_published_only_after_containment_cleanup(
    service_case: ServiceCase,
) -> None:
    engine = CleanupOrderingEngine()
    service_case.service.engine = engine
    engine.state_probe = lambda: service_case.service.ledger.read_current()[1].state

    report = service_case.service.run(approve(service_case))

    assert engine.cleaned
    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "storage-exhaustion"


def test_inactivity_pauses_and_restart_resumes_exact_workspace(
    service_case: ServiceCase,
) -> None:
    approved = approve(service_case)
    service_case.service.engine = InactiveEngine()

    paused = service_case.service.run(approved)

    assert paused.state is ReplicationRunState.PAUSED
    _, run = service_case.service.ledger.read_current(paused.run_ref)
    root = service_case.store.root / run.output_root
    assert (root / "author-reproduction/partial.checkpoint").is_file()

    restarted = ReplicationService(
        service_case.store,
        Tier2IntakeService(service_case.store, fetcher=service_case.fetcher),
        FakeEngine(),
        replay_configuration(),
    )
    resumed = restarted.resume(paused.run_ref)

    assert resumed.state is ReplicationRunState.PASSED
    assert service_case.fetcher.calls == 1


def test_unexpected_storage_growth_pauses_with_exact_observation(
    service_case: ServiceCase,
) -> None:
    service = ReplicationService(
        service_case.store,
        service_case.service.intake,
        GrowthEngine(),
        replay_configuration(),
        max_growth_bytes=1,
    )

    paused = service.run(approve(service_case))

    assert paused.state is ReplicationRunState.PAUSED
    assert paused.exception is not None
    assert paused.exception.code == "unexpected-growth"
    _, run = service.ledger.read_current(paused.run_ref)
    root = service_case.store.root / run.output_root
    observed_bytes = sum(
        path.stat().st_size for path in root.rglob("*") if path.is_file()
    )
    assert run.observations[-1].storage_bytes == max(8, observed_bytes)


def test_host_workspace_growth_pauses_when_engine_underreports_storage(
    service_case: ServiceCase,
) -> None:
    service = ReplicationService(
        service_case.store,
        service_case.service.intake,
        FakeEngine(),
        replay_configuration(),
        max_growth_bytes=2,
    )

    paused = service.run(approve(service_case))

    assert paused.state is ReplicationRunState.PAUSED
    assert paused.exception is not None
    assert paused.exception.code == "unexpected-growth"
    _, run = service.ledger.read_current(paused.run_ref)
    assert run.observations[-1].storage_bytes > 2


def test_resume_rejects_a_file_planted_after_authenticated_pause(
    service_case: ServiceCase,
) -> None:
    approved = approve(service_case)
    service_case.service.engine = InactiveEngine()
    paused = service_case.service.run(approved)
    _, run = service_case.service.ledger.read_current(paused.run_ref)
    planted = service_case.store.root / run.output_root / "planted.csv"
    planted.write_text("attacker", encoding="utf-8")
    service_case.service.engine = FakeEngine()

    report = service_case.service.resume(paused.run_ref)

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "RESUME_EVIDENCE_INVALID"
    assert service_case.fetcher.calls == 1


def test_attempt_and_acquired_roots_are_bound_to_supplied_run_root(
    service_case: ServiceCase,
) -> None:
    report = service_case.service.run(approve(service_case))

    assert report.state is ReplicationRunState.PASSED
    engine = service_case.service.engine
    assert isinstance(engine, FakeEngine)
    for plan in engine.plans:
        assert service_case.store.root in plan.input_root.parents
        assert service_case.store.root in plan.output_root.parents

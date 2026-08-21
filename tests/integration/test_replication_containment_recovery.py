"""Durable containment failure and runtime-authority recovery regressions."""

from __future__ import annotations

import pytest
from replication_runtime_fixtures import OwnerCrashEngine, ProcessLoss
from replication_service_fixtures import ServiceCase, approve

from envresearch.replication.contracts import ReplicationRunState


def test_owner_release_persistence_failure_is_readable_containment_exception(
    service_case: ServiceCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = approve(service_case)
    original = service_case.service.ledger.release_runtime_owner
    failed = False

    def fail_once(run_ref):  # type: ignore[no-untyped-def]
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("release ledger write failed")
        return original(run_ref)

    monkeypatch.setattr(service_case.service.ledger, "release_runtime_owner", fail_once)

    report = service_case.service.run(approved)

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "CONTAINMENT_CLEANUP_FAILED"
    _, run = service_case.service.ledger.read_current(report.run_ref)
    assert run.runtime_owner is not None


def test_recovery_rejects_runtime_authority_drift_before_containment(
    service_case: ServiceCase,
) -> None:
    approved = approve(service_case)
    first_engine = OwnerCrashEngine()
    service_case.service.engine = first_engine
    with pytest.raises(ProcessLoss):
        service_case.service.run(approved)

    class DriftedEngine(OwnerCrashEngine):
        executable_sha256 = "f" * 64
        endpoint = "unix:///tmp/drifted-runtime.sock"

    drifted = DriftedEngine()
    service_case.service.engine = drifted
    report = service_case.service.run(approved)

    assert drifted.contained == []
    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "CONTAINMENT_CLEANUP_FAILED"
    _, run = service_case.service.ledger.read_current(report.run_ref)
    assert run.runtime_owner is not None

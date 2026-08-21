"""PASSED status independently reopens raw and sealed run evidence."""

from __future__ import annotations

import json

import pytest
from replication_service_fixtures import FakeEngine, ServiceCase, approve

from envresearch.replication._service_support import read_inventory


def test_passed_status_rejects_a_missing_author_output_artifact(
    service_case: ServiceCase,
) -> None:
    report = service_case.service.run(approve(service_case))
    _, run = service_case.service.ledger.read_current(report.run_ref)
    reference = run.author_outputs[0].artifact_ref
    path = service_case.store.root / (
        f"artifacts/replication/outputs/{reference.content_hash}.json"
    )
    path.unlink()

    with pytest.raises(ValueError, match="PASSED evidence"):
        service_case.service.status(report.run_ref)


def test_passed_status_persists_its_failed_independent_verification(
    service_case: ServiceCase,
) -> None:
    report = service_case.service.run(approve(service_case))
    _, run = service_case.service.ledger.read_current(report.run_ref)
    reference = run.author_outputs[0].artifact_ref
    output = service_case.store.root / (
        f"artifacts/replication/outputs/{reference.content_hash}.json"
    )
    verification_root = service_case.store.root / "artifacts/replication/verifications"
    before = set(verification_root.glob("*.json"))
    output.unlink()

    with pytest.raises(ValueError, match="PASSED evidence"):
        service_case.service.status(report.run_ref)

    added = set(verification_root.glob("*.json")) - before
    assert len(added) == 1
    persisted = json.loads(added.pop().read_text(encoding="utf-8"))
    assert persisted["payload"]["findings"]


def test_passed_status_rehashes_immutable_raw_author_output(
    service_case: ServiceCase,
) -> None:
    report = service_case.service.run(approve(service_case))
    engine = service_case.service.engine
    assert isinstance(engine, FakeEngine)
    raw_output = engine.plans[0].output_root / "output/results.csv"
    raw_output.write_text("estimate\n9.9\n", encoding="utf-8")

    with pytest.raises(ValueError, match="PASSED evidence"):
        service_case.service.status(report.run_ref)


def test_passed_status_rehashes_materialized_expected_input(
    service_case: ServiceCase,
) -> None:
    report = service_case.service.run(approve(service_case))
    engine = service_case.service.engine
    assert isinstance(engine, FakeEngine)
    expected = engine.plans[0].input_root / "expected/results.csv"
    expected.write_text("estimate\n9.9\n", encoding="utf-8")

    with pytest.raises(ValueError, match="PASSED evidence"):
        service_case.service.status(report.run_ref)


def test_passed_status_rehashes_the_acquired_archive(service_case: ServiceCase) -> None:
    report = service_case.service.run(approve(service_case))
    _, run = service_case.service.ledger.read_current(report.run_ref)
    inventory = read_inventory(service_case.store, run.acquired_inventory_ref)
    archive = service_case.store.root / (
        f"artifacts/replication/raw/{inventory.archive_sha256}.tar.gz"
    )
    archive.unlink()

    with pytest.raises(ValueError, match="PASSED evidence"):
        service_case.service.status(report.run_ref)

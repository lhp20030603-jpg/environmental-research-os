"""Adversarial tests for confined, measured connector acquisitions."""

from __future__ import annotations

import hashlib
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest

from envresearch.connectors.contracts import ConnectorReceipt
from envresearch.connectors.gateway import ConditionalDataGateRequired, ConnectorGateway
from envresearch.connectors.usage_meter import TrustedUsageEvidence
from envresearch.models.evidence import AcquisitionBudget, DatasetCandidate


def _candidate(**changes: object) -> DatasetCandidate:
    values: dict[str, object] = {
        "dataset_id": "air-quality",
        "source": "https://example.invalid/air-quality",
        "public_access": True,
        "requires_credentials": False,
        "clear_license": True,
        "license": "CC-BY-4.0",
        "estimated_download_bytes": 1,
        "estimated_local_storage_bytes": 1,
        "estimated_api_calls": 0,
        "estimated_external_cost": Decimal(0),
        "estimated_elapsed_seconds": 1,
        "suitable_for_design": True,
        "suitability_reason": "Design-compatible public measurements.",
        "access_reason": "Public agency catalogue.",
    }
    values.update(changes)
    return DatasetCandidate.model_validate(values)


def _receipt(content: bytes, **changes: object) -> ConnectorReceipt:
    values: dict[str, object] = {
        "connector_id": "local",
        "connector_version": "1.0",
        "source": "https://example.invalid/air-quality",
        "acquired_at": datetime(2026, 8, 6, tzinfo=UTC),
        "license": "CC-BY-4.0",
        "bytes": len(content),
        "local_storage_bytes": len(content),
        "api_calls": 0,
        "external_cost": Decimal(0),
        "elapsed_seconds": 1,
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    values.update(changes)
    return ConnectorReceipt.model_validate(values)


def _budget(limit: int = 10) -> AcquisitionBudget:
    return AcquisitionBudget(
        max_download_bytes=limit,
        max_local_storage_bytes=limit,
        max_api_calls=1,
        max_external_cost=Decimal(0),
        max_elapsed_seconds=10,
    )


class WritingConnector:
    connector_id = "local"
    connector_version = "1.0"

    def __init__(self, content: bytes, claimed: ConnectorReceipt | None = None) -> None:
        self.content = content
        self.claimed = claimed or _receipt(content)
        self.calls = 0

    def inspect(self, source: str) -> DatasetCandidate:
        return _candidate(source=source)

    def acquire(self, candidate: DatasetCandidate, target: Path) -> ConnectorReceipt:
        self.calls += 1
        target.write_bytes(self.content)
        return self.claimed


def _trusted_zero_usage(*_: object) -> TrustedUsageEvidence:
    return TrustedUsageEvidence(api_calls=0, external_cost=Decimal(0))


class AbruptExitConnector(WritingConnector):
    """A child-process connector that dies after leaving partial staged bytes."""

    def acquire(self, candidate: DatasetCandidate, target: Path) -> ConnectorReceipt:
        target.write_bytes(b"partial")
        os._exit(17)


def _run_abrupt_acquisition(root: Path) -> None:
    ConnectorGateway(_budget(), acquisition_root=root).acquire(
        AbruptExitConnector(b"complete"),
        _candidate(),
        "abrupt-process",
        data_risk="public",
    )


def _gateway(tmp_path: Path, limit: int = 10) -> ConnectorGateway:
    return ConnectorGateway(
        _budget(limit),
        acquisition_root=tmp_path / "acquisitions",
        usage_evidence_provider=_trusted_zero_usage,
    )


def _assert_durable_quarantine(root: Path) -> None:
    records = list(root.rglob("*.json"))
    assert records, "a fail-closed acquisition must leave an audit record"
    assert any('"status":"quarantined"' in item.read_text() for item in records)


def test_gateway_measures_1000_bytes_despite_one_byte_receipt(tmp_path: Path) -> None:
    content = b"x" * 1_000
    connector = WritingConnector(
        content,
        _receipt(content, bytes=1, local_storage_bytes=1, sha256="a" * 64),
    )
    gateway = _gateway(tmp_path, limit=10)

    with pytest.raises(ConditionalDataGateRequired) as caught:
        gateway.acquire(connector, _candidate(), "lie-1000-vs-1", data_risk="public")

    assert caught.value.receipt is not None
    assert caught.value.receipt.bytes == 1_000
    assert "actual download bytes exceed budget" in caught.value.reasons
    assert (
        "connector receipt bytes do not match measured output" in caught.value.reasons
    )
    _assert_durable_quarantine(tmp_path / "acquisitions")


@pytest.mark.parametrize("failure", ["missing", "symlink", "hardlink"])
def test_gateway_rejects_missing_or_linked_output(tmp_path: Path, failure: str) -> None:
    content = b"safe"

    class UnsafeConnector(WritingConnector):
        def acquire(
            self, candidate: DatasetCandidate, target: Path
        ) -> ConnectorReceipt:
            target.parent.mkdir(parents=True, exist_ok=True)
            if failure == "missing":
                target.unlink()
            elif failure == "symlink":
                target.unlink()
                outside = tmp_path / "outside"
                outside.write_bytes(content)
                target.symlink_to(outside)
            elif failure == "hardlink":
                target.unlink()
                outside = tmp_path / "outside"
                outside.write_bytes(content)
                os.link(outside, target)
            return _receipt(content)

    with pytest.raises(ConditionalDataGateRequired):
        _gateway(tmp_path).acquire(
            UnsafeConnector(content), _candidate(), failure, data_risk="public"
        )

    _assert_durable_quarantine(tmp_path / "acquisitions")


def test_gateway_hashes_pinned_inode_and_rejects_target_swap(tmp_path: Path) -> None:
    original = b"original"

    class SwappingConnector(WritingConnector):
        def acquire(
            self, candidate: DatasetCandidate, target: Path
        ) -> ConnectorReceipt:
            target.write_bytes(original)
            replacement = target.with_name("replacement")
            replacement.write_bytes(b"swapped")
            os.replace(replacement, target)
            return _receipt(original)

    with pytest.raises(ConditionalDataGateRequired) as caught:
        _gateway(tmp_path).acquire(
            SwappingConnector(original), _candidate(), "swap", data_risk="public"
        )

    assert "unsafe acquisition output" in caught.value.reasons[0]


def test_target_swap_after_measurement_cannot_publish_accepted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replacement in the publication window must fail before terminal state."""
    gateway = _gateway(tmp_path)
    original_publish = gateway.store.publish_state

    def swap_before_publish(*args: object, **kwargs: object) -> None:
        target = gateway.store.root / "outputs" / "local" / "publish-swap" / "payload"
        replacement = target.with_name("replacement")
        replacement.write_bytes(b"evil")
        os.replace(replacement, target)
        original_publish(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(gateway.store, "publish_state", swap_before_publish)

    with pytest.raises(ConditionalDataGateRequired, match="changed"):
        gateway.acquire(
            WritingConnector(b"safe"),
            _candidate(),
            "publish-swap",
            data_risk="public",
        )

    assert gateway.store.load_state("local", "publish-swap") is None


def test_partial_output_after_connector_death_is_audited_and_blocks_retry(
    tmp_path: Path,
) -> None:
    class DyingConnector(WritingConnector):
        def acquire(
            self, candidate: DatasetCandidate, target: Path
        ) -> ConnectorReceipt:
            self.calls += 1
            target.write_bytes(b"partial")
            raise RuntimeError("connector process died")

    connector = DyingConnector(b"complete")
    gateway = _gateway(tmp_path)
    with pytest.raises(RuntimeError, match="process died"):
        gateway.acquire(connector, _candidate(), "process-death", data_risk="public")
    with pytest.raises(ConditionalDataGateRequired, match="residue"):
        gateway.acquire(connector, _candidate(), "process-death", data_risk="public")

    assert connector.calls == 1
    _assert_durable_quarantine(tmp_path / "acquisitions")


def test_abrupt_process_death_releases_lock_and_residue_blocks_retry(
    tmp_path: Path,
) -> None:
    """OS process termination leaves a detectable partial target, not a stale lock."""
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("requires POSIX fork semantics")
    root = tmp_path / "acquisitions"
    process = multiprocessing.get_context("fork").Process(
        target=_run_abrupt_acquisition, args=(root,)
    )
    process.start()
    process.join(10)

    assert process.exitcode == 17
    with pytest.raises(ConditionalDataGateRequired, match="residue"):
        ConnectorGateway(_budget(), acquisition_root=root).acquire(
            WritingConnector(b"complete"),
            _candidate(),
            "abrupt-process",
            data_risk="public",
        )
    _assert_durable_quarantine(root)


def test_concurrent_identical_acquisitions_are_idempotent(tmp_path: Path) -> None:
    connector = WritingConnector(b"same")
    barrier = Barrier(2)

    def acquire() -> ConnectorReceipt | None:
        barrier.wait()
        return _gateway(tmp_path).acquire(
            connector, _candidate(), "same-request", data_risk="public"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: acquire(), range(2)))

    assert results[0] == results[1]
    assert connector.calls == 1


def test_reuse_reapplies_current_stricter_actual_budget(tmp_path: Path) -> None:
    """An accepted output cannot bypass a smaller budget in a later gateway."""
    content = b"x" * 20
    first = _gateway(tmp_path, limit=30).acquire(
        WritingConnector(content), _candidate(), "budget-reuse", data_risk="public"
    )
    connector = WritingConnector(content)

    with pytest.raises(ConditionalDataGateRequired) as caught:
        _gateway(tmp_path, limit=10).acquire(
            connector, _candidate(), "budget-reuse", data_risk="public"
        )

    assert first is not None
    assert connector.calls == 0
    assert "actual download bytes exceed budget" in caught.value.reasons


@pytest.mark.parametrize("malformed", [None, object()])
def test_malformed_connector_return_is_durably_fail_closed(
    tmp_path: Path, malformed: object
) -> None:
    """Non-receipt connector results cannot escape the durable audit boundary."""

    class MalformedConnector(WritingConnector):
        def acquire(  # type: ignore[override]
            self, candidate: DatasetCandidate, target: Path
        ) -> object:
            target.write_bytes(b"content")
            return malformed

    with pytest.raises(ConditionalDataGateRequired, match="validation failed"):
        _gateway(tmp_path).acquire(
            MalformedConnector(b"content"),
            _candidate(),
            f"malformed-{type(malformed).__name__}",
            data_risk="public",
        )

    _assert_durable_quarantine(tmp_path / "acquisitions")


def test_receipt_serialization_failure_is_durably_fail_closed(tmp_path: Path) -> None:
    """Connector-controlled receipt methods cannot escape fail-closed auditing."""

    class ExplodingReceipt(ConnectorReceipt):
        def model_dump(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("TOP-SECRET receipt failure")

    base = _receipt(b"content")
    claimed = ExplodingReceipt.model_validate(base.model_dump(mode="python"))

    with pytest.raises(ConditionalDataGateRequired, match="validation failed"):
        _gateway(tmp_path).acquire(
            WritingConnector(b"content", claimed),
            _candidate(),
            "serialization-failure",
            data_risk="public",
        )

    records = list((tmp_path / "acquisitions").rglob("*.json"))
    assert records
    assert all("TOP-SECRET" not in record.read_text() for record in records)


def test_concurrent_conflicting_request_is_rejected(tmp_path: Path) -> None:
    """Only one of two simultaneous, different requests may invoke a connector."""
    barrier = Barrier(2)
    sources = (
        "https://example.invalid/first",
        "https://example.invalid/second",
    )
    connectors = (
        WritingConnector(b"first", _receipt(b"first", source=sources[0])),
        WritingConnector(b"second", _receipt(b"second", source=sources[1])),
    )

    def acquire(index: int) -> ConnectorReceipt | ConditionalDataGateRequired:
        barrier.wait()
        try:
            return _gateway(tmp_path).acquire(
                connectors[index],
                _candidate(source=sources[index]),
                "conflict",
                data_risk="public",
            )
        except ConditionalDataGateRequired as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(acquire, range(2)))

    assert sum(isinstance(result, ConnectorReceipt) for result in results) == 1
    rejected = next(
        result for result in results if isinstance(result, ConditionalDataGateRequired)
    )
    assert "conflicting" in str(rejected)
    assert sum(connector.calls for connector in connectors) == 1


@pytest.mark.parametrize("data_risk", ["sensitive", "private"])
def test_sensitive_or_private_risk_requires_gate_before_io(
    tmp_path: Path, data_risk: str
) -> None:
    connector = WritingConnector(b"not-written")

    with pytest.raises(ConditionalDataGateRequired, match=data_risk):
        _gateway(tmp_path).acquire(
            connector, _candidate(), f"risk-{data_risk}", data_risk=data_risk
        )

    assert connector.calls == 0

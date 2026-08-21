"""Trusted acquisition-usage regression tests."""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from envresearch.connectors.contracts import ConnectorReceipt
from envresearch.connectors.gateway import ConditionalDataGateRequired, ConnectorGateway
from envresearch.connectors.usage_meter import TrustedUsageEvidence
from envresearch.models.evidence import AcquisitionBudget, DatasetCandidate


def _candidate() -> DatasetCandidate:
    return DatasetCandidate(
        dataset_id="metered-data",
        source="https://example.invalid/metered",
        public_access=True,
        requires_credentials=False,
        clear_license=True,
        license="CC-BY-4.0",
        estimated_download_bytes=1,
        estimated_local_storage_bytes=1,
        estimated_api_calls=0,
        estimated_external_cost=Decimal(0),
        estimated_elapsed_seconds=0,
        suitable_for_design=True,
        suitability_reason="Local regression fixture.",
        access_reason="Public test fixture.",
    )


def _budget(
    *, api_calls: int = 10, cost: Decimal = Decimal(10), elapsed: int = 10
) -> AcquisitionBudget:
    return AcquisitionBudget(
        max_download_bytes=10,
        max_local_storage_bytes=10,
        max_api_calls=api_calls,
        max_external_cost=cost,
        max_elapsed_seconds=elapsed,
    )


def _receipt() -> ConnectorReceipt:
    return ConnectorReceipt(
        connector_id="metered",
        connector_version="1.0",
        source=_candidate().source,
        acquired_at=datetime(2026, 8, 7, tzinfo=UTC),
        license="CC-BY-4.0",
        bytes=1,
        local_storage_bytes=1,
        api_calls=0,
        external_cost=Decimal(0),
        elapsed_seconds=0,
        sha256=hashlib.sha256(b"x").hexdigest(),
    )


class MeteredConnector:
    connector_id = "metered"
    connector_version = "1.0"

    def __init__(
        self,
        *,
        api_calls: int = 0,
        delay: float = 0,
        failure: BaseException | None = None,
    ) -> None:
        self.api_calls = api_calls
        self.delay = delay
        self.failure = failure
        self.calls = 0

    def inspect(self, source: str) -> DatasetCandidate:
        return _candidate()

    def acquire(self, candidate: DatasetCandidate, target: Path) -> ConnectorReceipt:
        self.calls += 1
        target.write_bytes(b"x")
        if self.delay:
            time.sleep(self.delay)
        if self.failure is not None:
            raise self.failure
        return _receipt()

def _gateway(
    root: Path,
    budget: AcquisitionBudget,
    *,
    actual_api_calls: int = 0,
    actual_external_cost: Decimal = Decimal(0),
) -> ConnectorGateway:
    return ConnectorGateway(
        budget,
        acquisition_root=root,
        usage_evidence_provider=lambda _connector, _candidate, _request_id: (
            TrustedUsageEvidence(
                api_calls=actual_api_calls,
                external_cost=actual_external_cost,
            )
        ),
    )


def test_gateway_measures_real_elapsed_time_instead_of_connector_claim(tmp_path: Path) -> None:
    connector = MeteredConnector(delay=1.05)

    with pytest.raises(ConditionalDataGateRequired) as caught:
        _gateway(tmp_path / "acquisitions", _budget(elapsed=0)).acquire(
            connector, _candidate(), "elapsed-lie", data_risk="public"
        )

    assert "actual elapsed seconds exceed budget" in caught.value.reasons


def test_caller_supplied_clock_is_never_trusted_as_gateway_elapsed(tmp_path: Path) -> None:
    gateway = ConnectorGateway(
        _budget(elapsed=0),
        acquisition_root=tmp_path / "acquisitions",
        monotonic_clock=lambda: 0.0,
        usage_evidence_provider=lambda *_: TrustedUsageEvidence(
            api_calls=0, external_cost=Decimal(0)
        ),
    )

    with pytest.raises(ConditionalDataGateRequired) as caught:
        gateway.acquire(
            MeteredConnector(), _candidate(), "untrusted-clock", data_risk="public"
        )

    assert "actual elapsed seconds are unverified" in caught.value.reasons


def test_interrupting_clock_preserves_interrupt_and_audit(tmp_path: Path) -> None:
    def interrupt() -> float:
        raise KeyboardInterrupt

    gateway = ConnectorGateway(
        _budget(),
        acquisition_root=tmp_path / "acquisitions",
        monotonic_clock=interrupt,
        usage_evidence_provider=lambda *_: TrustedUsageEvidence(
            api_calls=0, external_cost=Decimal(0)
        ),
    )

    with pytest.raises(KeyboardInterrupt):
        gateway.acquire(
            MeteredConnector(), _candidate(), "clock-interrupt", data_risk="public"
        )

    records = list((tmp_path / "acquisitions").rglob("*.json"))
    assert any("acquisition or usage evidence failed" in item.read_text() for item in records)


@pytest.mark.parametrize(
    ("connector", "budget", "reason", "actual_api_calls", "actual_cost"),
    [
        (
            MeteredConnector(api_calls=1),
            _budget(api_calls=0),
            "actual api calls",
            1,
            Decimal(0),
        ),
        (
            MeteredConnector(api_calls=1),
            _budget(cost=Decimal(0)),
            "actual external cost",
            1,
            Decimal("0.25"),
        ),
    ],
)
def test_gateway_ignores_api_and_cost_under_reporting(
    tmp_path: Path,
    connector: MeteredConnector,
    budget: AcquisitionBudget,
    reason: str,
    actual_api_calls: int,
    actual_cost: Decimal,
) -> None:
    with pytest.raises(ConditionalDataGateRequired) as caught:
        _gateway(
            tmp_path / "acquisitions",
            budget,
            actual_api_calls=actual_api_calls,
            actual_external_cost=actual_cost,
        ).acquire(
            connector, _candidate(), reason.replace(" ", "-"), data_risk="public"
        )

    assert any(reason in item for item in caught.value.reasons)


def test_unmetered_api_and_cost_dimensions_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ConditionalDataGateRequired) as caught:
        ConnectorGateway(
            _budget(), acquisition_root=tmp_path / "acquisitions"
        ).acquire(
            MeteredConnector(), _candidate(), "unmetered", data_risk="public"
        )

    assert "actual api calls are unverified" in caught.value.reasons
    assert "actual external cost is unverified" in caught.value.reasons


def test_reuse_applies_stricter_policy_to_persisted_metered_usage(tmp_path: Path) -> None:
    root = tmp_path / "acquisitions"
    connector = MeteredConnector(api_calls=1)
    accepted = _gateway(
        root,
        _budget(),
        actual_api_calls=1,
        actual_external_cost=Decimal("0.25"),
    ).acquire(
        connector, _candidate(), "metered-reuse", data_risk="public"
    )

    with pytest.raises(ConditionalDataGateRequired) as caught:
        _gateway(
            root,
            _budget(api_calls=0, cost=Decimal(0)),
            actual_api_calls=0,
            actual_external_cost=Decimal(0),
        ).acquire(
            MeteredConnector(), _candidate(), "metered-reuse", data_risk="public"
        )

    assert accepted is not None
    assert accepted.api_calls == 1
    assert accepted.external_cost == Decimal("0.25")
    assert connector.calls == 1
    assert "actual api calls exceed budget" in caught.value.reasons
    assert "actual external cost exceeds budget" in caught.value.reasons


@pytest.mark.parametrize(
    "mutation", ["remove_usage", "change_api_calls", "change_verification_sources"]
)
def test_reuse_fails_closed_for_unbound_or_legacy_usage_state(
    tmp_path: Path, mutation: str
) -> None:
    root = tmp_path / "acquisitions"
    connector = MeteredConnector(api_calls=1)
    _gateway(root, _budget(), actual_api_calls=1).acquire(
        connector, _candidate(), f"tampered-{mutation}", data_risk="public"
    )
    state_path = next((root / "control/state").glob("*.json"))
    state = json.loads(state_path.read_text())
    if mutation == "remove_usage":
        state.pop("verified_usage")
    elif mutation == "change_api_calls":
        state["verified_usage"]["api_calls"] = 0
    else:
        verification = state["verified_usage"]["verification"]
        verification["bytes"] = "trusted_evidence"
        verification["api_calls"] = "gateway_measured"
    state_path.write_text(json.dumps(state))

    with pytest.raises(ConditionalDataGateRequired, match="state is invalid"):
        _gateway(root, _budget(api_calls=0)).acquire(
            MeteredConnector(),
            _candidate(),
            f"tampered-{mutation}",
            data_risk="public",
        )

    assert connector.calls == 1


def test_interrupting_evidence_provider_preserves_interrupt_and_audit(
    tmp_path: Path,
) -> None:
    def interrupt(*_: object) -> TrustedUsageEvidence:
        raise KeyboardInterrupt

    gateway = ConnectorGateway(
        _budget(),
        acquisition_root=tmp_path / "acquisitions",
        usage_evidence_provider=interrupt,
    )

    with pytest.raises(KeyboardInterrupt):
        gateway.acquire(
            MeteredConnector(), _candidate(), "provider-interrupt", data_risk="public"
        )

    records = [
        json.loads(path.read_text())
        for path in (tmp_path / "acquisitions").rglob("*.json")
    ]
    record = next(item for item in records if item["request_id"] == "provider-interrupt")
    assert record["reasons"] == ["acquisition or usage evidence failed: KeyboardInterrupt"]
    assert record["verified_usage"]["verification"]["api_calls"] == "unverified"


def test_interrupted_metering_is_persisted_as_quarantined_evidence(tmp_path: Path) -> None:
    root = tmp_path / "acquisitions"
    connector = MeteredConnector(
        api_calls=2, failure=RuntimeError("interrupted")
    )

    with pytest.raises(RuntimeError, match="interrupted"):
        _gateway(
            root,
            _budget(),
            actual_api_calls=2,
            actual_external_cost=Decimal("0.50"),
        ).acquire(
            connector, _candidate(), "interrupted-meter", data_risk="public"
        )

    records = [json.loads(path.read_text()) for path in root.rglob("*.json")]
    record = next(item for item in records if item["request_id"] == "interrupted-meter")
    assert record["status"] == "quarantined"
    assert record["verified_usage"]["api_calls"] == 2
    assert record["verified_usage"]["external_cost"] == "0.50"
    assert record["verified_usage"]["verification"]["elapsed_seconds"] == (
        "gateway_measured"
    )


def test_concurrent_meters_do_not_share_counters(tmp_path: Path) -> None:
    root = tmp_path / "acquisitions"

    def acquire(index: int) -> int:
        gateway = _gateway(root, _budget(), actual_api_calls=index)
        gateway.acquire(
            MeteredConnector(api_calls=index),
            _candidate(),
            f"concurrent-{index}",
            data_risk="public",
        )
        state = gateway.store.load_state("metered", f"concurrent-{index}")
        assert state is not None and state.verified_usage is not None
        assert state.verified_usage.api_calls is not None
        return state.verified_usage.api_calls

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(acquire, (1, 2)))

    assert results == (1, 2)

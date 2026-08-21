"""Tests for connector gateway safety without connector I/O."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.connectors.acquisition_store import (
    AcquisitionStore,
    UnsafeAcquisitionOutput,
)
from envresearch.connectors.contracts import (
    ConnectorReceipt,
    ConnectorUnavailable,
    DataConnector,
    LiteratureQuery,
)
from envresearch.connectors.gateway import (
    ConditionalDataGateRequired,
    ConnectorGateway,
    literature_gateway,
)
from envresearch.connectors.usage_meter import TrustedUsageEvidence
from envresearch.models.evidence import (
    AcquisitionBudget,
    AcquisitionDecision,
    AcquisitionPolicy,
    DataProvenancePayload,
    DatasetCandidate,
)


class FakeConnector:
    """In-memory connector that exposes whether acquisition was requested."""
    connector_id = "fake-data"
    connector_version = "1.0"
    def __init__(self, receipt: ConnectorReceipt, content: bytes = b"x" * 10) -> None:
        self.receipt = receipt
        self.content = content
        self.acquire_calls = 0
    def inspect(self, source: str) -> DatasetCandidate:
        raise AssertionError(f"unexpected inspection of {source}")
    def acquire(self, candidate: DatasetCandidate, target: Path) -> ConnectorReceipt:
        self.acquire_calls += 1
        target.write_bytes(self.content)
        return self.receipt

def _trusted_zero_usage(*_: object) -> TrustedUsageEvidence:
    return TrustedUsageEvidence(api_calls=0, external_cost=Decimal(0))

def _gateway(tmp_path: Path, policy: AcquisitionPolicy | None = None) -> ConnectorGateway:
    return ConnectorGateway(
        budget(), acquisition_root=tmp_path / "acquisitions", policy=policy,
        usage_evidence_provider=_trusted_zero_usage,
    )

class UnsafePolicy(AcquisitionPolicy):
    """A deliberately unsafe extension that attempts to loosen mandatory rules."""

    def evaluate(
        self, candidate: DatasetCandidate, budget: AcquisitionBudget
    ) -> AcquisitionDecision:
        return AcquisitionDecision(
            action="auto_acquire", reasons=("unsafe extension allows acquisition",)
        )

class PlanningOnlyPolicy(AcquisitionPolicy):
    """A valid extension that tightens an otherwise automatic decision."""

    def evaluate(
        self, candidate: DatasetCandidate, budget: AcquisitionBudget
    ) -> AcquisitionDecision:
        return AcquisitionDecision(
            action="planning_only", reasons=("extension requests more planning",)
        )


def candidate(**changes: object) -> DatasetCandidate:
    """Return one public, licensed candidate eligible for fake acquisition."""
    values: dict[str, object] = {
        "dataset_id": "public-air-quality",
        "source": "https://example.invalid/public-air-quality",
        "public_access": True,
        "requires_credentials": False,
        "clear_license": True,
        "license": "CC-BY-4.0",
        "estimated_download_bytes": 10,
        "estimated_local_storage_bytes": 10,
        "estimated_api_calls": 1,
        "estimated_external_cost": Decimal(0),
        "estimated_elapsed_seconds": 10,
        "suitable_for_design": True,
        "suitability_reason": "Includes the approved exposure and outcome variables.",
        "access_reason": "Published by the agency in a public catalogue.",
    }
    values.update(changes)
    return DatasetCandidate.model_validate(values)


def receipt(**changes: object) -> ConnectorReceipt:
    """Return an in-budget receipt matching the fake connector and candidate."""
    values: dict[str, object] = {
        "connector_id": "fake-data",
        "connector_version": "1.0",
        "source": "https://example.invalid/public-air-quality",
        "acquired_at": datetime(2026, 8, 5, tzinfo=UTC),
        "license": "CC-BY-4.0",
        "bytes": 10,
        "local_storage_bytes": 10,
        "api_calls": 1,
        "external_cost": Decimal(0),
        "elapsed_seconds": 10,
        "sha256": hashlib.sha256(b"x" * 10).hexdigest(),
    }
    values.update(changes)
    return ConnectorReceipt.model_validate(values)


def budget() -> AcquisitionBudget:
    """Return a fully explicit budget for gateway tests."""
    return AcquisitionBudget(
        max_download_bytes=100,
        max_local_storage_bytes=100,
        max_api_calls=10,
        max_external_cost=Decimal(0),
        max_elapsed_seconds=60,
    )


def test_data_connector_protocol_is_structurally_checkable() -> None:
    """A fake connector satisfies the contract without a real integration."""
    assert isinstance(FakeConnector(receipt()), DataConnector)


def test_acquisition_store_derives_targets_below_private_owned_root(
    tmp_path: Path,
) -> None:
    """Connector and request IDs can select names, never caller-chosen paths."""
    store = AcquisitionStore(tmp_path / "gateway-owned")

    target = store.target("fake-data", "request-1")

    assert target == (
        (tmp_path / "gateway-owned").resolve()
        / "outputs"
        / "fake-data"
        / "request-1"
        / "payload.pending"
    )
    assert target.is_relative_to(store.root)
    assert store.root.stat().st_mode & 0o777 == 0o700
    with pytest.raises(ValueError, match="unsafe"):
        store.target("fake-data", "../../outside")


def test_existing_unsafe_lock_is_rejected_without_mutating_linked_inode(
    tmp_path: Path,
) -> None:
    """Lock validation must precede any permission mutation on an existing inode."""
    store = AcquisitionStore(tmp_path / "gateway-owned")
    with store.locked("fake-data", "request-1"):
        pass
    lock = next((store.root / "control" / "locks").iterdir())
    outside = tmp_path / "outside-lock"
    os.link(lock, outside)
    outside.chmod(0o640)

    with (
        pytest.raises(UnsafeAcquisitionOutput, match="single-link"),
        store.locked("fake-data", "request-1"),
    ):
        pass

    assert outside.stat().st_mode & 0o777 == 0o640


def test_swap_during_state_write_leaves_no_consumable_accepted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _gateway(tmp_path)
    original_write = gateway.store._root.write_file_noreplace
    injected = False

    def write_then_swap(relative: Path, data: bytes, *, mode: int) -> None:
        nonlocal injected
        original_write(relative, data, mode=mode)
        if relative.parent == Path("control/state") and not injected:
            injected = True
            target = gateway.store.root / "outputs/fake-data/state-race/payload"
            replacement = target.with_name("replacement")
            replacement.write_bytes(b"evil")
            os.replace(replacement, target)

    monkeypatch.setattr(gateway.store._root, "write_file_noreplace", write_then_swap)
    with pytest.raises(ConditionalDataGateRequired, match="changed"):
        gateway.acquire(
            FakeConnector(receipt()), candidate(), "state-race", data_risk="public"
        )

    assert gateway.store.load_state("fake-data", "state-race") is None
    with pytest.raises(ConditionalDataGateRequired, match="residue"):
        gateway.acquire(
            FakeConnector(receipt()), candidate(), "state-race", data_risk="public"
        )


class UnavailableLiteratureConnector:
    """A local connector failure used to exercise explicit degraded coverage."""

    connector_id = "zotero-json-export"
    connector_version = "1.0.0"

    def search(self, query: LiteratureQuery) -> tuple[object, ...]:
        raise ConnectorUnavailable(
            connector_id=self.connector_id,
            reason_code="EXPORT_UNREADABLE",
            diagnostic="local export cannot be read",
        )


def test_literature_gateway_returns_explicit_degraded_coverage_on_outage() -> None:
    """A known connector outage preserves a deterministic planning-only result."""
    coverage = literature_gateway().literature_search(
        UnavailableLiteratureConnector(), LiteratureQuery(text="water")
    )

    assert coverage.status == "degraded"
    assert coverage.records == ()
    assert coverage.reason_code == "CONNECTOR_UNAVAILABLE"
    assert coverage.connector_id == "zotero-json-export"


@pytest.mark.parametrize(
    ("connector_id", "reason_code"),
    [
        ("different-connector", "EXPORT_UNREADABLE"),
        ("zotero-json-export", "INTERNAL_BUG"),
    ],
)
def test_literature_gateway_propagates_untrusted_connector_failures(
    connector_id: str, reason_code: str
) -> None:
    """Only a known failure from the invoked connector may become degraded coverage."""

    class UntrustedFailureConnector(UnavailableLiteratureConnector):
        def search(self, query: LiteratureQuery) -> tuple[object, ...]:
            raise ConnectorUnavailable(
                connector_id=connector_id,
                reason_code=reason_code,
                diagnostic="fixed safe diagnostic",
            )

    with pytest.raises(ConnectorUnavailable):
        literature_gateway().literature_search(
            UntrustedFailureConnector(), LiteratureQuery(text="water")
        )


def test_literature_gateway_propagates_programming_errors() -> None:
    """Unexpected connector errors must remain visible rather than degrading coverage."""

    class BrokenConnector(UnavailableLiteratureConnector):
        def search(self, query: LiteratureQuery) -> tuple[object, ...]:
            raise RuntimeError("programming error")

    with pytest.raises(RuntimeError, match="programming error"):
        literature_gateway().literature_search(
            BrokenConnector(), LiteratureQuery(text="water")
        )


def test_planning_only_does_not_call_connector(tmp_path: Path) -> None:
    """Unsuitable data remains a planning artifact and makes no acquisition call."""
    connector = FakeConnector(receipt())
    gateway = _gateway(tmp_path)

    result = gateway.acquire(
        connector,
        candidate(suitable_for_design=False),
        "planning-only",
        data_risk="public",
    )

    assert result is None
    assert connector.acquire_calls == 0


def test_pre_acquisition_gate_does_not_call_connector(tmp_path: Path) -> None:
    """Conditional sources require a human gate before any connector I/O."""
    connector = FakeConnector(receipt())
    gateway = _gateway(tmp_path)

    with pytest.raises(ConditionalDataGateRequired) as caught:
        gateway.acquire(
            connector,
            candidate(public_access=False),
            "private-source",
            data_risk="private",
        )

    assert caught.value.receipt is None
    assert connector.acquire_calls == 0


@pytest.mark.parametrize(
    "unsafe_candidate",
    [candidate(public_access=False), candidate(estimated_download_bytes=101)],
)
def test_gateway_baseline_policy_cannot_be_loosened_by_extension(
    tmp_path: Path, unsafe_candidate: DatasetCandidate
) -> None:
    """Mandatory non-public and over-budget gates win over unsafe policy injection."""
    connector = FakeConnector(receipt())
    gateway = _gateway(tmp_path, UnsafePolicy())

    with pytest.raises(ConditionalDataGateRequired) as caught:
        gateway.acquire(connector, unsafe_candidate, "unsafe", data_risk="public")

    assert (
        caught.value.reasons
        == AcquisitionPolicy().evaluate(unsafe_candidate, budget()).reasons
    )
    assert connector.acquire_calls == 0


def test_gateway_policy_extension_may_tighten_an_automatic_decision(
    tmp_path: Path,
) -> None:
    """An extension can add a restriction only after baseline safety has passed."""
    connector = FakeConnector(receipt())

    result = _gateway(tmp_path, PlanningOnlyPolicy()).acquire(
        connector, candidate(), "planning-extension", data_risk="public"
    )

    assert result is None
    assert connector.acquire_calls == 0


def test_in_budget_acquisition_returns_non_quarantined_receipt(tmp_path: Path) -> None:
    """A public, licensed in-budget acquisition remains eligible for provenance."""
    connector = FakeConnector(receipt())

    result = _gateway(tmp_path).acquire(
        connector, candidate(), "accepted", data_risk="public"
    )

    assert result is not None
    assert result.quarantined is False
    assert connector.acquire_calls == 1


def test_actual_budget_excess_quarantines_and_gates_receipt(tmp_path: Path) -> None:
    """Post-acquisition overuse is preserved but cannot be promoted as provenance."""
    content = b"x" * 101
    connector = FakeConnector(
        receipt(
            bytes=len(content),
            local_storage_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        ),
        content,
    )
    gateway = _gateway(tmp_path)

    with pytest.raises(ConditionalDataGateRequired) as caught:
        gateway.acquire(connector, candidate(), "over-budget", data_risk="public")

    assert connector.acquire_calls == 1
    assert caught.value.receipt is not None
    assert caught.value.receipt.quarantined is True
    assert (
        "actual download bytes exceed budget" in caught.value.receipt.quarantine_reasons
    )
    with pytest.raises(ValueError, match="quarantined receipt"):
        DataProvenancePayload.from_receipt("public-air-quality", caught.value.receipt)


def test_receipt_quarantine_revalidates_reasons_and_preserves_receipt_fields() -> None:
    """Quarantine cannot bypass receipt invariants and keeps audited measurements."""
    original = receipt()

    with pytest.raises(ValidationError, match="blank"):
        original.quarantine(("",))
    with pytest.raises(ValidationError):
        original.quarantine(["over budget"])  # type: ignore[arg-type]

    quarantined = original.quarantine(("actual download bytes exceed budget",))

    assert quarantined.quarantined is True
    assert quarantined.quarantine_reasons == ("actual download bytes exceed budget",)
    assert quarantined.model_dump(exclude={"quarantined", "quarantine_reasons"}) == (
        original.model_dump(exclude={"quarantined", "quarantine_reasons"})
    )

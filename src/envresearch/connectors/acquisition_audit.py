"""Durable acquisition audit-record construction."""

from __future__ import annotations

from datetime import UTC, datetime

from envresearch.connectors.acquisition_gate import ConditionalDataGateRequired
from envresearch.connectors.acquisition_store import AcquisitionStore
from envresearch.connectors.contracts import (
    AcquisitionAuditRecord,
    ConnectorReceipt,
    DataConnector,
    DataRisk,
    MeasuredAcquisition,
)
from envresearch.connectors.usage_meter import VerifiedUsage
from envresearch.models.evidence import DatasetCandidate


def load_acquisition_state(
    store: AcquisitionStore,
    connector: DataConnector,
    candidate: DatasetCandidate,
    request_id: str,
    data_risk: DataRisk,
    fingerprint: str,
) -> AcquisitionAuditRecord | None:
    """Load accepted state or durably gate invalid and legacy records."""
    try:
        return store.load_state(connector.connector_id, request_id)
    except (OSError, ValueError) as error:
        reasons = ("accepted acquisition state is invalid or unverified",)
        record_acquisition(
            store,
            connector,
            candidate,
            request_id,
            data_risk,
            fingerprint,
            reasons=reasons,
            claimed=None,
            verified=None,
            measured=None,
            verified_usage=None,
        )
        raise ConditionalDataGateRequired(reasons) from error


def record_acquisition(
    store: AcquisitionStore,
    connector: DataConnector,
    candidate: DatasetCandidate,
    request_id: str,
    data_risk: DataRisk,
    fingerprint: str,
    *,
    reasons: tuple[str, ...],
    claimed: ConnectorReceipt | None,
    verified: ConnectorReceipt | None,
    measured: MeasuredAcquisition | None,
    verified_usage: VerifiedUsage | None,
) -> AcquisitionAuditRecord:
    """Build and append one accepted or quarantined audit record."""
    record = AcquisitionAuditRecord(
        request_id=request_id,
        request_fingerprint=fingerprint,
        connector_id=connector.connector_id,
        connector_version=connector.connector_version,
        source=candidate.source,
        license=candidate.license,
        data_risk=data_risk,
        recorded_at=datetime.now(UTC),
        status="quarantined" if reasons else "accepted",
        target=store.relative_target(connector.connector_id, request_id).as_posix(),
        reasons=reasons,
        claimed_receipt=claimed,
        verified_receipt=verified,
        measured=measured,
        verified_usage=verified_usage,
    )
    store.record(record)
    return record

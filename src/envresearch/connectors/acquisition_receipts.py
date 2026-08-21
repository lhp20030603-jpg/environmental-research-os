"""Pure validation and identity helpers for measured connector receipts."""

from __future__ import annotations

import hashlib
import json

from envresearch.connectors.contracts import (
    ConnectorReceipt,
    DataConnector,
    DataRisk,
    MeasuredAcquisition,
)
from envresearch.connectors.usage_meter import VerifiedUsage
from envresearch.models.evidence import DatasetCandidate


def validate_risk(value: object) -> DataRisk:
    """Require one explicit acquisition data-risk classification."""
    if value not in {"public", "sensitive", "private"}:
        raise ValueError("data_risk must be public, sensitive, or private")
    return value


def acquisition_fingerprint(
    connector: DataConnector, candidate: DatasetCandidate, data_risk: DataRisk
) -> str:
    """Bind idempotency to connector, candidate, and explicit risk identity."""
    payload = {
        "connector_id": connector.connector_id,
        "connector_version": connector.connector_version,
        "candidate": candidate.model_dump(mode="json"),
        "data_risk": data_risk,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_connector_receipt(
    connector: DataConnector, candidate: DatasetCandidate, receipt: object
) -> ConnectorReceipt:
    """Revalidate one claimed receipt and bind its declared source identity."""
    if not isinstance(receipt, ConnectorReceipt):
        raise TypeError("connector result must be a ConnectorReceipt")
    validated = ConnectorReceipt.model_validate(receipt.model_dump(mode="python"))
    if validated.connector_id != connector.connector_id:
        raise ValueError("receipt connector ID does not match connector")
    if validated.connector_version != connector.connector_version:
        raise ValueError("receipt connector version does not match connector")
    if validated.source != candidate.source:
        raise ValueError("receipt source does not match candidate")
    if validated.license != candidate.license:
        raise ValueError("receipt license does not match candidate")
    return validated


def receipt_with_measurement(
    receipt: ConnectorReceipt,
    measured: MeasuredAcquisition,
    usage: VerifiedUsage,
) -> ConnectorReceipt:
    """Replace every connector usage claim with trusted gateway measurements."""
    if not usage.fully_verified:
        raise ValueError("cannot create a verified receipt from unverified usage")
    if (
        usage.bytes is None
        or usage.local_storage_bytes is None
        or usage.api_calls is None
        or usage.external_cost is None
        or usage.elapsed_seconds is None
    ):
        raise ValueError("verified usage is missing a measured value")
    return ConnectorReceipt.model_validate(
        receipt.model_dump(mode="python")
        | {
            "bytes": usage.bytes,
            "local_storage_bytes": usage.local_storage_bytes,
            "api_calls": usage.api_calls,
            "external_cost": usage.external_cost,
            "elapsed_seconds": usage.elapsed_seconds,
            "sha256": measured.sha256,
            "quarantined": False,
            "quarantine_reasons": (),
        }
    )


def measurement_mismatches(
    receipt: ConnectorReceipt, measured: MeasuredAcquisition
) -> tuple[str, ...]:
    """Describe every connector byte/storage/hash claim that measurement disproves."""
    reasons: list[str] = []
    if receipt.bytes != measured.bytes:
        reasons.append("connector receipt bytes do not match measured output")
    if receipt.local_storage_bytes != measured.local_storage_bytes:
        reasons.append("connector receipt storage does not match measured output")
    if receipt.sha256 != measured.sha256:
        reasons.append("connector receipt sha256 does not match measured output")
    return tuple(reasons)

"""Progressive acquisition gateway that never implements external connector I/O."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import monotonic

from envresearch.connectors.acquisition_audit import (
    load_acquisition_state,
    record_acquisition,
)
from envresearch.connectors.acquisition_gate import ConditionalDataGateRequired
from envresearch.connectors.acquisition_receipts import (
    acquisition_fingerprint,
    measurement_mismatches,
    receipt_with_measurement,
    validate_connector_receipt,
    validate_risk,
)
from envresearch.connectors.acquisition_store import (
    AcquisitionStore,
    PinnedAcquisitionTarget,
    UnsafeAcquisitionOutput,
)
from envresearch.connectors.contracts import (
    AcquisitionAuditRecord,
    ConnectorReceipt,
    DataConnector,
    DataRisk,
    MeasuredAcquisition,
)
from envresearch.connectors.literature_gateway import (
    LiteratureGateway,
    literature_gateway,
)
from envresearch.connectors.usage_meter import (
    GatewayUsageSession,
    UsageEvidenceProvider,
    VerifiedUsage,
    actual_usage_reasons,
)
from envresearch.models.evidence import (
    AcquisitionBudget,
    AcquisitionPolicy,
    DatasetCandidate,
)

__all__ = ["ConditionalDataGateRequired", "ConnectorGateway"]
__all__ += ["LiteratureGateway", "literature_gateway"]

class ConnectorGateway:
    """Enforce policy before and actual-budget controls after connector calls."""

    def __init__(
        self,
        budget: AcquisitionBudget,
        *,
        acquisition_root: Path,
        policy: AcquisitionPolicy | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        usage_evidence_provider: UsageEvidenceProvider | None = None,
    ) -> None:
        self.budget = budget
        self.policy = policy
        self.store = AcquisitionStore(acquisition_root)
        self.last_receipt: ConnectorReceipt | None = None
        self._monotonic_clock = monotonic if monotonic_clock is None else monotonic_clock
        self._clock_is_trusted = self._monotonic_clock is monotonic
        self._usage_evidence_provider = usage_evidence_provider

    def acquire(
        self,
        connector: DataConnector,
        candidate: DatasetCandidate,
        request_id: str,
        *,
        data_risk: DataRisk,
    ) -> ConnectorReceipt | None:
        """Acquire to a confined target and accept only gateway-measured output."""
        baseline = AcquisitionPolicy().evaluate(candidate, self.budget)
        if baseline.action == "planning_only":
            return None
        if baseline.action == "gate_required":
            raise ConditionalDataGateRequired(baseline.reasons)
        if self.policy is not None:
            extension = self.policy.evaluate(candidate, self.budget)
            if extension.action == "planning_only":
                return None
            if extension.action == "gate_required":
                raise ConditionalDataGateRequired(extension.reasons)
        risk = validate_risk(data_risk)
        if risk != "public":
            raise ConditionalDataGateRequired((f"data risk is {risk}",))
        fingerprint = acquisition_fingerprint(connector, candidate, risk)
        with self.store.locked(connector.connector_id, request_id):
            return self._acquire_locked(
                connector, candidate, request_id, risk, fingerprint
            )

    def _acquire_locked(
        self,
        connector: DataConnector,
        candidate: DatasetCandidate,
        request_id: str,
        data_risk: DataRisk,
        fingerprint: str,
    ) -> ConnectorReceipt:
        existing = load_acquisition_state(
            self.store, connector, candidate, request_id, data_risk, fingerprint
        )
        if existing is not None:
            return self._reuse_existing(
                connector, candidate, request_id, data_risk, fingerprint, existing
            )
        if self.store.exists(connector.connector_id, request_id):
            residue_reasons = ("unverified acquisition residue blocks retry",)
            self._record(
                connector,
                candidate,
                request_id,
                data_risk,
                fingerprint,
                reasons=residue_reasons,
            )
            raise ConditionalDataGateRequired(residue_reasons)

        with self.store.prepare(connector.connector_id, request_id) as pinned:
            return self._acquire_prepared(
                connector,
                candidate,
                request_id,
                data_risk,
                fingerprint,
                pinned,
            )

    def _acquire_prepared(
        self,
        connector: DataConnector,
        candidate: DatasetCandidate,
        request_id: str,
        data_risk: DataRisk,
        fingerprint: str,
        pinned: PinnedAcquisitionTarget,
    ) -> ConnectorReceipt:
        session = GatewayUsageSession(
            self._monotonic_clock,
            clock_is_trusted=self._clock_is_trusted,
            evidence_provider=self._usage_evidence_provider,
        )
        try:
            claimed = session.invoke(connector, candidate, pinned.path, request_id)
        except BaseException as error:
            failure_reasons = (
                f"acquisition or usage evidence failed: {type(error).__name__}",
            )
            self._record(
                connector,
                candidate,
                request_id,
                data_risk,
                fingerprint,
                reasons=failure_reasons,
                verified_usage=session.usage,
            )
            raise

        try:
            validated = validate_connector_receipt(connector, candidate, claimed)
        except Exception as error:
            receipt_reasons = ("connector receipt validation failed",)
            self._record(
                connector,
                candidate,
                request_id,
                data_risk,
                fingerprint,
                reasons=receipt_reasons,
                verified_usage=session.usage,
            )
            raise ConditionalDataGateRequired(receipt_reasons) from error

        try:
            measured = self.store.measure_pinned(pinned)
        except (OSError, UnsafeAcquisitionOutput, ValueError) as error:
            output_reasons = (f"unsafe acquisition output: {error}",)
            self.last_receipt = None
            self._record(
                connector,
                candidate,
                request_id,
                data_risk,
                fingerprint,
                reasons=output_reasons,
                claimed=validated,
                verified_usage=session.usage,
            )
            raise ConditionalDataGateRequired(output_reasons) from error

        verified_usage = session.usage.with_file_measurement(
            bytes_count=measured.bytes,
            local_storage_bytes=measured.local_storage_bytes,
        )
        verified = (
            receipt_with_measurement(validated, measured, verified_usage)
            if verified_usage.fully_verified
            else None
        )
        measured_reasons = measurement_mismatches(validated, measured)
        measured_reasons += actual_usage_reasons(self.budget, verified_usage)
        if validated.quarantined:
            measured_reasons += ("connector returned a quarantined receipt",)
        if measured_reasons:
            measured_reasons = tuple(dict.fromkeys(measured_reasons))
            quarantined = (
                verified.quarantine(measured_reasons) if verified is not None else None
            )
            self.last_receipt = quarantined
            self._record(
                connector,
                candidate,
                request_id,
                data_risk,
                fingerprint,
                reasons=measured_reasons,
                claimed=validated,
                verified=quarantined,
                measured=measured,
                verified_usage=verified_usage,
            )
            raise ConditionalDataGateRequired(measured_reasons, quarantined)

        if verified is None:
            raise RuntimeError("verified usage unexpectedly lacks a receipt")
        try:
            self.store.promote(pinned, measured)
        except (OSError, UnsafeAcquisitionOutput, ValueError) as error:
            promotion_reasons = (
                f"acquisition output changed before promotion: {error}",
            )
            quarantined = verified.quarantine(promotion_reasons)
            self.last_receipt = quarantined
            self._record(
                connector,
                candidate,
                request_id,
                data_risk,
                fingerprint,
                reasons=promotion_reasons,
                claimed=validated,
                verified=quarantined,
                measured=measured,
                verified_usage=verified_usage,
            )
            raise ConditionalDataGateRequired(promotion_reasons, quarantined) from error

        accepted = self._record(
            connector,
            candidate,
            request_id,
            data_risk,
            fingerprint,
            reasons=(),
            claimed=validated,
            verified=verified,
            measured=measured,
            verified_usage=verified_usage,
        )
        try:
            self.store.publish_state(accepted, pinned=pinned, measured=measured)
        except (OSError, UnsafeAcquisitionOutput, ValueError) as error:
            publication_reasons = (
                f"acquisition output changed during publication: {error}",
            )
            quarantined = verified.quarantine(publication_reasons)
            self.last_receipt = quarantined
            self._record(
                connector,
                candidate,
                request_id,
                data_risk,
                fingerprint,
                reasons=publication_reasons,
                claimed=validated,
                verified=quarantined,
                measured=measured,
                verified_usage=verified_usage,
            )
            raise ConditionalDataGateRequired(
                publication_reasons, quarantined
            ) from error
        self.last_receipt = verified
        return verified

    def _reuse_existing(
        self,
        connector: DataConnector,
        candidate: DatasetCandidate,
        request_id: str,
        data_risk: DataRisk,
        fingerprint: str,
        existing: AcquisitionAuditRecord,
    ) -> ConnectorReceipt:
        if existing.request_fingerprint != fingerprint:
            reasons = ("conflicting acquisition request ID is already accepted",)
            self._record(
                connector,
                candidate,
                request_id,
                data_risk,
                fingerprint,
                reasons=reasons,
                claimed=existing.claimed_receipt,
                verified=existing.verified_receipt,
                measured=existing.measured,
                verified_usage=existing.verified_usage,
            )
            raise ConditionalDataGateRequired(reasons)
        if existing.verified_receipt is None:
            raise ValueError("accepted acquisition state lacks a verified receipt")
        budget_reasons: tuple[str, ...]
        if existing.verified_usage is None:
            budget_reasons = ("actual acquisition usage is unverified",)
        else:
            budget_reasons = actual_usage_reasons(self.budget, existing.verified_usage)
        if budget_reasons:
            quarantined = existing.verified_receipt.quarantine(budget_reasons)
            self.last_receipt = quarantined
            self._record(
                connector,
                candidate,
                request_id,
                data_risk,
                fingerprint,
                reasons=budget_reasons,
                claimed=existing.claimed_receipt,
                verified=quarantined,
                measured=existing.measured,
                verified_usage=existing.verified_usage,
            )
            raise ConditionalDataGateRequired(budget_reasons, quarantined)
        try:
            measured = self.store.measure(connector.connector_id, request_id)
        except (OSError, UnsafeAcquisitionOutput, ValueError) as error:
            reasons = (f"accepted acquisition output is no longer safe: {error}",)
            self._record(
                connector,
                candidate,
                request_id,
                data_risk,
                fingerprint,
                reasons=reasons,
            )
            raise ConditionalDataGateRequired(reasons) from error
        if existing.measured != measured:
            reasons = ("accepted acquisition output no longer matches durable state",)
            self._record(
                connector,
                candidate,
                request_id,
                data_risk,
                fingerprint,
                reasons=reasons,
                measured=measured,
                claimed=existing.claimed_receipt,
                verified=existing.verified_receipt,
                verified_usage=existing.verified_usage,
            )
            raise ConditionalDataGateRequired(reasons)
        self.last_receipt = existing.verified_receipt
        return existing.verified_receipt

    def _record(
        self,
        connector: DataConnector,
        candidate: DatasetCandidate,
        request_id: str,
        data_risk: DataRisk,
        fingerprint: str,
        *,
        reasons: tuple[str, ...],
        claimed: ConnectorReceipt | None = None,
        verified: ConnectorReceipt | None = None,
        measured: MeasuredAcquisition | None = None,
        verified_usage: VerifiedUsage | None = None,
    ) -> AcquisitionAuditRecord:
        return record_acquisition(
            self.store,
            connector,
            candidate,
            request_id,
            data_risk,
            fingerprint,
            reasons=reasons,
            claimed=claimed,
            verified=verified,
            measured=measured,
            verified_usage=verified_usage,
        )

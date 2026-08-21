"""Private immutable report publication helpers for replication service."""

from envresearch.models.artifact import ArtifactRef
from envresearch.replication._attempt_support import AttemptCoordinator
from envresearch.replication._evidence_support import persist_verification_report
from envresearch.replication._ledger_models import VerificationPublicationError
from envresearch.replication._service_models import ReplicationFault, ReplicationReport
from envresearch.replication.contracts import (
    ReplicationException,
    ReplicationRunState,
)
from envresearch.replication.ledger import ReplicationLedger, ReplicationRun
from envresearch.replication.verify import ReplicationVerifier
from envresearch.storage.research_artifacts import ResearchArtifactStore


class ServiceReportingMixin:
    """Share fail-closed report mechanics without widening the public API."""

    _store: ResearchArtifactStore
    ledger: ReplicationLedger
    verifier: ReplicationVerifier

    def status(self, reference: ArtifactRef) -> ReplicationReport:
        """Return an authenticated current ledger or immutable pre-ledger failure."""
        if reference.artifact_id == "replication-ledger":
            return self._authenticated_report(*self.ledger.read_current(reference))
        coordinator = AttemptCoordinator(self._store, reference)
        with coordinator.locked():
            report = coordinator.read_failure()
            if report is None:
                raise ValueError("replication status reference is unknown")
            return self._attempt_report(*report)

    def _finalize(self, run_ref: ArtifactRef) -> ReplicationReport:
        try:
            verification = self.verifier.verify(run_ref)
            verification_ref = persist_verification_report(self._store, verification)
            if not verification.passed:
                finding = verification.findings[0]
                fault = ReplicationFault(
                    "VERIFICATION_FAILED", finding.message, (verification_ref,)
                )
                return self._fail(run_ref, fault)
            passed = self.ledger.publish_verification(run_ref, verification)
        except VerificationPublicationError as error:
            fault = ReplicationFault(
                "VERIFICATION_FAILED", str(error), (error.report_ref,)
            )
            return self._fail(run_ref, fault)
        except (OSError, TypeError, ValueError) as error:
            return self._fail(
                run_ref, ReplicationFault("VERIFICATION_FAILED", str(error))
            )
        return self._report(*self.ledger.read_current(passed))

    @staticmethod
    def _report(reference: ArtifactRef, run: ReplicationRun) -> ReplicationReport:
        return ReplicationReport(
            reference,
            run.state,
            run.exception,
            run.author_outputs,
            run.derived_ref,
            run.verification_ref,
        )

    def _authenticated_report(
        self, reference: ArtifactRef, run: ReplicationRun
    ) -> ReplicationReport:
        if run.state is ReplicationRunState.PASSED:
            verification = self.verifier.verify(reference)
            persist_verification_report(self._store, verification)
            if not verification.passed:
                message = verification.findings[0].message
                raise ValueError(f"PASSED evidence is invalid: {message}")
        return self._report(reference, run)

    def _fail(self, run_ref: ArtifactRef, fault: ReplicationFault) -> ReplicationReport:
        failed = self.ledger.exception(run_ref, fault.record())
        return self._report(*self.ledger.read_current(failed))

    def _attempt(
        self, coordinator: AttemptCoordinator, fault: ReplicationFault
    ) -> ReplicationReport:
        return self._attempt_report(
            *coordinator.persist_failure(fault.record(), fault.evidence)
        )

    @staticmethod
    def _attempt_report(
        reference: ArtifactRef, exception: ReplicationException
    ) -> ReplicationReport:
        return ReplicationReport(reference, ReplicationRunState.EXCEPTION, exception)

"""Independent read-only verification of exact current replication evidence."""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]

from envresearch.models.artifact import ArtifactRef, ResearchArtifact
from envresearch.replication._service_support import (
    artifact_path,
    restore_proposal,
)
from envresearch.replication._verification_models import (
    VerificationFinding as _VerificationFinding,
)
from envresearch.replication._verification_models import (
    VerificationPayload as _VerificationPayload,
)
from envresearch.replication._verification_models import (
    VerificationReport as _VerificationReport,
)
from envresearch.replication._verification_models import (
    finding,
    restore,
    seal_verification,
)
from envresearch.replication._verify_raw_evidence import raw_evidence_finding
from envresearch.replication._verify_support import (
    admission_reference_findings,
    ledger_history_path,
    read_artifact_reference,
    read_ledger_reference,
    require_copy,
    require_inputs,
    require_ledger_evidence,
    require_log_evidence,
    require_output_evidence,
    required_refs,
)
from envresearch.replication.contracts import ApprovedTier2Intake
from envresearch.replication.did_r import parse_derived_report
from envresearch.replication.ledger import ReplicationRun
from envresearch.storage.research_artifacts import ResearchArtifactStore

VerificationFinding = _VerificationFinding
VerificationPayload = _VerificationPayload
VerificationReport = _VerificationReport

_LEDGER = Path("artifacts/replication/replication-ledger.yaml")
_REPORT = Path("artifacts/replication/replication-report.json")


class ReplicationVerifier:
    """Reopen sealed run evidence without exposing persistence or promotion."""

    def __init__(self, store: ResearchArtifactStore) -> None:
        self._store = store

    def verify(self, run_ref: ArtifactRef) -> VerificationReport:
        """Return a sealed report of findings for one exact current run ref."""

        findings: list[VerificationFinding] = []
        ledger = self._read_ledger(_LEDGER, run_ref, "LEDGER_CURRENT_INVALID", findings)
        if ledger is None:
            return seal_verification(run_ref, (run_ref,), findings)
        self._verify_ledger_copy(
            ledger_history_path(run_ref), ledger, "LEDGER_HISTORY_INVALID", findings
        )
        self._verify_ledger_copy(_REPORT, ledger, "LEDGER_REPORT_INVALID", findings)

        approved = self._read_checked(
            artifact_path("approved", ledger.payload.approved_intake_ref),
            ledger.payload.approved_intake_ref,
            "approved-tier2-intake",
            "tier2-intake",
            "APPROVAL_REFERENCE_INVALID",
            findings,
        )
        proposal_ref = self._proposal_ref(approved, findings)
        proposal = self._read_checked(
            artifact_path("proposals", proposal_ref),
            proposal_ref,
            "tier2-intake-proposal",
            "tier2-intake",
            "PROPOSAL_REFERENCE_INVALID",
            findings,
        )
        inventory = self._read_checked(
            artifact_path("inventories", ledger.payload.acquired_inventory_ref),
            ledger.payload.acquired_inventory_ref,
            "acquired-tier2-package-inventory",
            "tier2-intake",
            "INVENTORY_REFERENCE_INVALID",
            findings,
        )
        runtime = self._read_checked(
            artifact_path("runtime", ledger.payload.runtime_ref),
            ledger.payload.runtime_ref,
            "tier2-runtime-observation",
            "tier2-container",
            "RUNTIME_REFERENCE_INVALID",
            findings,
        )
        self._verify_admission_chain(
            ledger, approved, proposal, inventory, runtime, proposal_ref, findings
        )
        attempt = self._read_checked(
            Path(
                "artifacts/replication/attempts/claims/reports/"
                f"{ledger.payload.attempt_ref.content_hash}.json"
            ),
            ledger.payload.attempt_ref,
            "tier2-replication-attempt-claim",
            "replication-service",
            "ATTEMPT_REFERENCE_INVALID",
            findings,
        )
        self._verify_attempt(ledger, attempt, findings)

        for output in ledger.payload.author_outputs:
            log = self._read_checked(
                artifact_path("logs", output.log_ref),
                output.log_ref,
                "tier2-execution-log",
                "tier2-replication",
                "LOG_REFERENCE_INVALID",
                findings,
            )
            self._verify_log(ledger, log, findings)
            artifact = self._read_checked(
                artifact_path("outputs", output.artifact_ref),
                output.artifact_ref,
                "tier2-author-output",
                "tier2-replication",
                "OUTPUT_REFERENCE_INVALID",
                findings,
            )
            self._verify_output(
                ledger, output.model_dump(mode="json"), artifact, findings
            )
        self._verify_declared_outputs(ledger, proposal, findings)
        if raw_finding := raw_evidence_finding(
            self._store, ledger, proposal, inventory
        ):
            findings.append(raw_finding)

        if ledger.payload.derived_ref is not None:
            derived_log_ref = ledger.payload.derived_log_ref
            if derived_log_ref is None:
                findings.append(
                    VerificationFinding(
                        code="LOG_REFERENCE_INVALID",
                        message="derived execution log is missing",
                    )
                )
            else:
                derived_log = self._read_checked(
                    artifact_path("logs", derived_log_ref),
                    derived_log_ref,
                    "tier2-execution-log",
                    "tier2-replication",
                    "LOG_REFERENCE_INVALID",
                    findings,
                )
                self._verify_log(ledger, derived_log, findings)
            derived = self._read_checked(
                artifact_path("derived", ledger.payload.derived_ref),
                ledger.payload.derived_ref,
                "tier2-derived-output",
                "tier2-replication",
                "DERIVED_REFERENCE_INVALID",
                findings,
            )
            self._verify_derived(ledger, derived, findings)
        self._verify_ledger_inputs(ledger, findings)
        refs = required_refs(ledger, proposal_ref)
        return seal_verification(run_ref, refs, findings)

    def _read_ledger(
        self,
        path: Path,
        reference: ArtifactRef,
        code: str,
        findings: list[VerificationFinding],
    ) -> ResearchArtifact[ReplicationRun] | None:
        try:
            return read_ledger_reference(self._store, path, reference)
        except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
            findings.append(finding(code, path, error))
            return None

    def _verify_ledger_copy(
        self,
        path: Path,
        expected: ResearchArtifact[ReplicationRun],
        code: str,
        findings: list[VerificationFinding],
    ) -> None:
        try:
            require_copy(self._store, path, expected)
        except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
            findings.append(finding(code, path, error))

    def _read_checked(
        self,
        path: Path,
        reference: ArtifactRef,
        artifact_id: str,
        producer: str,
        code: str,
        findings: list[VerificationFinding],
    ) -> ResearchArtifact[object] | None:
        try:
            return read_artifact_reference(
                self._store,
                path,
                reference,
                artifact_id=artifact_id,
                producer=producer,
            )
        except (OSError, TypeError, ValueError) as error:
            findings.append(finding(code, path, error))
            return None

    @staticmethod
    def _proposal_ref(
        approved: ResearchArtifact[object] | None,
        findings: list[VerificationFinding],
    ) -> ArtifactRef:
        try:
            if approved is None:
                raise ValueError("approved intake is unavailable")
            payload = restore(ApprovedTier2Intake, approved.payload)
            return payload.proposal_ref
        except (TypeError, ValueError) as error:
            findings.append(
                VerificationFinding(
                    code="APPROVAL_REFERENCE_INVALID",
                    message=str(error),
                    evidence=("approved intake payload",),
                )
            )
            return ArtifactRef(
                artifact_id="missing-proposal",
                artifact_version=1,
                content_hash="0" * 64,
            )

    @staticmethod
    def _verify_admission_chain(
        ledger: ResearchArtifact[ReplicationRun],
        approved: ResearchArtifact[object] | None,
        proposal: ResearchArtifact[object] | None,
        inventory: ResearchArtifact[object] | None,
        runtime: ResearchArtifact[object] | None,
        proposal_ref: ArtifactRef,
        findings: list[VerificationFinding],
    ) -> None:
        errors = admission_reference_findings(
            ledger, approved, proposal, inventory, runtime, proposal_ref
        )
        findings.extend(
            VerificationFinding(code=code, message=message) for code, message in errors
        )

    @staticmethod
    def _verify_output(
        ledger: ResearchArtifact[ReplicationRun],
        result: dict[str, object],
        artifact: ResearchArtifact[object] | None,
        findings: list[VerificationFinding],
    ) -> None:
        if artifact is None:
            return
        try:
            require_output_evidence(ledger, result, artifact)
        except ValueError:
            findings.append(
                VerificationFinding(
                    code="OUTPUT_REFERENCE_INVALID",
                    message="author output evidence differs from the completed ledger",
                )
            )

    @staticmethod
    def _verify_log(
        ledger: ResearchArtifact[ReplicationRun],
        artifact: ResearchArtifact[object] | None,
        findings: list[VerificationFinding],
    ) -> None:
        if artifact is None:
            return
        try:
            require_log_evidence(
                artifact,
                (
                    ledger.payload.approved_intake_ref,
                    ledger.payload.acquired_inventory_ref,
                    ledger.payload.runtime_ref,
                ),
            )
        except ValueError as error:
            findings.append(
                VerificationFinding(code="LOG_REFERENCE_INVALID", message=str(error))
            )

    @staticmethod
    def _verify_attempt(
        ledger: ResearchArtifact[ReplicationRun],
        artifact: ResearchArtifact[object] | None,
        findings: list[VerificationFinding],
    ) -> None:
        try:
            if artifact is None or not isinstance(artifact.payload, dict):
                raise ValueError("attempt claim is unavailable")
            if artifact.envelope.input_artifacts != (
                ledger.payload.approved_intake_ref,
            ):
                raise ValueError("attempt claim does not bind approval")
            if artifact.payload.get("output_root") != ledger.payload.output_root:
                raise ValueError("attempt claim output root differs from ledger")
        except ValueError as error:
            findings.append(
                VerificationFinding(
                    code="ATTEMPT_REFERENCE_INVALID", message=str(error)
                )
            )

    @staticmethod
    def _verify_declared_outputs(
        ledger: ResearchArtifact[ReplicationRun],
        proposal: ResearchArtifact[object] | None,
        findings: list[VerificationFinding],
    ) -> None:
        try:
            if proposal is None:
                raise ValueError("proposal is unavailable")
            declared = restore_proposal(proposal.payload).expected_outputs
            observed = ledger.payload.author_outputs
            if len({item.path for item in observed}) != len(observed):
                raise ValueError("output comparison entries are not unique")
            if {item.path for item in observed} != {item.path for item in declared}:
                raise ValueError("declared outputs and comparisons differ")
        except (TypeError, ValueError) as error:
            findings.append(
                VerificationFinding(code="DECLARED_OUTPUT_INVALID", message=str(error))
            )

    @staticmethod
    def _verify_derived(
        ledger: ResearchArtifact[ReplicationRun],
        artifact: ResearchArtifact[object] | None,
        findings: list[VerificationFinding],
    ) -> None:
        if artifact is None:
            return
        expected_inputs = (
            ledger.payload.approved_intake_ref,
            ledger.payload.acquired_inventory_ref,
            ledger.payload.runtime_ref,
            *(item.artifact_ref for item in ledger.payload.author_outputs),
            *(
                (ledger.payload.derived_log_ref,)
                if ledger.payload.derived_log_ref
                else ()
            ),
        )
        try:
            require_inputs(artifact, expected_inputs)
            parse_derived_report(artifact.payload)
        except (TypeError, ValueError) as error:
            findings.append(
                VerificationFinding(
                    code="DERIVED_REFERENCE_INVALID", message=str(error)
                )
            )

    @staticmethod
    def _verify_ledger_inputs(
        ledger: ResearchArtifact[ReplicationRun], findings: list[VerificationFinding]
    ) -> None:
        try:
            require_ledger_evidence(ledger)
        except ValueError:
            findings.append(
                VerificationFinding(
                    code="LEDGER_INPUT_CHAIN_INVALID",
                    message="ledger envelope omits or reorders current evidence refs",
                )
            )

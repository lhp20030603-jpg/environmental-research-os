"""Transactional independent audit of evidence-bound paper drafts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_reconstruction import (
    audit_transitive_refs,
    reconstruct_audit_findings,
)
from envresearch.paper._audit_store import (
    AuditStore,
    audit_id,
    audit_subject,
)
from envresearch.paper._audit_transaction import publish_locked, status_locked
from envresearch.paper._draft_store import PAPER_DRAFT_SUBJECT, DraftStore
from envresearch.paper.argument_contracts import ArgumentMap
from envresearch.paper.argument_map import ARGUMENT_MAP_SUBJECT
from envresearch.paper.audit_contracts import (
    PaperAuditReport,
    analysis_ref_key,
    output_ref_key,
)
from envresearch.paper.citation_authority import CitationAuthoritySnapshot
from envresearch.paper.contracts import ClaimEvidenceLedger
from envresearch.paper.draft_builder import DraftService
from envresearch.paper.draft_contracts import PaperDraft
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperBuilderError,
    PaperIntegrityInvalid,
)
from envresearch.paper.ledger import CLAIM_LEDGER_SUBJECT


@dataclass(frozen=True, slots=True)
class _AuditInputs:
    draft: PaperDraft
    argument_map: ArgumentMap
    ledger: ClaimEvidenceLedger
    citations: CitationAuthoritySnapshot


class PaperAuditService:
    """Publish and independently reopen complete immutable paper audits."""

    def __init__(self, *, draft_service: DraftService) -> None:
        self.draft_service = draft_service
        self.map_service = draft_service.map_service
        self.ledger_service = draft_service.ledger_service
        self.citation_authority = draft_service.citation_authority
        self.registry = draft_service.registry
        self.drafts = DraftStore(self.registry)
        self.store = AuditStore(self.registry)

    def audit(self, draft_ref: ArtifactRef) -> ArtifactRef:
        """Independently reconstruct, publish, and promote one complete audit."""
        subject = audit_subject(draft_ref)
        with self._paper_authority_lease(subject):
            return self._publish_locked(draft_ref, required_current=draft_ref)

    def status(
        self, audit_ref: ArtifactRef, draft_ref: ArtifactRef
    ) -> PaperAuditReport:
        """Reopen audit bytes and independently reconstruct every exact input."""
        subject = audit_subject(draft_ref)
        with self._paper_authority_lease(subject):
            return self._status_locked(audit_ref, draft_ref, required_current=draft_ref)

    def _publish_locked(
        self, draft_ref: ArtifactRef, *, required_current: ArtifactRef
    ) -> ArtifactRef:
        return publish_locked(self, draft_ref, required_current)

    def _status_locked(
        self,
        audit_ref: ArtifactRef,
        draft_ref: ArtifactRef,
        *,
        required_current: ArtifactRef,
    ) -> PaperAuditReport:
        return status_locked(self, audit_ref, draft_ref, required_current)

    @contextmanager
    def _paper_authority_lease(self, audit_lock: str) -> Iterator[None]:
        """Hold every external and paper authority in one global lock order."""
        try:
            with ExitStack() as stack:
                stack.enter_context(self.ledger_service.resolver.authority_lease())
                stack.enter_context(self.citation_authority.authority_lease())
                stack.enter_context(self.registry.lock(CLAIM_LEDGER_SUBJECT))
                stack.enter_context(self.registry.lock(ARGUMENT_MAP_SUBJECT))
                stack.enter_context(self.registry.lock(PAPER_DRAFT_SUBJECT))
                stack.enter_context(self.registry.lock(audit_lock))
                yield
        except PaperBuilderError:
            raise
        except ValueError as exc:
            raise PaperAuthorityInvalid(
                "paper audit authority lease is not current",
                finding_kind="audit-authority-lease-invalid",
            ) from exc
        except (OSError, TypeError) as exc:
            raise PaperIntegrityInvalid(
                "paper audit authority lease failed",
                finding_kind="audit-authority-lease-invalid",
            ) from exc

    def _open_inputs(
        self, draft_ref: ArtifactRef, required_current: ArtifactRef
    ) -> _AuditInputs:
        if self.drafts.current() != required_current:
            raise PaperAuthorityInvalid(
                "paper draft reference is not current",
                finding_kind="draft-not-current",
            )
        draft = self.drafts.load(draft_ref)
        try:
            argument_map = self.map_service.status(draft.map_ref, draft.ledger_ref)
            ledger = self.ledger_service.status(
                draft.ledger_ref, argument_map.transition_ref
            )
            citations = self.citation_authority.reopen(draft.citation_report_ref)
        except PaperBuilderError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise PaperIntegrityInvalid(
                "paper audit upstream bytes are invalid",
                finding_kind="audit-upstream-integrity-invalid",
            ) from exc
        if (
            argument_map.ledger_ref != draft.ledger_ref
            or ledger.transition_ref != argument_map.transition_ref
            or citations.report[0] != draft.citation_report_ref
        ):
            raise PaperAuthorityInvalid(
                "paper audit exact upstream references disagree",
                finding_kind="audit-upstream-invalid",
            )
        if self.drafts.current() != required_current:
            raise PaperAuthorityInvalid(
                "paper draft current changed during audit input reopening",
                finding_kind="draft-not-current",
            )
        return _AuditInputs(draft, argument_map, ledger, citations)

    def _require_same_inputs(
        self,
        draft_ref: ArtifactRef,
        required_current: ArtifactRef,
        expected: _AuditInputs,
    ) -> _AuditInputs:
        current = self._open_inputs(draft_ref, required_current)
        if current != expected:
            raise PaperAuthorityInvalid(
                "paper audit upstream changed during reconstruction",
                finding_kind="audit-upstream-changed",
            )
        return current

    @staticmethod
    def _materialize(draft_ref: ArtifactRef, inputs: _AuditInputs) -> PaperAuditReport:
        findings = reconstruct_audit_findings(
            draft_ref=draft_ref,
            draft=inputs.draft,
            argument_map=inputs.argument_map,
            ledger=inputs.ledger,
            citation_snapshot=inputs.citations,
        )
        analysis_refs = tuple(
            sorted(
                {item.analysis_ref for item in inputs.ledger.claims},
                key=analysis_ref_key,
            )
        )
        output_refs = tuple(
            sorted(
                {
                    output
                    for item in inputs.ledger.claims
                    for output in item.output_evidence
                },
                key=output_ref_key,
            )
        )
        _, citation_report = inputs.citations.report
        transition_refs = tuple(
            sorted(
                {
                    inputs.argument_map.transition_ref,
                    inputs.ledger.transition_ref,
                    *(item.transition_ref for item in inputs.ledger.claims),
                },
                key=lambda item: (
                    item.artifact_id,
                    item.artifact_version,
                    item.content_hash,
                ),
            )
        )
        snapshot_refs = tuple(
            sorted(
                {item.snapshot_ref for item in inputs.ledger.claims},
                key=lambda item: (
                    item.artifact_id,
                    item.artifact_version,
                    item.content_hash,
                ),
            )
        )
        citation_source_refs = tuple(
            sorted(
                {
                    *(item[0] for item in inputs.citations.source_sheets),
                    *citation_report.source_sheet_refs,
                },
                key=lambda item: (
                    item.artifact_id,
                    item.artifact_version,
                    item.content_hash,
                ),
            )
        )
        return PaperAuditReport(
            schema_version="paper.audit-report.v1",
            audit_id=audit_id(draft_ref),
            producer="paper-builder-auditor-v1",
            draft_ref=draft_ref,
            map_ref=inputs.draft.map_ref,
            ledger_ref=inputs.draft.ledger_ref,
            citation_report_ref=inputs.draft.citation_report_ref,
            transitive_refs=audit_transitive_refs(
                draft_ref,
                inputs.draft,
                inputs.argument_map,
                inputs.ledger,
                inputs.citations,
            ),
            transition_refs=transition_refs,
            snapshot_refs=snapshot_refs,
            citation_source_refs=citation_source_refs,
            claim_fact_map_refs=citation_report.claim_fact_map_refs,
            blinded_brief_refs=citation_report.blinded_brief_refs,
            accepted_artifact_refs=citation_report.accepted_artifact_refs,
            analysis_refs=analysis_refs,
            output_refs=output_refs,
            findings=findings,
            verdict="blocked" if findings else "clean",
        )

    def _validate_report(
        self,
        reference: ArtifactRef,
        report: PaperAuditReport,
        inputs: _AuditInputs,
    ) -> None:
        expected = self._materialize(report.draft_ref, inputs)
        if report != expected:
            raise PaperIntegrityInvalid(
                "paper audit does not match independent reconstruction",
                finding_kind="audit-reconstruction-mismatch",
            )
        self._require_same_report(reference, report)

    def _validate_pending(
        self,
        reference: ArtifactRef,
        report: PaperAuditReport,
        inputs: _AuditInputs,
    ) -> None:
        self._require_pending(reference, report.draft_ref)
        self._require_same_report(reference, report)
        expected = self._materialize(report.draft_ref, inputs)
        if expected != report:
            raise PaperIntegrityInvalid(
                "paper audit reconstruction changed during publication",
                finding_kind="audit-reconstruction-mismatch",
            )
        self._require_pending(reference, report.draft_ref)

    def _final_authority(
        self,
        draft_ref: ArtifactRef,
        required_current: ArtifactRef,
        expected: _AuditInputs,
    ) -> None:
        self._require_same_inputs(draft_ref, required_current, expected)
        try:
            self.citation_authority.require_current(expected.citations.token)
        except PaperBuilderError:
            raise
        except ValueError as exc:
            raise PaperAuthorityInvalid(
                "paper audit citation generation is not current",
                finding_kind="citation-source-not-current",
            ) from exc
        except (OSError, TypeError) as exc:
            raise PaperIntegrityInvalid(
                "paper audit citation authority failed",
                finding_kind="citation-authority-invalid",
            ) from exc

    def _require_current(self, audit_ref: ArtifactRef, draft_ref: ArtifactRef) -> None:
        if self.store.current(draft_ref) != audit_ref:
            raise PaperAuthorityInvalid(
                "paper audit reference is not current for its draft",
                finding_kind="audit-not-current",
            )

    def _require_pending(self, audit_ref: ArtifactRef, draft_ref: ArtifactRef) -> None:
        if self.store.pending(draft_ref) != audit_ref:
            raise PaperAuthorityInvalid(
                "paper audit pending reference changed during publication",
                finding_kind="audit-not-current",
            )

    def _require_same_report(
        self, reference: ArtifactRef, expected: PaperAuditReport
    ) -> PaperAuditReport:
        reopened = self.store.load(reference)
        if reopened != expected:
            raise PaperIntegrityInvalid(
                "paper audit changed during reconstruction",
                finding_kind="audit-reconstruction-mismatch",
            )
        return reopened


__all__ = [
    "PaperAuditService",
    "audit_subject",
    "audit_transitive_refs",
    "reconstruct_audit_findings",
]

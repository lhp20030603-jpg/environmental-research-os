"""Transactional closure and promotion of one blocked paper draft."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_store import audit_subject
from envresearch.paper._draft_store import PAPER_DRAFT_SUBJECT, DraftStore
from envresearch.paper._revision_draft import successor_draft
from envresearch.paper._revision_recovery import resume_committed, resume_pending
from envresearch.paper._revision_store import RevisionStore, revision_subject
from envresearch.paper._revision_validation import (
    materialize_revision,
    require_revision_chain,
)
from envresearch.paper.argument_map import ARGUMENT_MAP_SUBJECT
from envresearch.paper.audit_contracts import PaperAuditReport
from envresearch.paper.auditor import PaperAuditService
from envresearch.paper.draft_contracts import PaperDraft, PaperDraftCandidate
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperBuilderError,
    PaperIntegrityInvalid,
    PaperSupportInvalid,
)
from envresearch.paper.ledger import CLAIM_LEDGER_SUBJECT
from envresearch.paper.revision_contracts import DraftRevision


class RevisionService:
    """Close every blocked finding and atomically promote the clean successor."""

    def __init__(self, *, audit_service: PaperAuditService) -> None:
        self.audits = audit_service
        self.registry = audit_service.registry
        self.drafts = DraftStore(self.registry)
        self.store = RevisionStore(self.registry)
        self.ledger_service = audit_service.ledger_service
        self.citation_authority = audit_service.citation_authority

    def revise(
        self, predecessor_ref: ArtifactRef, candidate: PaperDraftCandidate
    ) -> ArtifactRef:
        """Stage a clean next generation, then promote the draft pointer last."""
        preview = self.drafts.load(predecessor_ref)
        staged = successor_draft(predecessor_ref, preview, candidate)
        expected_successor_ref = self.drafts.expected_ref(staged)
        with self._authority_lease(predecessor_ref, expected_successor_ref):
            current = self.drafts.current()
            if current not in {predecessor_ref, expected_successor_ref}:
                raise PaperAuthorityInvalid(
                    "paper revision predecessor is stale",
                    finding_kind="revision-predecessor-not-current",
                )
            prepared_ref = self.store.current(predecessor_ref)
            if prepared_ref is not None:
                return resume_committed(
                    self,
                    prepared_ref,
                    predecessor_ref,
                    expected_successor_ref,
                    current,
                )
            pending_ref = self.store.pending(predecessor_ref)
            if pending_ref is not None:
                return resume_pending(
                    self,
                    pending_ref,
                    predecessor_ref,
                    expected_successor_ref,
                    current,
                )
            if self.store.committed(predecessor_ref) is not None:
                raise PaperIntegrityInvalid(
                    "paper revision commit marker has no matching pending pointer",
                    finding_kind="revision-commit-invalid",
                )
            if current == expected_successor_ref:
                raise PaperIntegrityInvalid(
                    "promoted paper revision has no committed closure envelope",
                    finding_kind="revision-commit-missing",
                )
            predecessor = self._require_predecessor(predecessor_ref, preview)
            successor = successor_draft(predecessor_ref, predecessor, candidate)
            if self.drafts.expected_ref(successor) != expected_successor_ref:
                raise PaperIntegrityInvalid(
                    "paper revision candidate changed before staging",
                    finding_kind="revision-candidate-changed",
                )
            predecessor_report, predecessor_audit_ref = self._blocked_audit(
                predecessor_ref
            )
            successor_ref = self.drafts.publish_exact(successor)
            successor_audit_ref = self.audits._publish_locked(
                successor_ref, required_current=predecessor_ref
            )
            successor_report = self.audits._status_locked(
                successor_audit_ref,
                successor_ref,
                required_current=predecessor_ref,
            )
            if successor_report.verdict != "clean" or successor_report.findings:
                raise PaperSupportInvalid(
                    "paper revision does not close every audit finding",
                    finding_kind="revision-partial-closure",
                )
            revision = materialize_revision(
                predecessor,
                predecessor_ref,
                predecessor_audit_ref,
                predecessor_report,
                successor_ref,
                successor_audit_ref,
            )
            revision_ref = self.store.publish(revision)
            self.store.prepare(predecessor_ref, revision_ref)
            if self.store.load(revision_ref) != revision:
                raise PaperIntegrityInvalid(
                    "paper revision changed during preparation",
                    finding_kind="revision-reconstruction-mismatch",
                )
            self.store.commit(predecessor_ref, revision_ref)
            self._require_pre_promotion(
                predecessor_ref,
                predecessor_audit_ref,
                successor_ref,
                successor_audit_ref,
                revision_ref,
            )
            try:
                self.drafts.promote_if_current(
                    previous=predecessor_ref, installed=successor_ref
                )
            except PaperBuilderError:
                raise
            except (OSError, TypeError, ValueError) as exc:
                current = self.drafts.current()
                if current == successor_ref:
                    return revision_ref
                if current != predecessor_ref:
                    raise PaperAuthorityInvalid(
                        "paper revision lost draft promotion authority",
                        finding_kind="revision-promotion-conflict",
                    ) from exc
                raise PaperIntegrityInvalid(
                    "paper revision promotion failed",
                    finding_kind="revision-promotion-failed",
                ) from exc
            return revision_ref

    def status(
        self, revision_ref: ArtifactRef, predecessor_ref: ArtifactRef
    ) -> DraftRevision:
        """Reopen the promoted revision and both audits under exact authority."""
        preview = self.store.load(revision_ref)
        if preview.predecessor_ref != predecessor_ref:
            raise PaperAuthorityInvalid(
                "paper revision binds another predecessor",
                finding_kind="revision-predecessor-mismatch",
            )
        with self._authority_lease(predecessor_ref, preview.successor_ref):
            return self._status_locked(revision_ref, predecessor_ref, preview)

    def _status_locked(
        self,
        revision_ref: ArtifactRef,
        predecessor_ref: ArtifactRef,
        preview: DraftRevision | None = None,
        *,
        required_current: ArtifactRef | None = None,
    ) -> DraftRevision:
        """Reconstruct one revision while the caller owns its authority lease."""
        preview = self.store.load(revision_ref) if preview is None else preview
        if preview.predecessor_ref != predecessor_ref:
            raise PaperAuthorityInvalid(
                "paper revision binds another predecessor",
                finding_kind="revision-predecessor-mismatch",
            )
        if self.store.current(predecessor_ref) != revision_ref:
            raise PaperAuthorityInvalid(
                "paper revision reference is not current",
                finding_kind="revision-not-current",
            )
        current = (
            preview.successor_ref if required_current is None else required_current
        )
        if self.drafts.current() != current:
            raise PaperAuthorityInvalid(
                "paper revision successor is not current",
                finding_kind="revision-successor-not-current",
            )
        revision = self.store.load(revision_ref)
        if revision != preview:
            raise PaperIntegrityInvalid(
                "paper revision changed during status",
                finding_kind="revision-reconstruction-mismatch",
            )
        predecessor = self.drafts.load(predecessor_ref)
        successor = self.drafts.load(revision.successor_ref)
        require_revision_chain(revision, predecessor, successor)
        blocked = self.audits._status_locked(
            revision.predecessor_audit_ref,
            predecessor_ref,
            required_current=current,
        )
        clean = self.audits._status_locked(
            revision.successor_audit_ref,
            revision.successor_ref,
            required_current=current,
        )
        expected = materialize_revision(
            predecessor,
            predecessor_ref,
            revision.predecessor_audit_ref,
            blocked,
            revision.successor_ref,
            revision.successor_audit_ref,
        )
        if clean.verdict != "clean" or clean.findings or revision != expected:
            raise PaperIntegrityInvalid(
                "paper revision closure reconstruction failed",
                finding_kind="revision-closure-mismatch",
            )
        if (
            self.store.current(predecessor_ref) != revision_ref
            or self.drafts.current() != current
            or self.store.load(revision_ref) != revision
        ):
            raise PaperAuthorityInvalid(
                "paper revision authority changed during status",
                finding_kind="revision-not-current",
            )
        return revision

    def _blocked_audit(
        self, predecessor_ref: ArtifactRef
    ) -> tuple[PaperAuditReport, ArtifactRef]:
        audit_ref = self.audits.store.current(predecessor_ref)
        if audit_ref is None:
            audit_ref = self.audits._publish_locked(
                predecessor_ref, required_current=predecessor_ref
            )
        report = self.audits._status_locked(
            audit_ref, predecessor_ref, required_current=predecessor_ref
        )
        if report.verdict != "blocked" or not report.findings:
            raise PaperSupportInvalid(
                "paper revision requires a blocked predecessor audit with findings",
                finding_kind="revision-predecessor-clean",
            )
        return report, audit_ref

    def _require_predecessor(
        self, predecessor_ref: ArtifactRef, preview: PaperDraft
    ) -> PaperDraft:
        if self.drafts.current() != predecessor_ref:
            raise PaperAuthorityInvalid(
                "paper revision predecessor is not current",
                finding_kind="revision-predecessor-not-current",
            )
        predecessor = self.drafts.load(predecessor_ref)
        if predecessor != preview:
            raise PaperIntegrityInvalid(
                "paper revision predecessor changed before staging",
                finding_kind="revision-predecessor-changed",
            )
        self.audits._open_inputs(predecessor_ref, predecessor_ref)
        return predecessor

    def _require_pre_promotion(
        self,
        predecessor_ref: ArtifactRef,
        predecessor_audit_ref: ArtifactRef,
        successor_ref: ArtifactRef,
        successor_audit_ref: ArtifactRef,
        revision_ref: ArtifactRef,
    ) -> None:
        if (
            self.drafts.current() != predecessor_ref
            or self.audits.store.current(predecessor_ref) != predecessor_audit_ref
            or self.audits.store.current(successor_ref) != successor_audit_ref
            or self.store.current(predecessor_ref) != revision_ref
        ):
            raise PaperAuthorityInvalid(
                "paper revision authority changed before promotion",
                finding_kind="revision-authority-changed",
            )

    @contextmanager
    def _authority_lease(
        self, predecessor_ref: ArtifactRef, successor_ref: ArtifactRef
    ) -> Iterator[None]:
        """Hold the shared authority order through final draft promotion."""
        try:
            with ExitStack() as stack:
                stack.enter_context(self.ledger_service.resolver.authority_lease())
                stack.enter_context(self.citation_authority.authority_lease())
                stack.enter_context(self.registry.lock(CLAIM_LEDGER_SUBJECT))
                stack.enter_context(self.registry.lock(ARGUMENT_MAP_SUBJECT))
                stack.enter_context(self.registry.lock(PAPER_DRAFT_SUBJECT))
                for subject in sorted(
                    (audit_subject(predecessor_ref), audit_subject(successor_ref))
                ):
                    stack.enter_context(self.registry.lock(subject))
                stack.enter_context(
                    self.registry.lock(revision_subject(predecessor_ref))
                )
                yield
        except PaperBuilderError:
            raise
        except ValidationError as exc:
            raise PaperSupportInvalid(
                "paper revision payload is invalid",
                finding_kind="revision-payload-invalid",
            ) from exc
        except (OSError, TypeError, ValueError) as exc:
            raise PaperIntegrityInvalid(
                "paper revision authority lease failed",
                finding_kind="revision-authority-invalid",
            ) from exc


__all__ = ["RevisionService"]

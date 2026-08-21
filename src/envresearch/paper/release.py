"""Current audited Paper Builder release and stable V1.0 handoff."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.paper.audit_contracts import PaperAuditReport
from envresearch.paper.auditor import PaperAuditService, audit_subject
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperIntegrityInvalid,
    PaperSupportInvalid,
)
from envresearch.paper.release_contracts import PaperReleaseCandidate, release_id
from envresearch.paper.release_revisions import (
    RevisionPair,
    chain_authority,
    preview_chain,
    reopen_chain_locked,
)
from envresearch.paper.revision import RevisionService

PAPER_RELEASE_SUBJECT = "paper-release"
PAPER_RELEASE_PENDING_SUBJECT = "paper-release-pending"
PAPER_RELEASE_LOCK_SUBJECT = "paper-release-publication"


class _ReleaseStore:
    def __init__(self, service: PaperReleaseService) -> None:
        self.service = service
        self.registry = service.registry

    def current(self) -> ArtifactRef | None:
        pending = self._pointer(PAPER_RELEASE_PENDING_SUBJECT)
        committed = self._pointer(PAPER_RELEASE_SUBJECT)
        if pending is None and committed is None:
            return None
        if pending is None or pending != committed:
            raise PaperIntegrityInvalid(
                "paper release pointer pair is torn",
                finding_kind="release-current-invalid",
            )
        self.load(pending)
        return pending

    def pending(self) -> ArtifactRef | None:
        return self._pointer(PAPER_RELEASE_PENDING_SUBJECT)

    def committed(self) -> ArtifactRef | None:
        return self._pointer(PAPER_RELEASE_SUBJECT)

    def recover_uncommitted(self) -> None:
        """Restore an authenticated prior commit without exposing torn intent."""
        pending = self.pending()
        committed = self.committed()
        if pending == committed:
            if pending is not None:
                self.load(pending)
            return
        if pending is None:
            raise PaperIntegrityInvalid(
                "paper release commit marker has no matching pending pointer",
                finding_kind="release-commit-invalid",
            )
        if committed is None:
            return
        self.load(committed)
        try:
            restored = self.registry.restore_current_if_unchanged(
                PAPER_RELEASE_PENDING_SUBJECT,
                installed=pending,
                previous=committed,
            )
            if not restored:
                raise ValueError("release recovery lost pending-pointer ownership")
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper release pending-pointer recovery failed",
                finding_kind="release-recovery-failed",
            ) from exc

    def commit(self, reference: ArtifactRef) -> None:
        """Linearize one already revalidated prepared release."""
        try:
            if self.pending() != reference:
                raise ValueError("release pending pointer changed before commit")
            self.registry.set_current(PAPER_RELEASE_SUBJECT, reference)
        except (OSError, ValueError, ValidationError) as exc:
            if self.current() == reference:
                return
            raise PaperIntegrityInvalid(
                "paper release commit publication failed",
                finding_kind="release-commit-failed",
            ) from exc

    def load(self, reference: ArtifactRef) -> PaperReleaseCandidate:
        relative = (
            Path("exit/objects")
            / reference.artifact_id
            / f"v{reference.artifact_version}-{reference.content_hash}.json"
        )
        try:
            data = self.registry.files.read(relative)
            if hashlib.sha256(data).hexdigest() != reference.content_hash:
                raise ValueError("release content hash mismatch")
            candidate = PaperReleaseCandidate.model_validate_json(data)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper release bytes are invalid",
                finding_kind="release-bytes-invalid",
            ) from exc
        if (
            reference.artifact_id != candidate.release_id
            or reference.artifact_version != 1
            or data != candidate.model_dump_json().encode()
        ):
            raise PaperIntegrityInvalid(
                "paper release reference identity is invalid",
                finding_kind="release-identity-invalid",
            )
        return candidate

    def _pointer(self, subject: str) -> ArtifactRef | None:
        try:
            return self.registry.current(subject)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper release pointer is invalid",
                finding_kind="release-pointer-invalid",
            ) from exc


class PaperReleaseService:
    """Publish and reopen the sole current clean audited paper candidate."""

    def __init__(self, *, audit_service: PaperAuditService) -> None:
        self.audit_service = audit_service
        self.registry = audit_service.registry
        self.store = _ReleaseStore(self)

    def build(
        self,
        audit_ref: ArtifactRef,
        draft_ref: ArtifactRef,
        *,
        revision_ref: ArtifactRef | None = None,
    ) -> ArtifactRef:
        """Publish only one explicitly referenced clean current audit chain."""
        draft = self.audit_service.drafts.load(draft_ref)
        if draft.generation == 1:
            if revision_ref is not None:
                raise PaperAuthorityInvalid(
                    "generation-one release cannot bind a revision",
                    finding_kind="release-revision-invalid",
                )
            with (
                self.audit_service._paper_authority_lease(audit_subject(draft_ref)),
                self.registry.lock(PAPER_RELEASE_LOCK_SUBJECT),
            ):
                return self._build_locked(audit_ref, draft_ref, ())
        revisions = RevisionService(audit_service=self.audit_service)
        chain = preview_chain(revisions, draft_ref, revision_ref)
        with (
            chain_authority(revisions, chain),
            self.registry.lock(PAPER_RELEASE_LOCK_SUBJECT),
        ):
            if self.audit_service.drafts.load(draft_ref) != draft:
                raise PaperIntegrityInvalid(
                    "release draft changed before revision reopening",
                    finding_kind="release-draft-changed",
                )
            chain = reopen_chain_locked(revisions, chain, draft_ref)
            return self._build_locked(audit_ref, draft_ref, chain)

    def _build_locked(
        self,
        audit_ref: ArtifactRef,
        draft_ref: ArtifactRef,
        chain: tuple[RevisionPair, ...],
    ) -> ArtifactRef:
        report = self.audit_service._status_locked(
            audit_ref, draft_ref, required_current=draft_ref
        )
        expected = self._materialize(audit_ref, report, chain)
        return self._publish_locked(expected)

    def status(self, release_ref: ArtifactRef) -> PaperReleaseCandidate:
        """Read and independently reopen one explicit release without recovery."""
        with self._authority_lease(release_ref) as (initial, chain):
            return self._status_locked(release_ref, initial, chain)

    @contextmanager
    def _authority_lease(
        self, release_ref: ArtifactRef
    ) -> Iterator[tuple[PaperReleaseCandidate, tuple[RevisionPair, ...]]]:
        """Hold the complete release authority for an already known exact ref."""
        initial = self.store.load(release_ref)
        draft = self.audit_service.drafts.load(initial.draft_ref)
        if draft.generation == 1:
            if initial.revision_ref is not None or initial.revision is not None:
                raise PaperIntegrityInvalid(
                    "generation-one release has a revision closure",
                    finding_kind="release-revision-invalid",
                )
            authority = self.audit_service._paper_authority_lease(
                audit_subject(initial.draft_ref)
            )
            revisions = None
            chain: tuple[RevisionPair, ...] = ()
        else:
            revisions = RevisionService(audit_service=self.audit_service)
            chain = tuple(zip(initial.revision_refs, initial.revisions))
            if not chain or chain[-1] != (initial.revision_ref, initial.revision):
                raise PaperIntegrityInvalid(
                    "release revision payload changed",
                    finding_kind="release-revision-mismatch",
                )
            authority = chain_authority(revisions, chain)
        with authority, self.registry.lock(PAPER_RELEASE_LOCK_SUBJECT):
            if self.audit_service.drafts.load(initial.draft_ref) != draft:
                raise PaperIntegrityInvalid(
                    "release draft changed before status reopening",
                    finding_kind="release-draft-changed",
                )
            yield initial, chain

    def _status_locked(
        self,
        release_ref: ArtifactRef,
        initial: PaperReleaseCandidate,
        chain: tuple[RevisionPair, ...],
    ) -> PaperReleaseCandidate:
        """Independently reconstruct a release while its full authority is held."""
        revisions = (
            RevisionService(audit_service=self.audit_service) if chain else None
        )
        if revisions is not None:
            if chain[-1] != (initial.revision_ref, initial.revision):
                raise PaperIntegrityInvalid(
                    "release revision payload changed",
                    finding_kind="release-revision-mismatch",
                )
            reopened = reopen_chain_locked(revisions, chain, initial.draft_ref)
            if reopened != chain:
                raise PaperIntegrityInvalid(
                    "release revision reconstruction changed",
                    finding_kind="release-revision-mismatch",
                )
        if self.store.current() != release_ref:
            raise PaperAuthorityInvalid(
                "paper release reference is not current",
                finding_kind="release-not-current",
            )
        release = self._require_same_release(release_ref, initial)
        report = self.audit_service._status_locked(
            release.audit_ref,
            release.draft_ref,
            required_current=release.draft_ref,
        )
        expected = self._materialize(
            release.audit_ref,
            report,
            chain,
        )
        if release != expected:
            raise PaperIntegrityInvalid(
                "paper release does not match independent reconstruction",
                finding_kind="release-reconstruction-mismatch",
            )
        self._require_same_release(release_ref, release)
        if self.store.current() != release_ref:
            raise PaperAuthorityInvalid(
                "paper release changed during status",
                finding_kind="release-not-current",
            )
        return release

    def handoff(
        self, release_ref: ArtifactRef
    ) -> tuple[ArtifactRef, PaperReleaseCandidate]:
        """Return the stable V1.0 pair after a fresh exact-chain status."""
        return release_ref, self.status(release_ref)

    @staticmethod
    def _materialize(
        audit_ref: ArtifactRef,
        report: PaperAuditReport,
        chain: tuple[RevisionPair, ...] = (),
    ) -> PaperReleaseCandidate:
        if report.verdict != "clean" or report.findings:
            raise PaperSupportInvalid(
                "paper audit has open findings and is not releasable",
                finding_kind="audit-findings-open",
            )
        revision_refs = tuple(item[0] for item in chain)
        revisions = tuple(item[1] for item in chain)
        revision_ref = revision_refs[-1] if revision_refs else None
        revision = revisions[-1] if revisions else None
        try:
            return PaperReleaseCandidate(
                schema_version="paper.release-candidate.v1",
                release_id=release_id(audit_ref, revision_refs),
                producer="paper-builder-release-v1",
                audit_ref=audit_ref,
                draft_ref=report.draft_ref,
                map_ref=report.map_ref,
                ledger_ref=report.ledger_ref,
                citation_report_ref=report.citation_report_ref,
                revision_ref=revision_ref,
                revision=revision,
                revision_refs=revision_refs,
                revisions=revisions,
                transitive_refs=report.transitive_refs,
                analysis_refs=report.analysis_refs,
                output_refs=report.output_refs,
                audit_report=report,
                verdict="current-green",
            )
        except ValidationError as exc:
            raise PaperIntegrityInvalid(
                "paper release materialization is invalid",
                finding_kind="release-materialization-invalid",
            ) from exc

    def _publish_locked(self, candidate: PaperReleaseCandidate) -> ArtifactRef:
        self.store.recover_uncommitted()
        pending = self.store.pending()
        committed = self.store.committed()
        current = self.store.current() if pending == committed else None
        if current is not None and self.store.load(current) == candidate:
            return current
        if pending is not None and pending != current:
            if self.store.load(pending) != candidate:
                raise PaperAuthorityInvalid(
                    "a different paper release publication is pending",
                    finding_kind="release-current-conflict",
                )
            reference = pending
        else:
            try:
                reference = self.registry.publish(candidate.release_id, candidate)
                self.registry.set_current(PAPER_RELEASE_PENDING_SUBJECT, reference)
            except (OSError, ValueError, ValidationError) as exc:
                raise PaperIntegrityInvalid(
                    "paper release publication failed",
                    finding_kind="release-publication-failed",
                ) from exc
        if self._require_same_release(reference, candidate) != candidate:
            raise PaperIntegrityInvalid(
                "paper release pending reconstruction failed",
                finding_kind="release-reconstruction-mismatch",
            )
        report = self.audit_service._status_locked(
            candidate.audit_ref,
            candidate.draft_ref,
            required_current=candidate.draft_ref,
        )
        if (
            self._materialize(
                candidate.audit_ref,
                report,
                tuple(zip(candidate.revision_refs, candidate.revisions)),
            )
            != candidate
        ):
            raise PaperIntegrityInvalid(
                "paper release authority changed during publication",
                finding_kind="release-reconstruction-mismatch",
            )
        self.store.commit(reference)
        return reference

    def _require_same_release(
        self, reference: ArtifactRef, expected: PaperReleaseCandidate
    ) -> PaperReleaseCandidate:
        reopened = self.store.load(reference)
        if reopened != expected:
            raise PaperIntegrityInvalid(
                "paper release changed during reconstruction",
                finding_kind="release-reconstruction-mismatch",
            )
        return reopened


__all__ = [
    "PAPER_RELEASE_SUBJECT",
    "PaperReleaseCandidate",
    "PaperReleaseService",
    "release_id",
]

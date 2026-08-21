"""Exact prepared and committed paper revision recovery."""

from __future__ import annotations

from typing import Protocol

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._draft_store import DraftStore
from envresearch.paper._revision_store import RevisionStore
from envresearch.paper._revision_validation import (
    materialize_revision,
    require_revision_chain,
)
from envresearch.paper.auditor import PaperAuditService
from envresearch.paper.errors import PaperAuthorityInvalid, PaperIntegrityInvalid


class _RecoveryService(Protocol):
    audits: PaperAuditService
    drafts: DraftStore
    store: RevisionStore


def resume_committed(
    service: _RecoveryService,
    revision_ref: ArtifactRef,
    predecessor_ref: ArtifactRef,
    expected_successor_ref: ArtifactRef,
    required_current: ArtifactRef,
) -> ArtifactRef:
    revision = service.store.load(revision_ref)
    if revision.successor_ref != expected_successor_ref:
        raise PaperAuthorityInvalid(
            "a different paper revision is already committed",
            finding_kind="revision-current-conflict",
        )
    _validate_closure(
        service,
        revision_ref,
        predecessor_ref,
        expected_successor_ref,
        required_current,
        "committed",
    )
    if required_current == predecessor_ref:
        service.drafts.promote_if_current(
            previous=predecessor_ref, installed=expected_successor_ref
        )
    return revision_ref


def resume_pending(
    service: _RecoveryService,
    revision_ref: ArtifactRef,
    predecessor_ref: ArtifactRef,
    expected_successor_ref: ArtifactRef,
    required_current: ArtifactRef,
) -> ArtifactRef:
    if required_current != predecessor_ref:
        raise PaperIntegrityInvalid(
            "promoted paper revision has only an uncommitted intent",
            finding_kind="revision-commit-missing",
        )
    revision = service.store.load(revision_ref)
    if revision.successor_ref != expected_successor_ref:
        raise PaperAuthorityInvalid(
            "a different paper revision intent is already pending",
            finding_kind="revision-current-conflict",
        )
    if service.store.committed(predecessor_ref) is not None:
        raise PaperIntegrityInvalid(
            "paper revision pending and commit markers disagree",
            finding_kind="revision-commit-invalid",
        )
    _validate_closure(
        service,
        revision_ref,
        predecessor_ref,
        expected_successor_ref,
        predecessor_ref,
        "pending",
    )
    service.store.commit(predecessor_ref, revision_ref)
    service.drafts.promote_if_current(
        previous=predecessor_ref, installed=expected_successor_ref
    )
    return revision_ref


def _validate_closure(
    service: _RecoveryService,
    revision_ref: ArtifactRef,
    predecessor_ref: ArtifactRef,
    successor_ref: ArtifactRef,
    required_current: ArtifactRef,
    stage: str,
) -> None:
    revision = service.store.load(revision_ref)
    predecessor = service.drafts.load(predecessor_ref)
    successor = service.drafts.load(successor_ref)
    require_revision_chain(revision, predecessor, successor)
    blocked = service.audits._status_locked(
        revision.predecessor_audit_ref,
        predecessor_ref,
        required_current=required_current,
    )
    clean = service.audits._status_locked(
        revision.successor_audit_ref,
        successor_ref,
        required_current=required_current,
    )
    expected = materialize_revision(
        predecessor,
        predecessor_ref,
        revision.predecessor_audit_ref,
        blocked,
        successor_ref,
        revision.successor_audit_ref,
    )
    if (
        blocked.verdict != "blocked"
        or not blocked.findings
        or clean.verdict != "clean"
        or clean.findings
        or revision != expected
    ):
        raise PaperIntegrityInvalid(
            f"{stage} paper revision cannot be reconstructed",
            finding_kind="revision-closure-mismatch",
        )


__all__ = ["resume_committed", "resume_pending"]

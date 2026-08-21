"""Locked audit transaction primitives shared with paper revision closure."""

from __future__ import annotations

from typing import TYPE_CHECKING

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_store import audit_subject
from envresearch.paper.audit_contracts import PaperAuditReport
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperBuilderError,
    PaperIntegrityInvalid,
)

if TYPE_CHECKING:
    from envresearch.paper.auditor import PaperAuditService


def publish_locked(
    service: PaperAuditService,
    draft_ref: ArtifactRef,
    required_current: ArtifactRef,
) -> ArtifactRef:
    """Publish one audit while the caller owns the full authority lock set."""
    service.store.recover_uncommitted(draft_ref)
    inputs = service._open_inputs(draft_ref, required_current)
    report = service._materialize(draft_ref, inputs)
    service._require_same_inputs(draft_ref, required_current, inputs)
    prior = service.store.current(draft_ref)
    if prior is not None:
        service._require_current(prior, draft_ref)
        existing = service.store.load(prior)
        if existing != report:
            raise PaperAuthorityInvalid(
                "a different paper audit is already current for this draft",
                finding_kind="audit-current-conflict",
            )
        service._validate_report(prior, existing, inputs)
        service._final_authority(draft_ref, required_current, inputs)
        service._require_current(prior, draft_ref)
        return prior
    try:
        reference = service.registry.publish(report.audit_id, report)
    except (OSError, ValueError) as exc:
        raise PaperIntegrityInvalid(
            "paper audit immutable publication failed",
            finding_kind="audit-publication-failed",
        ) from exc
    service._require_same_inputs(draft_ref, required_current, inputs)
    try:
        service.registry.set_current(audit_subject(draft_ref), reference)
    except (OSError, ValueError) as exc:
        service.store.restore(draft_ref=draft_ref, previous=prior, installed=reference)
        raise PaperIntegrityInvalid(
            "paper audit current publication failed",
            finding_kind="audit-publication-failed",
        ) from exc
    try:
        service._validate_pending(reference, report, inputs)
        service._final_authority(draft_ref, required_current, inputs)
        service.store.commit(draft_ref, reference)
        service._final_authority(draft_ref, required_current, inputs)
        service._require_current(reference, draft_ref)
    except PaperBuilderError:
        service.store.restore(draft_ref=draft_ref, previous=prior, installed=reference)
        raise
    return reference


def status_locked(
    service: PaperAuditService,
    audit_ref: ArtifactRef,
    draft_ref: ArtifactRef,
    required_current: ArtifactRef,
) -> PaperAuditReport:
    """Reconstruct an exact target audit under one caller-owned lock stack."""
    service._require_current(audit_ref, draft_ref)
    report = service.store.load(audit_ref)
    if report.draft_ref != draft_ref:
        raise PaperAuthorityInvalid(
            "paper audit binds another draft",
            finding_kind="audit-draft-reference-mismatch",
        )
    inputs = service._open_inputs(draft_ref, required_current)
    service._validate_report(audit_ref, report, inputs)
    service._require_same_inputs(draft_ref, required_current, inputs)
    final = service._require_same_report(audit_ref, report)
    service._final_authority(draft_ref, required_current, inputs)
    service._require_current(audit_ref, draft_ref)
    return final


__all__ = ["publish_locked", "status_locked"]

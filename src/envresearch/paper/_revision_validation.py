"""Exact closure reconstruction shared by revision publication and status."""

from __future__ import annotations

from envresearch.models.artifact import ArtifactRef
from envresearch.paper.audit_contracts import PaperAuditReport
from envresearch.paper.draft_contracts import PaperDraft
from envresearch.paper.errors import PaperIntegrityInvalid
from envresearch.paper.revision_contracts import (
    DraftRevision,
    FindingClosureWitness,
    revision_id,
)


def materialize_revision(
    predecessor: PaperDraft,
    predecessor_ref: ArtifactRef,
    predecessor_audit_ref: ArtifactRef,
    report: PaperAuditReport,
    successor_ref: ArtifactRef,
    successor_audit_ref: ArtifactRef,
) -> DraftRevision:
    """Derive the complete closure envelope only from audited findings."""
    witnesses = tuple(
        FindingClosureWitness(
            finding_id=item.finding_id,
            finding_kind=item.finding_kind,
            code=item.code,
            predecessor_target=item.target,
            claim_ids=item.claim_ids,
            successor_validation="clean-independent-audit",
        )
        for item in report.findings
    )
    return DraftRevision(
        schema_version="paper.draft-revision.v1",
        revision_id=revision_id(predecessor_ref),
        producer="paper-builder-revision-v1",
        predecessor_ref=predecessor_ref,
        predecessor_audit_ref=predecessor_audit_ref,
        successor_ref=successor_ref,
        successor_audit_ref=successor_audit_ref,
        predecessor_generation=predecessor.generation,
        successor_generation=predecessor.generation + 1,
        map_ref=predecessor.map_ref,
        ledger_ref=predecessor.ledger_ref,
        citation_report_ref=predecessor.citation_report_ref,
        closed_finding_ids=tuple(item.finding_id for item in report.findings),
        closure_witnesses=witnesses,
    )


def require_revision_chain(
    revision: DraftRevision, predecessor: PaperDraft, successor: PaperDraft
) -> None:
    """Require consecutive drafts with one unchanged exact upstream chain."""
    predecessor_upstreams = (
        predecessor.map_ref,
        predecessor.ledger_ref,
        predecessor.citation_report_ref,
    )
    successor_upstreams = (
        successor.map_ref,
        successor.ledger_ref,
        successor.citation_report_ref,
    )
    revision_upstreams = (
        revision.map_ref,
        revision.ledger_ref,
        revision.citation_report_ref,
    )
    if (
        successor.predecessor_ref != revision.predecessor_ref
        or predecessor.draft_id != successor.draft_id
        or successor.generation != predecessor.generation + 1
        or predecessor_upstreams != successor_upstreams
        or revision_upstreams != predecessor_upstreams
    ):
        raise PaperIntegrityInvalid(
            "paper revision draft chain is invalid",
            finding_kind="revision-chain-invalid",
        )


__all__ = ["materialize_revision", "require_revision_chain"]

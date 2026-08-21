"""Reusable real-authority helpers for paper-audit integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from econometrics_valuation_verifier_fixtures import ValuationVerifierBackend
from paper_draft_integration_fixtures import DraftStack

from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.econometrics.service import LocalAnalysisService
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.auditor import PaperAuditService
from envresearch.paper.contracts import AnalysisOutputRef
from envresearch.paper.draft_builder import PAPER_DRAFT_SUBJECT
from envresearch.storage.research_artifacts import ResearchArtifactStore


@dataclass(frozen=True, slots=True)
class ReopeningResolver:
    transition_ref: ArtifactRef
    analysis_ref: LocalAnalysisReference
    analysis_service: LocalAnalysisService

    def resolve(
        self, transition_ref: ArtifactRef
    ) -> tuple[tuple[LocalAnalysisReference, LocalAnalysisReport], ...]:
        if transition_ref != self.transition_ref:
            raise ValueError("accepted transition changed")
        return ((self.analysis_ref, self.analysis_service.status(self.analysis_ref)),)

    def require_current(self, transition_ref: ArtifactRef) -> None:
        if transition_ref != self.transition_ref:
            raise ValueError("accepted transition changed")

    @contextmanager
    def authority_lease(self) -> Iterator[None]:
        yield


def publish_draft(stack: DraftStack) -> ArtifactRef:
    return stack.draft_service.publish(
        stack.candidate,
        map_ref=stack.map_ref,
        ledger_ref=stack.ledger_ref,
        citation_report_ref=stack.report_ref,
    )


def audit_service(stack: DraftStack) -> PaperAuditService:
    return PaperAuditService(draft_service=stack.draft_service)


def expected_report_lineage(
    stack: DraftStack, draft_ref: ArtifactRef
) -> tuple[
    tuple[ArtifactRef, ...],
    tuple[LocalAnalysisReference, ...],
    tuple[AnalysisOutputRef, ...],
]:
    """Assemble expected lineage without any audit production helper."""
    draft = stack.draft_service.store.load(draft_ref)
    argument_map = stack.map_service.status(draft.map_ref, draft.ledger_ref)
    ledger = stack.ledger_service.status(draft.ledger_ref, argument_map.transition_ref)
    snapshot = stack.citation_authority.reopen(draft.citation_report_ref)
    _, citation_report = snapshot.report
    refs = {
        draft_ref,
        draft.map_ref,
        draft.ledger_ref,
        draft.citation_report_ref,
        argument_map.transition_ref,
        ledger.transition_ref,
        *(row.transition_ref for row in ledger.claims),
        *(row.snapshot_ref for row in ledger.claims),
        *(reference for reference, _ in snapshot.source_sheets),
        *citation_report.source_sheet_refs,
        *citation_report.claim_fact_map_refs,
        *citation_report.blinded_brief_refs,
        *citation_report.accepted_artifact_refs,
    }
    analyses = tuple(
        sorted(
            {row.analysis_ref for row in ledger.claims},
            key=lambda item: (
                item.analysis_id,
                item.generation,
                item.sha256,
                str(item.relative_path),
            ),
        )
    )
    outputs = tuple(
        sorted(
            {item for row in ledger.claims for item in row.output_evidence},
            key=lambda item: (
                item.analysis_ref.analysis_id,
                item.analysis_ref.generation,
                item.analysis_ref.sha256,
                str(item.analysis_ref.relative_path),
                item.name,
                item.sha256,
                item.size_bytes,
                item.result_pointers,
            ),
        )
    )
    ordered_refs = tuple(
        sorted(
            refs,
            key=lambda item: (
                item.artifact_id,
                item.artifact_version,
                item.content_hash,
            ),
        )
    )
    return ordered_refs, analyses, outputs


def forge_numeric_draft(stack: DraftStack, draft_ref: ArtifactRef) -> ArtifactRef:
    draft = stack.draft_service.store.load(draft_ref)
    results = next(item for item in draft.paragraphs if item.section == "results")
    numeric = next(
        token
        for token in results.text.replace(",", " ").split()
        if any(character.isdigit() for character in token)
    )
    forged = draft.model_copy(
        update={
            "paragraphs": tuple(
                paragraph.model_copy(
                    update={"text": paragraph.text.replace(numeric, "99.999", 1)}
                )
                if paragraph.section == "results"
                else paragraph
                for paragraph in draft.paragraphs
            )
        }
    )
    reference = stack.draft_service.registry.publish(draft.draft_id, forged)
    stack.draft_service.registry.set_current(PAPER_DRAFT_SUBJECT, reference)
    return reference


def install_reopening_resolver(
    stack: DraftStack,
) -> tuple[Path, LocalAnalysisReport]:
    cached = stack.ledger_service.resolver
    analysis_ref, report = cached.reports[0]  # type: ignore[attr-defined]
    analysis_root = stack.draft_service.registry.root.parent / "analysis"
    service = LocalAnalysisService(
        ResearchArtifactStore(analysis_root),
        ValuationVerifierBackend("contingent-valuation"),
    )
    stack.ledger_service.resolver = ReopeningResolver(
        stack.transition_ref, analysis_ref, service
    )
    return analysis_root, report


__all__ = [
    "ReopeningResolver",
    "audit_service",
    "expected_report_lineage",
    "forge_numeric_draft",
    "install_reopening_resolver",
    "publish_draft",
]

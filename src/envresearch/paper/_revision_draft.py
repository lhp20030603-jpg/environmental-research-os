"""Service-owned materialization of exact successor draft generations."""

from __future__ import annotations

from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.paper.draft_contracts import PaperDraft, PaperDraftCandidate
from envresearch.paper.errors import PaperSupportInvalid


def successor_draft(
    predecessor_ref: ArtifactRef,
    predecessor: PaperDraft,
    candidate: PaperDraftCandidate,
) -> PaperDraft:
    """Canonicalize caller manuscript content into the exact next generation."""
    try:
        candidate = PaperDraftCandidate.model_validate(
            candidate.model_dump(mode="python")
        )
    except (AttributeError, TypeError, ValidationError) as exc:
        raise PaperSupportInvalid(
            "paper revision candidate is invalid",
            finding_kind="revision-candidate-invalid",
        ) from exc
    if manuscript_payload(predecessor) == candidate_payload(candidate):
        raise PaperSupportInvalid(
            "paper revision must make a material manuscript change",
            finding_kind="revision-unchanged",
        )
    return PaperDraft(
        schema_version="paper.draft.v1",
        draft_id=predecessor.draft_id,
        producer="paper-builder-draft-v1",
        generation=predecessor.generation + 1,
        predecessor_ref=predecessor_ref,
        map_ref=predecessor.map_ref,
        ledger_ref=predecessor.ledger_ref,
        citation_report_ref=predecessor.citation_report_ref,
        paragraphs=tuple(sorted(candidate.paragraphs, key=lambda item: item.position)),
        claim_bindings=tuple(
            sorted(
                candidate.claim_bindings,
                key=lambda item: (item.paragraph_id, item.start, item.end),
            )
        ),
        citation_bindings=tuple(
            sorted(
                candidate.citation_bindings,
                key=lambda item: (item.paragraph_id, item.start, item.end),
            )
        ),
        tables=tuple(sorted(candidate.tables, key=lambda item: item.binding_id)),
        figures=tuple(sorted(candidate.figures, key=lambda item: item.binding_id)),
    )


def manuscript_payload(draft: PaperDraft) -> dict[str, object]:
    return candidate_payload(
        PaperDraftCandidate(
            paragraphs=draft.paragraphs,
            claim_bindings=draft.claim_bindings,
            citation_bindings=draft.citation_bindings,
            tables=draft.tables,
            figures=draft.figures,
        )
    )


def candidate_payload(candidate: PaperDraftCandidate) -> dict[str, object]:
    """Return one canonical manuscript-only representation for comparison."""
    canonical = PaperDraftCandidate(
        paragraphs=tuple(sorted(candidate.paragraphs, key=lambda item: item.position)),
        claim_bindings=tuple(
            sorted(
                candidate.claim_bindings,
                key=lambda item: (item.paragraph_id, item.start, item.end),
            )
        ),
        citation_bindings=tuple(
            sorted(
                candidate.citation_bindings,
                key=lambda item: (item.paragraph_id, item.start, item.end),
            )
        ),
        tables=tuple(sorted(candidate.tables, key=lambda item: item.binding_id)),
        figures=tuple(sorted(candidate.figures, key=lambda item: item.binding_id)),
    )
    return canonical.model_dump(mode="json")


__all__ = ["candidate_payload", "manuscript_payload", "successor_draft"]

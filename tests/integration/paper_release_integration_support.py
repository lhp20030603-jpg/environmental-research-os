"""Honest real-stack attacks for final Paper Builder release acceptance."""

from __future__ import annotations

from collections.abc import Callable

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._draft_store import PAPER_DRAFT_SUBJECT
from envresearch.paper.draft_contracts import PaperDraft


def forge_draft(stack, draft_ref: ArtifactRef, attack: str) -> ArtifactRef:  # type: ignore[no-untyped-def]
    """Publish one coherent forged current draft for independent audit."""
    draft = stack.draft_service.store.load(draft_ref)
    attacks: dict[str, Callable[[PaperDraft], PaperDraft]] = {
        "citation-mismatch": _citation_mismatch,
        "numeric-contradiction": _numeric_contradiction,
        "unsupported-claim": _unsupported_claim,
        "unit-overreach": _unit_overreach,
        "policy-overclaim": _policy_overclaim,
    }
    forged = attacks[attack](draft)
    reference = stack.draft_service.registry.publish(
        draft.draft_id, forged, version=draft.generation
    )
    stack.draft_service.registry.set_current(PAPER_DRAFT_SUBJECT, reference)
    return reference


def _replace_section(draft: PaperDraft, section: str, text: str) -> PaperDraft:
    return draft.model_copy(
        update={
            "paragraphs": tuple(
                item.model_copy(update={"text": text})
                if item.section == section
                else item
                for item in draft.paragraphs
            )
        }
    )


def _citation_mismatch(draft: PaperDraft) -> PaperDraft:
    paragraph = next(item for item in draft.paragraphs if item.section == "methods")
    return _replace_section(
        draft, "methods", paragraph.text.replace("eligible", "ineligible")
    )


def _numeric_contradiction(draft: PaperDraft) -> PaperDraft:
    paragraph = next(item for item in draft.paragraphs if item.section == "results")
    tokens = paragraph.text.replace(",", " ").split()
    numeric = next(item for item in tokens if any(char.isdigit() for char in item))
    return _replace_section(
        draft, "results", paragraph.text.replace(numeric, "999.99", 1)
    )


def _unsupported_claim(draft: PaperDraft) -> PaperDraft:
    binding = draft.claim_bindings[0].model_copy(
        update={"claim_ids": ("unsupported-claim",)}
    )
    return draft.model_copy(
        update={"claim_bindings": (binding, *draft.claim_bindings[1:])}
    )


def _unit_overreach(draft: PaperDraft) -> PaperDraft:
    return draft.model_copy(
        update={
            "claim_bindings": tuple(
                item.model_copy(update={"unit": "EUR"}) for item in draft.claim_bindings
            )
        }
    )


def _policy_overclaim(draft: PaperDraft) -> PaperDraft:
    return _replace_section(
        draft, "title", "Policymakers must adopt this program nationwide."
    )


__all__ = ["forge_draft"]

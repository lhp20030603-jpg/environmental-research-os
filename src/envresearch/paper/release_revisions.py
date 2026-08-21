"""Ordered revision ancestry reopening for release authority."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._revision_store import revision_subject
from envresearch.paper.argument_map import ARGUMENT_MAP_SUBJECT
from envresearch.paper.auditor import audit_subject
from envresearch.paper.draft_builder import PAPER_DRAFT_SUBJECT
from envresearch.paper.errors import PaperAuthorityInvalid, PaperIntegrityInvalid
from envresearch.paper.ledger import CLAIM_LEDGER_SUBJECT
from envresearch.paper.revision import RevisionService
from envresearch.paper.revision_contracts import DraftRevision

RevisionPair = tuple[ArtifactRef, DraftRevision]


def preview_chain(
    service: RevisionService,
    terminal_ref: ArtifactRef,
    terminal_revision_ref: ArtifactRef | None,
) -> tuple[RevisionPair, ...]:
    """Follow every predecessor to generation one using exact current closures."""
    draft_ref = terminal_ref
    pairs: list[RevisionPair] = []
    while True:
        draft = service.drafts.load(draft_ref)
        if draft.generation == 1:
            break
        predecessor_ref = draft.predecessor_ref
        if predecessor_ref is None:
            raise PaperIntegrityInvalid(
                "later draft generation has no predecessor",
                finding_kind="release-revision-ancestry-invalid",
            )
        revision_ref = service.store.current(predecessor_ref)
        if revision_ref is None:
            raise PaperAuthorityInvalid(
                "release revision ancestry is incomplete",
                finding_kind="release-revision-required",
            )
        revision = service.store.load(revision_ref)
        if revision.successor_ref != draft_ref:
            raise PaperAuthorityInvalid(
                "release revision ancestry is discontinuous",
                finding_kind="release-revision-invalid",
            )
        pairs.append((revision_ref, revision))
        draft_ref = predecessor_ref
    pairs.reverse()
    if not pairs or pairs[-1][0] != terminal_revision_ref:
        raise PaperAuthorityInvalid(
            "terminal release revision is not exact",
            finding_kind="release-revision-invalid",
        )
    return tuple(pairs)


@contextmanager
def chain_authority(
    service: RevisionService, pairs: tuple[RevisionPair, ...]
) -> Iterator[None]:
    """Hold the global authority order for an entire revision ancestry."""
    try:
        with ExitStack() as stack:
            stack.enter_context(service.ledger_service.resolver.authority_lease())
            stack.enter_context(service.citation_authority.authority_lease())
            stack.enter_context(service.registry.lock(CLAIM_LEDGER_SUBJECT))
            stack.enter_context(service.registry.lock(ARGUMENT_MAP_SUBJECT))
            stack.enter_context(service.registry.lock(PAPER_DRAFT_SUBJECT))
            audit_subjects = {
                audit_subject(reference)
                for _, revision in pairs
                for reference in (revision.predecessor_ref, revision.successor_ref)
            }
            for subject in sorted(audit_subjects):
                stack.enter_context(service.registry.lock(subject))
            for subject in sorted(
                revision_subject(revision.predecessor_ref) for _, revision in pairs
            ):
                stack.enter_context(service.registry.lock(subject))
            yield
    except (PaperAuthorityInvalid, PaperIntegrityInvalid):
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise PaperIntegrityInvalid(
            "release revision ancestry lease failed",
            finding_kind="release-revision-ancestry-invalid",
        ) from exc


def reopen_chain_locked(
    service: RevisionService,
    pairs: tuple[RevisionPair, ...],
    terminal_ref: ArtifactRef,
) -> tuple[RevisionPair, ...]:
    """Reconstruct each exact closure while the complete chain is locked."""
    reopened = tuple(
        (
            revision_ref,
            service._status_locked(
                revision_ref,
                revision.predecessor_ref,
                revision,
                required_current=terminal_ref,
            ),
        )
        for revision_ref, revision in pairs
    )
    if reopened != pairs:
        raise PaperIntegrityInvalid(
            "release revision ancestry changed",
            finding_kind="release-revision-mismatch",
        )
    return reopened

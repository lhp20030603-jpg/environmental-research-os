"""Transactional publication and status for exact evidence-bound paper drafts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._draft_candidate import deterministic_draft_candidate
from envresearch.paper._draft_store import PAPER_DRAFT_SUBJECT, DraftStore, draft_id
from envresearch.paper.argument_contracts import ArgumentMap
from envresearch.paper.argument_map import ARGUMENT_MAP_SUBJECT, ArgumentMapService
from envresearch.paper.citation_authority import (
    CitationAuthority,
    CitationAuthoritySnapshot,
)
from envresearch.paper.contracts import ClaimEvidenceLedger
from envresearch.paper.draft_contracts import PaperDraft, PaperDraftCandidate
from envresearch.paper.draft_validation import validate_draft
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperBuilderError,
    PaperIntegrityInvalid,
    PaperSupportInvalid,
)
from envresearch.paper.ledger import CLAIM_LEDGER_SUBJECT


@dataclass(frozen=True, slots=True)
class _Upstreams:
    argument_map: ArgumentMap
    ledger: ClaimEvidenceLedger
    citations: CitationAuthoritySnapshot


class DraftService:
    """Publish and independently reopen one current exact paper draft."""

    def __init__(
        self,
        *,
        map_service: ArgumentMapService,
        citation_authority: CitationAuthority,
    ) -> None:
        self.map_service = map_service
        self.ledger_service = map_service.ledger_service
        self.citation_authority = citation_authority
        self.registry = map_service.registry
        self.store = DraftStore(self.registry)

    def publish(
        self,
        candidate: PaperDraftCandidate,
        *,
        map_ref: ArtifactRef,
        ledger_ref: ArtifactRef,
        citation_report_ref: ArtifactRef,
    ) -> ArtifactRef:
        """Validate, publish, and promote a draft under repeated exact authority."""
        candidate = self._strict_candidate(candidate)
        with self._paper_authority_lease():
            upstreams = self._open_upstreams(map_ref, ledger_ref, citation_report_ref)
            validate_draft(
                candidate,
                argument_map=upstreams.argument_map,
                ledger=upstreams.ledger,
                citation_snapshot=upstreams.citations,
            )
            draft = self._materialize(
                candidate,
                map_ref=map_ref,
                ledger_ref=ledger_ref,
                citation_report_ref=citation_report_ref,
            )
            self._validate_draft(draft, upstreams)
            self._require_same_upstreams(
                upstreams, map_ref, ledger_ref, citation_report_ref
            )
            prior = self.store.current()
            if prior is not None:
                return self._recover_existing(
                    prior,
                    draft,
                    upstreams,
                    map_ref,
                    ledger_ref,
                    citation_report_ref,
                )
            try:
                reference = self.registry.publish(draft.draft_id, draft)
            except (OSError, ValueError) as exc:
                raise PaperIntegrityInvalid(
                    "paper draft immutable publication failed",
                    finding_kind="draft-publication-failed",
                ) from exc
            self._require_same_upstreams(
                upstreams, map_ref, ledger_ref, citation_report_ref
            )
            try:
                self.registry.set_current(PAPER_DRAFT_SUBJECT, reference)
            except (OSError, ValueError) as exc:
                self.store.restore(previous=prior, installed=reference)
                raise PaperIntegrityInvalid(
                    "paper draft current publication failed",
                    finding_kind="draft-publication-failed",
                ) from exc
            try:
                return self._validate_promotion(
                    reference,
                    draft,
                    upstreams,
                    map_ref,
                    ledger_ref,
                    citation_report_ref,
                )
            except PaperBuilderError:
                self.store.restore(previous=prior, installed=reference)
                raise

    def status(
        self,
        draft_ref: ArtifactRef,
        *,
        map_ref: ArtifactRef,
        ledger_ref: ArtifactRef,
    ) -> PaperDraft:
        """Reopen draft bytes and every transitive upstream authority twice."""
        with self._paper_authority_lease():
            self._require_current(draft_ref)
            draft = self.store.load(draft_ref)
            self._require_stored_refs(draft, map_ref, ledger_ref)
            upstreams = self._open_upstreams(
                map_ref, ledger_ref, draft.citation_report_ref
            )
            self._validate_draft(draft, upstreams)
            self._require_current(draft_ref)
            self._require_same_draft(draft_ref, draft)
            self._require_same_upstreams(
                upstreams, map_ref, ledger_ref, draft.citation_report_ref
            )
            self._validate_draft(draft, upstreams)
            final = self._require_same_draft(draft_ref, draft)
            self._require_current(draft_ref)
            self.citation_authority.require_current(upstreams.citations.token)
            return final

    @contextmanager
    def _paper_authority_lease(self) -> Iterator[None]:
        """Hold external authorities before paper locks in the shared order."""
        with ExitStack() as stack:
            stack.enter_context(self.ledger_service.resolver.authority_lease())
            stack.enter_context(self.citation_authority.authority_lease())
            stack.enter_context(self.registry.lock(CLAIM_LEDGER_SUBJECT))
            stack.enter_context(self.registry.lock(ARGUMENT_MAP_SUBJECT))
            stack.enter_context(self.registry.lock(PAPER_DRAFT_SUBJECT))
            yield

    def _recover_existing(
        self,
        reference: ArtifactRef,
        draft: PaperDraft,
        upstreams: _Upstreams,
        map_ref: ArtifactRef,
        ledger_ref: ArtifactRef,
        report_ref: ArtifactRef,
    ) -> ArtifactRef:
        existing = self.store.load(reference)
        if existing != draft:
            raise PaperAuthorityInvalid(
                "a different paper draft is already current",
                finding_kind="draft-current-conflict",
            )
        self._require_same_upstreams(upstreams, map_ref, ledger_ref, report_ref)
        self._require_current(reference)
        self._validate_draft(existing, upstreams)
        self._require_same_draft(reference, existing)
        self._require_same_upstreams(upstreams, map_ref, ledger_ref, report_ref)
        self._require_same_draft(reference, existing)
        self._require_current(reference)
        self.citation_authority.require_current(upstreams.citations.token)
        return reference

    def _validate_promotion(
        self,
        reference: ArtifactRef,
        draft: PaperDraft,
        upstreams: _Upstreams,
        map_ref: ArtifactRef,
        ledger_ref: ArtifactRef,
        report_ref: ArtifactRef,
    ) -> ArtifactRef:
        self._require_same_draft(reference, draft)
        self._require_current(reference)
        self._require_same_upstreams(upstreams, map_ref, ledger_ref, report_ref)
        self._validate_draft(draft, upstreams)
        self._require_current(reference)
        self._require_same_draft(reference, draft)
        self._require_same_upstreams(upstreams, map_ref, ledger_ref, report_ref)
        self._require_same_draft(reference, draft)
        self._require_current(reference)
        self.citation_authority.require_current(upstreams.citations.token)
        return reference

    def _open_upstreams(
        self, map_ref: ArtifactRef, ledger_ref: ArtifactRef, report_ref: ArtifactRef
    ) -> _Upstreams:
        try:
            argument_map = self.map_service.status(map_ref, ledger_ref)
            ledger = self.ledger_service.status(ledger_ref, argument_map.transition_ref)
            citations = self.citation_authority.reopen(report_ref)
            return _Upstreams(argument_map, ledger, citations)
        except PaperBuilderError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise PaperAuthorityInvalid(
                "paper draft upstream authority is invalid",
                finding_kind="draft-upstream-invalid",
            ) from exc

    def _require_same_upstreams(
        self,
        expected: _Upstreams,
        map_ref: ArtifactRef,
        ledger_ref: ArtifactRef,
        report_ref: ArtifactRef,
    ) -> _Upstreams:
        current = self._open_upstreams(map_ref, ledger_ref, report_ref)
        if current != expected:
            raise PaperAuthorityInvalid(
                "paper draft upstream changed during publication",
                finding_kind="draft-upstream-changed",
            )
        return current

    def _validate_draft(self, draft: PaperDraft, upstreams: _Upstreams) -> None:
        validate_draft(
            draft,
            argument_map=upstreams.argument_map,
            ledger=upstreams.ledger,
            citation_snapshot=upstreams.citations,
            map_ref=draft.map_ref,
            ledger_ref=draft.ledger_ref,
            citation_report_ref=draft.citation_report_ref,
        )

    def _require_current(self, reference: ArtifactRef) -> None:
        if self.store.current() != reference:
            raise PaperAuthorityInvalid(
                "paper draft reference is not current",
                finding_kind="draft-not-current",
            )

    def _require_same_draft(
        self, reference: ArtifactRef, expected: PaperDraft
    ) -> PaperDraft:
        current = self.store.load(reference)
        if current != expected:
            raise PaperIntegrityInvalid(
                "paper draft changed during validation",
                finding_kind="draft-reconstruction-mismatch",
            )
        return current

    @staticmethod
    def _require_stored_refs(
        draft: PaperDraft, map_ref: ArtifactRef, ledger_ref: ArtifactRef
    ) -> None:
        if draft.map_ref != map_ref or draft.ledger_ref != ledger_ref:
            raise PaperAuthorityInvalid(
                "paper draft binds another map or ledger",
                finding_kind="draft-reference-mismatch",
            )

    @staticmethod
    def _strict_candidate(candidate: PaperDraftCandidate) -> PaperDraftCandidate:
        try:
            return PaperDraftCandidate.model_validate(
                candidate.model_dump(mode="python")
            )
        except (AttributeError, TypeError, ValidationError) as exc:
            raise PaperSupportInvalid(
                "paper draft candidate is invalid",
                finding_kind="draft-candidate-invalid",
            ) from exc

    @staticmethod
    def _materialize(
        candidate: PaperDraftCandidate,
        *,
        map_ref: ArtifactRef,
        ledger_ref: ArtifactRef,
        citation_report_ref: ArtifactRef,
    ) -> PaperDraft:
        return PaperDraft(
            schema_version="paper.draft.v1",
            draft_id=draft_id(map_ref, ledger_ref, citation_report_ref),
            producer="paper-builder-draft-v1",
            map_ref=map_ref,
            ledger_ref=ledger_ref,
            citation_report_ref=citation_report_ref,
            paragraphs=tuple(
                sorted(candidate.paragraphs, key=lambda item: item.position)
            ),
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


__all__ = [
    "PAPER_DRAFT_SUBJECT",
    "DraftService",
    "deterministic_draft_candidate",
]

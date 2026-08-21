"""Authenticated immutable storage helpers for exact paper drafts."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.draft_contracts import PaperDraft
from envresearch.paper.errors import PaperAuthorityInvalid, PaperIntegrityInvalid

PAPER_DRAFT_SUBJECT = "paper-draft"


class DraftStore:
    """Single-read canonical loader and compare-and-restore current pointer."""

    def __init__(self, registry: ExitRegistry) -> None:
        self.registry = registry

    def current(self) -> ArtifactRef | None:
        try:
            return self.registry.current(PAPER_DRAFT_SUBJECT)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper draft current pointer is invalid",
                finding_kind="draft-current-invalid",
            ) from exc

    def load(self, reference: ArtifactRef) -> PaperDraft:
        relative = (
            Path("exit/objects")
            / reference.artifact_id
            / f"v{reference.artifact_version}-{reference.content_hash}.json"
        )
        try:
            data = self.registry.files.read(relative)
            if hashlib.sha256(data).hexdigest() != reference.content_hash:
                raise ValueError("draft content hash mismatch")
            draft = PaperDraft.model_validate_json(data)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper draft bytes are invalid",
                finding_kind="draft-bytes-invalid",
            ) from exc
        expected_id = draft_id(
            draft.map_ref, draft.ledger_ref, draft.citation_report_ref
        )
        if (
            reference.artifact_version != draft.generation
            or reference.artifact_id != draft.draft_id
            or draft.draft_id != expected_id
        ):
            raise PaperIntegrityInvalid(
                "paper draft reference identity is invalid",
                finding_kind="draft-identity-invalid",
            )
        if data != draft.model_dump_json().encode():
            raise PaperIntegrityInvalid(
                "paper draft bytes are not canonical",
                finding_kind="draft-bytes-noncanonical",
            )
        return draft

    def restore(self, *, previous: ArtifactRef | None, installed: ArtifactRef) -> None:
        try:
            self.registry.restore_current_if_unchanged(
                PAPER_DRAFT_SUBJECT, installed=installed, previous=previous
            )
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper draft current rollback failed",
                finding_kind="draft-rollback-failed",
            ) from exc

    def expected_ref(self, draft: PaperDraft) -> ArtifactRef:
        """Compute the exact immutable ref without changing registry state."""
        data = draft.model_dump_json().encode()
        return ArtifactRef(
            artifact_id=draft.draft_id,
            artifact_version=draft.generation,
            content_hash=hashlib.sha256(data).hexdigest(),
        )

    def publish_exact(self, draft: PaperDraft) -> ArtifactRef:
        """Publish and reopen one exact generation without changing current."""
        try:
            reference = self.registry.publish(
                draft.draft_id, draft, version=draft.generation
            )
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper draft immutable publication failed",
                finding_kind="draft-publication-failed",
            ) from exc
        if reference != self.expected_ref(draft) or self.load(reference) != draft:
            raise PaperIntegrityInvalid(
                "paper draft immutable reconstruction failed",
                finding_kind="draft-reconstruction-mismatch",
            )
        return reference

    def promote_if_current(
        self, *, previous: ArtifactRef, installed: ArtifactRef
    ) -> None:
        """CAS the sole draft current pointer as a revision's final operation."""
        current = self.current()
        if current == installed:
            return
        if current != previous:
            raise PaperAuthorityInvalid(
                "paper draft predecessor is not current",
                finding_kind="draft-predecessor-not-current",
            )
        try:
            self.registry.set_current(PAPER_DRAFT_SUBJECT, installed)
        except (OSError, ValueError, ValidationError) as exc:
            if self.current() == installed:
                return
            raise PaperIntegrityInvalid(
                "paper draft revision promotion failed",
                finding_kind="draft-promotion-failed",
            ) from exc


def draft_id(
    map_ref: ArtifactRef, ledger_ref: ArtifactRef, citation_report_ref: ArtifactRef
) -> str:
    """Return deterministic service-owned identity for one authority triple."""
    return (
        f"paper-draft-{map_ref.content_hash[:8]}-"
        f"{ledger_ref.content_hash[:8]}-{citation_report_ref.content_hash[:8]}"
    )


__all__ = ["PAPER_DRAFT_SUBJECT", "DraftStore", "draft_id"]

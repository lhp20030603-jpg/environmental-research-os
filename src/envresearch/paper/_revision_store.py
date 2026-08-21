"""Two-phase immutable storage for exact paper revision envelopes."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.errors import PaperIntegrityInvalid
from envresearch.paper.revision_contracts import DraftRevision, revision_id


def revision_subject(predecessor_ref: ArtifactRef) -> str:
    return revision_id(predecessor_ref)


def revision_commit_subject(predecessor_ref: ArtifactRef) -> str:
    return revision_id(predecessor_ref).replace(
        "paper-revision-", "paper-revision-commit-", 1
    )


class RevisionStore:
    """Single-read canonical revision loader with prepared commit markers."""

    def __init__(self, registry: ExitRegistry) -> None:
        self.registry = registry

    def pending(self, predecessor_ref: ArtifactRef) -> ArtifactRef | None:
        return self._pointer(revision_subject(predecessor_ref))

    def current(self, predecessor_ref: ArtifactRef) -> ArtifactRef | None:
        pending = self.pending(predecessor_ref)
        committed = self.committed(predecessor_ref)
        return pending if pending is not None and pending == committed else None

    def committed(self, predecessor_ref: ArtifactRef) -> ArtifactRef | None:
        return self._pointer(revision_commit_subject(predecessor_ref))

    def load(self, reference: ArtifactRef) -> DraftRevision:
        relative = (
            Path("exit/objects")
            / reference.artifact_id
            / f"v{reference.artifact_version}-{reference.content_hash}.json"
        )
        try:
            data = self.registry.files.read(relative)
            if hashlib.sha256(data).hexdigest() != reference.content_hash:
                raise ValueError("paper revision content hash mismatch")
            revision = DraftRevision.model_validate_json(data)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper revision bytes are invalid",
                finding_kind="revision-bytes-invalid",
            ) from exc
        if (
            reference.artifact_version != 1
            or reference.artifact_id != revision.revision_id
            or revision.revision_id != revision_id(revision.predecessor_ref)
            or data != revision.model_dump_json().encode()
        ):
            raise PaperIntegrityInvalid(
                "paper revision identity is invalid",
                finding_kind="revision-identity-invalid",
            )
        return revision

    def publish(self, revision: DraftRevision) -> ArtifactRef:
        try:
            reference = self.registry.publish(revision.revision_id, revision)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper revision immutable publication failed",
                finding_kind="revision-publication-failed",
            ) from exc
        if self.load(reference) != revision:
            raise PaperIntegrityInvalid(
                "paper revision immutable reconstruction failed",
                finding_kind="revision-reconstruction-mismatch",
            )
        return reference

    def prepare(self, predecessor_ref: ArtifactRef, reference: ArtifactRef) -> None:
        self._set(revision_subject(predecessor_ref), reference, "preparation")

    def commit(self, predecessor_ref: ArtifactRef, reference: ArtifactRef) -> None:
        if self.pending(predecessor_ref) != reference:
            raise PaperIntegrityInvalid(
                "paper revision pending pointer changed before commit",
                finding_kind="revision-commit-failed",
            )
        self._set(revision_commit_subject(predecessor_ref), reference, "commit")
        if self.current(predecessor_ref) != reference:
            raise PaperIntegrityInvalid(
                "paper revision commit was not durable",
                finding_kind="revision-commit-failed",
            )

    def _set(self, subject: str, reference: ArtifactRef, stage: str) -> None:
        try:
            self.registry.set_current(subject, reference)
        except (OSError, ValueError, ValidationError) as exc:
            if self._pointer(subject) == reference:
                return
            raise PaperIntegrityInvalid(
                f"paper revision {stage} publication failed",
                finding_kind="revision-publication-failed",
            ) from exc

    def _pointer(self, subject: str) -> ArtifactRef | None:
        try:
            return self.registry.current(subject)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper revision current pointer is invalid",
                finding_kind="revision-current-invalid",
            ) from exc


__all__ = [
    "RevisionStore",
    "revision_commit_subject",
    "revision_subject",
]

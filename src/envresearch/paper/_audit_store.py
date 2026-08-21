"""Canonical immutable storage for exact paper-audit reports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.audit_contracts import PaperAuditReport
from envresearch.paper.errors import PaperIntegrityInvalid


def audit_id(draft_ref: ArtifactRef) -> str:
    return f"paper-audit-{_draft_ref_digest(draft_ref)}"


def audit_subject(draft_ref: ArtifactRef) -> str:
    return f"paper-audit-{_draft_ref_digest(draft_ref)}"


def audit_commit_subject(draft_ref: ArtifactRef) -> str:
    return f"paper-audit-commit-{_draft_ref_digest(draft_ref)}"


def _draft_ref_digest(draft_ref: ArtifactRef) -> str:
    return hashlib.sha256(
        json.dumps(
            draft_ref.model_dump(mode="json"),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


class AuditStore:
    """Single-read canonical report loader and scoped current-pointer recovery."""

    def __init__(self, registry: ExitRegistry) -> None:
        self.registry = registry

    def current(self, draft_ref: ArtifactRef) -> ArtifactRef | None:
        """Return only a fully validated two-phase audit promotion."""
        current = self._pointer(audit_subject(draft_ref))
        committed = self._pointer(audit_commit_subject(draft_ref))
        return current if current is not None and current == committed else None

    def pending(self, draft_ref: ArtifactRef) -> ArtifactRef | None:
        """Return the raw first-phase pointer for validation and recovery."""
        return self._pointer(audit_subject(draft_ref))

    def recover_uncommitted(self, draft_ref: ArtifactRef) -> None:
        """Clear an owned first-phase pointer before reopening any upstream."""
        pending = self._pointer(audit_subject(draft_ref))
        committed = self._pointer(audit_commit_subject(draft_ref))
        if pending == committed:
            return
        if pending is None:
            raise PaperIntegrityInvalid(
                "paper audit commit marker has no matching current pointer",
                finding_kind="audit-commit-invalid",
            )
        if committed is not None:
            committed_report = self.load(committed)
            if committed_report.draft_ref != draft_ref:
                raise PaperIntegrityInvalid(
                    "paper audit commit marker binds another draft",
                    finding_kind="audit-commit-invalid",
                )
        try:
            restored = self.registry.restore_current_if_unchanged(
                audit_subject(draft_ref), installed=pending, previous=committed
            )
            if not restored:
                raise ValueError("audit recovery lost current-pointer ownership")
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper audit pending-pointer recovery failed",
                finding_kind="audit-recovery-failed",
            ) from exc

    def commit(self, draft_ref: ArtifactRef, reference: ArtifactRef) -> None:
        """Linearize one already-validated first-phase audit as current."""
        try:
            if self._pointer(audit_subject(draft_ref)) != reference:
                raise ValueError("audit pending pointer changed before commit")
            self.registry.set_current(audit_commit_subject(draft_ref), reference)
        except (OSError, ValueError, ValidationError) as exc:
            if self.current(draft_ref) == reference:
                return
            raise PaperIntegrityInvalid(
                "paper audit commit publication failed",
                finding_kind="audit-commit-failed",
            ) from exc

    def load(self, reference: ArtifactRef) -> PaperAuditReport:
        relative = (
            Path("exit/objects")
            / reference.artifact_id
            / f"v{reference.artifact_version}-{reference.content_hash}.json"
        )
        try:
            data = self.registry.files.read(relative)
            if hashlib.sha256(data).hexdigest() != reference.content_hash:
                raise ValueError("audit content hash mismatch")
            report = PaperAuditReport.model_validate_json(data)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper audit bytes are invalid",
                finding_kind="audit-bytes-invalid",
            ) from exc
        if (
            reference.artifact_version != 1
            or reference.artifact_id != report.audit_id
            or report.audit_id != audit_id(report.draft_ref)
        ):
            raise PaperIntegrityInvalid(
                "paper audit reference identity is invalid",
                finding_kind="audit-identity-invalid",
            )
        if data != report.model_dump_json().encode():
            raise PaperIntegrityInvalid(
                "paper audit bytes are not canonical",
                finding_kind="audit-bytes-noncanonical",
            )
        return report

    def restore(
        self,
        *,
        draft_ref: ArtifactRef,
        previous: ArtifactRef | None,
        installed: ArtifactRef,
    ) -> None:
        try:
            marker_restored = self.registry.restore_current_if_unchanged(
                audit_commit_subject(draft_ref),
                installed=installed,
                previous=previous,
            )
            if not marker_restored:
                raise ValueError("audit rollback lost commit-marker ownership")
            pointer_restored = self.registry.restore_current_if_unchanged(
                audit_subject(draft_ref), installed=installed, previous=previous
            )
            if not pointer_restored:
                raise ValueError("audit rollback lost current-pointer ownership")
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper audit current rollback failed",
                finding_kind="audit-rollback-failed",
            ) from exc

    def _pointer(self, subject: str) -> ArtifactRef | None:
        try:
            return self.registry.current(subject)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "paper audit current pointer is invalid",
                finding_kind="audit-current-invalid",
            ) from exc


__all__ = ["AuditStore", "audit_commit_subject", "audit_id", "audit_subject"]

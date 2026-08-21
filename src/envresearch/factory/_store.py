"""Canonical immutable storage for governed research-factory runs."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.factory.contracts import ResearchFactoryRun
from envresearch.factory.errors import FactoryIntegrityInvalid
from envresearch.models.artifact import ArtifactRef

FACTORY_RUN_PREPARED_SUBJECT = "research-factory-run-prepared"
FACTORY_RUN_COMMITTED_SUBJECT = "research-factory-run"


class _FactoryRunStore:
    """One-read loading and recoverable two-pointer publication."""

    def __init__(self, registry: ExitRegistry) -> None:
        self.registry = registry
        self.prepared_subject = FACTORY_RUN_PREPARED_SUBJECT
        self.committed_subject = FACTORY_RUN_COMMITTED_SUBJECT

    def prepared(self) -> ArtifactRef | None:
        return self._pointer(self.prepared_subject)

    def committed(self) -> ArtifactRef | None:
        return self._pointer(self.committed_subject)

    def current(self) -> ArtifactRef | None:
        prepared = self.prepared()
        committed = self.committed()
        if prepared is None and committed is None:
            return None
        if prepared is None or prepared != committed:
            raise FactoryIntegrityInvalid(
                "factory run pointer pair is torn", finding_kind="run-current-invalid"
            )
        self.load(prepared)
        return prepared

    def probe_recovery_intent(
        self, design_ref: ArtifactRef, release_ref: ArtifactRef
    ) -> None:
        """Authenticate installed caller-bound intent before upstream reopening."""
        prepared = self.prepared()
        committed = self.committed()
        if committed is not None and prepared != committed:
            raise FactoryIntegrityInvalid(
                "factory run recovery pointers conflict",
                finding_kind="run-recovery-conflict",
            )
        intent = prepared or committed
        if intent is None:
            return
        run = self.load(intent)
        if run.design_ref != design_ref or run.release_ref != release_ref:
            raise FactoryIntegrityInvalid(
                "factory run recovery intent conflicts with caller",
                finding_kind="run-intent-conflict",
            )

    def prepare(self, run: ResearchFactoryRun) -> ArtifactRef:
        """Publish canonical bytes and durably install one prepared intent."""
        expected = self.registry.publish(run.factory_run_id, run)
        prior = self.prepared()
        if prior is not None:
            if prior == expected and self.load(prior) == run:
                return prior
            raise FactoryIntegrityInvalid(
                "a conflicting factory run intent is already prepared",
                finding_kind="run-intent-conflict",
            )
        try:
            self.registry.set_current(self.prepared_subject, expected)
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            if self.prepared() == expected:
                return expected
            raise FactoryIntegrityInvalid(
                "factory run preparation failed", finding_kind="run-prepare-failed"
            ) from exc
        return expected

    def commit(self, reference: ArtifactRef) -> None:
        """Linearize one authenticated prepared reference idempotently."""
        if self.prepared() != reference:
            raise FactoryIntegrityInvalid(
                "factory run prepared pointer changed before commit",
                finding_kind="run-commit-invalid",
            )
        try:
            self.registry.set_current(self.committed_subject, reference)
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            if self.committed() == reference:
                return
            raise FactoryIntegrityInvalid(
                "factory run commit failed", finding_kind="run-commit-failed"
            ) from exc

    def compare_and_restore(
        self,
        subject: str,
        *,
        installed: ArtifactRef,
        previous: ArtifactRef | None,
    ) -> None:
        """Restore only a pointer still owned by this failed transaction."""
        try:
            restored = self.registry.restore_current_if_unchanged(
                subject, installed=installed, previous=previous
            )
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                "factory run pointer recovery failed",
                finding_kind="run-recovery-failed",
            ) from exc
        if not restored:
            raise FactoryIntegrityInvalid(
                "factory run recovery lost pointer ownership",
                finding_kind="run-recovery-conflict",
            )

    def load(self, reference: ArtifactRef) -> ResearchFactoryRun:
        """Read canonical object bytes once, then authenticate model and identity."""
        try:
            data = self.registry.files.read(self.object_path(reference))
            if hashlib.sha256(data).hexdigest() != reference.content_hash:
                raise ValueError("factory run content hash mismatch")
            run = ResearchFactoryRun.model_validate_json(data)
            if data != run.model_dump_json().encode():
                raise ValueError("factory run bytes are not canonical")
            if (
                reference.artifact_id != run.factory_run_id
                or reference.artifact_version != 1
            ):
                raise ValueError("factory run reference identity is invalid")
            return run
        except FactoryIntegrityInvalid:
            raise
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                "factory run immutable bytes are invalid",
                finding_kind="run-bytes-invalid",
            ) from exc

    def object_bytes(self, reference: ArtifactRef) -> bytes:
        """Return authenticated canonical bytes for testable publication evidence."""
        run = self.load(reference)
        return run.model_dump_json().encode()

    @staticmethod
    def object_path(reference: ArtifactRef) -> Path:
        return (
            Path("exit/objects")
            / reference.artifact_id
            / f"v{reference.artifact_version}-{reference.content_hash}.json"
        )

    def _pointer(self, subject: str) -> ArtifactRef | None:
        try:
            return self.registry.current(subject)
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                "factory run pointer is invalid", finding_kind="run-pointer-invalid"
            ) from exc


__all__ = ["_FactoryRunStore"]

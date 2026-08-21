"""Private process-safe attempt claims, failure aliases, and workspace roots."""

from __future__ import annotations

import fcntl
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import ClassVar, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator

from envresearch.models.artifact import (
    ArtifactEnvelope,
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.replication.contracts import ReplicationException
from envresearch.storage.research_artifacts import ResearchArtifactStore

_FROZEN = ConfigDict(extra="forbid", frozen=True, strict=True)
_ATTEMPT = re.compile(r"^[0-9a-f]{32}$")
Payload = TypeVar("Payload")


class AttemptClaim(BaseModel):
    model_config = _FROZEN

    subject_ref: ArtifactRef
    attempt_id: str
    output_root: str
    claimed_at: str

    @field_validator("attempt_id")
    @classmethod
    def require_attempt_id(cls, value: str) -> str:
        if not _ATTEMPT.fullmatch(value):
            raise ValueError("attempt ID must be canonical lowercase hex")
        return value


class AttemptCoordinator:
    _threads: ClassVar[dict[tuple[Path, str], Lock]] = {}

    def __init__(self, store: ResearchArtifactStore, subject: ArtifactRef) -> None:
        self.store, self.subject = store, subject

    @contextmanager
    def locked(self) -> Iterator[None]:
        lock_path = self.store.root / (
            f"artifacts/replication/attempts/locks/{self.subject.content_hash}.lock"
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        key = (self.store.root, self.subject.content_hash)
        with self._threads.setdefault(key, Lock()):
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                self._recover_claim()
                self._recover_failure()
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def claim(self) -> tuple[ArtifactRef, AttemptClaim]:
        current = _claim_current(self.subject)
        if (self.store.root / current).exists():
            return self._read_claim(current)
        attempt_id = uuid4().hex
        claim = AttemptClaim(
            subject_ref=self.subject,
            attempt_id=attempt_id,
            output_root=(
                f"artifacts/replication/runs/{self.subject.content_hash}/{attempt_id}"
            ),
            claimed_at=datetime.now(UTC).isoformat(),
        )
        artifact = _seal("tier2-replication-attempt-claim", claim, (self.subject,))
        reference = _reference(artifact)
        self.store.write_structured(_claim_pending(self.subject), artifact)
        self.store.write_structured(_claim_report(reference), artifact)
        self.store.write_structured(current, artifact)
        (self.store.root / _claim_pending(self.subject)).unlink()
        return reference, claim

    def read_claim(self) -> tuple[ArtifactRef, AttemptClaim] | None:
        current = _claim_current(self.subject)
        if not (self.store.root / current).exists():
            return None
        return self._read_claim(current)

    def persist_failure(
        self, exception: ReplicationException, evidence: tuple[ArtifactRef, ...]
    ) -> tuple[ArtifactRef, ReplicationException]:
        if observed := self.read_failure():
            return observed
        payload = {
            "subject_ref": self.subject.model_dump(mode="json"),
            "exception": exception.model_dump(mode="json"),
        }
        artifact = _seal(
            "tier2-replication-attempt",
            payload,
            (self.subject, *evidence),
        )
        reference = _reference(artifact)
        self.store.write_structured(_failure_pending(self.subject), artifact)
        self.store.write_structured(_failure_report(reference), artifact)
        self.store.write_structured(_failure_current(self.subject), artifact)
        (self.store.root / _failure_pending(self.subject)).unlink()
        return reference, exception

    def read_failure(self) -> tuple[ArtifactRef, ReplicationException] | None:
        path = _failure_current(self.subject)
        if not (self.store.root / path).exists():
            return None
        artifact = self.store.read_structured(
            path, TypeAdapter(ResearchArtifact[object])
        )
        _require_artifact(artifact, "tier2-replication-attempt")
        if not isinstance(artifact.payload, dict):
            raise TypeError("attempt payload must be an object")
        observed = ArtifactRef.model_validate(artifact.payload.get("subject_ref"))
        exception_payload = artifact.payload.get("exception")
        if not isinstance(exception_payload, dict):
            raise TypeError("attempt exception must be an object")
        restored = dict(exception_payload)
        restored["evidence_refs"] = tuple(restored.get("evidence_refs", ()))
        exception = ReplicationException.model_validate(restored)
        if observed != self.subject:
            raise ValueError("attempt subject does not match current alias")
        if artifact.envelope.input_artifacts != (
            self.subject,
            *exception.evidence_refs,
        ):
            raise ValueError("attempt inputs differ from exception evidence")
        reference = _reference(artifact)
        _require_copy(self.store, _failure_report(reference), artifact)
        return reference, exception

    def allocate_root(self, claim: AttemptClaim) -> Path:
        root = _resolve_root(self.store.root, claim, self.subject)
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir(mode=0o700, exist_ok=False)
        return root

    def resume_root(self, attempt_ref: ArtifactRef, output_root: str) -> Path:
        observed = self.read_claim()
        if observed is None or observed[0] != attempt_ref:
            raise ValueError("paused run attempt claim is not current")
        claim = observed[1]
        if claim.output_root != output_root:
            raise ValueError("paused run output root differs from attempt claim")
        root = _resolve_root(self.store.root, claim, self.subject)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("paused output root is not the bound checkpoint root")
        return root

    def _read_claim(self, path: Path) -> tuple[ArtifactRef, AttemptClaim]:
        artifact = self.store.read_structured(
            path, TypeAdapter(ResearchArtifact[AttemptClaim])
        )
        _require_artifact(artifact, "tier2-replication-attempt-claim")
        if artifact.payload.subject_ref != self.subject:
            raise ValueError("claim subject does not match current alias")
        if artifact.envelope.input_artifacts != (self.subject,):
            raise ValueError("claim inputs do not bind its subject")
        reference = _reference(artifact)
        _require_copy(self.store, _claim_report(reference), artifact)
        _resolve_root(self.store.root, artifact.payload, self.subject)
        return reference, artifact.payload

    def _recover_claim(self) -> None:
        self._recover(
            _claim_pending(self.subject),
            _claim_current(self.subject),
            "tier2-replication-attempt-claim",
            _claim_report,
        )

    def _recover_failure(self) -> None:
        self._recover(
            _failure_pending(self.subject),
            _failure_current(self.subject),
            "tier2-replication-attempt",
            _failure_report,
        )

    def _recover(
        self,
        pending: Path,
        current: Path,
        artifact_id: str,
        report_path: Callable[[ArtifactRef], Path],
    ) -> None:
        if not (self.store.root / pending).exists():
            return
        artifact = self.store.read_structured(
            pending, TypeAdapter(ResearchArtifact[object])
        )
        _require_artifact(artifact, artifact_id)
        if (self.store.root / current).exists():
            observed = self.store.read_structured(
                current, TypeAdapter(ResearchArtifact[object])
            )
            if observed != artifact:
                raise ValueError("attempt current alias already has another writer")
        reference = _reference(artifact)
        target = report_path(reference)
        self.store.write_structured(target, artifact)
        self.store.write_structured(current, artifact)
        (self.store.root / pending).unlink()


def _seal(
    artifact_id: str, payload: object, inputs: tuple[ArtifactRef, ...]
) -> ResearchArtifact[object]:
    return seal_artifact(
        ResearchArtifact(
            envelope=ArtifactEnvelope(
                artifact_id=artifact_id,
                artifact_version=1,
                run_id="tier2-replication",
                created_at=datetime.now(UTC),
                producer=ProducerIdentity(
                    component="replication-service", version="0.3.0"
                ),
                input_artifacts=inputs,
                validation_status=ArtifactLifecycle.VALIDATED,
            ),
            payload=payload,
        )
    )


def _require_artifact(artifact: ResearchArtifact[Payload], artifact_id: str) -> None:
    if artifact.envelope.artifact_id != artifact_id:
        raise ValueError("attempt artifact ID mismatch")
    if artifact.envelope.producer.component != "replication-service":
        raise ValueError("attempt producer mismatch")
    if artifact.envelope.validation_status is not ArtifactLifecycle.VALIDATED:
        raise ValueError("attempt artifact is not validated")


def _require_copy(
    store: ResearchArtifactStore,
    path: Path,
    artifact: ResearchArtifact[Payload],
) -> None:
    observed = store.read_structured(path, TypeAdapter(ResearchArtifact[object]))
    if observed.model_dump(mode="json") != artifact.model_dump(mode="json"):
        raise ValueError("attempt report differs from current alias")


def _reference(artifact: ResearchArtifact[Payload]) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact.envelope.artifact_id,
        artifact_version=artifact.envelope.artifact_version,
        content_hash=artifact.envelope.content_hash or "",
    )


def _resolve_root(
    authority_root: Path, claim: AttemptClaim, subject: ArtifactRef
) -> Path:
    expected = f"artifacts/replication/runs/{subject.content_hash}/{claim.attempt_id}"
    if claim.output_root != expected:
        raise ValueError("attempt output root is not canonical")
    authority = authority_root.resolve()
    root = authority / claim.output_root
    if any(parent.is_symlink() for parent in (root, *root.parents[:4])):
        raise ValueError("attempt output root must not traverse symlinks")
    resolved = root.resolve()
    if authority not in resolved.parents:
        raise ValueError("attempt output root escapes run authority")
    return resolved


def _claim_current(subject: ArtifactRef) -> Path:
    return Path(
        f"artifacts/replication/attempts/claims/current/{subject.content_hash}.json"
    )


def _claim_pending(subject: ArtifactRef) -> Path:
    return Path(
        f"artifacts/replication/attempts/claims/.pending/{subject.content_hash}.json"
    )


def _claim_report(reference: ArtifactRef) -> Path:
    return Path(
        f"artifacts/replication/attempts/claims/reports/{reference.content_hash}.json"
    )


def _failure_current(subject: ArtifactRef) -> Path:
    return Path(f"artifacts/replication/attempts/current/{subject.content_hash}.json")


def _failure_pending(subject: ArtifactRef) -> Path:
    return Path(f"artifacts/replication/attempts/.pending/{subject.content_hash}.json")


def _failure_report(reference: ArtifactRef) -> Path:
    return Path(f"artifacts/replication/attempts/reports/{reference.content_hash}.json")

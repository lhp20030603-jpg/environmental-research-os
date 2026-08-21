"""Private authenticated storage and recovery for the replication ledger."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import ClassVar, TypeVar, cast

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, TypeAdapter

from envresearch.models.artifact import (
    ArtifactEnvelope,
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.replication._ledger_models import ReplicationRun
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ReplicationRunState,
)
from envresearch.storage.research_artifacts import ResearchArtifactStore

_LEDGER_PATH = Path("artifacts/replication/replication-ledger.yaml")
_REPORT_PATH = Path("artifacts/replication/replication-report.json")
_PENDING_PATH = Path("artifacts/replication/.pending-ledger.yaml")
_LEDGER_PRODUCER = ProducerIdentity(component="replication-ledger", version="0.3.0")
_CHECKPOINT_ID = "tier2-workspace-checkpoint"
_CHECKPOINT_PRODUCER = ProducerIdentity(
    component="replication-service", version="0.3.0"
)
Model = TypeVar("Model", bound=BaseModel)


def _from_json(model: type[Model], value: object) -> Model:
    return model.model_validate_json(json.dumps(value))


class LedgerPersistenceMixin:
    _thread_locks: ClassVar[dict[Path, Lock]] = {}
    _store: ResearchArtifactStore
    _failure_injector: Callable[[str], None] | None

    def _ledger_inputs(self, payload: ReplicationRun) -> tuple[ArtifactRef, ...]:
        raise NotImplementedError

    def _verification_refs(
        self, current: ResearchArtifact[ReplicationRun]
    ) -> tuple[ArtifactRef, ...]:
        raise NotImplementedError

    def _require_current(
        self, run_ref: ArtifactRef, states: set[ReplicationRunState]
    ) -> ResearchArtifact[ReplicationRun]:
        current = self._current()
        if self._reference(current) != run_ref:
            raise ValueError("replication run reference is not current")
        if current.payload.state not in states:
            raise ValueError("replication run is not in a permitted state")
        return current

    def _supersede(
        self, current: ResearchArtifact[ReplicationRun], **updates: object
    ) -> ArtifactRef:
        payload = current.payload.model_copy(update=updates)
        promoted = self._publish(payload, version=current.envelope.artifact_version + 1)
        return self._reference(promoted)

    def _publish(
        self, payload: ReplicationRun, *, version: int
    ) -> ResearchArtifact[ReplicationRun]:
        artifact = seal_artifact(
            ResearchArtifact(
                envelope=ArtifactEnvelope(
                    artifact_id="replication-ledger",
                    artifact_version=version,
                    run_id="tier2-replication",
                    created_at=datetime.now(UTC),
                    producer=_LEDGER_PRODUCER,
                    input_artifacts=self._ledger_inputs(payload),
                    validation_status=ArtifactLifecycle.VALIDATED,
                ),
                payload=payload,
            )
        )
        typed = TypeAdapter(ResearchArtifact[ReplicationRun]).validate_python(artifact)
        stored = cast(ResearchArtifact[object], typed)
        # A sealed pending record makes an interrupted three-file publication recoverable.
        self._store.write_structured(_PENDING_PATH, stored)
        self._checkpoint("history")
        self._store.write_structured(self._history_path(version), stored)
        self._checkpoint("current")
        self._store.write_structured(_LEDGER_PATH, stored)
        self._checkpoint("report")
        self._store.write_structured(_REPORT_PATH, stored)
        (self._store.root / _PENDING_PATH).unlink()
        return typed

    def _current(self) -> ResearchArtifact[ReplicationRun]:
        artifact = self._store.read_structured(
            _LEDGER_PATH, TypeAdapter(ResearchArtifact[ReplicationRun])
        )
        if artifact.envelope.artifact_id != "replication-ledger":
            raise ValueError("replication ledger artifact ID is invalid")
        if artifact.envelope.producer != _LEDGER_PRODUCER:
            raise ValueError("replication ledger producer is invalid")
        if artifact.envelope.validation_status is not ArtifactLifecycle.VALIDATED:
            raise ValueError("replication ledger is not validated")
        return artifact

    def _require_public_copies(self, current: ResearchArtifact[ReplicationRun]) -> None:
        for path in (
            self._history_path(current.envelope.artifact_version),
            _REPORT_PATH,
        ):
            observed = self._store.read_structured(
                path, TypeAdapter(ResearchArtifact[ReplicationRun])
            )
            if observed != current:
                raise ValueError("replication ledger public copy differs from current")

    def _require_terminal_evidence(
        self, current: ResearchArtifact[ReplicationRun]
    ) -> None:
        run = current.payload
        if run.state is ReplicationRunState.EXCEPTION and run.exception is None:
            raise ValueError("exception ledger lacks a typed exception")
        if run.state is ReplicationRunState.PAUSED:
            self._require_pause_checkpoint(current)
        if run.runtime_owner is not None and (
            run.state is not ReplicationRunState.RUNNING
            and (
                run.exception is None
                or run.exception.code != "CONTAINMENT_CLEANUP_FAILED"
            )
        ):
            raise ValueError("terminal ledger retains an invalid runtime owner")
        if run.state is not ReplicationRunState.PASSED:
            if run.verification_ref is not None:
                raise ValueError("non-passed ledger claims verification evidence")
            return
        if run.verification_ref is None or current.envelope.artifact_version < 2:
            raise ValueError("passed ledger lacks verification evidence")
        try:
            predecessor = self._store.read_structured(
                self._history_path(current.envelope.artifact_version - 1),
                TypeAdapter(ResearchArtifact[ReplicationRun]),
            )
        except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
            raise ValueError("completed predecessor is invalid") from error
        predecessor_envelope = predecessor.envelope
        if (
            predecessor_envelope.artifact_id != "replication-ledger"
            or predecessor_envelope.producer != _LEDGER_PRODUCER
            or predecessor_envelope.validation_status is not ArtifactLifecycle.VALIDATED
            or predecessor_envelope.artifact_version
            != current.envelope.artifact_version - 1
        ):
            raise ValueError("completed predecessor identity is invalid")
        predecessor_ref = self._reference(predecessor)
        expected = predecessor.payload.model_copy(
            update={
                "state": ReplicationRunState.PASSED,
                "verification_ref": run.verification_ref,
            }
        )
        if not predecessor.payload.verification_pending or run != expected:
            raise ValueError("completed predecessor chain is invalid")
        from envresearch.replication.verify import (
            VerificationPayload,
            VerificationReport,
        )

        raw_artifact = self._store.read_structured(
            Path(
                "artifacts/replication/verifications/"
                f"{run.verification_ref.content_hash}.json"
            ),
            TypeAdapter(ResearchArtifact[object]),
        )
        payload = _from_json(VerificationPayload, raw_artifact.payload)
        artifact = TypeAdapter(ResearchArtifact[VerificationPayload]).validate_python(
            raw_artifact.model_copy(update={"payload": payload})
        )
        if (
            self._reference_untyped(cast(ResearchArtifact[object], artifact))
            != run.verification_ref
        ):
            raise ValueError("verification artifact reference is invalid")
        report = VerificationReport(artifact=artifact)
        if (
            not report.passed
            or report.run_ref != predecessor_ref
            or report.verified_refs != self._verification_refs(predecessor)
        ):
            raise ValueError(
                "verification artifact does not bind completed predecessor"
            )

    def _require_pause_checkpoint(
        self, current: ResearchArtifact[ReplicationRun]
    ) -> None:
        run = current.payload
        if run.exception is None or len(run.exception.evidence_refs) != 1:
            raise ValueError("paused ledger lacks exactly one workspace checkpoint")
        reference = run.exception.evidence_refs[0]
        if reference.artifact_id != _CHECKPOINT_ID:
            raise ValueError("paused ledger evidence is not a workspace checkpoint")
        artifact = self._store.read_structured(
            Path(f"artifacts/replication/checkpoints/{reference.content_hash}.json"),
            TypeAdapter(ResearchArtifact[object]),
        )
        envelope = artifact.envelope
        if (
            self._reference_untyped(artifact) != reference
            or envelope.artifact_id != _CHECKPOINT_ID
            or envelope.producer != _CHECKPOINT_PRODUCER
            or envelope.validation_status is not ArtifactLifecycle.VALIDATED
            or len(envelope.input_artifacts) != 2
            or envelope.input_artifacts[1] != run.attempt_ref
        ):
            raise ValueError("workspace checkpoint identity is invalid")
        predecessor_ref = envelope.input_artifacts[0]
        if (
            predecessor_ref.artifact_id != "replication-ledger"
            or predecessor_ref.artifact_version != current.envelope.artifact_version - 1
        ):
            raise ValueError("workspace checkpoint predecessor is invalid")
        predecessor = self._history(predecessor_ref)
        if (
            predecessor.envelope.producer != _LEDGER_PRODUCER
            or predecessor.envelope.validation_status is not ArtifactLifecycle.VALIDATED
            or run
            != predecessor.payload.model_copy(
                update={
                    "state": ReplicationRunState.PAUSED,
                    "exception": run.exception,
                }
            )
        ):
            raise ValueError("paused ledger checkpoint chain is invalid")
        payload = artifact.payload
        if (
            not isinstance(payload, dict)
            or payload.get("output_root") != run.output_root
            or not isinstance(payload.get("files"), list)
        ):
            raise ValueError("workspace checkpoint payload is invalid")

    def _history(self, reference: ArtifactRef) -> ResearchArtifact[ReplicationRun]:
        artifact = self._store.read_structured(
            self._history_path(reference.artifact_version),
            TypeAdapter(ResearchArtifact[ReplicationRun]),
        )
        if self._reference(artifact) != reference:
            raise ValueError("replication ledger reference mismatch")
        return artifact

    def _require_acquired_binds_approved(
        self, acquired_ref: ArtifactRef, approved_ref: ArtifactRef
    ) -> None:
        artifact = self._store.read_structured(
            Path(f"artifacts/replication/inventories/{acquired_ref.content_hash}.json"),
            TypeAdapter(ResearchArtifact[object]),
        )
        if self._reference_untyped(artifact) != acquired_ref:
            raise ValueError("acquired inventory artifact reference mismatch")
        inventory = _from_json(AcquiredPackageInventory, artifact.payload)
        if inventory.approved_intake_ref != approved_ref:
            raise ValueError("acquired inventory is not bound to approved intake")

    def _require_attempt(
        self, approved_ref: ArtifactRef, attempt_ref: ArtifactRef, output_root: str
    ) -> None:
        from envresearch.replication._attempt_support import AttemptCoordinator

        observed = AttemptCoordinator(self._store, approved_ref).read_claim()
        if observed is None or observed[0] != attempt_ref:
            raise ValueError("replication attempt claim is not current")
        if observed[1].output_root != output_root:
            raise ValueError("replication output root differs from attempt claim")

    @staticmethod
    def _reference(artifact: ResearchArtifact[ReplicationRun]) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=artifact.envelope.artifact_id,
            artifact_version=artifact.envelope.artifact_version,
            content_hash=artifact.envelope.content_hash or "",
        )

    @staticmethod
    def _reference_untyped(artifact: ResearchArtifact[object]) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=artifact.envelope.artifact_id,
            artifact_version=artifact.envelope.artifact_version,
            content_hash=artifact.envelope.content_hash or "",
        )

    @staticmethod
    def _history_path(version: int) -> Path:
        return Path(
            f"artifacts/replication/.versions/replication-ledger/{version:04d}.yaml"
        )

    def _exists(self, path: Path) -> bool:
        return (self._store.root / path).exists()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize one ledger identity across threads and cooperating processes."""
        lock = self._store.root / "artifacts/replication/.replication-ledger.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        thread_lock = self._thread_locks.setdefault(self._store.root, Lock())
        with thread_lock:
            descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                self._recover_pending()
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _checkpoint(self, boundary: str) -> None:
        if self._failure_injector is not None:
            self._failure_injector(boundary)

    def _recover_pending(self) -> None:
        """Finish an interrupted publication from its sealed pending artifact."""
        pending = self._store.root / _PENDING_PATH
        if not pending.exists():
            return
        artifact = self._store.read_structured(
            _PENDING_PATH, TypeAdapter(ResearchArtifact[ReplicationRun])
        )
        stored = cast(ResearchArtifact[object], artifact)
        self._store.write_structured(
            self._history_path(artifact.envelope.artifact_version), stored
        )
        self._store.write_structured(_LEDGER_PATH, stored)
        self._store.write_structured(_REPORT_PATH, stored)
        pending.unlink()

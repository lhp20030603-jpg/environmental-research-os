"""Authenticated all-or-nothing source revision for one blind case."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from envresearch.models.artifact import ArtifactRef, ResearchArtifact
from envresearch.models.benchmark_claims import CuratorSourceSheet
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.principal import PrincipalAssignment
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.artifact_lifecycle_support import history_path
from envresearch.workers.contracts import require_safe_order_id
from envresearch.workers.filesystem import PinnedRoot
from envresearch.workers.native import rename_exchange_at

if TYPE_CHECKING:
    from envresearch.benchmarks.blind_artifacts import BlindArtifactPaths
    from envresearch.workers.control import QueueControl

_HASH = hashlib.sha256


class BlindRevisionTransaction:
    """Stage lifecycle transitions privately and atomically exchange current state."""

    def __init__(
        self,
        lifecycle: ResearchArtifactLifecycle,
        control: QueueControl,
        paths: BlindArtifactPaths,
    ) -> None:
        self.lifecycle = lifecycle
        self.control = control
        self.paths = paths

    def execute(
        self,
        source: CuratorSourceSheet,
        *,
        revision_id: str,
        reason: str,
        actor: str,
        curator: PrincipalAssignment,
    ) -> ArtifactRef:
        """Execute while the caller holds the case publication lock."""
        require_safe_order_id(revision_id)
        observed = self._preflight(source, curator)
        intent = self._intent(source, revision_id, reason, actor, curator, observed)
        self._authenticate_intent(revision_id, intent)
        with tempfile.TemporaryDirectory(
            prefix=f"blind-{revision_id}-", dir=self.lifecycle.workspace
        ) as temporary:
            shadow_root = Path(temporary)
            self._copy_case_state(shadow_root)
            replacement = self._stage_transitions(
                shadow_root, source, revision_id, reason, actor
            )
            self._require_unchanged(observed)
            self._publish_histories(shadow_root)
            self._require_unchanged(observed)
            self._exchange_current_case(shadow_root)
        return _artifact_ref(replacement)

    def recover_committed(
        self,
        source: CuratorSourceSheet,
        *,
        revision_id: str,
        reason: str,
        actor: str,
        curator: PrincipalAssignment,
    ) -> ArtifactRef | None:
        """Return an exactly authenticated already-committed revision."""
        require_safe_order_id(revision_id)
        record = self._read_authenticated_intent(revision_id)
        if record is None:
            return None
        intent = record.get("intent")
        if not isinstance(intent, dict):
            raise TypeError("blind revision intent is invalid")
        expected = {
            "revision_id": revision_id,
            "run_id": self.lifecycle.run_id,
            "reason": reason,
            "actor": actor,
            "curator": curator.model_dump(mode="json"),
            "source": source.model_dump(mode="json"),
        }
        if any(intent.get(key) != value for key, value in expected.items()):
            raise RuntimeError("blind revision intent identity collision")
        raw_targets = intent.get("targets")
        if not isinstance(raw_targets, list):
            raise TypeError("blind revision targets are invalid")
        targets: dict[Path, ArtifactRef] = {}
        for raw in raw_targets:
            if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
                raise TypeError("blind revision target is invalid")
            path = Path(raw["path"])
            targets[path] = ArtifactRef.model_validate(raw.get("ref"))
        self._verify_committed(source, revision_id, curator, targets)
        return self.lifecycle.artifact_ref(self.paths.source_sheet)

    def _preflight(
        self, source: CuratorSourceSheet, curator: PrincipalAssignment
    ) -> dict[Path, ArtifactRef]:
        observed: dict[Path, ArtifactRef] = {}
        for path in (self.paths.source_sheet, *self.paths.descendants):
            if not (self.lifecycle.workspace / path).exists():
                continue
            envelope = self.lifecycle.current_envelope(path)
            if envelope.validation_status is not ArtifactLifecycle.VALIDATED:
                raise ValueError("blind revision requires a validated current chain")
            current = self.lifecycle.artifact_ref(path)
            if self.lifecycle.validated_history_ref(path) != current:
                raise FileExistsError("blind revision current history is stale")
            observed[path] = current
        if self.paths.source_sheet not in observed:
            raise FileNotFoundError("blind source sheet is missing")
        current_source = self.lifecycle.require_validated(
            self.paths.source_sheet, producer=curator.producer, inputs=()
        )
        current_payload = CuratorSourceSheet.model_validate_json(
            json.dumps(current_source.payload)
        )
        if source.source_generation <= current_payload.source_generation:
            raise ValueError("source generation must advance")
        return observed

    def _intent(
        self,
        source: CuratorSourceSheet,
        revision_id: str,
        reason: str,
        actor: str,
        curator: PrincipalAssignment,
        observed: dict[Path, ArtifactRef],
    ) -> bytes:
        payload = {
            "revision_id": revision_id,
            "run_id": self.lifecycle.run_id,
            "reason": reason,
            "actor": actor,
            "curator": curator.model_dump(mode="json"),
            "source": source.model_dump(mode="json"),
            "targets": [
                {"path": path.as_posix(), "ref": ref.model_dump(mode="json")}
                for path, ref in observed.items()
            ],
        }
        return _canonical(payload)

    def _authenticate_intent(self, revision_id: str, intent: bytes) -> None:
        self.control.storage.ensure_directory(Path("blind-revisions"))
        unsigned = {
            "revision_id": revision_id,
            "intent_sha256": _HASH(intent).hexdigest(),
            "intent": json.loads(intent),
        }
        record = {
            **unsigned,
            "mac": hmac.new(self.control.key, _canonical(unsigned), _HASH).hexdigest(),
        }
        data = _canonical(record)
        path = Path("blind-revisions") / f"{revision_id}.json"
        if self.control.storage.exists(path):
            durable = self.control.storage.read_file(
                path,
                description="blind revision intent",
                required_mode=0o600,
                required_owner=os.geteuid(),
            )
            if durable != data:
                raise RuntimeError("blind revision intent identity collision")
            return
        self.control.storage.write_file_noreplace(path, data, mode=0o600)

    def _read_authenticated_intent(self, revision_id: str) -> dict[str, object] | None:
        path = Path("blind-revisions") / f"{revision_id}.json"
        if not self.control.storage.exists(path):
            return None
        data = self.control.storage.read_file(
            path,
            description="blind revision intent",
            required_mode=0o600,
            required_owner=os.geteuid(),
        )
        record = json.loads(data)
        if not isinstance(record, dict) or data != _canonical(record):
            raise ValueError("blind revision intent authentication failed")
        mac = record.get("mac")
        unsigned = {key: value for key, value in record.items() if key != "mac"}
        expected = hmac.new(self.control.key, _canonical(unsigned), _HASH).hexdigest()
        if not isinstance(mac, str) or not hmac.compare_digest(mac, expected):
            raise ValueError("blind revision intent authentication failed")
        intent = record.get("intent")
        if (
            record.get("revision_id") != revision_id
            or not isinstance(intent, dict)
            or record.get("intent_sha256") != _HASH(_canonical(intent)).hexdigest()
        ):
            raise ValueError("blind revision intent authentication mismatch")
        return record

    def _verify_committed(
        self,
        source: CuratorSourceSheet,
        revision_id: str,
        curator: PrincipalAssignment,
        targets: dict[Path, ArtifactRef],
    ) -> None:
        source_path = self.paths.source_sheet
        current = self.lifecycle.read_artifact(source_path)
        if (
            current.payload != source.model_dump(mode="json")
            or current.envelope.producer != curator.producer
            or self.lifecycle.validated_history_ref(source_path)
            != self.lifecycle.artifact_ref(source_path)
        ):
            raise ValueError("committed blind revision source is invalid")
        source_superseded = self.lifecycle.read_history(
            source_path, current.envelope.artifact_version - 2
        )
        self._require_revision_history(
            source_superseded, revision_id, targets.pop(source_path, None)
        )
        expected_descendants = {
            path
            for path in self.paths.descendants
            if (self.lifecycle.workspace / path).exists()
        }
        if set(targets) != expected_descendants:
            raise ValueError("committed blind revision targets are incomplete")
        for path, target in targets.items():
            current_descendant = self.lifecycle.read_artifact(path)
            history = self.lifecycle.read_history(
                path, current_descendant.envelope.artifact_version
            )
            if history != current_descendant:
                raise FileExistsError("committed blind revision history is stale")
            self._require_revision_history(history, revision_id, target)

    @staticmethod
    def _require_revision_history(
        artifact: ResearchArtifact[object],
        revision_id: str,
        target: ArtifactRef | None,
    ) -> None:
        provenance = artifact.envelope.provenance
        if (
            target is None
            or artifact.envelope.validation_status is not ArtifactLifecycle.SUPERSEDED
            or provenance.get("revision_id") != revision_id
            or provenance.get("supersedes_ref") != target.model_dump(mode="json")
        ):
            raise ValueError("committed blind revision history is invalid")

    def _copy_case_state(self, shadow_root: Path) -> None:
        for relative in (self.paths.source_sheet.parent, self._history_case_root()):
            source = self.lifecycle.workspace / relative
            if source.exists():
                shutil.copytree(source, shadow_root / relative)

    def _stage_transitions(
        self,
        shadow_root: Path,
        source: CuratorSourceSheet,
        revision_id: str,
        reason: str,
        actor: str,
    ) -> ResearchArtifact[object]:
        shadow = ResearchArtifactLifecycle(shadow_root, self.lifecycle.run_id)
        for path in reversed(self.paths.descendants):
            if (shadow_root / path).exists():
                shadow.supersede(
                    path, revision_id=revision_id, reason=reason, actor=actor
                )
        shadow.supersede(
            self.paths.source_sheet,
            revision_id=revision_id,
            reason=reason,
            actor=actor,
        )
        replacement = shadow.persist_structured(
            self.paths.source_sheet,
            source,
            self.lifecycle.current_envelope(self.paths.source_sheet).producer,
            (),
        )
        self._require_staged_histories(shadow, replacement)
        return replacement

    def _require_staged_histories(
        self,
        shadow: ResearchArtifactLifecycle,
        replacement: ResearchArtifact[object],
    ) -> None:
        for path in (self.paths.source_sheet, *self.paths.descendants):
            if not (shadow.workspace / path).exists():
                continue
            version = shadow.current_envelope(path).artifact_version
            shadow.read_history(path, version)
        source_version = replacement.envelope.artifact_version
        shadow.read_history(self.paths.source_sheet, source_version - 1)

    def _publish_histories(self, shadow_root: Path) -> None:
        relative_root = self._history_case_root()
        shadow_history = shadow_root / relative_root
        if not shadow_history.exists():
            return
        pinned = PinnedRoot(self.lifecycle.workspace)
        try:
            for source in sorted(shadow_history.rglob("*")):
                if not source.is_file():
                    continue
                relative = relative_root / source.relative_to(shadow_history)
                destination = self.lifecycle.workspace / relative
                data = source.read_bytes()
                if destination.exists():
                    if destination.read_bytes() != data:
                        raise FileExistsError("blind revision history collision")
                    continue
                pinned.write_file_noreplace(relative, data, mode=0o644)
        finally:
            pinned.close()

    def _require_unchanged(self, observed: dict[Path, ArtifactRef]) -> None:
        durable = {path: self.lifecycle.artifact_ref(path) for path in observed}
        if durable != observed:
            raise FileExistsError("blind current chain changed during revision")

    def _exchange_current_case(self, shadow_root: Path) -> None:
        live = self.lifecycle.workspace / self.paths.source_sheet.parent
        staged = shadow_root / self.paths.source_sheet.parent
        parent = live.parent
        candidate = Path(tempfile.mkdtemp(prefix=".blind-revision-", dir=parent))
        try:
            shutil.copytree(staged, candidate, dirs_exist_ok=True)
            parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                rename_exchange_at(parent_fd, live.name, parent_fd, candidate.name)
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            shutil.rmtree(candidate, ignore_errors=True)

    def _history_case_root(self) -> Path:
        return history_path(self.paths.source_sheet, 1).parent.parent


def _artifact_ref(artifact: ResearchArtifact[object]) -> ArtifactRef:
    envelope = artifact.envelope
    if envelope.content_hash is None:
        raise ValueError("blind replacement artifact is unsealed")
    return ArtifactRef(
        artifact_id=envelope.artifact_id,
        artifact_version=envelope.artifact_version,
        content_hash=envelope.content_hash,
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()

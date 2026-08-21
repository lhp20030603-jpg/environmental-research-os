"""Protected authentication and public projection for revision intents."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator

from envresearch.research.revision_models import RevisionIntent
from envresearch.workers.queue import FilesystemWorkerQueue

_HASH = hashlib.sha256


class _IntentAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1.0"] = "1.0"
    revision_id: str
    intent_sha256: str
    intent: dict[str, JsonValue]
    mac: str

    @field_validator("intent_sha256", "mac")
    @classmethod
    def require_digest(cls, value: str) -> str:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("revision intent digest must be lowercase SHA-256")
        return value


class ProtectedRevisionIntents:
    """Make private authenticated anchors authoritative over public intent files."""

    def __init__(self, queue: FilesystemWorkerQueue) -> None:
        self.queue = queue
        self.queue.control.storage.ensure_directory(Path("revision-intents"))

    def persist(self, intent: RevisionIntent) -> None:
        """Authenticate intent content before publishing its public projection."""
        data = _canonical(intent.model_dump(mode="json"))
        anchor = self._seal(intent, data)
        relative = self._protected_path(intent.revision_id)
        encoded = _canonical(anchor.model_dump(mode="json"))
        if self.queue.control.storage.exists(relative):
            if self._read_anchor(intent.revision_id) != anchor:
                raise RuntimeError("revision intent protected identity collision")
        else:
            try:
                self.queue.control.storage.write_file_noreplace(
                    relative, encoded, mode=0o600
                )
            except FileExistsError:
                if self._read_anchor(intent.revision_id) != anchor:
                    raise RuntimeError("revision intent protected identity collision")
        self._publish_public(intent, data)

    def all(self) -> list[RevisionIntent]:
        """Load protected intents and verify or recover each public projection."""
        names = self.queue.control.storage.list_directory(Path("revision-intents"))
        intents: list[RevisionIntent] = []
        protected_ids: set[str] = set()
        for name in names:
            if not name.endswith(".json"):
                raise ValueError("protected revision intent namespace is invalid")
            revision_id = name.removesuffix(".json")
            anchor = self._read_anchor(revision_id)
            intent = RevisionIntent.model_validate(anchor.intent)
            data = _canonical(intent.model_dump(mode="json"))
            if (
                intent.revision_id != revision_id
                or anchor.intent_sha256 != _HASH(data).hexdigest()
            ):
                raise ValueError("revision intent authentication mismatch")
            self._publish_public(intent, data)
            protected_ids.add(revision_id)
            intents.append(intent)
        self._reject_unprotected_public_intents(protected_ids)
        return intents

    def _publish_public(self, intent: RevisionIntent, data: bytes) -> None:
        relative = self._public_path(intent.revision_id)
        if self.queue.exchange.exists(relative):
            existing = self.queue.exchange.read_file(
                relative,
                description="revision intent",
                required_mode=0o600,
                required_owner=os.geteuid(),
            )
            if existing != data:
                raise ValueError("revision intent authentication mismatch")
            return
        try:
            self.queue.exchange.write_file_noreplace(relative, data, mode=0o600)
        except FileExistsError:
            self._publish_public(intent, data)

    def _read_anchor(self, revision_id: str) -> _IntentAnchor:
        data = self.queue.control.storage.read_file(
            self._protected_path(revision_id),
            description="protected revision intent",
            required_mode=0o600,
            required_owner=os.geteuid(),
        )
        anchor = _IntentAnchor.model_validate_json(data)
        unsigned = anchor.model_dump(mode="json", exclude={"mac"})
        if data != _canonical(
            anchor.model_dump(mode="json")
        ) or not hmac.compare_digest(
            anchor.mac,
            hmac.new(self.queue.control.key, _canonical(unsigned), _HASH).hexdigest(),
        ):
            raise ValueError("revision intent authentication failed")
        return anchor

    def _seal(self, intent: RevisionIntent, data: bytes) -> _IntentAnchor:
        payload = intent.model_dump(mode="json")
        unsigned = {
            "schema_version": "1.0",
            "revision_id": intent.revision_id,
            "intent_sha256": _HASH(data).hexdigest(),
            "intent": payload,
        }
        return _IntentAnchor(
            schema_version="1.0",
            revision_id=intent.revision_id,
            intent_sha256=_HASH(data).hexdigest(),
            intent=payload,
            mac=hmac.new(
                self.queue.control.key, _canonical(unsigned), _HASH
            ).hexdigest(),
        )

    def _reject_unprotected_public_intents(self, protected: set[str]) -> None:
        try:
            names = self.queue.exchange.list_directory(Path("revisions"))
        except FileNotFoundError:
            return
        for name in names:
            if name == "journal.jsonl":
                continue
            if not name.startswith("rev-") or name not in protected:
                raise ValueError("revision intent lacks protected authentication")

    @staticmethod
    def _protected_path(revision_id: str) -> Path:
        return Path("revision-intents") / f"{revision_id}.json"

    @staticmethod
    def _public_path(revision_id: str) -> Path:
        return Path("revisions") / revision_id / "intent.json"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()

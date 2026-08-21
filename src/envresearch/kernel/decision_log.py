"""Durable append-only audit records for generic research decisions."""

import json
import math
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
)

from envresearch.storage.secure_journal import SecureJournal


def _require_nonblank(value: str, field_name: str) -> str:
    """Return a normalized required text field."""
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _normalize_json_value(value: object) -> JsonValue:
    """Validate and deep-copy one finite JSON value without coercion."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            normalized[key] = _normalize_json_value(item)
        return normalized
    raise ValueError("value must be JSON-compatible")


def normalize_json_object(value: object, *, field_name: str) -> dict[str, JsonValue]:
    """Validate and deep-copy a JSON object required as decision metadata."""
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a JSON object")
    normalized = _normalize_json_value(value)
    assert isinstance(normalized, dict)
    return normalized


class DecisionLogEntry(BaseModel):
    """One generic, attributable, immutable research decision record."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(
        validation_alias=AliasChoices("event_id", "decision_id"),
    )
    timestamp: datetime
    actor: str
    decision_kind: str
    status: str
    subject: str
    reason: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def decision_id(self) -> str:
        """Expose the durable event identity under its decision-log name."""
        return self.event_id

    @field_validator(
        "event_id", "actor", "decision_kind", "status", "subject", "reason"
    )
    @classmethod
    def require_nonblank_text(cls, value: str, info: Any) -> str:
        """Reject anonymous or semantically empty decision records."""
        return _require_nonblank(value, info.field_name)

    @field_validator("timestamp")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Require the unambiguous UTC representation used by event replay."""
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("timestamps must be UTC-aware")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def require_json_metadata(cls, value: object) -> dict[str, JsonValue]:
        """Keep generic metadata portable, finite, and immune to caller mutation."""
        try:
            return normalize_json_object(value, field_name="metadata")
        except TypeError as error:
            raise ValueError(str(error)) from error


class DecisionLogCorruptionError(ValueError):
    """Raised when a durable decision log contains an unreadable record."""


class DecisionLog:
    """Persist generic decision records as durable, canonical JSONL."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._journal = SecureJournal(path)

    def close(self) -> None:
        """Release the descriptor-pinned journal roots."""
        self._journal.close()

    def __del__(self) -> None:
        journal = getattr(self, "_journal", None)
        if journal is not None:
            try:
                journal.close()
            except OSError:
                pass

    def append(self, entry: DecisionLogEntry) -> None:
        """Append a decision once, rejecting identity reuse with changed content."""
        durable_entry = self._revalidate_entry(entry)
        try:
            self._journal.append_unique(
                durable_entry.model_dump(mode="json"),
                identity_fields=("event_id",),
            )
        except RuntimeError as error:
            if str(error) != "journal identity collision":
                raise
            raise RuntimeError("decision identity collision") from error

    def read_all(self) -> list[DecisionLogEntry]:
        """Return every record, rejecting corrupt or colliding JSONL histories."""
        return self._read_all_unlocked()

    def _read_all_unlocked(self) -> list[DecisionLogEntry]:
        entries: list[DecisionLogEntry] = []
        identities: dict[str, bytes] = {}
        try:
            payloads = self._journal.read_all()
            for line_number, value in enumerate(payloads, start=1):
                try:
                    entry = DecisionLogEntry.model_validate(value)
                    canonical = self._serialize(entry)
                    prior = identities.get(entry.decision_id)
                    if prior is not None:
                        if prior != canonical:
                            raise RuntimeError("decision identity collision")
                        raise RuntimeError("duplicate decision identity")
                    identities[entry.decision_id] = canonical
                    entries.append(entry)
                except (
                    json.JSONDecodeError,
                    RuntimeError,
                    UnicodeError,
                    ValidationError,
                    TypeError,
                    ValueError,
                ) as error:
                    raise DecisionLogCorruptionError(
                        f"decision log corruption in {self.path} at line {line_number}: {error}"
                    ) from error
        except DecisionLogCorruptionError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise DecisionLogCorruptionError(str(error)) from error
        return entries

    def ensure(self) -> None:
        """Create and authenticate the mandatory empty ledger."""
        self._journal.ensure()

    @staticmethod
    def _revalidate_entry(entry: DecisionLogEntry) -> DecisionLogEntry:
        """Revalidate model instances before persistence to defeat forged copies."""
        return DecisionLogEntry.model_validate(dict(entry.__dict__))

    @staticmethod
    def _serialize(entry: DecisionLogEntry) -> bytes:
        """Produce a compact, deterministic JSON representation for identity checks."""
        return json.dumps(
            entry.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

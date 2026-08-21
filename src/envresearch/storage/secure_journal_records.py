"""Canonical record and head authentication for secure research journals."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

HASH = hashlib.sha256
ZERO_HASH = "0" * 64


class SecureRecord(BaseModel):
    """Authenticated chain metadata stored beside an unchanged public payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1.0"] = "1.0"
    sequence: int
    previous_sha256: str
    record_sha256: str
    mac: str

    @field_validator("sequence")
    @classmethod
    def require_sequence(cls, value: int) -> int:
        if value < 1:
            raise ValueError("journal sequence must be positive")
        return value

    @field_validator("previous_sha256", "record_sha256", "mac")
    @classmethod
    def require_digest(cls, value: str) -> str:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("journal digest must be lowercase SHA-256")
        return value


class JournalHead(BaseModel):
    """Authenticated protected identity of the latest durable public record."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1.0"] = "1.0"
    journal_id: str
    device: int
    inode: int
    record_count: int
    size_bytes: int
    last_sha256: str
    mac: str


def seal_record(
    key: bytes, payload: Mapping[str, object], sequence: int, previous: str
) -> tuple[dict[str, Any], SecureRecord, bytes]:
    """Return normalized payload, metadata, and one flattened canonical record."""
    durable = json_object(payload)
    if "_journal" in durable:
        raise ValueError("journal payload uses a reserved field")
    unsigned = {
        "payload": durable,
        "previous_sha256": previous,
        "sequence": sequence,
    }
    unsigned_bytes = canonical(unsigned)
    digest = HASH(unsigned_bytes).hexdigest()
    mac = hmac.new(key, unsigned_bytes + digest.encode(), HASH).hexdigest()
    metadata = SecureRecord(
        sequence=sequence,
        previous_sha256=previous,
        record_sha256=digest,
        mac=mac,
    )
    return durable, metadata, canonical({**durable, "_journal": metadata.model_dump()})


def parse_records(
    data: bytes, key: bytes, path: Path
) -> tuple[list[dict[str, Any]], list[SecureRecord]]:
    """Authenticate canonical records and their strict chronological hash chain."""
    payloads: list[dict[str, Any]] = []
    records: list[SecureRecord] = []
    previous = ZERO_HASH
    for line_number, raw in enumerate(data.splitlines(keepends=True), start=1):
        try:
            if not raw.endswith(b"\n"):
                raise ValueError("truncated journal record")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise TypeError("journal record must be an object")
            metadata = SecureRecord.model_validate(value.pop("_journal", None))
            payload, expected, encoded = seal_record(
                key, value, metadata.sequence, metadata.previous_sha256
            )
            if raw != encoded + b"\n" or metadata != expected:
                raise ValueError("journal record authentication failed")
            if metadata.sequence != line_number or metadata.previous_sha256 != previous:
                raise ValueError("journal hash chain is broken")
            previous = metadata.record_sha256
            payloads.append(payload)
            records.append(metadata)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(
                f"journal corruption in {path} at line {line_number}: {error}"
            ) from error
    return payloads, records


def seal_head(key: bytes, values: Mapping[str, object]) -> JournalHead:
    """Authenticate one normalized journal-head payload."""
    data = {"schema_version": "1.0", **json_object(values)}
    return JournalHead(
        schema_version="1.0",
        journal_id=str(data["journal_id"]),
        device=int(data["device"]),
        inode=int(data["inode"]),
        record_count=int(data["record_count"]),
        size_bytes=int(data["size_bytes"]),
        last_sha256=str(data["last_sha256"]),
        mac=hmac.new(key, canonical(data), HASH).hexdigest(),
    )


def verify_head(data: bytes, key: bytes) -> JournalHead:
    """Parse one canonical head and verify its protected MAC."""
    head = JournalHead.model_validate_json(data)
    unsigned = head.model_dump(exclude={"mac"})
    if data != canonical(head.model_dump()) or not hmac.compare_digest(
        head.mac, hmac.new(key, canonical(unsigned), HASH).hexdigest()
    ):
        raise ValueError("journal head authentication failed")
    return head


def json_object(value: Mapping[str, object]) -> dict[str, Any]:
    """Deep-copy one finite JSON mapping through its canonical representation."""
    encoded = canonical(dict(value))
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("journal payload must be a JSON object")
    return decoded


def canonical(value: object) -> bytes:
    """Return strict deterministic UTF-8 JSON bytes."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()

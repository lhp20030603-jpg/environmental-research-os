"""Authoritative persistence for typed research artifacts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar, cast

import yaml  # type: ignore[import-untyped]
from pydantic import TypeAdapter

from envresearch.models.artifact import (
    ArtifactEnvelope,
    ResearchArtifact,
    verify_artifact,
)
from envresearch.storage.artifacts import ArtifactRecord
from envresearch.storage.atomic import _sync_parent_directory, atomic_write_bytes
from envresearch.storage.hashing import sha256_file
from envresearch.storage.paths import safe_join

T = TypeVar("T")
_AUTHORITATIVE_NAMESPACES = frozenset(
    {"artifacts", "connector-receipts", "decisions", "node-checkpoints"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ResearchArtifactStore:
    """Persist verified research artifacts in authoritative namespaces only."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def write_structured(
        self, relative: Path, artifact: ResearchArtifact[object]
    ) -> ArtifactRecord:
        """Atomically persist a sealed artifact as canonical JSON or safe YAML."""
        suffix = _require_path(relative, {".json", ".yaml"})
        verify_artifact(artifact)
        payload = artifact.model_dump(mode="json")
        data = _dump_json(payload) if suffix == ".json" else _dump_yaml(payload)
        return _write_record(self.root, relative, data)

    def read_structured(
        self, relative: Path, adapter: TypeAdapter[ResearchArtifact[T]]
    ) -> ResearchArtifact[T]:
        """Load, validate, and verify a canonical JSON or YAML artifact."""
        suffix = _require_path(relative, {".json", ".yaml"})
        path, _ = _resolve_authoritative_path(self.root, relative, {".json", ".yaml"})
        data = path.read_bytes()
        value = json.loads(data) if suffix == ".json" else yaml.safe_load(data)
        if not isinstance(value, dict):
            raise TypeError("structured artifact must contain an object")
        artifact = adapter.validate_python(value)
        verify_artifact(cast(ResearchArtifact[object], artifact))
        return artifact

    def write_csv(
        self,
        relative: Path,
        fieldnames: tuple[str, ...],
        rows: Iterable[Mapping[str, object]],
        envelope: ArtifactEnvelope,
    ) -> tuple[ArtifactRecord, ArtifactRecord]:
        """Persist deterministic CSV bytes and a complete metadata sidecar."""
        csv_path, csv_relative = _resolve_authoritative_path(
            self.root, relative, {".csv"}
        )
        metadata_relative = relative.with_suffix(".meta.json")
        metadata_path, resolved_metadata_relative = _resolve_authoritative_path(
            self.root, metadata_relative, {".json"}
        )
        if not fieldnames or len(set(fieldnames)) != len(fieldnames):
            raise ValueError("CSV fieldnames must be unique and nonempty")
        if any(not isinstance(fieldname, str) for fieldname in fieldnames):
            raise TypeError("CSV fieldnames must be strings")

        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            if not isinstance(row, Mapping):
                raise TypeError("CSV rows must be mappings")
            writer.writerow(row)
        csv_data = stream.getvalue().encode("utf-8")
        content_hash = hashlib.sha256(csv_data).hexdigest()
        sealed_envelope = envelope.model_copy(update={"content_hash": content_hash})

        csv_record, metadata_record = _write_csv_pair(
            csv_path,
            csv_relative,
            csv_data,
            metadata_path,
            resolved_metadata_relative,
            _dump_json(sealed_envelope.model_dump(mode="json")),
        )
        return csv_record, metadata_record

    def write_markdown(
        self, relative: Path, envelope: ArtifactEnvelope, body: str
    ) -> ArtifactRecord:
        """Persist Markdown with a complete, integrity-protected YAML front matter."""
        _require_path(relative, {".md"})
        normalized_body = _normalize_body(body)
        content_hash = hashlib.sha256(normalized_body).hexdigest()
        sealed_envelope = envelope.model_copy(update={"content_hash": content_hash})
        front_matter = _dump_yaml(sealed_envelope.model_dump(mode="json"))
        data = b"---\n" + front_matter + b"---\n" + normalized_body
        return _write_record(self.root, relative, data)

    def read_markdown(self, relative: Path) -> tuple[ArtifactEnvelope, str]:
        """Load Markdown, validating the front matter envelope and body digest."""
        _require_path(relative, {".md"})
        path, _ = _resolve_authoritative_path(self.root, relative, {".md"})
        data = path.read_bytes()
        front_matter, body = _split_markdown(data)
        value = yaml.safe_load(front_matter)
        if not isinstance(value, dict):
            raise TypeError("Markdown front matter must contain an object")
        envelope = ArtifactEnvelope.model_validate(value)
        _verify_body_hash(envelope, body)
        return envelope, body.decode("utf-8")


def _require_path(relative: Path, allowed_suffixes: set[str]) -> str:
    """Require a non-traversing path in an authoritative namespace."""
    if relative.is_absolute():
        raise ValueError("path must be relative to workspace")
    if ".." in relative.parts:
        raise ValueError("path traversal is not allowed")
    if not relative.parts or relative.parts[0] not in _AUTHORITATIVE_NAMESPACES:
        raise PermissionError("path is outside authoritative artifact namespaces")
    suffix = relative.suffix
    if suffix not in allowed_suffixes:
        raise ValueError("unsupported artifact extension")
    return suffix


def _resolve_authoritative_path(
    root: Path, relative: Path, allowed_suffixes: set[str]
) -> tuple[Path, Path]:
    """Resolve a path and require both lexical and physical authority."""
    _require_path(relative, allowed_suffixes)
    resolved_root = root.resolve()
    path = safe_join(resolved_root, relative)
    resolved_relative = path.relative_to(resolved_root)
    _require_path(resolved_relative, allowed_suffixes)
    return path, resolved_relative


def _dump_json(payload: object) -> bytes:
    """Serialize canonical compact UTF-8 JSON."""
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _dump_yaml(payload: object) -> bytes:
    """Serialize deterministic, safe Unicode YAML."""
    return str(yaml.safe_dump(payload, sort_keys=True, allow_unicode=True)).encode(
        "utf-8"
    )


def _write_record(root: Path, relative: Path, data: bytes) -> ArtifactRecord:
    """Write bytes atomically and return their persisted metadata."""
    path, normalized_relative = _resolve_authoritative_path(
        root, relative, {relative.suffix}
    )
    atomic_write_bytes(path, data)
    return _record_for_path(path, normalized_relative)


def _write_csv_pair(
    csv_path: Path,
    csv_relative: Path,
    csv_data: bytes,
    metadata_path: Path,
    metadata_relative: Path,
    metadata_data: bytes,
) -> tuple[ArtifactRecord, ArtifactRecord]:
    """Stage and publish a CSV and sidecar without exposing an orphan CSV."""
    previous_csv = csv_path.read_bytes() if csv_path.exists() else None
    previous_metadata = metadata_path.read_bytes() if metadata_path.exists() else None
    csv_stage: Path | None = _stage_bytes(csv_path, csv_data)
    metadata_stage: Path | None = None
    try:
        metadata_stage = _stage_bytes(metadata_path, metadata_data)
        if csv_stage is None:
            raise RuntimeError("CSV staging unexpectedly failed")
        _replace_and_sync(csv_stage, csv_path)
        csv_stage = None
        if metadata_stage is None:
            raise RuntimeError("metadata staging unexpectedly failed")
        _replace_and_sync(metadata_stage, metadata_path)
        metadata_stage = None
    except OSError:
        _restore_pair(csv_path, previous_csv, metadata_path, previous_metadata)
        raise
    finally:
        for staged in (csv_stage, metadata_stage):
            if staged is not None:
                try:
                    staged.unlink(missing_ok=True)
                except OSError:
                    pass
    return (
        _record_for_path(csv_path, csv_relative),
        _record_for_path(metadata_path, metadata_relative),
    )


def _stage_bytes(path: Path, data: bytes) -> Path:
    """Atomically write unpublished bytes beside their eventual destination."""
    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.stage")
    atomic_write_bytes(staged, data)
    return staged


def _restore_pair(
    csv_path: Path,
    previous_csv: bytes | None,
    metadata_path: Path,
    previous_metadata: bytes | None,
) -> None:
    """Best-effort restore of a pair while retaining the original failure."""
    for path, previous in (
        (csv_path, previous_csv),
        (metadata_path, previous_metadata),
    ):
        try:
            _restore_file(path, previous)
        except OSError:
            pass


def _restore_file(path: Path, previous: bytes | None) -> None:
    """Restore one pre-publication file state with durable directory updates."""
    if previous is None:
        if path.exists():
            path.unlink()
            _sync_parent_directory(path.parent)
    else:
        staged = _stage_bytes(path, previous)
        try:
            _replace_and_sync(staged, path)
        except Exception:
            staged.unlink(missing_ok=True)
            raise


def _replace_and_sync(source: Path, destination: Path) -> None:
    """Replace one staged file and durably synchronize its directory entry."""
    os.replace(source, destination)
    _sync_parent_directory(destination.parent)


def _record_for_path(path: Path, relative_path: Path) -> ArtifactRecord:
    """Return durable metadata for an already-persisted file."""
    return ArtifactRecord(
        relative_path=relative_path,
        sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
        written_at=datetime.now(UTC),
    )


def _normalize_body(body: str) -> bytes:
    """Normalize Markdown line endings before UTF-8 hashing and storage."""
    return body.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _split_markdown(data: bytes) -> tuple[bytes, bytes]:
    """Return front matter and normalized body from one Markdown document."""
    if not data.startswith(b"---\n"):
        raise ValueError("Markdown must start with YAML front matter")
    delimiter = data.find(b"\n---\n", len(b"---\n"))
    if delimiter == -1:
        raise ValueError("Markdown front matter is not terminated")
    front_matter = data[len(b"---\n") : delimiter + 1]
    body = data[delimiter + len(b"\n---\n") :]
    try:
        body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Markdown body must be UTF-8") from error
    return front_matter, body


def _verify_body_hash(envelope: ArtifactEnvelope, body: bytes) -> None:
    """Validate the stored body against the front matter digest."""
    content_hash = envelope.content_hash
    if content_hash is None or not _SHA256.fullmatch(content_hash):
        raise ValueError("Markdown content hash must be a lowercase SHA-256")
    if hashlib.sha256(body).hexdigest() != content_hash:
        raise ValueError("Markdown content hash mismatch")

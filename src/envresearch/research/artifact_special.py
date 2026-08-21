"""Revision-aware CSV and Markdown lifecycle publication."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from envresearch.models.artifact import ArtifactEnvelope, ArtifactRef, ProducerIdentity
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.evidence import LiteratureMapPayload
from envresearch.research.artifact_csv import _EVIDENCE_FIELDS, _csv_bytes
from envresearch.research.artifact_lifecycle_support import (
    normalize_body,
    producer_identity,
)

if TYPE_CHECKING:
    from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle


def persist_csv(
    lifecycle: ResearchArtifactLifecycle,
    payload: LiteratureMapPayload,
    component: str | ProducerIdentity,
    inputs: tuple[ArtifactRef, ...],
) -> None:
    """Publish an evidence matrix after either initial or superseded state."""
    path = Path("artifacts/evidence-matrix.csv")
    rows = tuple(item.model_dump(mode="json") for item in payload.evidence_rows)
    producer = producer_identity(component)
    first = _next_version(lifecycle, path)
    lifecycle._history(path, rows, producer, inputs, first, ArtifactLifecycle.PRODUCED)
    data = _csv_bytes(rows)
    digest = hashlib.sha256(data).hexdigest()
    validated = lifecycle._history(
        path,
        {"format": "csv", "authoritative_content_hash": digest, "rows": rows},
        producer,
        inputs,
        first + 1,
        ArtifactLifecycle.VALIDATED,
    )
    expected = validated.envelope.model_copy(update={"content_hash": digest})
    metadata_path = path.with_suffix(".meta.json")
    if (lifecycle.workspace / metadata_path).exists():
        actual = ArtifactEnvelope.model_validate(lifecycle.raw.read_json(metadata_path))
        if actual.validation_status is not ArtifactLifecycle.SUPERSEDED:
            if actual != expected or (lifecycle.workspace / path).read_bytes() != data:
                raise FileExistsError("conflicting evidence matrix exists")
            return
    lifecycle.store.write_csv(
        path, tuple(rows[0]) if rows else _EVIDENCE_FIELDS, rows, expected
    )


def persist_markdown(
    lifecycle: ResearchArtifactLifecycle,
    path: Path,
    payload: dict[str, Any],
    component: str | ProducerIdentity,
    inputs: tuple[ArtifactRef, ...],
) -> None:
    """Publish a Markdown memo after either initial or superseded state."""
    producer = producer_identity(component)
    first = _next_version(lifecycle, path)
    lifecycle._history(
        path, payload, producer, inputs, first, ArtifactLifecycle.PRODUCED
    )
    body = normalize_body(str(payload["body"]))
    digest = hashlib.sha256(body.encode()).hexdigest()
    validated = lifecycle._history(
        path,
        {
            "format": "markdown",
            "authoritative_content_hash": digest,
            "metadata": payload["metadata"],
            "body": body,
        },
        producer,
        inputs,
        first + 1,
        ArtifactLifecycle.VALIDATED,
    )
    expected = validated.envelope.model_copy(
        update={
            "content_hash": digest,
            "provenance": {
                "node": producer.component,
                "artifact_path": path.as_posix(),
                "identification": payload["metadata"],
            },
        }
    )
    if (lifecycle.workspace / path).exists():
        actual, actual_body = lifecycle.store.read_markdown(path)
        if actual.validation_status is not ArtifactLifecycle.SUPERSEDED:
            if actual != expected or actual_body != body:
                raise FileExistsError("conflicting identification memo exists")
            return
    lifecycle.store.write_markdown(path, expected, body)


def supersede_special(
    lifecycle: ResearchArtifactLifecycle,
    path: Path,
    *,
    revision_id: str,
    reason: str,
    actor: str,
) -> None:
    """Append superseded history and update CSV/Markdown authoritative metadata."""
    if path.suffix == ".csv":
        current = ArtifactEnvelope.model_validate(
            lifecycle.raw.read_json(path.with_suffix(".meta.json"))
        )
        body: str | None = None
    else:
        current, body = lifecycle.store.read_markdown(path)
    if current.validation_status is ArtifactLifecycle.SUPERSEDED:
        if current.provenance.get("revision_id") != revision_id:
            raise RuntimeError("artifact belongs to a conflicting revision")
        return
    provenance = dict(current.provenance)
    provenance.update(
        {
            "revision_id": revision_id,
            "revision_reason": reason,
            "revision_actor": actor,
            "supersedes_ref": lifecycle.artifact_ref(path).model_dump(mode="json"),
        }
    )
    previous = lifecycle.read_history(path, current.artifact_version)
    superseded = lifecycle._history(
        path,
        previous.payload,
        producer_identity(actor),
        current.input_artifacts,
        current.artifact_version + 1,
        ArtifactLifecycle.SUPERSEDED,
        provenance=provenance,
    )
    if path.suffix == ".csv":
        expected = superseded.envelope.model_copy(
            update={"content_hash": current.content_hash}
        )
        lifecycle.raw.write_json(
            path.with_suffix(".meta.json"), expected.model_dump(mode="json")
        )
    else:
        assert body is not None
        expected = superseded.envelope.model_copy(
            update={"content_hash": hashlib.sha256(body.encode()).hexdigest()}
        )
        lifecycle.store.write_markdown(path, expected, body)


def _next_version(lifecycle: ResearchArtifactLifecycle, path: Path) -> int:
    if not (lifecycle.workspace / path).exists():
        return 1
    if path.suffix == ".csv":
        metadata_path = path.with_suffix(".meta.json")
        if not (lifecycle.workspace / metadata_path).exists():
            return 1
        current = ArtifactEnvelope.model_validate(
            lifecycle.raw.read_json(metadata_path)
        )
    else:
        current, _ = lifecycle.store.read_markdown(path)
    if current.validation_status is ArtifactLifecycle.SUPERSEDED:
        return current.artifact_version + 1
    return max(1, current.artifact_version - 1)

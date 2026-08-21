"""Canonical schema and identity helpers for node checkpoints."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from envresearch.kernel.artifact_graph import ArtifactGraph, ArtifactNode
from envresearch.kernel.task_identity import payload_hash
from envresearch.models.artifact import ArtifactRef
from envresearch.workers.filesystem import read_regular_at

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
INPUT_KEY = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)@([1-9][0-9]*)$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
STRICT_FROZEN = ConfigDict(
    extra="forbid",
    frozen=True,
    strict=True,
    validate_default=True,
    revalidate_instances="always",
)


def utc_now() -> datetime:
    """Return the production timestamp for a new node checkpoint."""
    return datetime.now(UTC)


class InputSetMismatch(ValueError):
    """Valid references do not match a node's declared input set."""


class NodeCheckpoint(BaseModel):
    """One immutable, independently verifiable Artifact DAG completion."""

    model_config = STRICT_FROZEN

    schema_version: Literal["1.0"] = "1.0"
    node_id: str
    node_version: str
    definition_hash: str
    input_hashes: dict[str, str]
    output_hashes: dict[str, str]
    completed_at: datetime
    checkpoint_hash: str

    @field_validator("node_id", "node_version")
    @classmethod
    def require_safe_identity(cls, value: str) -> str:
        if not SAFE_ID.fullmatch(value):
            raise ValueError("identity must be a canonical safe filename segment")
        return value

    @field_validator("definition_hash", "checkpoint_hash")
    @classmethod
    def require_hash(cls, value: str) -> str:
        if not SHA256.fullmatch(value):
            raise ValueError("hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("input_hashes")
    @classmethod
    def require_input_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        if tuple(value) != tuple(sorted(value)):
            raise ValueError("input hashes must use deterministic key order")
        for key, digest in value.items():
            if not INPUT_KEY.fullmatch(key):
                raise ValueError("input hash key must be artifact-id@positive-version")
            if not SHA256.fullmatch(digest):
                raise ValueError("input hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("output_hashes")
    @classmethod
    def require_output_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        if tuple(value) != tuple(sorted(value)):
            raise ValueError("output hashes must use deterministic key order")
        for relative, digest in value.items():
            require_safe_relative(Path(relative), "output hash path")
            if not SHA256.fullmatch(digest):
                raise ValueError("output hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("completed_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("timestamps must be UTC-aware")
        return value


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def load_json_object(data: bytes) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    value = json.loads(data.decode(), object_pairs_hook=reject_duplicates)
    if not isinstance(value, dict):
        raise TypeError("JSON record must contain one object")
    return value


def read_checkpoint_at(
    parent_fd: int, filename: str, expected_node_id: str
) -> tuple[NodeCheckpoint, bytes]:
    data = read_regular_at(parent_fd, filename, description="node checkpoint")
    load_json_object(data)
    checkpoint = NodeCheckpoint.model_validate_json(data, strict=True)
    if checkpoint.node_id != expected_node_id:
        raise ValueError("checkpoint node ID does not match its path")
    canonical = checkpoint.model_dump(mode="json")
    if data != canonical_bytes(canonical):
        raise ValueError("checkpoint bytes are not canonical")
    without_hash = dict(canonical)
    without_hash.pop("checkpoint_hash")
    if payload_hash(without_hash) != checkpoint.checkpoint_hash:
        raise ValueError("checkpoint hash mismatch")
    return checkpoint, data


def revalidate_node(node: ArtifactNode) -> ArtifactNode:
    if not isinstance(node, ArtifactNode):
        raise TypeError("node must be an ArtifactNode")
    rebuilt = ArtifactNode(
        node_id=node.node_id,
        worker_role=node.worker_role,
        dependencies=node.dependencies,
        input_paths=node.input_paths,
        output_paths=node.output_paths,
        required_gate=node.required_gate,
        version=node.version,
    )
    if rebuilt != node:
        raise ValueError("node instance is not canonical")
    if rebuilt.version is None:
        raise ValueError("checkpointed node version must be present")
    return rebuilt


def revalidate_graph(graph: ArtifactGraph) -> ArtifactGraph:
    if not isinstance(graph, ArtifactGraph):
        raise TypeError("graph must be an ArtifactGraph")
    rebuilt = ArtifactGraph(revalidate_node(node) for node in graph.nodes)
    if rebuilt.nodes != graph.nodes:
        raise ValueError("artifact graph instance is not canonical")
    return rebuilt


def input_hashes(node: ArtifactNode, inputs: Iterable[ArtifactRef]) -> dict[str, str]:
    expected_ids = tuple(path.stem for path in node.input_paths)
    if len(set(expected_ids)) != len(expected_ids):
        raise ValueError("declared input artifact IDs must be unique")
    references: list[ArtifactRef] = []
    seen_ids: set[str] = set()
    for reference in tuple(inputs):
        if not isinstance(reference, ArtifactRef):
            raise TypeError("inputs must contain ArtifactRef instances")
        validated = ArtifactRef.model_validate(dict(reference.__dict__), strict=True)
        require_safe_id(validated.artifact_id, "input artifact ID")
        if validated.artifact_id in seen_ids:
            raise ValueError(f"duplicate input artifact: {validated.artifact_id}")
        seen_ids.add(validated.artifact_id)
        references.append(validated)
    if seen_ids != set(expected_ids):
        raise InputSetMismatch("unknown input or changed declared input set")
    pairs = {
        f"{item.artifact_id}@{item.artifact_version}": item.content_hash
        for item in references
    }
    if len(pairs) != len(references):
        raise ValueError("duplicate input identity")
    return {key: pairs[key] for key in sorted(pairs)}


def declared_outputs(node: ArtifactNode, outputs: Iterable[Path]) -> tuple[Path, ...]:
    supplied = tuple(Path(path) for path in outputs)
    if len(set(supplied)) != len(supplied):
        raise ValueError("duplicate output path")
    for path in supplied:
        require_safe_relative(path, "output path")
    if set(supplied) != set(node.output_paths):
        raise ValueError("outputs must match the declared output set exactly")
    return node.output_paths


def definition_hash(node: ArtifactNode) -> str:
    return payload_hash(
        {
            "node_id": node.node_id,
            "version": node.version,
            "worker_role": node.worker_role,
            "dependencies": list(node.dependencies),
            "input_paths": [path.as_posix() for path in node.input_paths],
            "output_paths": [path.as_posix() for path in node.output_paths],
            "required_gate": node.required_gate,
        }
    )


def make_checkpoint(
    node: ArtifactNode,
    definition: str,
    inputs: dict[str, str],
    outputs: dict[str, str],
    completed_at: datetime,
) -> NodeCheckpoint:
    core: dict[str, object] = {
        "schema_version": "1.0",
        "node_id": node.node_id,
        "node_version": node.version,
        "definition_hash": definition,
        "input_hashes": inputs,
        "output_hashes": outputs,
        "completed_at": completed_at,
    }
    placeholder = NodeCheckpoint.model_validate(
        {**core, "checkpoint_hash": "0" * 64}, strict=True
    )
    digest = payload_hash(
        placeholder.model_dump(mode="json", exclude={"checkpoint_hash"})
    )
    return NodeCheckpoint.model_validate(
        {**core, "checkpoint_hash": digest}, strict=True
    )


def same_publication(
    checkpoint: NodeCheckpoint,
    node: ArtifactNode,
    definition: str,
    inputs: Mapping[str, str],
    outputs: Mapping[str, str],
) -> bool:
    return (
        checkpoint.node_id == node.node_id
        and checkpoint.node_version == node.version
        and checkpoint.definition_hash == definition
        and checkpoint.input_hashes == inputs
        and checkpoint.output_hashes == outputs
    )


def checkpoint_name(node_id: str) -> str:
    require_safe_id(node_id, "node ID")
    return f"{node_id}.json"


def require_safe_id(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError(f"{field_name} must be a canonical safe filename segment")


def require_safe_relative(path: Path, field_name: str) -> None:
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"{field_name} must be a safe relative path")


def require_reason(reason: object) -> None:
    if (
        not isinstance(reason, str)
        or not reason
        or reason != reason.strip()
        or len(reason) > 512
        or any(ord(character) < 32 for character in reason)
    ):
        raise ValueError("invalidation reason must be canonical nonblank text")

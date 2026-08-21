"""Verification, replay, and namespace tests for node checkpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from envresearch.kernel.artifact_graph import ArtifactGraph, ArtifactNode
from envresearch.kernel.events import EventLog, EventRecord
from envresearch.kernel.node_checkpoints import NodeCheckpointStore
from envresearch.kernel.task_identity import payload_hash
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import WorkflowStatus


def ref(artifact_id: str, *, version: int = 1, digest: str = "a" * 64) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_version=version,
        content_hash=digest,
    )


def node(
    node_id: str,
    *,
    inputs: tuple[Path, ...] = (),
    outputs: tuple[Path, ...] = (),
) -> ArtifactNode:
    return ArtifactNode(
        node_id=node_id,
        worker_role="researcher",
        input_paths=inputs,
        output_paths=outputs,
        version="1",
    )


def write_outputs(root: Path, *paths: Path) -> None:
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"output:{path.as_posix()}\n".encode())


def canonical_rehash(path: Path, **changes: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(changes)
    payload["checkpoint_hash"] = payload_hash(
        {key: value for key, value in payload.items() if key != "checkpoint_hash"}
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )


def test_idempotent_publish_rejects_conflicting_pass_event(tmp_path: Path) -> None:
    output = Path("artifacts/result.json")
    declared = node("design", outputs=(output,))
    write_outputs(tmp_path, output)
    store = NodeCheckpointStore.for_workspace(tmp_path)
    store.publish(declared, (), (output,))
    EventLog(tmp_path / "events.jsonl").append(
        EventRecord(
            event_id="conflicting-pass-event",
            run_id="research-workflow",
            event_type="research.node.passed",
            actor="research-orchestrator",
            timestamp=datetime(2026, 8, 6, tzinfo=UTC),
            from_status=WorkflowStatus.RUNNING,
            to_status=WorkflowStatus.RUNNING,
            payload={"node_id": "design", "checkpoint_hash": "f" * 64},
        )
    )
    with pytest.raises(ValueError, match="pass event|checkpoint"):
        store.publish(declared, (), (output,))


def test_stale_pass_event_cannot_strand_a_new_checkpoint(tmp_path: Path) -> None:
    output = Path("artifacts/result.json")
    declared = node("design", outputs=(output,))
    write_outputs(tmp_path, output)
    store = NodeCheckpointStore.for_workspace(tmp_path)
    store.events.append(
        EventRecord(
            event_id="stale-pass-event",
            run_id="research-workflow",
            event_type="research.node.passed",
            actor="research-orchestrator",
            timestamp=datetime(2026, 8, 6, tzinfo=UTC),
            from_status=WorkflowStatus.RUNNING,
            to_status=WorkflowStatus.RUNNING,
            payload={"node_id": "design", "checkpoint_hash": "f" * 64},
        )
    )
    with pytest.raises(ValueError, match="pass event|checkpoint"):
        store.publish(declared, (), (output,))
    assert not (tmp_path / "node-checkpoints/design.json").exists()


@pytest.mark.parametrize(
    "tamper", ("bytes", "definition", "inputs", "outputs", "event", "duplicate-event")
)
def test_verify_fails_closed_for_checkpoint_and_event_tamper(
    tmp_path: Path, tamper: str
) -> None:
    output = Path("artifacts/result.json")
    declared = node("design", inputs=(Path("artifacts/brief.yaml"),), outputs=(output,))
    inputs = (ref("brief"),)
    write_outputs(tmp_path, output)
    store = NodeCheckpointStore.for_workspace(tmp_path)
    store.publish(declared, inputs, (output,))
    checkpoint_path = tmp_path / "node-checkpoints/design.json"
    event_path = tmp_path / "events.jsonl"
    if tamper == "bytes":
        checkpoint_path.write_bytes(checkpoint_path.read_bytes() + b" ")
    elif tamper == "definition":
        canonical_rehash(checkpoint_path, definition_hash="f" * 64)
    elif tamper == "inputs":
        canonical_rehash(checkpoint_path, input_hashes={"brief@1": "e" * 64})
    elif tamper == "outputs":
        canonical_rehash(
            checkpoint_path, output_hashes={"artifacts/result.json": "e" * 64}
        )
    elif tamper == "event":
        event_payload = json.loads(event_path.read_text(encoding="utf-8"))
        event_payload["payload"]["checkpoint_hash"] = "e" * 64
        event_path.write_text(json.dumps(event_payload) + "\n", encoding="utf-8")
    else:
        event_path.write_bytes(event_path.read_bytes() * 2)
    assert not store.verify(declared, inputs)


def test_verify_rejects_changed_input_and_current_output_bytes(tmp_path: Path) -> None:
    output = Path("artifacts/result.json")
    declared = node("design", inputs=(Path("artifacts/brief.yaml"),), outputs=(output,))
    write_outputs(tmp_path, output)
    store = NodeCheckpointStore.for_workspace(tmp_path)
    store.publish(declared, (ref("brief"),), (output,))
    assert not store.verify(declared, (ref("brief", version=2),))
    assert not store.verify(declared, (ref("brief", digest="b" * 64),))
    assert not store.verify(declared, (ref("unknown"),))
    (tmp_path / output).write_text("changed bytes", encoding="utf-8")
    assert not store.verify(declared, (ref("brief"),))


def test_publish_rejects_missing_symlink_and_nonregular_outputs(tmp_path: Path) -> None:
    output = Path("artifacts/result.json")
    declared = node("design", outputs=(output,))
    store = NodeCheckpointStore.for_workspace(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.publish(declared, (), (output,))
    target = tmp_path / "outside.json"
    target.write_text("outside", encoding="utf-8")
    (tmp_path / output).parent.mkdir()
    try:
        (tmp_path / output).symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    with pytest.raises(ValueError, match="regular non-symlink"):
        store.publish(declared, (), (output,))
    (tmp_path / output).unlink()
    (tmp_path / output).mkdir()
    with pytest.raises(ValueError, match="regular non-symlink"):
        store.publish(declared, (), (output,))


def test_checkpoint_namespace_and_active_collision_fail_closed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        (workspace / "node-checkpoints").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    with pytest.raises(ValueError, match="symlink"):
        NodeCheckpointStore.for_workspace(workspace)
    (workspace / "node-checkpoints").unlink()
    store = NodeCheckpointStore.for_workspace(workspace)
    output = Path("artifacts/result.json")
    write_outputs(workspace, output)
    collision_target = workspace / "collision.json"
    collision_target.write_text("do not overwrite", encoding="utf-8")
    (workspace / "node-checkpoints/design.json").symlink_to(collision_target)
    with pytest.raises((ValueError, FileExistsError)):
        store.publish(node("design", outputs=(output,)), (), (output,))
    assert collision_target.read_text(encoding="utf-8") == "do not overwrite"


def test_event_log_symlink_cannot_redirect_pass_event_outside_workspace(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-events.jsonl"
    outside.write_bytes(b"")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        (workspace / "events.jsonl").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    with pytest.raises(ValueError, match="event log.*non-symlink"):
        NodeCheckpointStore.for_workspace(workspace)
    assert outside.read_bytes() == b""


@pytest.mark.parametrize("tamper", ("unexpected-field", "duplicate-field"))
def test_verify_rejects_nonexact_event_json(tmp_path: Path, tamper: str) -> None:
    output = Path("artifacts/result.json")
    declared = node("design", outputs=(output,))
    write_outputs(tmp_path, output)
    store = NodeCheckpointStore.for_workspace(tmp_path)
    store.publish(declared, (), (output,))
    path = tmp_path / "events.jsonl"
    line = path.read_text(encoding="utf-8").rstrip("\n")
    if tamper == "unexpected-field":
        payload = json.loads(line)
        payload["unexpected"] = "tamper"
        line = json.dumps(payload)
    else:
        line = line.replace("{", '{"event_id":"duplicate",', 1)
    path.write_text(line + "\n", encoding="utf-8")
    assert not store.verify(declared, ())


def test_forged_invalidation_cannot_hide_duplicate_pass_history(tmp_path: Path) -> None:
    output = Path("artifacts/result.json")
    declared = node("design", outputs=(output,))
    write_outputs(tmp_path, output)
    store = NodeCheckpointStore.for_workspace(tmp_path)
    store.publish(declared, (), (output,))
    passed = store.events.read_all()[0]
    store.events.append(
        EventRecord(
            event_id="forged-invalidation",
            run_id="research-workflow",
            event_type="research.node.invalidated",
            actor="attacker",
            timestamp=datetime(2026, 8, 6, tzinfo=UTC),
            from_status=WorkflowStatus.RUNNING,
            to_status=WorkflowStatus.RUNNING,
            payload={"targets": ["design"]},
        )
    )
    store.events.append(passed)
    assert not store.verify(declared, ())


def test_completed_nodes_rejects_forged_graph_node(tmp_path: Path) -> None:
    declared = node("design")
    workflow = ArtifactGraph((declared,))
    object.__setattr__(declared, "version", "../forged")
    store = NodeCheckpointStore.for_workspace(tmp_path)
    with pytest.raises(ValueError, match="node version"):
        store.completed_nodes(workflow)


def test_workspace_parent_swap_is_rejected_without_writing_replacement(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = Path("artifacts/result.json")
    write_outputs(workspace, output)
    store = NodeCheckpointStore.for_workspace(workspace)
    original = tmp_path / "original"
    workspace.rename(original)
    workspace.mkdir()
    write_outputs(workspace, output)
    with pytest.raises(ValueError, match="workspace root changed"):
        store.publish(node("design", outputs=(output,)), (), (output,))
    assert not (workspace / "node-checkpoints/design.json").exists()
    assert not (workspace / "events.jsonl").exists()

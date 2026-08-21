"""Integration tests for non-linear research DAG recovery and invalidation."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from envresearch.kernel.artifact_graph import ArtifactGraph, ArtifactNode
from envresearch.kernel.events import EventLog
from envresearch.kernel.node_checkpoints import NodeCheckpointStore
from envresearch.kernel.task_identity import payload_hash


def node(
    node_id: str,
    *,
    dependencies: tuple[str, ...] = (),
    role: str = "researcher",
) -> ArtifactNode:
    """Build a graph node with one unique authoritative output."""
    return ArtifactNode(
        node_id=node_id,
        worker_role=role,
        dependencies=dependencies,
        output_paths=(Path("artifacts") / f"{node_id}.json",),
    )


def graph() -> ArtifactGraph:
    """Build two parallel branches that later join."""
    return ArtifactGraph(
        (
            node("approve-charter"),
            node("map-literature", dependencies=("approve-charter",)),
            node("inspect-data", dependencies=("approve-charter",)),
            node(
                "define-estimand",
                dependencies=("map-literature", "inspect-data"),
            ),
            node("rank-methods", dependencies=("define-estimand",)),
            node("draft-identification", dependencies=("define-estimand",)),
            node(
                "review-design",
                dependencies=("rank-methods", "draft-identification"),
            ),
            node("compose-plan", dependencies=("review-design",)),
        )
    )


def write_output(root: Path, declared: ArtifactNode) -> None:
    """Create the node's authoritative output bytes."""
    path = root / declared.output_paths[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{declared.node_id}\n", encoding="utf-8")


def publish(store: NodeCheckpointStore, declared: ArtifactNode) -> None:
    """Publish one no-input fixture checkpoint."""
    write_output(store.workspace, declared)
    store.publish(declared, (), declared.output_paths)


def test_parallel_branch_checkpoints_verify_without_linear_prefix(
    tmp_path: Path,
) -> None:
    """Independent roots recover even when no earlier linear prefix exists."""
    branches = ArtifactGraph((node("map-literature"), node("inspect-data")))
    store = NodeCheckpointStore.for_workspace(tmp_path)
    for declared in branches.nodes:
        publish(store, declared)

    assert store.completed_nodes(branches) == frozenset(
        {"map-literature", "inspect-data"}
    )


def test_invalid_dependency_prevents_descendant_completion_but_not_other_branch(
    tmp_path: Path,
) -> None:
    """A child is recoverable only while every ancestor checkpoint is valid."""
    workflow = ArtifactGraph(
        (
            node("root"),
            node("child", dependencies=("root",)),
            node("independent"),
        )
    )
    store = NodeCheckpointStore.for_workspace(tmp_path)
    for declared in workflow.nodes:
        publish(store, declared)
    (tmp_path / "artifacts/root.json").write_text("tampered", encoding="utf-8")

    assert store.completed_nodes(workflow) == frozenset({"independent"})


def test_changed_node_invalidates_only_completed_descendants(tmp_path: Path) -> None:
    """Design changes preserve valid work on unrelated parallel branches."""
    workflow = graph()
    store = NodeCheckpointStore.for_workspace(tmp_path)
    for declared in workflow.nodes:
        publish(store, declared)

    invalidated = store.invalidate(workflow, "define-estimand", reason="estimand changed")

    assert invalidated == frozenset(
        {
            "define-estimand",
            "rank-methods",
            "draft-identification",
            "review-design",
            "compose-plan",
        }
    )
    assert store.completed_nodes(workflow) == frozenset(
        {"approve-charter", "map-literature", "inspect-data"}
    )
    invalidation_events = [
        event
        for event in EventLog(tmp_path / "events.jsonl").read_all()
        if event.event_type == "research.node.invalidated"
    ]
    assert len(invalidation_events) == 1
    event = invalidation_events[0]
    assert event.payload["node_id"] == "define-estimand"
    assert event.payload["reason"] == "estimand changed"
    assert event.payload["targets"] == sorted(invalidated)
    assert set(event.payload["source_checkpoint_hashes"]) == set(invalidated)  # type: ignore[arg-type]
    archived = tmp_path / "node-checkpoints/superseded" / event.event_id
    assert {path.name for path in archived.iterdir()} == {
        f"{node_id}.json" for node_id in invalidated
    }

    assert store.invalidate(
        workflow, "define-estimand", reason="estimand changed"
    ) == invalidated
    assert len(
        [
            item
            for item in EventLog(tmp_path / "events.jsonl").read_all()
            if item.event_type == "research.node.invalidated"
        ]
    ) == 1


def test_definition_drift_still_supersedes_changed_node_and_descendants(
    tmp_path: Path,
) -> None:
    """The invalidation operation can retire a checkpoint whose definition changed."""
    original = ArtifactGraph(
        (node("root"), node("child", dependencies=("root",)), node("other"))
    )
    store = NodeCheckpointStore.for_workspace(tmp_path)
    for declared in original.nodes:
        publish(store, declared)
    changed = ArtifactGraph(
        (
            node("root", role="changed-role"),
            node("child", dependencies=("root",)),
            node("other"),
        )
    )

    assert store.completed_nodes(changed) == frozenset({"other"})
    assert store.invalidate(changed, "root", reason="definition changed") == frozenset(
        {"root", "child"}
    )
    assert store.completed_nodes(changed) == frozenset({"other"})


def test_changed_output_can_invalidate_prior_completion_and_descendants(
    tmp_path: Path,
) -> None:
    """A changed source output triggers recovery instead of stranding its checkpoint."""
    workflow = ArtifactGraph(
        (node("root"), node("child", dependencies=("root",)), node("other"))
    )
    store = NodeCheckpointStore.for_workspace(tmp_path)
    for declared in workflow.nodes:
        publish(store, declared)
    (tmp_path / "artifacts/root.json").write_text("changed", encoding="utf-8")

    assert store.completed_nodes(workflow) == frozenset({"other"})
    assert store.invalidate(workflow, "root", reason="output changed") == frozenset(
        {"root", "child"}
    )
    assert store.completed_nodes(workflow) == frozenset({"other"})


def test_invalidated_branch_can_be_recomputed_without_replaying_unaffected_work(
    tmp_path: Path,
) -> None:
    """A new pass after supersession becomes the sole active checkpoint generation."""
    workflow = ArtifactGraph(
        (node("root"), node("child", dependencies=("root",)), node("other"))
    )
    store = NodeCheckpointStore.for_workspace(tmp_path)
    for declared in workflow.nodes:
        publish(store, declared)
    store.invalidate(workflow, "root", reason="root changed")
    (tmp_path / "artifacts/root.json").write_text("root recomputed", encoding="utf-8")
    (tmp_path / "artifacts/child.json").write_text("child recomputed", encoding="utf-8")

    store.publish(workflow.nodes[0], (), workflow.nodes[0].output_paths)
    store.publish(workflow.nodes[1], (), workflow.nodes[1].output_paths)

    assert store.completed_nodes(workflow) == frozenset({"root", "child", "other"})
    assert store.verify(workflow.nodes[0], ())
    assert store.verify(workflow.nodes[1], ())
    assert len(
        [
            event
            for event in EventLog(tmp_path / "events.jsonl").read_all()
            if event.event_type == "research.node.passed"
            and event.payload.get("node_id") == "other"
        ]
    ) == 1


def test_partial_invalidation_retry_moves_remaining_files_and_appends_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash between no-replace moves resumes the same invalidation identity."""
    from envresearch.kernel import node_checkpoints

    workflow = ArtifactGraph(
        (
            node("root"),
            node("child-a", dependencies=("root",)),
            node("child-b", dependencies=("root",)),
        )
    )
    store = NodeCheckpointStore.for_workspace(tmp_path)
    for declared in workflow.nodes:
        publish(store, declared)
    real_move = node_checkpoints._move_noreplace
    calls = 0

    def crash_after_first(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        real_move(*args, **kwargs)  # type: ignore[arg-type]
        if calls == 1:
            raise OSError("invalidation interrupted")

    monkeypatch.setattr(node_checkpoints, "_move_noreplace", crash_after_first)
    with pytest.raises(OSError, match="interrupted"):
        store.invalidate(workflow, "root", reason="root changed")
    monkeypatch.setattr(node_checkpoints, "_move_noreplace", real_move)

    invalidated = store.invalidate(workflow, "root", reason="root changed")

    assert invalidated == frozenset({"root", "child-a", "child-b"})
    events = [
        event
        for event in EventLog(tmp_path / "events.jsonl").read_all()
        if event.event_type == "research.node.invalidated"
    ]
    assert len(events) == 1
    archived = tmp_path / "node-checkpoints/superseded" / events[0].event_id
    assert {path.name for path in archived.iterdir()} == {
        "root.json",
        "child-a.json",
        "child-b.json",
    }


def test_invalidation_retry_recovers_empty_archive_before_first_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A durable intent makes a crash before the first rename safely resumable."""
    workflow = ArtifactGraph((node("root"), node("child", dependencies=("root",))))
    store = NodeCheckpointStore.for_workspace(tmp_path)
    for declared in workflow.nodes:
        publish(store, declared)
    real_move_targets = store._move_targets
    crashed = False

    def crash_before_moves(*args: object, **kwargs: object) -> None:
        nonlocal crashed
        if not crashed:
            crashed = True
            raise OSError("crash before first move")
        real_move_targets(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "_move_targets", crash_before_moves)
    with pytest.raises(OSError, match="before first move"):
        store.invalidate(workflow, "root", reason="root changed")
    monkeypatch.setattr(store, "_move_targets", real_move_targets)

    assert store.invalidate(workflow, "root", reason="root changed") == frozenset(
        {"root", "child"}
    )
    events = [
        event
        for event in EventLog(tmp_path / "events.jsonl").read_all()
        if event.event_type == "research.node.invalidated"
    ]
    assert len(events) == 1


def test_invalidation_retry_recovers_interrupted_intent_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash writing the intent cannot poison its final archive identity."""
    from envresearch.kernel import node_checkpoints
    from envresearch.workers.tempfiles import temporary_name_for

    workflow = ArtifactGraph((node("root"), node("child", dependencies=("root",))))
    store = NodeCheckpointStore.for_workspace(tmp_path)
    for declared in workflow.nodes:
        publish(store, declared)
    real_write = node_checkpoints.write_file_noreplace_at
    crashed = False

    def crash_on_intent(
        parent_fd: int, name: str, data: bytes, *, mode: int
    ) -> None:
        nonlocal crashed
        if name == ".invalidation-intent.json" and not crashed:
            crashed = True
            temporary = temporary_name_for(name)
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                os.write(descriptor, data[: max(1, len(data) // 2)])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            raise OSError("intent publication interrupted")
        real_write(parent_fd, name, data, mode=mode)

    monkeypatch.setattr(node_checkpoints, "write_file_noreplace_at", crash_on_intent)
    with pytest.raises(OSError, match="intent publication interrupted"):
        store.invalidate(workflow, "root", reason="root changed")
    monkeypatch.setattr(node_checkpoints, "write_file_noreplace_at", real_write)

    assert store.invalidate(workflow, "root", reason="root changed") == frozenset(
        {"root", "child"}
    )


def test_superseded_collision_is_rejected_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preexisting archive target cannot be replaced during invalidation."""
    from envresearch.kernel import node_checkpoints

    workflow = ArtifactGraph((node("root"),))
    store = NodeCheckpointStore.for_workspace(tmp_path)
    publish(store, workflow.nodes[0])
    real_move = node_checkpoints._move_noreplace

    def collide(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
        os_data = b"attacker collision"
        node_checkpoints.write_file_noreplace_at(
            destination_fd, destination, os_data, mode=0o600
        )
        real_move(source_fd, source, destination_fd, destination)

    monkeypatch.setattr(node_checkpoints, "_move_noreplace", collide)

    with pytest.raises(FileExistsError):
        store.invalidate(workflow, "root", reason="root changed")
    assert (tmp_path / "node-checkpoints/root.json").is_file()
    archives = list((tmp_path / "node-checkpoints/superseded").glob("*/root.json"))
    assert len(archives) == 1
    assert archives[0].read_bytes() == b"attacker collision"
    assert not any(
        event.event_type == "research.node.invalidated"
        for event in EventLog(tmp_path / "events.jsonl").read_all()
    )


def test_preexisting_superseded_event_directory_is_a_collision(tmp_path: Path) -> None:
    """A guessed empty event directory cannot be adopted as a new transaction."""
    workflow = ArtifactGraph((node("root"),))
    store = NodeCheckpointStore.for_workspace(tmp_path)
    publish(store, workflow.nodes[0])
    checkpoint = json.loads(
        (tmp_path / "node-checkpoints/root.json").read_text(encoding="utf-8")
    )
    core = {
        "node_id": "root",
        "reason": "root changed",
        "source_checkpoint_hashes": {"root": checkpoint["checkpoint_hash"]},
        "targets": ["root"],
    }
    event_id = f"research.node.invalidated.{payload_hash(core)}"
    collision = tmp_path / "node-checkpoints/superseded" / event_id
    collision.parent.mkdir(mode=0o700)
    collision.mkdir(mode=0o700)

    with pytest.raises(FileExistsError):
        store.invalidate(workflow, "root", reason="root changed")

    assert (tmp_path / "node-checkpoints/root.json").is_file()
    assert list(collision.iterdir()) == []


def test_invalidated_checkpoint_bytes_are_preserved_exactly(tmp_path: Path) -> None:
    """Supersession is a byte-preserving rename, not a rewrite."""
    workflow = ArtifactGraph((node("root"),))
    store = NodeCheckpointStore.for_workspace(tmp_path)
    publish(store, workflow.nodes[0])
    active = tmp_path / "node-checkpoints/root.json"
    before = active.read_bytes()

    store.invalidate(workflow, "root", reason="root changed")
    event = EventLog(tmp_path / "events.jsonl").read_all()[-1]
    archived = tmp_path / "node-checkpoints/superseded" / event.event_id / "root.json"

    assert archived.read_bytes() == before
    assert not active.exists()
    payload = json.loads(before)
    assert event.payload["source_checkpoint_hashes"] == {
        "root": payload["checkpoint_hash"]
    }

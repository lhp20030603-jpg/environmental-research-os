"""Controller recovery regressions for globally exclusive invalidations."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest

from envresearch.kernel.artifact_graph import ArtifactGraph, ArtifactNode
from envresearch.kernel.events import EventLog, EventRecord
from envresearch.kernel.node_checkpoints import NodeCheckpointStore


def _node(node_id: str, dependencies: tuple[str, ...] = ()) -> ArtifactNode:
    return ArtifactNode(
        node_id=node_id,
        worker_role="researcher",
        dependencies=dependencies,
        output_paths=(Path("artifacts") / f"{node_id}.json",),
    )


def _publish(store: NodeCheckpointStore, declared: ArtifactNode) -> None:
    output = store.workspace / declared.output_paths[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"{declared.node_id}\n", encoding="utf-8")
    store.publish(declared, (), declared.output_paths)


@pytest.mark.parametrize("cut", (1, 41, -1))
def test_invalidation_retry_repairs_torn_event_after_all_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cut: int
) -> None:
    """A moved archive remains retryable after a torn invalidation event append."""
    workflow = ArtifactGraph((_node("root"), _node("child", ("root",))))
    store = NodeCheckpointStore.for_workspace(tmp_path)
    for declared in workflow.nodes:
        _publish(store, declared)
    real_append = store.events.append
    crashed = False

    def tear_invalidation(event: EventRecord) -> None:
        nonlocal crashed
        if event.event_type != "research.node.invalidated" or crashed:
            real_append(event)
            return
        crashed = True
        record = (event.model_dump_json() + "\n").encode()
        with (tmp_path / "events.jsonl").open("ab", buffering=0) as stream:
            stream.write(record[:cut])
            os.fsync(stream.fileno())
        raise OSError("invalidation append torn")

    monkeypatch.setattr(store.events, "append", tear_invalidation)
    with pytest.raises(OSError, match="torn"):
        store.invalidate(workflow, "root", reason="root changed")
    assert not (tmp_path / "node-checkpoints/root.json").exists()
    assert not (tmp_path / "node-checkpoints/child.json").exists()
    monkeypatch.setattr(store.events, "append", real_append)
    store.close()
    store = NodeCheckpointStore.for_workspace(tmp_path)

    assert store.invalidate(workflow, "root", reason="root changed") == frozenset(
        {"root", "child"}
    )
    events = EventLog(tmp_path / "events.jsonl").read_all()
    assert [event.event_type for event in events].count(
        "research.node.invalidated"
    ) == 1


@pytest.mark.parametrize(
    "requested",
    ("child", "unrelated"),
)
def test_other_source_cannot_start_while_invalidation_is_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requested: str
) -> None:
    """An interrupted transaction is globally exclusive across source nodes."""
    from envresearch.kernel import node_checkpoints

    workflow = ArtifactGraph(
        (
            _node("root"),
            _node("child", ("root",)),
            _node("grandchild", ("child",)),
            _node("unrelated"),
        )
    )
    store = NodeCheckpointStore.for_workspace(tmp_path)
    for declared in workflow.nodes:
        _publish(store, declared)
    real_move = node_checkpoints._move_noreplace
    calls = 0

    def crash_after_first(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        real_move(*args, **kwargs)  # type: ignore[arg-type]
        if calls == 1:
            raise OSError("move interrupted")

    monkeypatch.setattr(node_checkpoints, "_move_noreplace", crash_after_first)
    with pytest.raises(OSError, match="interrupted"):
        store.invalidate(workflow, "root", reason="root changed")
    monkeypatch.setattr(node_checkpoints, "_move_noreplace", real_move)
    store.close()
    store = NodeCheckpointStore.for_workspace(tmp_path)

    with pytest.raises(ValueError, match="pending|recover.*root|invalidation.*root"):
        store.invalidate(workflow, requested, reason="second change")

    assert (tmp_path / f"node-checkpoints/{requested}.json").is_file()
    assert store.invalidate(workflow, "root", reason="root changed") == frozenset(
        {"root", "child", "grandchild"}
    )


@pytest.mark.parametrize("conflict", ("actor", "payload", "timestamp", "content"))
def test_invalidation_retry_rejects_same_id_event_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict: str
) -> None:
    """A same-ID event inserted between preflights must equal the expected event."""
    workflow = ArtifactGraph((_node("root"),))
    store = NodeCheckpointStore.for_workspace(tmp_path)
    _publish(store, workflow.nodes[0])
    real_append = store.events.append

    def stop_before_event(event: EventRecord) -> None:
        if event.event_type == "research.node.invalidated":
            raise OSError("invalidation event interrupted")
        real_append(event)

    monkeypatch.setattr(store.events, "append", stop_before_event)
    with pytest.raises(OSError, match="interrupted"):
        store.invalidate(workflow, "root", reason="root changed")
    monkeypatch.setattr(store.events, "append", real_append)
    store.close()
    store = NodeCheckpointStore.for_workspace(tmp_path)
    real_read = store.events.read_for_expected

    def inject_conflict(expected: EventRecord) -> list[EventRecord]:
        changes: dict[str, object]
        if conflict == "actor":
            changes = {"actor": "attacker"}
        elif conflict == "payload":
            changes = {"payload": {**expected.payload, "reason": "forged"}}
        elif conflict == "timestamp":
            changes = {"timestamp": expected.timestamp + timedelta(seconds=1)}
        else:
            changes = {"run_id": "conflicting-run"}
        forged = expected.model_copy(update=changes)
        EventLog(tmp_path / "events.jsonl").append(forged)
        return real_read(expected)

    monkeypatch.setattr(store.events, "read_for_expected", inject_conflict)

    with pytest.raises(ValueError, match="event|invalidation|history|metadata"):
        store.invalidate(workflow, "root", reason="root changed")

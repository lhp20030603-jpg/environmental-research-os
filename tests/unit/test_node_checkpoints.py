"""Tests for independently recoverable research DAG node checkpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.kernel.artifact_graph import ArtifactNode
from envresearch.kernel.events import EventLog
from envresearch.kernel.node_checkpoints import NodeCheckpoint, NodeCheckpointStore
from envresearch.models.artifact import ArtifactRef


def ref(
    artifact_id: str,
    *,
    version: int = 1,
    digest: str = "a" * 64,
) -> ArtifactRef:
    """Build one complete immutable artifact identity."""
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_version=version,
        content_hash=digest,
    )


def node(
    node_id: str,
    *,
    role: str | None = "researcher",
    dependencies: tuple[str, ...] = (),
    inputs: tuple[Path, ...] = (),
    outputs: tuple[Path, ...] = (),
    gate: str | None = None,
    version: str | None = "1",
) -> ArtifactNode:
    """Build one declared graph node."""
    return ArtifactNode(
        node_id=node_id,
        worker_role=role,
        dependencies=dependencies,
        input_paths=inputs,
        output_paths=outputs,
        required_gate=gate,
        version=version,
    )


def write_outputs(root: Path, *paths: Path) -> None:
    """Create deterministic regular output files for a checkpoint."""
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"output:{path.as_posix()}\n".encode())


def canonical_rehash(path: Path, **changes: object) -> None:
    """Coherently change a checkpoint document, including its self-hash."""
    from envresearch.kernel.task_identity import payload_hash

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(changes)
    payload["checkpoint_hash"] = payload_hash(
        {key: value for key, value in payload.items() if key != "checkpoint_hash"}
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )


def test_checkpoint_model_is_strict_frozen_and_canonical() -> None:
    """The durable schema rejects coercion, extras, unsafe IDs, and non-UTC time."""
    valid = {
        "schema_version": "1.0",
        "node_id": "map-literature",
        "node_version": "1",
        "definition_hash": "a" * 64,
        "input_hashes": {"research-charter@1": "b" * 64},
        "output_hashes": {"artifacts/literature-map.json": "c" * 64},
        "completed_at": datetime(2026, 8, 6, tzinfo=UTC),
        "checkpoint_hash": "d" * 64,
    }
    checkpoint = NodeCheckpoint.model_validate(valid, strict=True)

    with pytest.raises(ValidationError, match="frozen"):
        checkpoint.node_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        NodeCheckpoint.model_validate({**valid, "extra": True}, strict=True)
    with pytest.raises(ValidationError, match="safe filename"):
        NodeCheckpoint.model_validate({**valid, "node_id": "../escape"}, strict=True)
    with pytest.raises(ValidationError, match="UTC"):
        NodeCheckpoint.model_validate(
            {
                **valid,
                "completed_at": datetime(
                    2026, 8, 6, tzinfo=timezone(timedelta(hours=8))
                ),
            },
            strict=True,
        )
    with pytest.raises(ValidationError):
        NodeCheckpoint.model_validate({**valid, "node_version": 1}, strict=True)


def test_publish_anchors_complete_inputs_exact_outputs_and_definition(
    tmp_path: Path,
) -> None:
    """A checkpoint binds every behavior field, ArtifactRef field, and output byte."""
    output = Path("artifacts/literature-map.json")
    declared = node(
        "map-literature",
        role="literature-cartographer",
        dependencies=("approve-charter",),
        inputs=(Path("artifacts/research-charter.yaml"),),
        outputs=(output,),
        gate="gate-1",
        version="2",
    )
    write_outputs(tmp_path, output)
    store = NodeCheckpointStore.for_workspace(tmp_path)

    checkpoint = store.publish(
        declared,
        inputs=(ref("research-charter", version=3, digest="1" * 64),),
        outputs=(output,),
    )

    assert checkpoint.node_id == "map-literature"
    assert checkpoint.node_version == "2"
    assert checkpoint.input_hashes == {"research-charter@3": "1" * 64}
    assert checkpoint.output_hashes == {
        "artifacts/literature-map.json": __import__("hashlib")
        .sha256((tmp_path / output).read_bytes())
        .hexdigest()
    }
    assert store.verify(
        declared,
        (ref("research-charter", version=3, digest="1" * 64),),
    )

    checkpoint_path = tmp_path / "node-checkpoints/map-literature.json"
    assert (
        checkpoint_path.read_bytes()
        == json.dumps(
            checkpoint.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    events = EventLog(tmp_path / "events.jsonl").read_all()
    assert [event.event_type for event in events] == ["research.node.passed"]
    assert events[0].payload == {
        "checkpoint_hash": checkpoint.checkpoint_hash,
        "definition_hash": checkpoint.definition_hash,
        "input_hashes": dict(checkpoint.input_hashes),
        "node_id": checkpoint.node_id,
        "node_version": checkpoint.node_version,
        "output_hashes": dict(checkpoint.output_hashes),
    }


def test_fixed_clock_controls_exact_checkpoint_bytes_across_physical_roots(
    tmp_path: Path,
) -> None:
    output = Path("artifacts/result.json")
    declared = node("design", outputs=(output,))
    fixed = datetime(2026, 8, 21, 1, 2, 3, tzinfo=UTC)
    later = fixed + timedelta(seconds=1)
    observed: list[tuple[NodeCheckpoint, bytes]] = []
    for name, instant in (("first", fixed), ("second", fixed), ("later", later)):
        root = tmp_path / name
        write_outputs(root, output)
        store = NodeCheckpointStore.for_workspace(
            root, clock=lambda instant=instant: instant
        )
        checkpoint = store.publish(declared, inputs=(), outputs=(output,))
        observed.append(
            (checkpoint, (root / "node-checkpoints/design.json").read_bytes())
        )
        store.close()

    assert observed[0] == observed[1]
    assert observed[2][0].checkpoint_hash != observed[0][0].checkpoint_hash
    assert observed[2][1] != observed[0][1]


@pytest.mark.parametrize(
    "mutation",
    ("node-id", "version", "role", "dependencies", "inputs", "outputs", "gate"),
)
def test_definition_hash_covers_every_behavior_field(
    tmp_path: Path, mutation: str
) -> None:
    """Changing any declared ArtifactNode behavior invalidates its checkpoint."""
    output = Path("artifacts/result.json")
    original = node(
        "design",
        role="researcher",
        dependencies=("root",),
        inputs=(Path("artifacts/input.yaml"),),
        outputs=(output,),
        gate="gate-1",
        version="1",
    )
    write_outputs(tmp_path, output)
    store = NodeCheckpointStore.for_workspace(tmp_path)
    inputs = (ref("input"),)
    store.publish(original, inputs, (output,))
    values: dict[str, object] = {
        "node_id": original.node_id,
        "role": original.worker_role,
        "dependencies": original.dependencies,
        "inputs": original.input_paths,
        "outputs": original.output_paths,
        "gate": original.required_gate,
        "version": original.version,
    }
    if mutation == "node-id":
        values["node_id"] = "design-v2"
    elif mutation == "version":
        values["version"] = "2"
    elif mutation == "role":
        values["role"] = "critic"
    elif mutation == "dependencies":
        values["dependencies"] = ("other-root",)
    elif mutation == "inputs":
        values["inputs"] = (Path("artifacts/other-input.yaml"),)
    elif mutation == "outputs":
        values["outputs"] = (Path("artifacts/other-result.json"),)
    else:
        values["gate"] = "gate-2"
    changed = node(
        values["node_id"],  # type: ignore[arg-type]
        role=values["role"],  # type: ignore[arg-type]
        dependencies=values["dependencies"],  # type: ignore[arg-type]
        inputs=values["inputs"],  # type: ignore[arg-type]
        outputs=values["outputs"],  # type: ignore[arg-type]
        gate=values["gate"],  # type: ignore[arg-type]
        version=values["version"],  # type: ignore[arg-type]
    )

    changed_inputs = (ref("other-input"),) if mutation == "inputs" else inputs
    assert not store.verify(changed, changed_inputs)


def test_publish_rejects_duplicate_changed_unknown_inputs_and_output_sets(
    tmp_path: Path,
) -> None:
    """Caller anchors must correspond one-for-one with node declarations."""
    outputs = (Path("artifacts/result.json"), Path("artifacts/summary.md"))
    declared = node(
        "design",
        inputs=(Path("artifacts/brief.yaml"),),
        outputs=outputs,
    )
    write_outputs(tmp_path, *outputs, Path("artifacts/extra.json"))
    store = NodeCheckpointStore.for_workspace(tmp_path)

    with pytest.raises(ValueError, match="duplicate input"):
        store.publish(declared, (ref("brief"), ref("brief")), outputs)
    with pytest.raises(ValueError, match="unknown input"):
        store.publish(declared, (ref("other"),), outputs)
    with pytest.raises(ValueError, match="declared output set"):
        store.publish(declared, (ref("brief"),), outputs[:1])
    with pytest.raises(ValueError, match="duplicate output"):
        store.publish(declared, (ref("brief"),), (outputs[0], outputs[0]))
    with pytest.raises(ValueError, match="declared output set"):
        store.publish(
            declared,
            (ref("brief"),),
            (*outputs, Path("artifacts/extra.json")),
        )


def test_publish_revalidates_forged_node_and_artifact_refs(tmp_path: Path) -> None:
    """model_copy and object-level validator bypasses fail at publication."""
    output = Path("artifacts/result.json")
    write_outputs(tmp_path, output)
    store = NodeCheckpointStore.for_workspace(tmp_path)
    declared = node(
        "design",
        inputs=(Path("artifacts/brief.yaml"),),
        outputs=(output,),
    )
    forged_ref = ref("brief").model_copy(update={"content_hash": "BAD"})
    object.__setattr__(declared, "version", "../bad")

    with pytest.raises((ValueError, ValidationError)):
        store.publish(declared, (ref("brief"),), (output,))

    valid_node = node(
        "design",
        inputs=(Path("artifacts/brief.yaml"),),
        outputs=(output,),
    )
    with pytest.raises(ValidationError):
        store.publish(valid_node, (forged_ref,), (output,))


def test_same_content_retry_is_idempotent_but_conflict_requires_invalidation(
    tmp_path: Path,
) -> None:
    """No active checkpoint is overwritten by a changed publication."""
    output = Path("artifacts/result.json")
    declared = node("design", outputs=(output,))
    write_outputs(tmp_path, output)
    store = NodeCheckpointStore.for_workspace(tmp_path)

    first = store.publish(declared, (), (output,))
    second = store.publish(declared, (), (output,))
    assert second == first
    assert len(EventLog(tmp_path / "events.jsonl").read_all()) == 1

    (tmp_path / output).write_text("changed", encoding="utf-8")
    with pytest.raises(FileExistsError, match="invalidate"):
        store.publish(declared, (), (output,))


def test_retry_after_pass_event_crash_completes_existing_checkpoint_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A durable checkpoint without its pass event is safely completable."""
    output = Path("artifacts/result.json")
    declared = node("design", outputs=(output,))
    write_outputs(tmp_path, output)
    store = NodeCheckpointStore.for_workspace(tmp_path)
    real_append = store.events.append
    failed = False

    def fail_once(event: object) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("event append interrupted")
        real_append(event)  # type: ignore[arg-type]

    monkeypatch.setattr(store.events, "append", fail_once)
    with pytest.raises(OSError, match="interrupted"):
        store.publish(declared, (), (output,))
    assert (tmp_path / "node-checkpoints/design.json").is_file()
    assert not store.verify(declared, ())

    recovered = store.publish(declared, (), (output,))

    assert store.verify(declared, ())
    assert len(EventLog(tmp_path / "events.jsonl").read_all()) == 1
    persisted_time = json.loads(
        (tmp_path / "node-checkpoints/design.json").read_text(encoding="utf-8")
    )["completed_at"]
    assert recovered.completed_at == datetime.fromisoformat(persisted_time)

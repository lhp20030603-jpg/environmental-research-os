"""Controller regression tests for node checkpoint durability boundaries."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest

from envresearch.kernel.artifact_graph import ArtifactNode
from envresearch.kernel.events import EventLog, EventRecord
from envresearch.kernel.node_checkpoints import NodeCheckpointStore
from envresearch.kernel.task_identity import payload_hash
from envresearch.models.enums import WorkflowStatus
from envresearch.storage.artifacts import ArtifactStore
from envresearch.workers.tempfiles import target_name_digest


def _node(node_id: str) -> ArtifactNode:
    return ArtifactNode(
        node_id=node_id,
        worker_role="researcher",
        output_paths=(Path("artifacts") / f"{node_id}.json",),
    )


def _publish(store: NodeCheckpointStore, declared: ArtifactNode) -> None:
    output = store.workspace / declared.output_paths[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"{declared.node_id}\n", encoding="utf-8")
    store.publish(declared, (), declared.output_paths)


@pytest.mark.parametrize("cut", (1, 31, -1))
def test_pass_event_retry_repairs_only_its_exact_torn_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cut: int
) -> None:
    """A retry repairs a deterministic pass record torn at several offsets."""
    declared = _node("design")
    store = NodeCheckpointStore.for_workspace(tmp_path)
    output = tmp_path / declared.output_paths[0]
    output.parent.mkdir(parents=True)
    output.write_text("design\n", encoding="utf-8")
    real_append = store.events.append
    crashed = False

    def tear_once(event: EventRecord) -> None:
        nonlocal crashed
        if crashed:
            real_append(event)
            return
        crashed = True
        record = (event.model_dump_json() + "\n").encode()
        prefix = record[:cut]
        descriptor = os.open(tmp_path / "events.jsonl", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, prefix)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        raise OSError("pass append torn")

    monkeypatch.setattr(store.events, "append", tear_once)
    with pytest.raises(OSError, match="torn"):
        store.publish(declared, (), declared.output_paths)
    monkeypatch.setattr(store.events, "append", real_append)
    store.close()
    store = NodeCheckpointStore.for_workspace(tmp_path)

    store.publish(declared, (), declared.output_paths)

    assert store.verify(declared, ())
    assert len(EventLog(tmp_path / "events.jsonl").read_all()) == 1


def test_pass_retry_rejects_unrelated_torn_suffix(tmp_path: Path) -> None:
    """Recovery never truncates an unterminated suffix from another record."""
    declared = _node("design")
    store = NodeCheckpointStore.for_workspace(tmp_path)
    output = tmp_path / declared.output_paths[0]
    output.parent.mkdir(parents=True)
    output.write_text("design\n", encoding="utf-8")
    checkpoint = store.publish(declared, (), declared.output_paths)
    path = tmp_path / "events.jsonl"
    path.write_bytes(path.read_bytes() + b'{"event_id":"unrelated')

    with pytest.raises(ValueError, match="event|suffix|corruption"):
        store.publish(declared, (), declared.output_paths)

    assert checkpoint.checkpoint_hash in path.read_text(encoding="utf-8")


def test_publish_preflight_failure_preserves_torn_event_and_namespace_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Namespace poison is rejected before exact torn-suffix repair mutates state."""
    declared = _node("design")
    store = NodeCheckpointStore.for_workspace(tmp_path)
    output = tmp_path / declared.output_paths[0]
    output.parent.mkdir(parents=True)
    output.write_text("design\n", encoding="utf-8")
    real_append = store.events.append

    def tear_pass(event: EventRecord) -> None:
        record = (event.model_dump_json() + "\n").encode()
        (tmp_path / "events.jsonl").write_bytes(record[:31])
        raise OSError("pass append torn")

    monkeypatch.setattr(store.events, "append", tear_pass)
    with pytest.raises(OSError, match="torn"):
        store.publish(declared, (), declared.output_paths)
    monkeypatch.setattr(store.events, "append", real_append)
    temporary = tmp_path / "node-checkpoints/.tmp-poison"
    temporary.write_bytes(b"protected temporary bytes")
    protected = (
        tmp_path / "events.jsonl",
        tmp_path / "node-checkpoints/design.json",
        temporary,
    )
    before = {path: path.read_bytes() for path in protected}

    with pytest.raises(ValueError, match="temporary|namespace"):
        store.publish(declared, (), declared.output_paths)

    assert {path: path.read_bytes() for path in protected} == before


def test_coherent_archive_copy_forgery_is_rejected_as_generation_corruption(
    tmp_path: Path,
) -> None:
    """An archive copy cannot retire a generation that remains active."""
    declared = _node("design")
    store = NodeCheckpointStore.for_workspace(tmp_path)
    _publish(store, declared)
    active = tmp_path / "node-checkpoints/design.json"
    checkpoint = json.loads(active.read_text(encoding="utf-8"))
    core = {
        "node_id": "design",
        "reason": "forged copy",
        "source_checkpoint_hashes": {
            "design": checkpoint["checkpoint_hash"],
        },
        "targets": ["design"],
    }
    event_id = f"research.node.invalidated.{payload_hash(core)}"
    superseded = tmp_path / "node-checkpoints/superseded"
    superseded.mkdir(mode=0o700)
    archive = superseded / event_id
    archive.mkdir(mode=0o700)
    shutil.copyfile(active, archive / "design.json")
    (archive / "design.json").chmod(0o600)
    store.events.append(
        EventRecord(
            event_id=event_id,
            run_id="research-workflow",
            event_type="research.node.invalidated",
            actor="research-orchestrator",
            timestamp=datetime.fromisoformat(checkpoint["completed_at"]),
            from_status=WorkflowStatus.RUNNING,
            to_status=WorkflowStatus.RUNNING,
            payload=core,
        )
    )

    with pytest.raises(ValueError, match="generation|active|collision"):
        store.publish(declared, (), declared.output_paths)


@pytest.mark.parametrize(
    "poison",
    (
        "foreign-stage",
        "symlink-stage",
        "unsafe-mode",
        "multiple-temps",
        "wrong-temp",
    ),
)
def test_superseded_namespace_poisoning_fails_before_publication(
    tmp_path: Path, poison: str
) -> None:
    """Every stage name, type, and helper temporary is preflighted."""
    store = NodeCheckpointStore.for_workspace(tmp_path)
    declared = _node("design")
    output = tmp_path / declared.output_paths[0]
    output.parent.mkdir(parents=True)
    output.write_text("design\n", encoding="utf-8")
    superseded = tmp_path / "node-checkpoints/superseded"
    superseded.mkdir(mode=0o700)
    event_id = f"research.node.invalidated.{('a' * 64)}"
    stage = superseded / f".{event_id}.stage"
    if poison == "foreign-stage":
        (superseded / ".research.node.invalidated.bad.stage").mkdir()
    elif poison == "symlink-stage":
        target = tmp_path / "outside-stage"
        target.mkdir()
        try:
            stage.symlink_to(target, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"symlinks unavailable: {error}")
    else:
        stage.mkdir(mode=0o755 if poison == "unsafe-mode" else 0o700)
        if poison == "unsafe-mode":
            with pytest.raises(ValueError, match="mode"):
                store.publish(declared, (), declared.output_paths)
            return
        correct = target_name_digest(".invalidation-intent.json")
        digests = (correct, correct) if poison == "multiple-temps" else ("b" * 64,)
        for index, digest in enumerate(digests):
            temporary = stage / f".tmp-{digest}-{index:032x}"
            temporary.write_bytes(b"partial")
            temporary.chmod(0o600)

    with pytest.raises(ValueError, match="stage|temporary|superseded"):
        store.publish(declared, (), declared.output_paths)

    assert not (tmp_path / "node-checkpoints/design.json").exists()


def test_multiple_staging_transactions_are_rejected(tmp_path: Path) -> None:
    """Even individually disposable stages are globally bounded to one."""
    store = NodeCheckpointStore.for_workspace(tmp_path)
    declared = _node("design")
    output = tmp_path / declared.output_paths[0]
    output.parent.mkdir(parents=True)
    output.write_text("design\n", encoding="utf-8")
    superseded = tmp_path / "node-checkpoints/superseded"
    superseded.mkdir(mode=0o700)
    for digest in ("a" * 64, "b" * 64):
        event_id = f"research.node.invalidated.{digest}"
        (superseded / f".{event_id}.stage").mkdir(mode=0o700)

    with pytest.raises(ValueError, match="multiple.*stag|staging.*multiple"):
        store.publish(declared, (), declared.output_paths)


def test_stale_active_checkpoint_temporary_fails_closed(tmp_path: Path) -> None:
    """A stale active helper temporary is never silently ignored."""
    store = NodeCheckpointStore.for_workspace(tmp_path)
    declared = _node("design")
    output = tmp_path / declared.output_paths[0]
    output.parent.mkdir(parents=True)
    output.write_text("design\n", encoding="utf-8")
    digest = target_name_digest("design.json")
    (tmp_path / "node-checkpoints" / f".tmp-{digest}-stale").write_bytes(b"partial")

    with pytest.raises(ValueError, match="temporary|namespace"):
        store.publish(declared, (), declared.output_paths)


def test_superseded_namespace_entry_count_is_bounded(tmp_path: Path) -> None:
    """Preflight rejects an attacker-sized archive set before inspecting entries."""
    store = NodeCheckpointStore.for_workspace(tmp_path)
    declared = _node("design")
    output = tmp_path / declared.output_paths[0]
    output.parent.mkdir(parents=True)
    output.write_text("design\n", encoding="utf-8")
    superseded = tmp_path / "node-checkpoints/superseded"
    superseded.mkdir(mode=0o700)
    for index in range(257):
        event_id = f"research.node.invalidated.{index:064x}"
        (superseded / f".{event_id}.stage").mkdir(mode=0o700)

    with pytest.raises(ValueError, match="too many"):
        store.publish(declared, (), declared.output_paths)


def test_disposable_stage_rejects_entry_injected_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup never unlinks a helper or foreign entry added after stage scan."""
    store = NodeCheckpointStore.for_workspace(tmp_path)
    declared = _node("design")
    output = tmp_path / declared.output_paths[0]
    output.parent.mkdir(parents=True)
    output.write_text("design\n", encoding="utf-8")
    superseded = tmp_path / "node-checkpoints/superseded"
    superseded.mkdir(mode=0o700)
    event_id = f"research.node.invalidated.{('a' * 64)}"
    stage = superseded / f".{event_id}.stage"
    stage.mkdir(mode=0o700)
    helper = stage / (
        f".tmp-{target_name_digest('.invalidation-intent.json')}-{'1' * 32}"
    )
    helper.write_bytes(b"validated helper bytes")
    helper.chmod(0o600)
    foreign = stage / "foreign-user-data"
    real_scan = store._archive._scan_stage

    def inject_after_scan(parent_fd: int, name: str, identity: str) -> object:
        result = real_scan(parent_fd, name, identity)
        foreign.write_bytes(b"foreign bytes")
        return result

    monkeypatch.setattr(store._archive, "_scan_stage", inject_after_scan)

    with pytest.raises(ValueError, match="stage|changed|foreign"):
        store.publish(declared, (), declared.output_paths)

    assert helper.read_bytes() == b"validated helper bytes"
    assert foreign.read_bytes() == b"foreign bytes"


def test_disposable_stage_rejects_parent_entry_swap_without_deleting_either_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup stays bound to the validated stage inode across a parent swap."""
    store = NodeCheckpointStore.for_workspace(tmp_path)
    declared = _node("design")
    output = tmp_path / declared.output_paths[0]
    output.parent.mkdir(parents=True)
    output.write_text("design\n", encoding="utf-8")
    superseded = tmp_path / "node-checkpoints/superseded"
    superseded.mkdir(mode=0o700)
    event_id = f"research.node.invalidated.{('a' * 64)}"
    stage = superseded / f".{event_id}.stage"
    stage.mkdir(mode=0o700)
    helper_name = (
        f".tmp-{target_name_digest('.invalidation-intent.json')}-{'1' * 32}"
    )
    helper = stage / helper_name
    helper.write_bytes(b"original helper bytes")
    helper.chmod(0o600)
    displaced = superseded / "displaced-stage"
    replacement = stage / "replacement-user-data"
    real_scan = store._archive._scan_stage

    def swap_after_scan(parent_fd: int, name: str, identity: str) -> object:
        result = real_scan(parent_fd, name, identity)
        stage.rename(displaced)
        stage.mkdir(mode=0o700)
        replacement.write_bytes(b"replacement bytes")
        return result

    monkeypatch.setattr(store._archive, "_scan_stage", swap_after_scan)

    with pytest.raises(ValueError, match="stage|changed|identity"):
        store.publish(declared, (), declared.output_paths)

    assert (displaced / helper_name).read_bytes() == b"original helper bytes"
    assert replacement.read_bytes() == b"replacement bytes"


def test_checkpoint_store_has_explicit_idempotent_lifecycle(tmp_path: Path) -> None:
    """The store and its shared event view reject use after explicit close."""
    declared = _node("design")
    with NodeCheckpointStore.for_workspace(tmp_path) as store:
        _publish(store, declared)
        assert store.verify(declared, ())

    store.close()
    with pytest.raises(RuntimeError, match="closed"):
        store.verify(declared, ())
    with pytest.raises(RuntimeError, match="closed"):
        store.events.read_all()


def test_checkpoint_store_constructor_failure_has_safe_partial_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rejecting a foreign event path never emits an unraisable destructor error."""
    unraisable: list[object] = []
    monkeypatch.setattr(sys, "unraisablehook", unraisable.append)

    with pytest.raises(ValueError, match="workspace events"):
        NodeCheckpointStore(
            ArtifactStore(tmp_path), EventLog(tmp_path / "wrong-events.jsonl")
        )

    assert unraisable == []

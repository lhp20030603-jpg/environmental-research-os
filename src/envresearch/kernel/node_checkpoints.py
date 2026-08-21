from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Self

from pydantic import ValidationError

from envresearch.kernel.artifact_graph import ArtifactGraph, ArtifactNode
from envresearch.kernel.events import EventLog, EventRecord
from envresearch.kernel.node_checkpoint_archive import (
    LOCK_NAME,
    InvalidationArchive,
    NamespaceSnapshot,
)
from envresearch.kernel.node_checkpoint_events import (
    PinnedNodeEventLog,
    passed_event,
    replay_generations,
)
from envresearch.kernel.node_checkpoint_invalidation import (
    invalidate_locked,
    invalidation_ids,
    pending_source_hashes,
)
from envresearch.kernel.node_checkpoint_queries import require_completed_invalidation
from envresearch.kernel.node_checkpoint_schema import (
    InputSetMismatch,
    NodeCheckpoint,
    canonical_bytes,
    checkpoint_name,
    declared_outputs,
    definition_hash,
    input_hashes,
    make_checkpoint,
    read_checkpoint_at,
    require_reason,
    require_safe_id,
    revalidate_graph,
    revalidate_node,
    same_publication,
    utc_now,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.storage.artifacts import ArtifactStore
from envresearch.workers.filesystem import (
    PinnedRoot,
    entry_exists_at,
    write_file_noreplace_at,
)
from envresearch.workers.native import locked_regular_at, rename_noreplace_at

Clock = Callable[[], datetime]
__all__ = ["NodeCheckpoint", "NodeCheckpointStore"]


class NodeCheckpointStore:
    def __init__(
        self,
        artifacts: ArtifactStore,
        events: EventLog,
        clock: Clock = utc_now,
    ) -> None:
        self.artifacts = artifacts
        self.workspace = artifacts.root
        self._closed = False
        self._checkpoints_fd = -1
        self._root: PinnedRoot | None = None
        self._clock = clock
        expected_events = self.workspace / "events.jsonl"
        if Path(os.path.abspath(events.path)) != expected_events:
            raise ValueError("node checkpoint event log must be workspace events.jsonl")
        root = PinnedRoot(self.workspace)
        self._root = root
        try:
            self._workspace_identity = _identity(os.fstat(root.fd))
            self._checkpoints_fd = root.open_directory(
                Path("node-checkpoints"), create=True
            )
            self._checkpoint_identity = _identity(os.fstat(self._checkpoints_fd))
            try:
                write_file_noreplace_at(
                    self._checkpoints_fd, LOCK_NAME, b"", mode=0o600
                )
            except FileExistsError:
                pass
            self.events = PinnedNodeEventLog(expected_events, root, self._ensure_open)
            self.events.validate_file()
            self._archive = InvalidationArchive(
                self._checkpoints_fd,
                write_once=lambda fd, name, data: write_file_noreplace_at(
                    fd, name, data, mode=0o600
                ),
                move_once=lambda source_fd, source, destination_fd, destination: (
                    _move_noreplace(source_fd, source, destination_fd, destination)
                ),
            )
            with locked_regular_at(self._checkpoints_fd, LOCK_NAME, timeout=10):
                pass
        except BaseException:
            self.close()
            raise

    @classmethod
    def for_workspace(cls, workspace: Path, clock: Clock = utc_now) -> Self:
        artifacts = ArtifactStore(workspace)
        return cls(artifacts, EventLog(artifacts.root / "events.jsonl"), clock)

    def __enter__(self) -> Self:
        self._ensure_open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._checkpoints_fd >= 0:
            os.close(self._checkpoints_fd)
            self._checkpoints_fd = -1
        if (root := getattr(self, "_root", None)) is not None:
            root.close()
            self._root = None

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass

    def publish(
        self,
        node: ArtifactNode,
        inputs: Iterable[ArtifactRef],
        outputs: Iterable[Path],
    ) -> NodeCheckpoint:
        self._ensure_open()
        declared = revalidate_node(node)
        anchored_inputs = input_hashes(declared, inputs)
        output_paths = declared_outputs(declared, outputs)
        with self._locked():
            output_hashes = self._hash_outputs(output_paths)
            definition = definition_hash(declared)
            name = checkpoint_name(declared.node_id)
            exists = entry_exists_at(self._checkpoints_fd, name)
            if exists:
                checkpoint, _ = read_checkpoint_at(
                    self._checkpoints_fd, name, declared.node_id
                )
                if not same_publication(
                    checkpoint, declared, definition, anchored_inputs, output_hashes
                ):
                    raise FileExistsError(
                        "conflicting node checkpoint exists; invalidate it first"
                    )
            else:
                checkpoint = make_checkpoint(
                    declared,
                    definition,
                    anchored_inputs,
                    output_hashes,
                    self._clock(),
                )
            expected = passed_event(checkpoint)
            events, _ = self.events.read_prefix()
            snapshot = self._archive.preflight(invalidation_ids(events))
            if snapshot.pending is not None:
                raise ValueError(
                    f"pending invalidation {snapshot.pending.event_id} must be recovered"
                )
            allow = {checkpoint.node_id: checkpoint.checkpoint_hash} if exists else {}
            replay_generations(
                events, snapshot.active, snapshot.archives, allow_unrecorded=allow
            )
            events = self.events.read_for_expected(expected)
            snapshot = self._archive.preflight(invalidation_ids(events))
            if snapshot.pending is not None:
                raise ValueError(
                    f"pending invalidation {snapshot.pending.event_id} must be recovered"
                )
            replay_generations(
                events, snapshot.active, snapshot.archives, allow_unrecorded=allow
            )
            if not exists:
                write_file_noreplace_at(
                    self._checkpoints_fd,
                    name,
                    canonical_bytes(checkpoint.model_dump(mode="json")),
                    mode=0o600,
                )
            self.events.append_expected(expected)
            return checkpoint

    def verify(self, node: ArtifactNode, inputs: Iterable[ArtifactRef]) -> bool:
        self._ensure_open()
        declared = revalidate_node(node)
        try:
            anchored_inputs = input_hashes(declared, inputs)
        except InputSetMismatch:
            return False
        try:
            with self._locked():
                events = self.events.read_all()
                snapshot = self._archive.preflight(invalidation_ids(events))
                state = self._replay(events, snapshot)
                checkpoint = snapshot.active[declared.node_id][0]
                return self._verify_checkpoint(
                    declared, anchored_inputs, checkpoint, state
                )
        except (
            KeyError,
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
            ValidationError,
        ):
            return False

    def completed_nodes(self, graph: ArtifactGraph) -> frozenset[str]:
        self._ensure_open()
        declared_graph = revalidate_graph(graph)
        with self._locked():
            events = self.events.read_all()
            snapshot = self._archive.preflight(invalidation_ids(events))
            state = self._replay(events, snapshot)
            own_valid = {
                node.node_id
                for node in declared_graph.nodes
                if node.node_id in snapshot.active
                and self._verify_checkpoint(
                    node,
                    snapshot.active[node.node_id][0].input_hashes,
                    snapshot.active[node.node_id][0],
                    state,
                )
            }
            valid = set(own_valid)
            while True:
                invalid = {
                    node.node_id
                    for node in declared_graph.nodes
                    if node.node_id in valid
                    and any(dependency not in valid for dependency in node.dependencies)
                }
                if not invalid:
                    return frozenset(valid)
                valid.difference_update(invalid)

    def invalidate(
        self,
        graph: ArtifactGraph,
        node_id: str,
        *,
        reason: str = "node changed",
    ) -> frozenset[str]:
        self._ensure_open()
        declared_graph = revalidate_graph(graph)
        require_safe_id(node_id, "node ID")
        require_reason(reason)
        with self._locked():
            return invalidate_locked(
                declared_graph,
                node_id,
                reason,
                self.events,
                self._archive,
                self._verify_output_hashes,
                self._move_targets,
            )

    def require_invalidation(
        self,
        node_id: str,
        reason: str,
        affected_nodes: frozenset[str],
    ) -> dict[str, str]:
        require_safe_id(node_id, "node ID")
        require_reason(reason)
        with self._locked():
            events = self.events.read_all()
            snapshot = self._archive.preflight(invalidation_ids(events))
            return require_completed_invalidation(
                events,
                snapshot,
                node_id=node_id,
                reason=reason,
                affected_nodes=affected_nodes,
            )

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_open()
        self._guard_paths()
        with locked_regular_at(self._checkpoints_fd, LOCK_NAME, timeout=10):
            self._guard_paths()
            yield

    def _ensure_open(self) -> None:
        if getattr(self, "_closed", True):
            raise RuntimeError("node checkpoint store is closed")

    def _guard_paths(self) -> None:
        try:
            root = os.stat(self.workspace, follow_symlinks=False)
        except FileNotFoundError as error:
            raise ValueError(
                "workspace root changed after store initialization"
            ) from error
        if (
            not stat.S_ISDIR(root.st_mode)
            or _identity(root) != self._workspace_identity
        ):
            raise ValueError("workspace root changed after store initialization")
        current = os.stat(
            "node-checkpoints", dir_fd=self._pinned_root().fd, follow_symlinks=False
        )
        if (
            not stat.S_ISDIR(current.st_mode)
            or _identity(current) != self._checkpoint_identity
        ):
            raise ValueError("node-checkpoints directory changed after initialization")

    def _replay(
        self, events: list[EventRecord], snapshot: NamespaceSnapshot
    ) -> dict[str, str]:
        return replay_generations(
            events,
            snapshot.active,
            snapshot.archives,
            pending_hashes=pending_source_hashes(snapshot.pending),
        )

    def _hash_outputs(self, paths: tuple[Path, ...]) -> dict[str, str]:
        root = self._pinned_root()
        return {
            relative.as_posix(): hashlib.sha256(
                root.read_file(relative, description="node output")
            ).hexdigest()
            for relative in sorted(paths, key=Path.as_posix)
        }

    def _pinned_root(self) -> PinnedRoot:
        self._ensure_open()
        if self._root is None:
            raise RuntimeError("node checkpoint store is closed")
        return self._root

    def _verify_checkpoint(
        self,
        node: ArtifactNode,
        anchored_inputs: Mapping[str, str],
        checkpoint: NodeCheckpoint,
        state: Mapping[str, str],
    ) -> bool:
        return (
            checkpoint.node_id == node.node_id
            and checkpoint.node_version == node.version
            and checkpoint.definition_hash == definition_hash(node)
            and checkpoint.input_hashes == anchored_inputs
            and set(checkpoint.output_hashes)
            == {path.as_posix() for path in node.output_paths}
            and self._verify_output_hashes(checkpoint.output_hashes)
            and state.get(node.node_id) == checkpoint.checkpoint_hash
        )

    def _verify_output_hashes(self, expected: Mapping[str, str]) -> bool:
        try:
            actual = self._hash_outputs(tuple(Path(path) for path in expected))
        except (OSError, ValueError):
            return False
        return actual == expected

    def _move_targets(
        self,
        archive_fd: int,
        source_node_id: str,
        targets: frozenset[str],
        records: Mapping[str, tuple[NodeCheckpoint, bytes]],
    ) -> None:
        self._archive.move_targets(archive_fd, source_node_id, targets, records)


def _move_noreplace(
    source_fd: int, source: str, destination_fd: int, destination: str
) -> None:
    rename_noreplace_at(source_fd, source, destination_fd, destination)
    os.fsync(source_fd)
    os.fsync(destination_fd)


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino

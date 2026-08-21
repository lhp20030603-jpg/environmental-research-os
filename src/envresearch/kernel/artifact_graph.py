"""Immutable dependency graphs for research artifact production."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from envresearch.kernel.task_identity import _SAFE_TASK_ID

__all__ = ["ArtifactGraph", "ArtifactNode"]


@dataclass(frozen=True, slots=True)
class ArtifactNode:
    """One versioned artifact-producing step in a declarative workflow."""

    node_id: str
    worker_role: str | None = None
    dependencies: tuple[str, ...] = ()
    input_paths: tuple[Path, ...] = ()
    output_paths: tuple[Path, ...] = ()
    required_gate: str | None = None
    version: str | None = "1"

    def __post_init__(self) -> None:
        """Normalize immutable collections and validate durable identifiers."""
        _require_safe_identifier(self.node_id, "node ID")
        if self.worker_role is not None:
            _require_safe_identifier(self.worker_role, "worker role")
        if self.required_gate is not None:
            _require_safe_identifier(self.required_gate, "required gate ID")
        if self.version is not None:
            _require_safe_identifier(self.version, "node version")

        dependencies = tuple(self.dependencies)
        for dependency in dependencies:
            _require_safe_identifier(dependency, "dependency ID")
        _require_unique(dependencies, "dependency")

        input_paths = _normalized_paths(self.input_paths, "input")
        output_paths = _normalized_paths(self.output_paths, "output")
        if set(input_paths).intersection(output_paths):
            raise ValueError("input and output paths must not overlap")

        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "input_paths", input_paths)
        object.__setattr__(self, "output_paths", output_paths)


@dataclass(frozen=True, slots=True, init=False)
class ArtifactGraph:
    """Validated, declaration-ordered artifact DAG.

    ``ready`` rejects unknown completed node IDs, since treating them as
    completed could obscure a malformed checkpoint. Unknown approved gate IDs
    are ignored: they do not match a required gate and therefore cannot make a
    graph node runnable.
    """

    nodes: tuple[ArtifactNode, ...]
    _nodes_by_id: Mapping[str, ArtifactNode] = field(repr=False)
    _descendants_by_id: Mapping[str, frozenset[str]] = field(repr=False)

    def __init__(self, nodes: Iterable[ArtifactNode]) -> None:
        """Freeze *nodes* after deterministic dependency validation."""
        ordered_nodes = tuple(nodes)
        nodes_by_id: dict[str, ArtifactNode] = {}
        output_owners: dict[Path, str] = {}

        for node in ordered_nodes:
            if not isinstance(node, ArtifactNode):
                raise TypeError("artifact graph nodes must be ArtifactNode instances")
            if node.node_id in nodes_by_id:
                raise ValueError(f"duplicate node ID: {node.node_id}")
            nodes_by_id[node.node_id] = node
            for output_path in node.output_paths:
                if output_path in output_owners:
                    raise ValueError(
                        "output path already claimed: "
                        f"{output_path.as_posix()} "
                        f"({output_owners[output_path]} and {node.node_id})"
                    )
                output_owners[output_path] = node.node_id

        _validate_dependencies(ordered_nodes, nodes_by_id)
        _validate_acyclic(ordered_nodes, nodes_by_id)
        _validate_input_producers(ordered_nodes, nodes_by_id, output_owners)
        descendants_by_id = _build_descendants(ordered_nodes)

        object.__setattr__(self, "nodes", ordered_nodes)
        object.__setattr__(self, "_nodes_by_id", MappingProxyType(nodes_by_id))
        object.__setattr__(
            self,
            "_descendants_by_id",
            MappingProxyType(descendants_by_id),
        )

    def ready(
        self,
        completed: frozenset[str],
        approved_gates: frozenset[str],
    ) -> tuple[ArtifactNode, ...]:
        """Return declared-order nodes whose dependencies and gates are ready."""
        self._validate_completed(completed)
        return tuple(
            node
            for node in self.nodes
            if node.node_id not in completed
            and all(dependency in completed for dependency in node.dependencies)
            and (
                node.required_gate is None or node.required_gate in approved_gates
            )
        )

    def descendants(self, node_id: str) -> frozenset[str]:
        """Return all downstream nodes while excluding ``node_id`` itself."""
        self._require_node(node_id)
        return self._descendants_by_id[node_id]

    def invalidate(
        self, node_id: str, completed: frozenset[str]
    ) -> frozenset[str]:
        """Return a changed node and only its completed downstream dependents."""
        self._require_node(node_id)
        self._validate_completed(completed)
        return frozenset(
            descendant
            for descendant in self._descendants_by_id[node_id]
            if descendant in completed
        ).union({node_id})

    def _require_node(self, node_id: str) -> None:
        if node_id not in self._nodes_by_id:
            raise ValueError(f"unknown artifact node ID: {node_id}")

    def _validate_completed(self, completed: frozenset[str]) -> None:
        unknown = set(completed).difference(self._nodes_by_id)
        if unknown:
            names = ", ".join(sorted(str(item) for item in unknown))
            raise ValueError(f"unknown completed node IDs: {names}")


def _require_safe_identifier(value: object, field_name: str) -> None:
    """Apply the durable task filename identity rule to one graph field."""
    if not isinstance(value, str) or not _SAFE_TASK_ID.fullmatch(value):
        raise ValueError(f"{field_name} must be a safe filename segment")


def _require_unique(values: tuple[str, ...], item_name: str) -> None:
    """Reject duplicate values while preserving the first duplicate in errors."""
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {item_name}: {value}")
        seen.add(value)


def _normalized_paths(paths: Iterable[Path], kind: str) -> tuple[Path, ...]:
    """Freeze only workspace-relative, nonescaping authoritative paths."""
    normalized = tuple(Path(path) for path in paths)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{kind} paths must be unique")
    for path in normalized:
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError(f"{kind} paths must be safe relative paths")
    return normalized


def _validate_dependencies(
    nodes: tuple[ArtifactNode, ...], nodes_by_id: Mapping[str, ArtifactNode]
) -> None:
    """Reject self and missing dependencies before traversing the graph."""
    for node in nodes:
        for dependency in node.dependencies:
            if dependency == node.node_id:
                raise ValueError(f"node {node.node_id} depends on itself")
            if dependency not in nodes_by_id:
                raise ValueError(
                    f"node {node.node_id} has missing dependency: {dependency}"
                )


def _validate_acyclic(
    nodes: tuple[ArtifactNode, ...], nodes_by_id: Mapping[str, ArtifactNode]
) -> None:
    """Use declaration-order DFS to report cycles deterministically."""
    unvisited, visiting, complete = 0, 1, 2
    states = {node.node_id: unvisited for node in nodes}
    trail: list[str] = []

    def visit(node_id: str) -> None:
        state = states[node_id]
        if state == complete:
            return
        if state == visiting:
            start = trail.index(node_id)
            cycle = trail[start:] + [node_id]
            raise ValueError(f"artifact graph cycle detected: {' -> '.join(cycle)}")

        states[node_id] = visiting
        trail.append(node_id)
        for dependency in nodes_by_id[node_id].dependencies:
            visit(dependency)
        trail.pop()
        states[node_id] = complete

    for node in nodes:
        visit(node.node_id)


def _validate_input_producers(
    nodes: tuple[ArtifactNode, ...],
    nodes_by_id: Mapping[str, ArtifactNode],
    output_owners: Mapping[Path, str],
) -> None:
    """Require consumers of graph-owned artifacts to depend on their producer."""
    for node in nodes:
        for input_path in node.input_paths:
            producer = output_owners.get(input_path)
            if producer is not None and not _depends_on(
                node.node_id, producer, nodes_by_id
            ):
                raise ValueError(
                    f"node {node.node_id} must depend on producer {producer} first "
                    f"for input path {input_path.as_posix()}"
                )


def _depends_on(
    node_id: str, expected_ancestor: str, nodes_by_id: Mapping[str, ArtifactNode]
) -> bool:
    """Return whether an already validated node transitively depends on another."""
    pending = list(nodes_by_id[node_id].dependencies)
    visited: set[str] = set()
    while pending:
        dependency = pending.pop()
        if dependency == expected_ancestor:
            return True
        if dependency not in visited:
            visited.add(dependency)
            pending.extend(nodes_by_id[dependency].dependencies)
    return False


def _build_descendants(
    nodes: tuple[ArtifactNode, ...],
) -> dict[str, frozenset[str]]:
    """Build immutable descendant sets using declaration-order reverse edges."""
    children: dict[str, list[str]] = {node.node_id: [] for node in nodes}
    for node in nodes:
        for dependency in node.dependencies:
            children[dependency].append(node.node_id)

    descendants: dict[str, frozenset[str]] = {}
    for node in nodes:
        found: set[str] = set()
        pending = list(reversed(children[node.node_id]))
        while pending:
            child = pending.pop()
            if child in found:
                continue
            found.add(child)
            pending.extend(reversed(children[child]))
        descendants[node.node_id] = frozenset(found)
    return descendants

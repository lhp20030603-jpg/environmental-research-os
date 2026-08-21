"""Tests for the immutable research artifact dependency graph."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from envresearch.kernel.artifact_graph import ArtifactGraph, ArtifactNode


def node(
    node_id: str,
    *,
    deps: tuple[str, ...] = (),
    inputs: tuple[Path, ...] = (),
    outputs: tuple[Path, ...] = (),
    gate: str | None = None,
) -> ArtifactNode:
    """Build a node with a stable worker identity for graph tests."""
    return ArtifactNode(
        node_id=node_id,
        worker_role="researcher",
        dependencies=deps,
        input_paths=inputs,
        output_paths=outputs,
        required_gate=gate,
    )


def discover_design_graph() -> ArtifactGraph:
    """Build a branched research-design graph with one approval gate."""
    return ArtifactGraph(
        (
            node("approve-charter"),
            node("map-literature", deps=("approve-charter",), gate="gate-1"),
            node("inspect-data", deps=("approve-charter",), gate="gate-1"),
            node("define-estimand", deps=("approve-charter",)),
            node("rank-methods", deps=("define-estimand",)),
            node("draft-identification", deps=("define-estimand",)),
            node(
                "review-design",
                deps=("rank-methods", "draft-identification"),
            ),
            node("compose-plan", deps=("review-design",)),
        )
    )


def test_literature_and_data_are_ready_together_after_gate_one() -> None:
    """Independent approved successors must retain declaration order."""
    graph = discover_design_graph()

    ready = graph.ready(
        completed=frozenset({"approve-charter"}),
        approved_gates=frozenset({"gate-1"}),
    )

    assert tuple(item.node_id for item in ready) == (
        "map-literature",
        "inspect-data",
        "define-estimand",
    )


def test_estimand_change_invalidates_only_completed_design_descendants() -> None:
    """A changed artifact must not invalidate unrelated completed work."""
    graph = discover_design_graph()

    invalid = graph.invalidate(
        "define-estimand", frozenset(item.node_id for item in graph.nodes)
    )

    assert invalid == frozenset(
        {
            "define-estimand",
            "rank-methods",
            "draft-identification",
            "review-design",
            "compose-plan",
        }
    )
    assert "map-literature" not in invalid


def test_graph_rejects_disconnected_cycle() -> None:
    """Validation must visit every declared component, not just its roots."""
    with pytest.raises(ValueError, match="cycle.*b.*a"):
        ArtifactGraph((node("root"), node("a", deps=("b",)), node("b", deps=("a",))))


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ("", "node ID"),
        ("has space", "node ID"),
        ("worker role", "worker role"),
        ("gate 1", "required gate ID"),
        ("version 1", "node version"),
    ],
)
def test_node_rejects_unsafe_identifiers(value: str, field: str) -> None:
    """Filesystem-derived identities cannot contain unsafe filename text."""
    kwargs: dict[str, object] = {"node_id": "valid", "worker_role": "worker"}
    if field == "node ID":
        kwargs["node_id"] = value
    elif field == "worker role":
        kwargs["worker_role"] = value
    elif field == "required gate ID":
        kwargs["required_gate"] = value
    else:
        kwargs["version"] = value

    with pytest.raises(ValueError, match=field):
        ArtifactNode(**kwargs)


def test_node_allows_absent_optional_metadata() -> None:
    """A pure dependency node need not declare a worker, gate, or version."""
    artifact = ArtifactNode(node_id="root", version=None)

    assert artifact.worker_role is None
    assert artifact.required_gate is None
    assert artifact.version is None


def test_node_rejects_unsafe_dependency_and_authoritative_paths() -> None:
    """Dependencies and authoritative paths must be local safe identifiers."""
    with pytest.raises(ValueError, match="dependency ID"):
        node("valid", deps=("bad dependency",))
    with pytest.raises(ValueError, match="input paths"):
        node("valid", inputs=(Path("../outside.json"),))
    with pytest.raises(ValueError, match="output paths"):
        node("valid", outputs=(Path("/tmp/output.json"),))


def test_graph_rejects_invalid_dependencies_and_output_ownership() -> None:
    """The graph must not leave dependency or writer identity ambiguous."""
    with pytest.raises(ValueError, match="duplicate node ID: first"):
        ArtifactGraph((node("first"), node("first")))
    with pytest.raises(ValueError, match="depends on itself"):
        ArtifactGraph((node("first", deps=("first",)),))
    with pytest.raises(ValueError, match="missing dependency: absent"):
        ArtifactGraph((node("first", deps=("absent",)),))
    with pytest.raises(ValueError, match="duplicate dependency: root"):
        ArtifactGraph((node("root"), node("first", deps=("root", "root"))))
    with pytest.raises(ValueError, match="output paths must be unique"):
        ArtifactGraph((node("first", outputs=(Path("result.json"), Path("result.json"))),))
    with pytest.raises(ValueError, match="output path already claimed: result.json"):
        ArtifactGraph(
            (
                node("first", outputs=(Path("result.json"),)),
                node("second", outputs=(Path("result.json"),)),
            )
        )


def test_graph_requires_input_producer_to_be_a_dependency() -> None:
    """An authoritative output cannot be read before its producer completes."""
    with pytest.raises(ValueError, match="must depend on producer first"):
        ArtifactGraph(
            (
                node("first", outputs=(Path("result.json"),)),
                node("second", inputs=(Path("result.json"),)),
            )
        )

    graph = ArtifactGraph(
        (
            node("first", outputs=(Path("result.json"),)),
            node("second", deps=("first",), inputs=(Path("result.json"),)),
        )
    )

    assert tuple(item.node_id for item in graph.ready(frozenset(), frozenset())) == (
        "first",
    )


def test_unknown_completed_ids_are_rejected_but_unknown_gates_are_ignored() -> None:
    """Unknown completions cannot satisfy dependencies; extra approvals are harmless."""
    graph = ArtifactGraph((node("first", gate="gate-1"),))

    with pytest.raises(ValueError, match="unknown completed node IDs: absent"):
        graph.ready(frozenset({"absent"}), frozenset())
    with pytest.raises(ValueError, match="unknown completed node IDs: absent"):
        graph.invalidate("first", frozenset({"absent"}))

    assert graph.ready(frozenset(), frozenset({"other-gate"})) == ()


def test_node_and_graph_snapshot_caller_collections_immutably() -> None:
    """Caller mutation after construction must not alter graph behavior."""
    dependencies = ["root"]
    outputs = [Path("result.json")]
    child = ArtifactNode(
        node_id="child",
        worker_role="worker",
        dependencies=dependencies,
        output_paths=outputs,
    )
    nodes = [node("root"), child]
    graph = ArtifactGraph(nodes)
    dependencies.append("future")
    outputs.append(Path("later.json"))
    nodes.reverse()

    assert child.dependencies == ("root",)
    assert child.output_paths == (Path("result.json"),)
    assert tuple(item.node_id for item in graph.nodes) == ("root", "child")
    with pytest.raises(FrozenInstanceError):
        child.node_id = "changed"  # type: ignore[misc]


def test_descendants_exclude_source_and_unknown_nodes_fail_clearly() -> None:
    """Descendant queries return exactly the affected downstream branch."""
    graph = discover_design_graph()

    assert graph.descendants("define-estimand") == frozenset(
        {"rank-methods", "draft-identification", "review-design", "compose-plan"}
    )
    with pytest.raises(ValueError, match="unknown artifact node ID: absent"):
        graph.descendants("absent")

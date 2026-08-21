"""Optional authoritative inputs that supplement fixed research graph paths."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from envresearch.connectors.contracts import ConnectorCoverage
from envresearch.kernel.artifact_graph import ArtifactGraph
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle

LITERATURE_COVERAGE_PATH = Path("connector-receipts/local-export-unavailable.json")


def with_literature_coverage_input(graph: ArtifactGraph) -> ArtifactGraph:
    """Declare the persisted coverage receipt as a map-literature input."""
    nodes = tuple(
        replace(node, input_paths=node.input_paths + (LITERATURE_COVERAGE_PATH,))
        if node.node_id == "map-literature"
        and LITERATURE_COVERAGE_PATH not in node.input_paths
        else node
        for node in graph.nodes
    )
    return ArtifactGraph(nodes)


def refresh_literature_coverage_input(
    graph: ArtifactGraph, workspace: Path
) -> ArtifactGraph:
    """Rebind a graph when another process published optional coverage."""
    if not (workspace / LITERATURE_COVERAGE_PATH).exists():
        return graph
    return with_literature_coverage_input(graph)


def adopt_literature_coverage_state(
    state: Any, graph: ArtifactGraph | None = None
) -> None:
    """Refresh every graph consumer after cross-process coverage publication."""
    rebound = graph or refresh_literature_coverage_input(state.graph, state.workspace)
    state.graph = rebound
    state._nodes = {node.node_id: node for node in rebound.nodes}
    state.semantics.replace_nodes(state._nodes)
    state.revisions.graph, state.revisions.nodes = rebound, state._nodes


def bind_literature_coverage(
    lifecycle: ResearchArtifactLifecycle,
    graph: ArtifactGraph,
    coverage: ConnectorCoverage,
) -> ArtifactGraph:
    """Persist gateway coverage and return the graph that binds it downstream."""
    if (lifecycle.workspace / "work-orders/map-literature.json").exists():
        raise ValueError("literature work order is already issued")
    entry = graph.nodes[0]
    if len(entry.input_paths) != 1:
        raise ValueError("research entry must declare one authoritative intake")
    inputs = (lifecycle.artifact_ref(entry.input_paths[0]),)
    lifecycle.persist_structured(
        LITERATURE_COVERAGE_PATH,
        coverage,
        "literature-gateway",
        inputs,
    )
    return with_literature_coverage_input(graph)

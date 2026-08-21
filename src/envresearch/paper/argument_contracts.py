"""Frozen caller-owned contracts for one typed paper argument graph."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from envresearch.models.artifact import ArtifactRef
from envresearch.paper.contracts import CANONICAL_ID, STRICT

ArgumentNodeType = Literal[
    "research-question",
    "contribution",
    "mechanism",
    "empirical-claim",
    "robustness",
    "limitation",
    "policy-implication",
]
ArgumentEdgeType = Literal["evidence-backed", "interpretive", "conditional"]


class ArgumentNode(BaseModel):
    """One typed proposition or exact set of empirical ledger claims."""

    model_config = STRICT

    node_id: str
    node_type: ArgumentNodeType
    proposition: str | None
    claim_ids: tuple[str, ...]

    @field_validator("node_id")
    @classmethod
    def require_node_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("argument node id must be canonical lowercase kebab-case")
        return value

    @field_validator("claim_ids")
    @classmethod
    def require_claim_ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not CANONICAL_ID.fullmatch(item) for item in value):
            raise ValueError(
                "argument claim ids must be canonical lowercase kebab-case"
            )
        return value

    @model_validator(mode="after")
    def require_typed_content(self) -> ArgumentNode:
        if self.node_type == "empirical-claim":
            if self.proposition is not None:
                raise ValueError("empirical nodes must not store prose")
            if not self.claim_ids or len(set(self.claim_ids)) != len(self.claim_ids):
                raise ValueError("empirical nodes require at least one unique claim id")
            return self
        if (
            self.proposition is None
            or not self.proposition.strip()
            or self.proposition != self.proposition.strip()
        ):
            raise ValueError(
                "non-empirical nodes require a canonical nonblank proposition"
            )
        if self.claim_ids:
            raise ValueError("non-empirical nodes must not store claim ids")
        return self


class ArgumentEdge(BaseModel):
    """One typed, directed support relation between argument nodes."""

    model_config = STRICT

    source_id: str
    target_id: str
    edge_type: ArgumentEdgeType

    @field_validator("source_id", "target_id")
    @classmethod
    def require_endpoint_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("argument edge endpoints must be canonical node ids")
        return value


class ArgumentMapCandidate(BaseModel):
    """Caller-owned graph content without artifact identity or authority fields."""

    model_config = STRICT

    nodes: tuple[ArgumentNode, ...] = Field(min_length=1)
    edges: tuple[ArgumentEdge, ...]


class ArgumentMap(BaseModel):
    """Canonical immutable graph bound to one exact ledger and transition."""

    model_config = STRICT

    schema_version: Literal["paper.argument-map.v1"]
    map_id: str
    producer: Literal["paper-builder-argument-map-v1"]
    ledger_ref: ArtifactRef
    transition_ref: ArtifactRef
    nodes: tuple[ArgumentNode, ...] = Field(min_length=1)
    edges: tuple[ArgumentEdge, ...]

    @field_validator("map_id")
    @classmethod
    def require_map_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("argument map id must be canonical lowercase kebab-case")
        return value

    @model_validator(mode="after")
    def require_canonical_graph_order(self) -> ArgumentMap:
        node_ids = tuple(node.node_id for node in self.nodes)
        if node_ids != tuple(sorted(node_ids)):
            raise ValueError("argument map nodes must use canonical order")
        edge_keys = tuple(
            (edge.source_id, edge.target_id, edge.edge_type) for edge in self.edges
        )
        if edge_keys != tuple(sorted(edge_keys)):
            raise ValueError("argument map edges must use canonical order")
        if any(
            node.claim_ids != tuple(sorted(node.claim_ids))
            for node in self.nodes
            if node.node_type == "empirical-claim"
        ):
            raise ValueError("argument map claim ids must use canonical order")
        return self


__all__ = [
    "ArgumentEdge",
    "ArgumentEdgeType",
    "ArgumentMap",
    "ArgumentMapCandidate",
    "ArgumentNode",
    "ArgumentNodeType",
]

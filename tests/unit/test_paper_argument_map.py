"""Strict contracts and graph semantics for the typed argument map."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.paper import (
    ArgumentEdge,
    ArgumentMap,
    ArgumentMapCandidate,
    ArgumentNode,
)
from envresearch.paper.argument_map import validate_argument_map
from envresearch.paper.errors import PaperSupportInvalid


def _ref(identity: str, digest: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=identity,
        artifact_version=1,
        content_hash=digest * 64,
    )


def test_argument_candidate_is_frozen_and_cannot_choose_authority() -> None:
    empirical = ArgumentNode(
        node_id="cv-result",
        node_type="empirical-claim",
        proposition=None,
        claim_ids=("contingent-valuation-median-wtp",),
    )
    contribution = ArgumentNode(
        node_id="valuation-contribution",
        node_type="contribution",
        proposition="The registered valuation design quantifies annual willingness to pay.",
        claim_ids=(),
    )
    candidate = ArgumentMapCandidate(
        nodes=(empirical, contribution),
        edges=(
            ArgumentEdge(
                source_id="cv-result",
                target_id="valuation-contribution",
                edge_type="evidence-backed",
            ),
        ),
    )

    assert candidate.nodes == (empirical, contribution)
    with pytest.raises(ValidationError, match="frozen"):
        candidate.nodes = ()  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs"):
        ArgumentMapCandidate.model_validate(
            {
                **candidate.model_dump(mode="python"),
                "ledger_ref": {
                    "artifact_id": "forged-ledger",
                    "artifact_version": 1,
                    "content_hash": "a" * 64,
                },
            }
        )


@pytest.mark.parametrize(
    ("update", "message"),
    (
        ({"claim_ids": ()}, "at least one unique claim"),
        (
            {
                "claim_ids": (
                    "contingent-valuation-median-wtp",
                    "contingent-valuation-median-wtp",
                )
            },
            "at least one unique claim",
        ),
        ({"claim_ids": ("CV result",)}, "canonical"),
        ({"proposition": "Copied empirical prose."}, "must not store prose"),
    ),
)
def test_empirical_nodes_store_only_unique_canonical_claim_ids(
    update: dict[str, object], message: str
) -> None:
    payload: dict[str, object] = {
        "node_id": "cv-result",
        "node_type": "empirical-claim",
        "proposition": None,
        "claim_ids": ("contingent-valuation-median-wtp",),
    }
    payload.update(update)

    with pytest.raises(ValidationError, match=message):
        ArgumentNode.model_validate(payload)


@pytest.mark.parametrize(
    ("update", "message"),
    (
        ({"proposition": None}, "canonical nonblank proposition"),
        ({"proposition": "  "}, "canonical nonblank proposition"),
        ({"proposition": " Padded proposition "}, "canonical nonblank proposition"),
        (
            {"claim_ids": ("contingent-valuation-median-wtp",)},
            "must not store claim ids",
        ),
    ),
)
def test_non_empirical_nodes_store_only_a_canonical_proposition(
    update: dict[str, object], message: str
) -> None:
    payload: dict[str, object] = {
        "node_id": "valuation-contribution",
        "node_type": "contribution",
        "proposition": "The design quantifies annual willingness to pay.",
        "claim_ids": (),
    }
    payload.update(update)

    with pytest.raises(ValidationError, match=message):
        ArgumentNode.model_validate(payload)


def test_graph_identifiers_and_types_are_closed_and_canonical() -> None:
    with pytest.raises(ValidationError, match="canonical"):
        ArgumentNode(
            node_id="CV Result",
            node_type="empirical-claim",
            proposition=None,
            claim_ids=("contingent-valuation-median-wtp",),
        )
    with pytest.raises(ValidationError):
        ArgumentNode(
            node_id="result",
            node_type="conclusion",  # type: ignore[arg-type]
            proposition="An unsupported eighth node type.",
            claim_ids=(),
        )
    with pytest.raises(ValidationError, match="canonical"):
        ArgumentEdge(
            source_id="CV Result",
            target_id="valuation-contribution",
            edge_type="evidence-backed",
        )
    with pytest.raises(ValidationError):
        ArgumentEdge(
            source_id="cv-result",
            target_id="valuation-contribution",
            edge_type="causes",  # type: ignore[arg-type]
        )


def test_argument_map_has_exact_frozen_service_owned_identity() -> None:
    node = ArgumentNode(
        node_id="research-question",
        node_type="research-question",
        proposition="What is the registered annual willingness to pay?",
        claim_ids=(),
    )
    argument_map = ArgumentMap(
        schema_version="paper.argument-map.v1",
        map_id="argument-map-aaaaaaaaaaaa",
        producer="paper-builder-argument-map-v1",
        ledger_ref=_ref("valuation-core-claims", "a"),
        transition_ref=_ref("valuation-transition-v031", "b"),
        nodes=(node,),
        edges=(),
    )

    assert argument_map.schema_version == "paper.argument-map.v1"
    with pytest.raises(ValidationError, match="frozen"):
        argument_map.edges = ()  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ArgumentMap.model_validate(
            {**argument_map.model_dump(mode="python"), "producer": "caller"}
        )


def _node(
    node_id: str,
    node_type: str = "mechanism",
    *,
    claims: tuple[str, ...] = (),
) -> ArgumentNode:
    if node_type == "empirical-claim":
        return ArgumentNode(
            node_id=node_id,
            node_type=node_type,
            proposition=None,
            claim_ids=claims or ("contingent-valuation-median-wtp",),
        )
    return ArgumentNode(
        node_id=node_id,
        node_type=node_type,  # type: ignore[arg-type]
        proposition=f"Canonical proposition for {node_id}.",
        claim_ids=(),
    )


def _edge(source: str, target: str, kind: str = "interpretive") -> ArgumentEdge:
    return ArgumentEdge(
        source_id=source,
        target_id=target,
        edge_type=kind,  # type: ignore[arg-type]
    )


def _candidate(
    *nodes: ArgumentNode, edges: tuple[ArgumentEdge, ...]
) -> ArgumentMapCandidate:
    return ArgumentMapCandidate(nodes=nodes, edges=edges)


@pytest.mark.parametrize(
    ("candidate", "message"),
    (
        (
            _candidate(_node("same"), _node("same"), edges=()),
            "node ids must be unique",
        ),
        (
            _candidate(
                _node("a"),
                _node("b"),
                edges=(_edge("a", "b"), _edge("a", "b")),
            ),
            "edges must be unique",
        ),
        (
            _candidate(_node("a"), edges=(_edge("a", "missing"),)),
            "edge endpoint",
        ),
        (
            _candidate(
                _node("a"),
                _node("b"),
                edges=(_edge("a", "b"), _edge("b", "a")),
            ),
            "acyclic",
        ),
        (
            _candidate(
                _node(
                    "result",
                    "empirical-claim",
                    claims=("contingent-valuation-unregistered",),
                ),
                edges=(),
            ),
            "unknown ledger claim",
        ),
    ),
)
def test_validator_rejects_duplicate_dangling_or_cyclic_graphs(
    candidate: ArgumentMapCandidate, message: str
) -> None:
    with pytest.raises(PaperSupportInvalid, match=message) as raised:
        validate_argument_map(
            candidate,
            ledger_claim_ids=frozenset({"contingent-valuation-median-wtp"}),
        )

    assert raised.value.code == "PAPER_SUPPORT_INVALID"


def test_contribution_requires_direct_evidence_backed_empirical_input() -> None:
    indirect = _candidate(
        _node("result", "empirical-claim"),
        _node("mechanism"),
        _node("contribution", "contribution"),
        edges=(
            _edge("result", "mechanism", "evidence-backed"),
            _edge("mechanism", "contribution", "interpretive"),
        ),
    )
    wrong_edge = _candidate(
        _node("result", "empirical-claim"),
        _node("contribution", "contribution"),
        edges=(_edge("result", "contribution", "interpretive"),),
    )

    for candidate in (indirect, wrong_edge):
        with pytest.raises(PaperSupportInvalid, match="contribution"):
            validate_argument_map(
                candidate,
                ledger_claim_ids=frozenset({"contingent-valuation-median-wtp"}),
            )

    accepted = _candidate(
        _node("result", "empirical-claim"),
        _node("contribution", "contribution"),
        edges=(_edge("result", "contribution", "evidence-backed"),),
    )
    assert validate_argument_map(
        accepted,
        ledger_claim_ids=frozenset({"contingent-valuation-median-wtp"}),
    ) == ("result", "contribution")


def test_policy_accepts_direct_conditional_or_evidence_backed_empirical_input() -> None:
    interpretive = _candidate(
        _node("mechanism"),
        _node("policy", "policy-implication"),
        edges=(_edge("mechanism", "policy", "interpretive"),),
    )
    free_text_conditional = _candidate(
        _node("limitation", "limitation"),
        _node("policy", "policy-implication"),
        edges=(_edge("limitation", "policy", "conditional"),),
    )
    empirical_conditional = _candidate(
        _node("result", "empirical-claim"),
        _node("policy", "policy-implication"),
        edges=(_edge("result", "policy", "conditional"),),
    )
    empirical_evidence = empirical_conditional.model_copy(
        update={"edges": (_edge("result", "policy", "evidence-backed"),)}
    )

    for candidate in (interpretive, free_text_conditional):
        with pytest.raises(PaperSupportInvalid, match="policy implication"):
            validate_argument_map(candidate, ledger_claim_ids=frozenset())

    for candidate in (empirical_conditional, empirical_evidence):
        assert validate_argument_map(
            candidate,
            ledger_claim_ids=frozenset({"contingent-valuation-median-wtp"}),
        ) == ("result", "policy")


def test_kahn_order_is_deterministic_for_multiple_ready_nodes() -> None:
    candidate = _candidate(
        _node("zeta"),
        _node("alpha"),
        _node("middle"),
        edges=(_edge("alpha", "middle"),),
    )

    assert validate_argument_map(candidate, ledger_claim_ids=frozenset()) == (
        "alpha",
        "middle",
        "zeta",
    )

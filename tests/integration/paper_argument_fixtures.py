"""Shared genuine-ledger fixtures for typed argument-map integration tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from paper_claim_fixtures import ProcessResolver, cv_resolver

from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.models.artifact import ArtifactRef
from envresearch.paper import ArgumentMapService
from envresearch.paper.argument_contracts import (
    ArgumentEdge,
    ArgumentMapCandidate,
    ArgumentNode,
)
from envresearch.paper.errors import PaperBuilderError
from envresearch.paper.ledger import ClaimLedgerService


def candidate(*, reverse: bool = False, suffix: str = "") -> ArgumentMapCandidate:
    nodes = (
        ArgumentNode(
            node_id="valuation-contribution",
            node_type="contribution",
            proposition=(
                f"The registered design quantifies annual willingness to pay.{suffix}"
            ),
            claim_ids=(),
        ),
        ArgumentNode(
            node_id="cv-results",
            node_type="empirical-claim",
            proposition=None,
            claim_ids=(
                "contingent-valuation-probability-range",
                "contingent-valuation-median-wtp",
                "contingent-valuation-bid-yes-shares",
            ),
        ),
        ArgumentNode(
            node_id="scope-limitation",
            node_type="limitation",
            proposition="The valuation is conditional on the registered response model.",
            claim_ids=(),
        ),
        ArgumentNode(
            node_id="policy-boundary",
            node_type="policy-implication",
            proposition="Use the estimate only within the registered model boundary.",
            claim_ids=(),
        ),
    )
    edges = (
        ArgumentEdge(
            source_id="cv-results",
            target_id="policy-boundary",
            edge_type="conditional",
        ),
        ArgumentEdge(
            source_id="cv-results",
            target_id="valuation-contribution",
            edge_type="evidence-backed",
        ),
    )
    if reverse:
        nodes = tuple(reversed(nodes))
        edges = tuple(reversed(edges))
    return ArgumentMapCandidate(nodes=nodes, edges=edges)


def services(
    tmp_path: Path,
) -> tuple[ClaimLedgerService, ArgumentMapService, ArtifactRef]:
    resolver = cv_resolver(tmp_path)
    ledger_service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )
    service = ArgumentMapService(ledger_service=ledger_service)
    return ledger_service, service, resolver.transition_ref


def build_worker(
    paper_root: str,
    transition_json: str,
    analysis_json: str,
    report_json: str,
    ledger_json: str,
    candidate_json: str,
    start: Any,
    results: Any,
) -> None:
    """Build one map in a spawned process and report its typed outcome."""
    transition_ref = ArtifactRef.model_validate_json(transition_json)
    resolver = ProcessResolver(
        transition_ref=transition_ref,
        analysis_ref=LocalAnalysisReference.model_validate_json(analysis_json),
        report=LocalAnalysisReport.model_validate_json(report_json),
    )
    ledger_service = ClaimLedgerService.for_resolver(
        paper_root=Path(paper_root), resolver=resolver
    )
    service = ArgumentMapService(ledger_service=ledger_service)
    ledger_ref = ArtifactRef.model_validate_json(ledger_json)
    map_candidate = ArgumentMapCandidate.model_validate_json(candidate_json)
    start.wait()
    try:
        reference = service.build(ledger_ref, map_candidate)
        results.put(("ok", reference.model_dump_json()))
    except PaperBuilderError as error:
        results.put((error.code, str(error)))
    except Exception as error:  # noqa: BLE001 - child returns contention outcome
        results.put((type(error).__name__, str(error)))


__all__ = ["build_worker", "candidate", "services"]

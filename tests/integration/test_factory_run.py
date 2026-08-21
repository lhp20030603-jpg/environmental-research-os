"""Genuine governed factory assembly, recovery, and read-only status."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from econometrics_valuation_verifier_fixtures import (
    ValuationVerifierBackend,
    spec_for,
)
from factory_fixtures import final_context_ref
from orchestrator_fixtures import (
    approve,
    broad_brief,
    candidate_payload,
    config,
    literature,
    memo_candidate,
    safe_feasibility,
    submit,
)
from paper_claim_fixtures import ResolverFixture, transition
from paper_draft_integration_fixtures import build_stack

from envresearch.econometrics.service import LocalAnalysisService
from envresearch.factory import V02ApprovedDesignResolver
from envresearch.factory.errors import FactoryIntegrityInvalid
from envresearch.factory.service import FactoryRunService
from envresearch.models.artifact import ArtifactRef
from envresearch.models.design import (
    AnalysisPlanPayload,
    ClaimMode,
    DesignReviewPayload,
    EstimandSpecPayload,
    EstimandType,
    MethodCandidate,
    MethodCandidateRole,
    MethodCandidatesPayload,
)
from envresearch.models.intake import ResearchIntakeMode
from envresearch.paper.argument_contracts import (
    ArgumentEdge,
    ArgumentMapCandidate,
    ArgumentNode,
)
from envresearch.paper.argument_map import ArgumentMapService
from envresearch.paper.auditor import PaperAuditService
from envresearch.paper.draft_builder import DraftService, deterministic_draft_candidate
from envresearch.paper.ledger import AcceptedEvidenceResolver, ClaimLedgerService
from envresearch.paper.release import PaperReleaseService
from envresearch.research.orchestrator import ResearchOrchestrator, ResearchRunPhase
from envresearch.storage.research_artifacts import ResearchArtifactStore

HEDONIC_LIMITATION = (
    "Model-conditional marginal implicit price; residual confounding and "
    "housing-market sorting remain outside the registered design."
)


@dataclass(frozen=True)
class ConnectedFactory:
    service: FactoryRunService
    design_ref: ArtifactRef
    release_ref: ArtifactRef
    orchestrators: tuple[ResearchOrchestrator, ...]

    def close(self) -> None:
        for orchestrator in self.orchestrators:
            orchestrator.close()


def connected_factory(tmp_path: Path) -> ConnectedFactory:
    """Build a real compatible V0.2 Final Gate and V0.4 audited release."""
    design_orchestrator = _hedonic_final_gate(tmp_path / "design")
    factory_root = tmp_path / "factory"
    resolver = V02ApprovedDesignResolver(design_orchestrator, factory_root)
    design_ref = resolver.build(
        design_orchestrator.lifecycle.artifact_ref(
            Path("artifacts/analysis-plan.yaml")
        ),
        final_context_ref(design_orchestrator),
    )
    release_service, release_ref, citation_orchestrator = _hedonic_release(
        tmp_path / "paper"
    )
    return ConnectedFactory(
        service=FactoryRunService(
            design_resolver=resolver, release_service=release_service
        ),
        design_ref=design_ref,
        release_ref=release_ref,
        orchestrators=(design_orchestrator, citation_orchestrator),
    )


def _hedonic_final_gate(root: Path) -> ResearchOrchestrator:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(config(root, ResearchIntakeMode.BROAD_TOPIC), broad_brief())
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    approve(orchestrator, "gate-1", selected_candidate_id="charter-air")
    orchestrator.advance()
    submit(orchestrator, "map-literature", literature())
    feasibility = safe_feasibility()
    candidate = feasibility.candidates[0].model_copy(
        update={
            "data_structures": ("panel",),
            "available_features": (
                "environmental_attribute",
                "georeferenced_measurement",
                "market_prices",
            ),
        }
    )
    submit(
        orchestrator,
        "inspect-data",
        feasibility.model_copy(update={"candidates": (candidate,)}),
    )
    orchestrator.advance()
    estimand = EstimandSpecPayload(
        estimand_id="implicit-price",
        estimand_type=EstimandType.CAUSAL,
        population="sample-household",
        unit="USD",
        exposure_or_treatment="environmental attribute",
        outcome="market price",
        comparison_or_counterfactual="same market absent attribute change",
        time_horizon="per-year",
        target_parameter="implicit-price",
        evidence_refs=("evidence-1",),
        assumption_refs=("market-equilibrium",),
    )
    submit(orchestrator, "define-estimand", estimand)
    orchestrator.advance()
    estimand_ref = orchestrator.lifecycle.artifact_ref(
        Path("artifacts/estimand-spec.yaml")
    )
    token = (
        f"artifact:{estimand_ref.artifact_id}@{estimand_ref.artifact_version}"
        f"#sha256:{estimand_ref.content_hash}"
    )
    common = {
        "estimand_compatible": True,
        "required_assumption_refs": ("market-equilibrium",),
        "required_data_structure": ("panel",),
        "diagnostics": ("registered diagnostics",),
        "fallback_or_limitations": (HEDONIC_LIMITATION,),
    }
    methods = MethodCandidatesPayload(
        estimand_ref=token,
        candidates=(
            MethodCandidate(
                method_profile_ref="hedonic@0.2.0",
                role=MethodCandidateRole.PRIMARY,
                rank=1,
                **common,
            ),
            MethodCandidate(
                method_profile_ref="spatiotemporal@0.2.0",
                role=MethodCandidateRole.ALTERNATIVE,
                rank=2,
                **common,
            ),
        ),
    )
    submit(orchestrator, "rank-methods", methods)
    orchestrator.advance()
    memo = memo_candidate(token)
    metadata = dict(memo["metadata"])  # type: ignore[arg-type]
    metadata.update(
        {
            "primary_method_profile_ref": "hedonic@0.2.0",
            "alternative_method_profile_refs": ["spatiotemporal@0.2.0"],
        }
    )
    submit(orchestrator, "draft-identification", {**memo, "metadata": metadata})
    orchestrator.advance()
    submit(
        orchestrator,
        "review-design",
        DesignReviewPayload(review_id="review-hedonic", findings=()),
    )
    orchestrator.advance()
    plan = AnalysisPlanPayload(
        estimand_ref=token,
        estimand_type=EstimandType.CAUSAL,
        estimand=estimand,
        primary_method_profile_ref="hedonic@0.2.0",
        alternative_method_profile_refs=("spatiotemporal@0.2.0",),
        data_boundaries=("price-base:2025",),
        assumptions=("market-equilibrium",),
        diagnostics=("registered diagnostics",),
        exclusion_rules=("exclude incomplete records",),
        robustness_plan=("registered sensitivity",),
        fallback_rules=(HEDONIC_LIMITATION,),
        claim_mode=ClaimMode.CAUSAL,
    )
    submit(orchestrator, "compose-plan", plan)
    orchestrator.advance()
    approve(orchestrator, "final-gate", accepted_major_ids=[])
    assert orchestrator.advance().phase is ResearchRunPhase.COMPLETE
    return orchestrator


def _hedonic_release(
    root: Path,
    *,
    citation_root: Path | None = None,
    accepted_root: Path | None = None,
    paper_root: Path | None = None,
    accepted_resolver: AcceptedEvidenceResolver | None = None,
    accepted_transition_ref: ArtifactRef | None = None,
    bind_citation_config: bool = False,
) -> tuple[PaperReleaseService, ArtifactRef, ResearchOrchestrator]:
    citation_stack = build_stack(
        citation_root or root / "citation",
        bind_explicit_config=bind_citation_config,
    )
    if accepted_resolver is None:
        analysis_service = LocalAnalysisService(
            ResearchArtifactStore(accepted_root or root / "hedonic-analysis"),
            ValuationVerifierBackend("hedonic-pricing"),
        )
        analysis_ref = analysis_service.run(spec_for("hedonic-pricing"))
        report = analysis_service.status(analysis_ref)
        resolver: AcceptedEvidenceResolver = ResolverFixture(
            transition(), ((analysis_ref, report),)
        )
        resolver.authority_root = accepted_root or root / "hedonic-analysis"  # type: ignore[attr-defined]
        transition_ref = transition()
    else:
        if accepted_transition_ref is None:
            raise ValueError("real accepted evidence requires an exact transition")
        resolver = accepted_resolver
        transition_ref = accepted_transition_ref
    ledgers = ClaimLedgerService.for_resolver(
        paper_root=paper_root or root / "hedonic-paper", resolver=resolver
    )
    maps = ArgumentMapService(ledger_service=ledgers)
    ledger_ref = ledgers.build(transition_ref)
    ledger = ledgers.status(ledger_ref, transition_ref)
    claim_ids = tuple(item.claim_id for item in ledger.claims)
    claim_nodes = tuple(
        ArgumentNode(
            node_id=("hedonic-result" if len(claim_ids) == 1 else f"claim-{index}"),
            node_type="empirical-claim",
            proposition=None,
            claim_ids=(claim_id,),
        )
        for index, claim_id in enumerate(claim_ids, start=1)
    )
    map_ref = maps.build(
        ledger_ref,
        ArgumentMapCandidate(
            nodes=(
                *claim_nodes,
                ArgumentNode(
                    node_id="valuation-contribution",
                    node_type="contribution",
                    proposition="The registered design estimates an implicit price.",
                    claim_ids=(),
                ),
            ),
            edges=tuple(
                ArgumentEdge(
                    source_id=node.node_id,
                    target_id="valuation-contribution",
                    edge_type="evidence-backed",
                )
                for node in claim_nodes
            ),
        ),
    )
    argument_map = maps.status(map_ref, ledger_ref)
    citations = citation_stack.citation_authority.reopen(citation_stack.report_ref)
    drafts = DraftService(
        map_service=maps, citation_authority=citation_stack.citation_authority
    )
    draft_ref = drafts.publish(
        deterministic_draft_candidate(
            argument_map=argument_map, ledger=ledger, citation_snapshot=citations
        ),
        map_ref=map_ref,
        ledger_ref=ledger_ref,
        citation_report_ref=citation_stack.report_ref,
    )
    audits = PaperAuditService(draft_service=drafts)
    audit_ref = audits.audit(draft_ref)
    releases = PaperReleaseService(audit_service=audits)
    return releases, releases.build(audit_ref, draft_ref), citation_stack.orchestrator


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_assemble_returns_one_exact_run_with_derived_promotion_status(
    tmp_path: Path,
) -> None:
    """Catch nondeterministic assembly or status changing the immutable verdict."""
    fixture = connected_factory(tmp_path)
    try:
        reference = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        status = fixture.service.status(reference)

        assert status.state == "promotion-required"
        assert status.run_ref == reference
        assert status.run.binding_report.verdict == "coherent"
        assert status.run.assembly_verdict == "assembled"
        assert (
            fixture.service.assemble(fixture.design_ref, fixture.release_ref)
            == reference
        )
    finally:
        fixture.close()


def test_prepared_run_recovers_with_identical_reference_after_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a crash between prepared and committed pointers changing run bytes."""
    fixture = connected_factory(tmp_path)
    original = fixture.service.store.commit
    try:
        monkeypatch.setattr(
            fixture.service.store,
            "commit",
            lambda reference: (_ for _ in ()).throw(OSError("injected commit crash")),
        )
        with pytest.raises(FactoryIntegrityInvalid):
            fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        prepared = fixture.service.store.prepared()
        assert prepared is not None
        assert fixture.service.store.committed() is None
        prepared_bytes = fixture.service.store.object_bytes(prepared)
        assert b"design_limitations" not in prepared_bytes
        assert b"claim_limitations" not in prepared_bytes

        monkeypatch.setattr(fixture.service.store, "commit", original)
        recovered = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        assert recovered == prepared
        assert fixture.service.status(recovered).run_ref == recovered
    finally:
        fixture.close()


def test_status_is_read_only_and_canonical_run_bytes_exclude_operational_time(
    tmp_path: Path,
) -> None:
    """Catch status repairs or service timestamps entering canonical run bytes."""
    fixture = connected_factory(tmp_path)
    try:
        reference = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        factory_root = fixture.service.design_resolver.factory_root
        before = _snapshot(factory_root)
        before_ctime = factory_root.stat().st_ctime_ns

        fixture.service.status(reference)

        assert _snapshot(factory_root) == before
        assert factory_root.stat().st_ctime_ns == before_ctime
        run_bytes = fixture.service.store.object_bytes(reference)
        assert b"timestamp" not in run_bytes
        assert b"retrospective-coherence" in run_bytes
    finally:
        fixture.close()

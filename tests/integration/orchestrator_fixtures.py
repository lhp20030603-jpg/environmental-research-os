"""Local-only fixtures shared by research orchestrator integration tests."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from envresearch.kernel.gates import GateDecision
from envresearch.models.design import (
    AnalysisPlanPayload,
    ClaimMode,
    DesignReviewPayload,
    EstimandSpecPayload,
    EstimandType,
    IdentificationMemoMetadata,
    MethodCandidate,
    MethodCandidateRole,
    MethodCandidatesPayload,
)
from envresearch.models.enums import GateStatus
from envresearch.models.evidence import (
    DataFeasibilityPayload,
    DatasetCandidate,
    EvidenceRow,
    LiteratureMapPayload,
    SourceRecord,
)
from envresearch.models.intake import (
    SCORE_FIELDS,
    CandidateCharter,
    CandidateChartersPayload,
    CharterScore,
    DistinctnessClaim,
    ResearchBriefPayload,
    ResearchIntakeMode,
    ScoreUncertainty,
)
from envresearch.research.orchestrator import ResearchOrchestrator, ResearchRunConfig
from envresearch.research.run_config import parse_explicit_config
from envresearch.research.workflow import ARTIFACT_PATHS


def config(tmp_path: Path, mode: ResearchIntakeMode) -> ResearchRunConfig:
    return ResearchRunConfig(
        workspace=tmp_path, run_id="run-orchestration", input_mode=mode
    )


def broad_brief() -> ResearchBriefPayload:
    return ResearchBriefPayload(
        intake_mode=ResearchIntakeMode.BROAD_TOPIC,
        broad_topic="Urban air-pollution policy",
    )


def structured_brief() -> ResearchBriefPayload:
    return ResearchBriefPayload(
        intake_mode=ResearchIntakeMode.STRUCTURED_BRIEF,
        structured_brief="Estimate how clean-air zones affect particulate exposure.",
    )


def charter(
    candidate_id: str, question: str, others: tuple[str, str]
) -> CandidateCharter:
    scores = {
        field: CharterScore(
            score=75.0,
            evidence_refs=("brief",),
            uncertainty=ScoreUncertainty.MEDIUM,
        )
        for field in SCORE_FIELDS
    }
    return CandidateCharter(
        candidate_id=candidate_id,
        research_question=question,
        scores=scores,
        distinctness_claims=tuple(
            DistinctnessClaim(
                other_candidate_id=other,
                different_exposure_or_policy=True,
                different_outcome_or_mechanism=False,
                explanation=f"{candidate_id} uses a different policy from {other}.",
            )
            for other in others
        ),
    )


def candidate_payload() -> CandidateChartersPayload:
    return CandidateChartersPayload(
        brief=broad_brief(),
        candidates=(
            charter(
                "charter-air",
                "Do clean-air zones reduce PM2.5?",
                ("charter-tax", "charter-transit"),
            ),
            charter(
                "charter-tax",
                "Do pollution taxes reduce emissions?",
                ("charter-air", "charter-transit"),
            ),
            charter(
                "charter-transit",
                "Does transit access reduce car use?",
                ("charter-air", "charter-tax"),
            ),
        ),
    )


def submit(orchestrator: ResearchOrchestrator, node_id: str, payload: object) -> None:
    filename = ARTIFACT_PATHS[node_id][0].name
    source = orchestrator.workspace / "fixture-inputs" / node_id / filename
    source.parent.mkdir(parents=True, exist_ok=True)
    value = (
        payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    )
    source.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    order = orchestrator.queue.read_order(node_id)
    orchestrator.queue.submit(node_id, source, expected_order_hash=order.order_hash)
    orchestrator.accept_submission(node_id)


def approve(
    orchestrator: ResearchOrchestrator, gate_id: str, **conditions: object
) -> None:
    bound = orchestrator.bound_gates.decision_conditions(gate_id)
    context = orchestrator.bound_gates.active_context(gate_id)
    assert context is not None
    orchestrator.decide_gate(
        gate_id,
        GateDecision(
            status=GateStatus.APPROVED,
            decided_by="human-reviewer",
            rationale="Approved fixture decision.",
            conditions={**bound, **conditions},
        ),
        gate_capability(orchestrator),
    )


def gate_capability(orchestrator: ResearchOrchestrator) -> str:
    """Read the owner-only capability in trusted integration fixtures."""
    return orchestrator.queue.control.storage.read_file(
        Path("principals/gate.capability"),
        description="test gate capability",
        required_mode=0o600,
    ).decode()


def revision_capability(orchestrator: ResearchOrchestrator) -> str:
    """Read the owner-only revision capability in trusted fixtures."""
    return orchestrator.queue.control.storage.read_file(
        Path("principals/revision.capability"),
        description="test revision capability",
        required_mode=0o600,
    ).decode()


def safe_feasibility() -> DataFeasibilityPayload:
    return DataFeasibilityPayload(
        research_design="Panel comparison",
        candidates=(
            DatasetCandidate(
                dataset_id="public-air",
                source="local fixture catalog",
                public_access=True,
                requires_credentials=False,
                clear_license=True,
                license="CC-BY-4.0",
                estimated_download_bytes=10,
                estimated_local_storage_bytes=20,
                estimated_api_calls=0,
                estimated_external_cost=Decimal(0),
                estimated_elapsed_seconds=1,
                suitable_for_design=True,
                suitability_reason="Contains the required variables.",
                access_reason="Public local fixture metadata.",
                data_structures=("panel",),
                available_features=(
                    "donor_pool",
                    "one_or_few_treated_units",
                    "pre_treatment_periods",
                    "treatment_timing_variation",
                    "untreated_comparison",
                ),
            ),
        ),
        recommendation="Use the public candidate at the design stage.",
        evidence_reason="Metadata and license are present.",
    )


def restricted_feasibility() -> DataFeasibilityPayload:
    candidate = (
        safe_feasibility()
        .candidates[0]
        .model_copy(
            update={
                "public_access": False,
                "requires_credentials": True,
                "clear_license": False,
                "license": "unknown",
                "access_reason": "Institutional permission is required.",
            }
        )
    )
    return safe_feasibility().model_copy(update={"candidates": (candidate,)})


def literature() -> LiteratureMapPayload:
    return LiteratureMapPayload(
        research_question="Do clean-air zones reduce PM2.5?",
        sources=(
            SourceRecord(
                source_id="paper-1",
                title="Clean-air zones and air quality",
                source="local Zotero export fixture",
                evidence_reason="Directly studies the policy and outcome.",
            ),
        ),
        evidence_rows=(
            EvidenceRow(
                evidence_id="evidence-1",
                source_id="paper-1",
                finding="Exposure fell after implementation.",
                relevance="Supports the proposed outcome definition.",
                evidence_reason="Reported in the local fixture abstract.",
            ),
        ),
        synthesis="The policy is studied, but distributional effects remain open.",
    )


def estimand() -> EstimandSpecPayload:
    return EstimandSpecPayload(
        estimand_id="estimand-air",
        estimand_type=EstimandType.CAUSAL,
        population="Residents of adopting cities",
        unit="city-month",
        exposure_or_treatment="Clean-air-zone adoption",
        outcome="Monthly PM2.5",
        comparison_or_counterfactual="Same cities absent adoption",
        time_horizon="Three years",
        target_parameter="Average treatment effect on treated cities",
        evidence_refs=("evidence-1",),
        assumption_refs=("assumption-parallel-trends",),
    )


def methods(estimand_ref: str = "estimand-air") -> MethodCandidatesPayload:
    common = {
        "estimand_compatible": True,
        "required_assumption_refs": ("assumption-parallel-trends",),
        "required_data_structure": ("city-month panel",),
        "diagnostics": ("pre-trend diagnostic",),
        "fallback_or_limitations": ("Use descriptive trends if support fails",),
    }
    return MethodCandidatesPayload(
        estimand_ref=estimand_ref,
        candidates=(
            MethodCandidate(
                method_profile_ref="did-event-study@0.2.0",
                role=MethodCandidateRole.PRIMARY,
                rank=1,
                **common,
            ),
            MethodCandidate(
                method_profile_ref="synthetic-control@0.2.0",
                role=MethodCandidateRole.ALTERNATIVE,
                rank=2,
                **common,
            ),
        ),
    )


def memo_candidate(estimand_ref: str = "estimand-air") -> dict[str, object]:
    metadata = IdentificationMemoMetadata(
        estimand_ref=estimand_ref,
        primary_method_profile_ref="did-event-study@0.2.0",
        alternative_method_profile_refs=("synthetic-control@0.2.0",),
        assumption_refs=("assumption-parallel-trends",),
        threat_refs=("threat-policy-selection",),
        diagnostic_refs=("pre-trend diagnostic",),
        evidence_refs=("evidence-1",),
        residual_risks=("Policy timing may remain endogenous.",),
    )
    return {
        "metadata": metadata.model_dump(mode="json"),
        "body": "# Identification\n\nCompare treated and untreated city trends.\n",
    }


def plan(estimand_ref: str = "estimand-air") -> AnalysisPlanPayload:
    return AnalysisPlanPayload(
        estimand_ref=estimand_ref,
        estimand_type=EstimandType.CAUSAL,
        primary_method_profile_ref="did-event-study@0.2.0",
        alternative_method_profile_refs=("synthetic-control@0.2.0",),
        data_boundaries=("Use only documented public data",),
        assumptions=("Parallel trends",),
        diagnostics=("Event-study pre-trends",),
        exclusion_rules=("Exclude cities without baseline coverage",),
        robustness_plan=("Alternative comparison groups",),
        fallback_rules=("Switch to descriptive claim mode if assumptions fail",),
        claim_mode=ClaimMode.CAUSAL,
    )


def ready_for_final_gate(
    tmp_path: Path,
    *,
    require_claim_verified_citations: bool = False,
    citation_catalog_roots: tuple[Path, ...] = (),
    explicit_config: bytes | None = None,
) -> ResearchOrchestrator:
    orchestrator = ResearchOrchestrator()
    run_config = config(tmp_path, ResearchIntakeMode.BROAD_TOPIC).model_copy(
        update={
            "require_claim_verified_citations": require_claim_verified_citations,
            "citation_catalog_roots": citation_catalog_roots,
        }
    )
    if explicit_config is not None:
        explicit = parse_explicit_config(explicit_config)
        run_config = run_config.model_copy(
            update={
                "config_sha256": explicit.sha256,
                "ranking_policy": explicit.ranking_policy,
                "acquisition_budget": explicit.acquisition_budget,
                "require_claim_verified_citations": (
                    explicit.require_claim_verified_citations
                ),
                "citation_catalog_roots": explicit.citation_catalog_roots,
            }
        )
    orchestrator.initialize(run_config, broad_brief(), explicit_config=explicit_config)
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    approve(orchestrator, "gate-1", selected_candidate_id="charter-air")
    orchestrator.advance()
    submit(orchestrator, "map-literature", literature())
    submit(orchestrator, "inspect-data", safe_feasibility())
    orchestrator.advance()
    submit(orchestrator, "define-estimand", estimand())
    orchestrator.advance()
    current_estimand = orchestrator.lifecycle.artifact_ref(
        Path("artifacts/estimand-spec.yaml")
    )
    token = (
        f"artifact:{current_estimand.artifact_id}@{current_estimand.artifact_version}"
        f"#sha256:{current_estimand.content_hash}"
    )
    submit(orchestrator, "rank-methods", methods(token))
    orchestrator.advance()
    submit(orchestrator, "draft-identification", memo_candidate(token))
    orchestrator.advance()
    submit(
        orchestrator,
        "review-design",
        DesignReviewPayload(review_id="review-1", findings=()),
    )
    orchestrator.advance()
    submit(orchestrator, "compose-plan", plan(token))
    final = orchestrator.advance()
    if require_claim_verified_citations:
        assert final.pending_gate_ids == ()
    else:
        assert final.pending_gate_ids == ("final-gate",)
    return orchestrator

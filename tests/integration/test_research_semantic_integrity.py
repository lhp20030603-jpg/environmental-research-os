"""Adversarial semantic-integrity checks at trusted promotion boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest
from orchestrator_fixtures import (
    approve,
    broad_brief,
    candidate_payload,
    config,
    estimand,
    literature,
    memo_candidate,
    methods,
    plan,
    safe_feasibility,
    submit,
)
from test_blind_registry_security import write_case

from envresearch.benchmarks.claim_integrity import CitationIntegrityReport
from envresearch.benchmarks.claim_report import report_payload
from envresearch.models.artifact import (
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.models.design import (
    ClaimMode,
    DesignReviewPayload,
    EstimandType,
    MethodCandidatesPayload,
)
from envresearch.models.evidence import (
    DataFeasibilityPayload,
    DatasetCandidate,
)
from envresearch.models.intake import ResearchIntakeMode
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.producer_identity import require_independent_critic


def _ready_for_parallel(tmp_path: Path) -> ResearchOrchestrator:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    approve(orchestrator, "gate-1", selected_candidate_id="charter-air")
    orchestrator.advance()
    return orchestrator


def _ready_for_methods(tmp_path: Path) -> ResearchOrchestrator:
    orchestrator = _ready_for_parallel(tmp_path)
    submit(orchestrator, "map-literature", literature())
    submit(orchestrator, "inspect-data", safe_feasibility())
    orchestrator.advance()
    submit(orchestrator, "define-estimand", estimand())
    orchestrator.advance()
    return orchestrator


def _ready_for_review(tmp_path: Path) -> tuple[ResearchOrchestrator, str]:
    orchestrator = _ready_for_methods(tmp_path)
    token = _artifact_token(orchestrator, "estimand-spec.yaml")
    submit(orchestrator, "rank-methods", methods(token))
    orchestrator.advance()
    submit(orchestrator, "draft-identification", memo_candidate(token))
    orchestrator.advance()
    return orchestrator, token


def _artifact_token(orchestrator: ResearchOrchestrator, name: str) -> str:
    ref = orchestrator.lifecycle.artifact_ref(Path("artifacts") / name)
    return (
        f"artifact:{ref.artifact_id}@{ref.artifact_version}#sha256:{ref.content_hash}"
    )


def test_literature_evidence_row_must_resolve_to_a_current_source(
    tmp_path: Path,
) -> None:
    """Deleting source resolution must make this invalid row promotable."""
    orchestrator = _ready_for_parallel(tmp_path)
    row = (
        literature()
        .evidence_rows[0]
        .model_copy(update={"source_id": "not-a-current-source"})
    )
    inconsistent = literature().model_copy(update={"evidence_rows": (row,)})

    with pytest.raises(ValueError, match="source"):
        submit(orchestrator, "map-literature", inconsistent)

    assert not (tmp_path / "artifacts/literature-map.json").exists()


def test_method_promotion_rejects_invented_estimand_and_profile_references(
    tmp_path: Path,
) -> None:
    """Removing exact ref/profile resolution must make this test fail."""
    orchestrator = _ready_for_methods(tmp_path)
    invented: MethodCandidatesPayload = methods().model_copy(
        update={
            "estimand_ref": "not-the-persisted-estimand",
            "candidates": tuple(
                item.model_copy(
                    update={"method_profile_ref": f"nonexistent-{index}@999"}
                )
                for index, item in enumerate(methods().candidates, start=1)
            ),
        }
    )

    with pytest.raises(ValueError, match="estimand|profile"):
        submit(orchestrator, "rank-methods", invented)

    assert not (tmp_path / "artifacts/method-candidates.json").exists()


def test_method_compatibility_is_recomputed_from_current_data_capabilities(
    tmp_path: Path,
) -> None:
    """Trusting the worker's compatible boolean must fail this valid control."""
    orchestrator = _ready_for_parallel(tmp_path)
    submit(orchestrator, "map-literature", literature())
    candidate_value = safe_feasibility().candidates[0].model_dump()
    candidate_value["data_structures"] = ("panel",)
    candidate_value["available_features"] = (
        "donor_pool",
        "one_or_few_treated_units",
        "pre_treatment_periods",
        "treatment_timing_variation",
        "untreated_comparison",
    )
    feasibility = safe_feasibility().model_copy(
        update={"candidates": (DatasetCandidate.model_validate(candidate_value),)}
    )
    submit(
        orchestrator,
        "inspect-data",
        DataFeasibilityPayload.model_validate(dict(feasibility.__dict__)),
    )
    orchestrator.advance()
    submit(orchestrator, "define-estimand", estimand())
    orchestrator.advance()
    candidates = methods().model_copy(
        update={
            "estimand_ref": _artifact_token(orchestrator, "estimand-spec.yaml"),
            "candidates": tuple(
                item.model_copy(
                    update={
                        "method_profile_ref": (
                            f"{item.method_profile_ref.split('@')[0]}@0.2.0"
                        )
                    }
                )
                for item in methods().candidates
            ),
        }
    )
    submit(orchestrator, "rank-methods", candidates)
    assert (tmp_path / "artifacts/method-candidates.json").exists()


def test_worker_compatibility_claim_cannot_override_current_data_capabilities(
    tmp_path: Path,
) -> None:
    """Trusting estimand_compatible=True must make this impossible method pass."""
    orchestrator = _ready_for_parallel(tmp_path)
    submit(orchestrator, "map-literature", literature())
    limited = (
        safe_feasibility()
        .candidates[0]
        .model_copy(
            update={
                "data_structures": ("panel",),
                "available_features": (
                    "treatment_timing_variation",
                    "untreated_comparison",
                ),
            }
        )
    )
    submit(
        orchestrator,
        "inspect-data",
        safe_feasibility().model_copy(update={"candidates": (limited,)}),
    )
    orchestrator.advance()
    submit(orchestrator, "define-estimand", estimand())
    orchestrator.advance()
    token = _artifact_token(orchestrator, "estimand-spec.yaml")

    with pytest.raises(ValueError, match="estimand_compatible"):
        submit(orchestrator, "rank-methods", methods(token))


def test_estimand_and_memo_evidence_ids_must_resolve_to_current_literature(
    tmp_path: Path,
) -> None:
    """Skipping current evidence lookup must make both invented IDs promotable."""
    estimand_run = _ready_for_parallel(tmp_path / "estimand")
    submit(estimand_run, "map-literature", literature())
    submit(estimand_run, "inspect-data", safe_feasibility())
    estimand_run.advance()
    bad_estimand = estimand().model_copy(
        update={"evidence_refs": ("unrelated-evidence",)}
    )
    with pytest.raises(ValueError, match="evidence reference"):
        submit(estimand_run, "define-estimand", bad_estimand)

    memo_run = _ready_for_methods(tmp_path / "memo")
    token = _artifact_token(memo_run, "estimand-spec.yaml")
    submit(memo_run, "rank-methods", methods(token))
    memo_run.advance()
    bad_memo = memo_candidate(token)
    bad_memo["metadata"]["evidence_refs"] = ["unrelated-evidence"]  # type: ignore[index]
    with pytest.raises(ValueError, match="evidence reference"):
        submit(memo_run, "draft-identification", bad_memo)


def test_authenticated_queue_identity_enters_artifact_provenance(
    tmp_path: Path,
) -> None:
    """Replacing queue identity with a role label must fail this assertion."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    filename = "candidate-charters.json"
    source = tmp_path / "incoming" / filename
    source.parent.mkdir()
    source.write_text(candidate_payload().model_dump_json(), encoding="utf-8")
    order = orchestrator.queue.read_order("frame-charters")
    assert order.principal_assignment is not None
    orchestrator.queue.submit(
        "frame-charters", source, expected_order_hash=order.order_hash
    )

    orchestrator.accept_submission("frame-charters")

    artifact = orchestrator.lifecycle.read_artifact(
        Path("artifacts/candidate-charters.json")
    )
    assert artifact.envelope.producer == order.principal_assignment.producer


def test_review_critic_context_must_differ_from_reviewed_worker_contexts(
    tmp_path: Path,
) -> None:
    """Removing critic-context independence must make this submission succeed."""
    orchestrator, _ = _ready_for_review(tmp_path)
    review = DesignReviewPayload(review_id="review-shared-context", findings=())
    source = tmp_path / "incoming" / "design-review-findings.json"
    source.parent.mkdir(exist_ok=True)
    source.write_text(review.model_dump_json(), encoding="utf-8")
    reused = ProducerIdentity(
        component="critic-agent",
        version="1.0.0",
        model="model-a",
        runtime="runtime-a",
        context_id="fixture-context-draft-identification",
    )
    order = orchestrator.queue.read_order("review-design")
    with pytest.raises(ValueError, match="assigned principal"):
        orchestrator.queue.submit(
            "review-design",
            source,
            producer=reused,
            expected_order_hash=order.order_hash,
        )

    assert not (tmp_path / "artifacts/design-review-findings.json").exists()


def test_memo_and_plan_selections_keep_exact_current_continuity(tmp_path: Path) -> None:
    """Accepting invented memo or plan selections must fail at their promotions."""
    memo_run = _ready_for_methods(tmp_path / "memo")
    token = _artifact_token(memo_run, "estimand-spec.yaml")
    submit(memo_run, "rank-methods", methods(token))
    memo_run.advance()
    bad_memo = memo_candidate("another-nonexistent-estimand")
    bad_memo["metadata"]["primary_method_profile_ref"] = "unrelated@999"  # type: ignore[index]
    with pytest.raises(ValueError, match="estimand|method"):
        submit(memo_run, "draft-identification", bad_memo)
    assert not (memo_run.workspace / "artifacts/identification-memo.md").exists()

    plan_run, plan_token = _ready_for_review(tmp_path / "plan")
    submit(
        plan_run,
        "review-design",
        DesignReviewPayload(review_id="review-plan", findings=()),
    )
    plan_run.advance()
    bad_plan = plan("yet-another-estimand").model_copy(
        update={"primary_method_profile_ref": "totally-fabricated@0"}
    )
    with pytest.raises(ValueError, match="estimand|method"):
        submit(plan_run, "compose-plan", bad_plan)
    assert plan_token != "yet-another-estimand"
    assert not (plan_run.workspace / "artifacts/analysis-plan.yaml").exists()


def test_final_gate_rechecks_current_method_semantics_before_approval(
    tmp_path: Path,
) -> None:
    """Relying only on checkpoint/input drift must fail this semantic gate test."""
    from orchestrator_fixtures import ready_for_final_gate

    orchestrator = ready_for_final_gate(tmp_path)
    path = Path("artifacts/method-candidates.json")
    current = orchestrator.lifecycle.read_artifact(path)
    assert isinstance(current.payload, dict)
    forged = seal_artifact(
        ResearchArtifact(
            envelope=current.envelope.model_copy(update={"content_hash": None}),
            payload={**current.payload, "estimand_ref": "not-the-persisted-estimand"},
        )
    )
    orchestrator.lifecycle.store.write_structured(path, forged)
    approve(orchestrator, "final-gate", accepted_major_ids=[])

    with pytest.raises(
        ValueError, match="method candidates estimand_ref|artifact envelope lineage"
    ):
        orchestrator.advance()


def test_plan_estimand_type_and_embedded_estimand_match_current_artifact(
    tmp_path: Path,
) -> None:
    """Skipping current estimand semantics must admit a contradictory terminal plan."""
    mismatch, token = _ready_for_review(tmp_path / "mismatch")
    submit(
        mismatch,
        "review-design",
        DesignReviewPayload(review_id="review-mismatch", findings=()),
    )
    mismatch.advance()
    contradictory = plan(token).model_copy(
        update={
            "estimand_type": EstimandType.DESCRIPTIVE,
            "claim_mode": ClaimMode.DESCRIPTIVE,
        }
    )
    with pytest.raises(ValueError, match="estimand_type"):
        submit(mismatch, "compose-plan", contradictory)

    embedded, embedded_token = _ready_for_review(tmp_path / "embedded")
    submit(
        embedded,
        "review-design",
        DesignReviewPayload(review_id="review-embedded", findings=()),
    )
    embedded.advance()
    exact_embedded = plan(embedded_token).model_copy(update={"estimand": estimand()})
    submit(embedded, "compose-plan", exact_embedded)
    assert (embedded.workspace / "artifacts/analysis-plan.yaml").exists()


def test_critic_independence_fails_closed_for_unknown_worker_context() -> None:
    """Ignoring a reviewed worker's missing context must fail this contract."""
    critic = ProducerIdentity(
        component="critic-agent", version="1.0.0", context_id="critic-context"
    )
    unknown_worker = ProducerIdentity(component="filesystem-worker", version="1.0")
    with pytest.raises(ValueError, match="upstream.*context|reviewed.*context"):
        require_independent_critic(critic, (unknown_worker,))
    spoofed_internal = ProducerIdentity(component="human-gate-1", version="0.2.0")
    with pytest.raises(ValueError, match="upstream.*context|reviewed.*context"):
        require_independent_critic(critic, (spoofed_internal,))


def test_queue_worker_cannot_spoof_internal_component_to_omit_context(
    tmp_path: Path,
) -> None:
    orchestrator = _ready_for_parallel(tmp_path)
    source = tmp_path / "incoming" / "literature-map.json"
    source.parent.mkdir(exist_ok=True)
    source.write_text(literature().model_dump_json(), encoding="utf-8")
    order = orchestrator.queue.read_order("map-literature")
    with pytest.raises(ValueError, match="assigned principal"):
        orchestrator.queue.submit(
            "map-literature",
            source,
            producer=ProducerIdentity(
                component="human-gate-1",
                version="spoofed",
                context_id=None,
            ),
            expected_order_hash=order.order_hash,
        )
    assert orchestrator.queue.collect("map-literature") == ()
    orchestrator.queue.submit(
        "map-literature", source, expected_order_hash=order.order_hash
    )
    orchestrator.accept_submission("map-literature")
    assert (tmp_path / "artifacts/literature-map.json").exists()


def test_strict_final_validation_requires_current_passing_citation_report(
    tmp_path: Path,
) -> None:
    """An enabled citation gate must reject final approval without its report."""
    from orchestrator_fixtures import ready_for_final_gate

    case_root = write_case(tmp_path / "blind-case")
    orchestrator = ready_for_final_gate(
        tmp_path,
        require_claim_verified_citations=True,
        citation_catalog_roots=(case_root,),
    )

    with pytest.raises(ValueError, match="citation integrity report"):
        orchestrator.semantics.validate_final()

    plan_ref = orchestrator.lifecycle.artifact_ref(Path("artifacts/analysis-plan.yaml"))
    report = CitationIntegrityReport(
        findings=(),
        passed=True,
        validator_version="claim-integrity-v1",
        source_sheet_refs=(plan_ref,),
        claim_fact_map_refs=(plan_ref,),
        blinded_brief_refs=(plan_ref,),
        accepted_artifact_refs=(plan_ref,),
        binding_sha256="a" * 64,
    )
    orchestrator.lifecycle.persist_structured(
        Path("artifacts/citation-integrity-report.json"),
        report_payload(report),
        "citation-integrity-validator",
        (plan_ref,),
    )
    with pytest.raises(ValueError, match="binding"):
        orchestrator.semantics.validate_final()

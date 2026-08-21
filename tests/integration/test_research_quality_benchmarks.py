"""Research-quality scoring and complete offline design replay acceptance."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from envresearch.benchmarks import design_replay, design_scenarios
from envresearch.benchmarks.design_inventory import authoritative_inventory
from envresearch.benchmarks.design_registry import (
    DesignBenchmarkManifest,
    DesignBenchmarkRegistry,
    replay_design_fixture,
)
from envresearch.benchmarks.design_scoring import (
    RESEARCH_QUALITY_DIMENSIONS,
    RESEARCH_QUALITY_RUBRIC_VERSION,
    ResearchQualityScorer,
)
from envresearch.models.design import (
    DesignFinding,
    DesignReviewPayload,
    MethodCandidate,
    MethodCandidateRole,
    ReviewSeverity,
)
from envresearch.models.method_screening import (
    MethodRejectionEvidence,
    MethodRequirementKind,
)
from envresearch.research.workflow import ResearchRunPhase

FIXTURE_ROOT = Path("benchmarks/design/fixtures")
COMPLETE_FIXTURES = (
    "broad-topic",
    "structured-brief",
    "structured-connector-outage",
    "restricted-data",
    "connector-outage",
    "blocking-review",
    "interrupted-run",
)


def _rejected_rdd(
    *limitations: str,
    requirement_refs: tuple[str, ...] = ("known_cutoff", "running_variable"),
) -> MethodCandidate:
    return MethodCandidate(
        method_profile_ref="rdd@0.2.0",
        role=MethodCandidateRole.REJECTED,
        rank=3,
        estimand_compatible=False,
        required_assumption_refs=("assumption-local-continuity",),
        required_data_structure=(
            "cross-section with known cutoff threshold and running variable",
        ),
        diagnostics=("density and covariate continuity diagnostics",),
        fallback_or_limitations=limitations,
        rejection_evidence=MethodRejectionEvidence(
            requirement_kind=MethodRequirementKind.FEATURE_SET,
            requirement_refs=requirement_refs,
            explanation=limitations[0],
        ),
    )


def test_registry_requires_the_versioned_six_dimension_rubric() -> None:
    catalog = DesignBenchmarkRegistry.discover(FIXTURE_ROOT)

    for manifest in catalog.values():
        assert manifest.rubric_version == RESEARCH_QUALITY_RUBRIC_VERSION
        assert tuple(manifest.rubric_thresholds) == RESEARCH_QUALITY_DIMENSIONS
        assert manifest.replay_fixture == Path("replay.yaml")


def test_v02_design_manifest_still_rejects_tier2_execution() -> None:
    manifest = DesignBenchmarkRegistry.discover(FIXTURE_ROOT)["broad-topic"]

    with pytest.raises(ValueError, match="Tier 2 is not allowed in v0.2"):
        DesignBenchmarkManifest.model_validate({**manifest.model_dump(), "tier": 2})


def test_replay_scenario_contract_is_consumed(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT / "broad-topic", fixture)
    replay_path = fixture / "replay.yaml"
    payload = yaml.safe_load(replay_path.read_text(encoding="utf-8"))
    payload["scenario"] = "connector_degradation"
    replay_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    result = replay_design_fixture(fixture)

    assert result.connector_coverage is not None
    assert result.connector_coverage.status == "degraded"
    assert Path("connector-receipts/local-export-unavailable.json") in (
        result.unexpected_authoritative_files
    )


@pytest.mark.parametrize("mutation", ("missing", "arbitrary"))
def test_registry_rejects_missing_or_arbitrary_rubric_keys(
    tmp_path: Path, mutation: str
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT / "broad-topic", fixture)
    manifest_path = fixture / "benchmark.yaml"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        payload["rubric_thresholds"].pop(RESEARCH_QUALITY_DIMENSIONS[-1])
    else:
        payload["rubric_thresholds"]["auditability"] = 5
    manifest_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly the six research-quality"):
        DesignBenchmarkRegistry.discover(fixture)


@pytest.mark.parametrize("fixture", COMPLETE_FIXTURES)
def test_offline_fixture_recovers_a_scored_approved_plan(fixture: str) -> None:
    result = replay_design_fixture(FIXTURE_ROOT / fixture)

    assert result.actual_phase is ResearchRunPhase.COMPLETE
    assert result.quality_scores is not None
    assert set(result.quality_scores.model_dump()) == set(RESEARCH_QUALITY_DIMENSIONS)
    assert all(item.passed for item in result.threshold_results)
    assert result.open_blockers == ()
    assert result.overall_pass is True
    assert Path("research-run-manifest.json") in result.actual_authoritative_files
    assert Path("decision-log.jsonl") in result.actual_authoritative_files
    assert Path("artifacts/analysis-plan.yaml") in result.actual_authoritative_files


def test_existing_design_replay_json_remains_backward_compatible() -> None:
    result = replay_design_fixture(FIXTURE_ROOT / "broad-topic")

    assert result.blind_case_evaluation is None
    assert "blind_case_evaluation" not in result.model_dump(mode="json")
    assert result.overall_pass is True


def test_blocking_review_replay_uses_durable_revision_state() -> None:
    result = replay_design_fixture(FIXTURE_ROOT / "blocking-review")

    assert result.overall_pass is True
    assert Path("revisions/journal.jsonl") in result.actual_authoritative_files
    assert any("superseded" in path.parts for path in result.actual_authoritative_files)


def test_inventory_keeps_repeated_dynamic_transactions_distinct(
    tmp_path: Path,
) -> None:
    for identity in ("a" * 64, "b" * 64):
        directory = (
            tmp_path
            / "node-checkpoints/superseded"
            / f"research.node.invalidated.{identity}"
        )
        directory.mkdir(parents=True)
        (directory / "compose-plan.json").write_text("{}", encoding="utf-8")

    inventory = authoritative_inventory(tmp_path)

    assert inventory == (
        Path(
            "node-checkpoints/superseded/"
            "{invalidation-compose-plan-1}/compose-plan.json"
        ),
        Path(
            "node-checkpoints/superseded/"
            "{invalidation-compose-plan-2}/compose-plan.json"
        ),
    )


def test_inventory_preserves_multi_node_invalidation_directory_grouping(
    tmp_path: Path,
) -> None:
    directory = (
        tmp_path
        / "node-checkpoints/superseded"
        / f"research.node.invalidated.{'c' * 64}"
    )
    directory.mkdir(parents=True)
    for node in ("compose-plan", "review-design"):
        (directory / f"{node}.json").write_text("{}", encoding="utf-8")

    inventory = authoritative_inventory(tmp_path)

    transaction = "{invalidation-compose-plan+review-design-1}"
    assert inventory == (
        Path(f"node-checkpoints/superseded/{transaction}/compose-plan.json"),
        Path(f"node-checkpoints/superseded/{transaction}/review-design.json"),
    )


def test_quality_gate_fails_when_a_scoring_dimension_is_mutated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ResearchQualityScorer,
        "_score_uncertainty_disclosure",
        lambda _self: 2,
    )

    result = replay_design_fixture(FIXTURE_ROOT / "broad-topic")

    assert result.quality_scores.uncertainty_disclosure == 2
    assert result.overall_pass is False
    assert "uncertainty_disclosure" in {
        item.dimension for item in result.threshold_results if not item.passed
    }


@pytest.mark.parametrize(
    "rationale",
    (
        "Current data do not include a known cutoff or running variable.",
        "The local continuity assumption is violated.",
        "No threshold exists.",
    ),
)
def test_quality_gate_rewards_an_explicitly_rejected_incompatible_method(
    monkeypatch: pytest.MonkeyPatch, rationale: str
) -> None:
    """Transparent screening must not penalize a compatible retained design."""
    original = design_scenarios.methods

    def methods_with_honest_rejection(estimand_ref: str) -> object:
        methods = original(estimand_ref)
        rejected = _rejected_rdd(rationale)
        return methods.model_copy(
            update={"candidates": (*methods.candidates, rejected)}
        )

    monkeypatch.setattr(design_scenarios, "methods", methods_with_honest_rejection)

    result = replay_design_fixture(FIXTURE_ROOT / "broad-topic")

    assert result.quality_scores.identification_credibility == 5
    assert result.overall_pass is True


def test_quality_gate_rejects_a_weak_method_rejection_rationale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic selection label is not a scientific incompatibility reason."""
    original = design_scenarios.methods

    def methods_with_weak_rejection(estimand_ref: str) -> object:
        methods = original(estimand_ref)
        rejected = _rejected_rdd(
            "Threshold not selected.", requirement_refs=("donor_pool",)
        )
        return methods.model_copy(
            update={"candidates": (*methods.candidates, rejected)}
        )

    monkeypatch.setattr(design_scenarios, "methods", methods_with_weak_rejection)

    with pytest.raises(ValueError, match="rejection evidence"):
        replay_design_fixture(FIXTURE_ROOT / "broad-topic")


@pytest.mark.parametrize("retained_index", (0, 1))
def test_replay_rejects_an_incompatible_retained_method(
    monkeypatch: pytest.MonkeyPatch, retained_index: int
) -> None:
    """Primary and alternative roles cannot self-assert incompatible RDD support."""
    original = design_scenarios.methods

    def methods_with_false_compatibility(estimand_ref: str) -> object:
        methods = original(estimand_ref)
        retained = methods.candidates[retained_index].model_copy(
            update={"method_profile_ref": "rdd@0.2.0"}
        )
        candidates = list(methods.candidates)
        candidates[retained_index] = retained
        return methods.model_copy(update={"candidates": tuple(candidates)})

    monkeypatch.setattr(design_scenarios, "methods", methods_with_false_compatibility)

    with pytest.raises(ValueError, match="estimand_compatible"):
        replay_design_fixture(FIXTURE_ROOT / "broad-topic")


@pytest.mark.parametrize(
    "updates",
    (
        {"estimand_compatible": True},
        {"fallback_or_limitations": ()},
        {"rejection_evidence": None},
    ),
)
def test_rejected_method_requires_incompatibility_and_a_rationale(
    updates: dict[str, object],
) -> None:
    """Compatible or unexplained rejections fail the durable candidate schema."""
    candidate = _rejected_rdd(
        "Current data do not include a known cutoff or running variable."
    ).model_dump(mode="json")

    with pytest.raises(ValidationError):
        MethodCandidate.model_validate({**candidate, **updates})


def test_quality_gate_fails_when_recovered_review_has_an_open_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocker = DesignFinding(
        finding_id="blocking-mutation",
        severity=ReviewSeverity.BLOCKING,
        resolved=False,
        finding="The comparison is not credible.",
        evidence_refs=("evidence-1",),
        remediation="Revise the comparison design.",
    )
    monkeypatch.setattr(
        ResearchQualityScorer,
        "_review",
        lambda _self: DesignReviewPayload(
            review_id="mutated-review", findings=(blocker,)
        ),
    )

    result = replay_design_fixture(FIXTURE_ROOT / "broad-topic")

    assert result.open_blockers == ("blocking-mutation",)
    assert result.overall_pass is False


def test_manifest_cannot_supply_self_reported_quality_scores(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT / "broad-topic", fixture)
    manifest_path = fixture / "benchmark.yaml"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    payload["quality_scores"] = {
        dimension: 5 for dimension in RESEARCH_QUALITY_DIMENSIONS
    }
    manifest_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="quality_scores"):
        DesignBenchmarkRegistry.discover(fixture)


def test_replay_rejects_mutated_cross_artifact_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = design_scenarios.methods

    def wrong_estimand(_estimand_ref: str) -> object:
        return original("artifact:invented@999#sha256:" + "0" * 64)

    monkeypatch.setattr(design_scenarios, "methods", wrong_estimand)

    with pytest.raises(ValueError, match="current artifact"):
        replay_design_fixture(FIXTURE_ROOT / "broad-topic")


def test_restricted_score_fails_if_approval_omits_current_risk_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(design_replay, "data_risk_reasons", lambda *_args: ())

    result = replay_design_fixture(FIXTURE_ROOT / "restricted-data")

    assert result.quality_scores.data_feasibility == 2
    assert result.overall_pass is False

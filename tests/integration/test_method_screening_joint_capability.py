"""Joint-capability method rejection benchmark regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from envresearch.benchmarks import design_scenarios
from envresearch.benchmarks.design_registry import replay_design_fixture
from envresearch.models.design import MethodCandidate, MethodCandidateRole
from envresearch.models.evidence import DataFeasibilityPayload
from envresearch.models.method_screening import (
    MethodRejectionEvidence,
    MethodRequirementKind,
)

FIXTURE_ROOT = Path("benchmarks/design/fixtures")


def _rejected_rdd() -> MethodCandidate:
    explanation = "No suitable dataset contains the complete RDD feature set."
    return MethodCandidate(
        method_profile_ref="rdd@0.2.0",
        role=MethodCandidateRole.REJECTED,
        rank=3,
        estimand_compatible=False,
        required_assumption_refs=("assumption-local-continuity",),
        required_data_structure=("panel with cutoff and running variable",),
        diagnostics=("density and covariate continuity diagnostics",),
        fallback_or_limitations=(explanation,),
        rejection_evidence=MethodRejectionEvidence(
            requirement_kind=MethodRequirementKind.FEATURE_SET,
            requirement_refs=("known_cutoff", "running_variable"),
            explanation=explanation,
        ),
    )


def test_rejection_evidence_handles_features_split_across_suitable_datasets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Joint capability is absent when no one dataset has the complete feature set."""
    original_methods = design_scenarios.methods
    original_feasibility = design_scenarios.feasibility

    def split_feature_feasibility(*, restricted: bool = False) -> object:
        feasibility = original_feasibility(restricted=restricted)
        base = feasibility.candidates[0]
        first = base.model_copy(
            update={
                "dataset_id": "local-air-cutoff",
                "available_features": (*base.available_features, "known_cutoff"),
            }
        )
        second = base.model_copy(
            update={
                "dataset_id": "local-air-running-variable",
                "available_features": (*base.available_features, "running_variable"),
            }
        )
        return DataFeasibilityPayload(
            research_design=feasibility.research_design,
            candidates=(first, second),
            recommendation=feasibility.recommendation,
            evidence_reason=feasibility.evidence_reason,
        )

    def methods_with_joint_rejection(estimand_ref: str) -> object:
        methods = original_methods(estimand_ref)
        return methods.model_copy(
            update={"candidates": (*methods.candidates, _rejected_rdd())}
        )

    monkeypatch.setattr(design_scenarios, "feasibility", split_feature_feasibility)
    monkeypatch.setattr(design_scenarios, "methods", methods_with_joint_rejection)

    result = replay_design_fixture(FIXTURE_ROOT / "broad-topic")

    assert result.quality_scores.identification_credibility == 5
    assert result.overall_pass is True

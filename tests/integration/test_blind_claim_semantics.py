"""Fail-closed integration checks for blind fact-reference semantics."""

from pathlib import Path

import pytest
from test_blind_workflow import ready_for_recommendation, valid_recommendation

from envresearch.benchmarks.blind_workflow import BlindEvaluationController
from envresearch.benchmarks.claim_report import report_from_payload
from envresearch.models.benchmark_claims import ClaimFactMap
from envresearch.research.order_policy import blind_claim_usages


def _mapping(controller: BlindEvaluationController) -> ClaimFactMap:
    service = controller.artifacts
    case_id = controller.case_id
    return service.lifecycle.read_payload(
        service.paths(case_id).claim_fact_map, ClaimFactMap
    )


def test_blind_claim_usages_bind_only_structured_fact_leaves(
    tmp_path: Path,
) -> None:
    """Prose and structural digests must not fabricate claim usages."""
    controller = ready_for_recommendation(tmp_path)
    payload = valid_recommendation(controller).model_dump(mode="json")

    usages = blind_claim_usages(payload, _mapping(controller))

    assert tuple((usage.json_pointer, usage.claim_id) for usage in usages) == (
        ("/fact_refs/0", "claim-001"),
    )


@pytest.mark.parametrize(
    "fact_refs",
    ((), ("fact-001", "fact-001"), ("fact-999",)),
)
def test_blind_claim_usages_reject_missing_duplicate_or_unmapped_fact(
    tmp_path: Path, fact_refs: tuple[str, ...]
) -> None:
    """Every fact leaf must resolve one unique current mapping."""
    controller = ready_for_recommendation(tmp_path)

    with pytest.raises(ValueError, match="citation integrity"):
        blind_claim_usages(
            {"fact_refs": fact_refs},
            _mapping(controller),
        )


def test_invalid_blind_prose_leaves_no_recommendation_generation(
    tmp_path: Path,
) -> None:
    """Semantic rejection must happen before queue submission and promotion."""
    controller = ready_for_recommendation(tmp_path)
    invalid = valid_recommendation(controller).model_copy(
        update={
            "estimand_interpretation": (
                "This intervention increased mortality by 999 percent."
            )
        }
    )
    paths = controller.artifacts.paths(controller.case_id)

    with pytest.raises(ValueError, match="citation integrity"):
        controller.accept_recommendation(invalid)

    assert not (controller.run_root / paths.recommendation).exists()
    assert not (controller.run_root / paths.citation_report).exists()


def test_valid_recommendation_publishes_current_v2_citation_lineage(
    tmp_path: Path,
) -> None:
    """The actual Task 9 handoff must publish the exact fact-only report."""
    controller = ready_for_recommendation(tmp_path)

    recommendation_ref = controller.accept_recommendation(
        valid_recommendation(controller)
    )
    paths = controller.artifacts.paths(controller.case_id)
    report = report_from_payload(
        controller.artifacts.lifecycle.read_artifact(paths.citation_report).payload
    )

    assert recommendation_ref == controller.artifacts.ref(
        controller.case_id, "recommendation"
    )
    assert report.passed is True
    assert report.validator_version == "blind-claim-integrity-v2"
    assert report.accepted_artifact_refs == (recommendation_ref,)

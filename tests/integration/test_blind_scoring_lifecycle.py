"""Task 10 scoring reads only authenticated Task 8/9 durable state."""

from __future__ import annotations

from pathlib import Path

import pytest
from blind_artifact_helpers import expert_sheet, source_sheet
from blind_signing_helpers import enroll_controller, signed_candidate
from test_blind_registry_security import write_case
from test_blind_workflow import _score_for, valid_recommendation

from envresearch.benchmarks.blind_scoring import BlindScorer
from envresearch.benchmarks.blind_workflow import BlindEvaluationController
from envresearch.models.benchmark_evaluation import AdjudicationVerdict
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.principal import PrincipalKind
from envresearch.workers import WorkerRole


def _controller(tmp_path: Path) -> BlindEvaluationController:
    controller = BlindEvaluationController.from_case(
        write_case(tmp_path / "case"), tmp_path / "run"
    )
    enroll_controller(controller)
    controller.replay_calibration()
    controller.accept_recommendation(valid_recommendation(controller))
    controller.issue_expert_orders()
    return controller


def _complete_adjudication(
    controller: BlindEvaluationController,
) -> tuple[object, object]:
    controller.accept_expert_score(1, _score_for(controller, 1))
    controller.accept_expert_score(2, _score_for(controller, 2, conflict=True))
    controller.issue_adjudication_order()
    assert controller.adjudicator_queue is not None
    blind_order = controller.adjudicator_queue.read_order("adjudicator-score")
    assignment = blind_order.principal_assignment
    assert assignment is not None
    expert_refs = {
        controller.artifacts.ref(controller.case_id, "expert_one"),
        controller.artifacts.ref(controller.case_id, "expert_two"),
    }
    assert expert_refs.isdisjoint(blind_order.input_artifacts)
    third = expert_sheet(blind_order.input_artifacts[1], assignment.principal_id)
    third_ref = controller.accept_adjudication(
        signed_candidate(
            controller, blind_order, third, PrincipalKind.ADJUDICATOR, 1
        )
    )
    final_order = controller.adjudicator_queue.read_order("adjudicate")
    assert final_order.input_artifacts == (
        controller.artifacts.ref(controller.case_id, "recommendation"),
        controller.artifacts.ref(controller.case_id, "expert_one"),
        controller.artifacts.ref(controller.case_id, "expert_two"),
        third_ref,
    )
    verdict = AdjudicationVerdict(
        score_sheet_ref=controller.artifacts.ref(controller.case_id, "expert_one"),
        verdict="accept",
        rationale="The durable third score resolves the disagreement.",
        adjudicator_principal=assignment.principal_id,
    )
    final_ref = controller.accept_adjudication(
        signed_candidate(
            controller, final_order, verdict, PrincipalKind.ADJUDICATOR, 1
        )
    )
    return third_ref, final_ref


def test_scorer_reads_current_expert_artifacts_and_authenticated_receipts(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    controller.accept_expert_score(1, _score_for(controller, 1))
    controller.accept_expert_score(2, _score_for(controller, 2))
    adjudicator_exchange = controller.run_root / "exchanges/adjudicator"
    assert not adjudicator_exchange.exists()

    result = BlindScorer.from_case(
        controller.artifacts, controller.case_id
    ).evaluate_case()

    assert result.passed is True
    assert result.requires_adjudication is False
    assert not adjudicator_exchange.exists()
    assert tuple(item.score_sheet_ref for item in result.original_score_artifacts) == (
        controller.artifacts.ref(controller.case_id, "expert_one"),
        controller.artifacts.ref(controller.case_id, "expert_two"),
    )
    with pytest.raises(TypeError):
        BlindScorer.from_case(controller.artifacts, controller.case_id).evaluate_case(
            result.original_score_artifacts[0]
        )


def test_signed_evidence_is_a_current_exact_lifecycle_input(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.accept_expert_score(1, _score_for(controller, 1))
    paths = controller.artifacts.paths(controller.case_id)

    evidence_ref = controller.artifacts.ref(controller.case_id, "expert_one_evidence")
    assert evidence_ref == controller.artifacts.lifecycle.validated_history_ref(
        paths.expert_one_evidence
    )
    assert controller.artifacts.lifecycle.current_envelope(
        paths.expert_one
    ).input_artifacts[-1] == evidence_ref


def test_tampered_signed_lifecycle_evidence_cannot_score(tmp_path: Path) -> None:
    import json

    controller = _controller(tmp_path)
    controller.accept_expert_score(1, _score_for(controller, 1))
    controller.accept_expert_score(2, _score_for(controller, 2))
    path = controller.artifacts.paths(controller.case_id).expert_one_evidence
    current = controller.run_root / path
    document = json.loads(current.read_text(encoding="utf-8"))
    signature = document["payload"]["signature"]
    replacement = "B" if signature[0] == "A" else "A"
    document["payload"]["signature"] = replacement + signature[1:]
    assert document["payload"]["signature"] != signature
    current.write_text(
        json.dumps(document, separators=(",", ":"), sort_keys=True), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="artifact|evidence|sealed|hash"):
        BlindScorer.from_case(
            controller.artifacts, controller.case_id
        ).evaluate_case()


def test_direct_score_publication_requires_signed_evidence(tmp_path: Path) -> None:
    from blind_artifact_helpers import CaseHarness, expert_sheet

    harness = CaseHarness(tmp_path)
    harness.through_recommendation()
    expert = harness.human(PrincipalKind.EXPERT, 1)

    with pytest.raises(ValueError, match="signed.*evidence"):
        harness.service.publish_expert_score(
            "case-rct",
            expert_sheet(
                harness.service.ref("case-rct", "recommendation"),
                expert.principal_id,
            ),
            expert,
            slot=1,
        )


def test_third_and_final_are_current_sealed_artifacts_bound_to_exact_orders(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    third_ref, final_ref = _complete_adjudication(controller)
    paths = controller.artifacts.paths(controller.case_id)

    assert third_ref == controller.artifacts.lifecycle.validated_history_ref(
        paths.third_score
    )
    assert final_ref == controller.artifacts.lifecycle.validated_history_ref(
        paths.adjudication
    )
    final_inputs = controller.artifacts.lifecycle.current_envelope(
        paths.adjudication
    ).input_artifacts
    assert final_inputs[:-1] == controller.adjudicator_queue.read_order(
        "adjudicate"
    ).input_artifacts
    assert final_inputs[-1] == controller.artifacts.ref(
        controller.case_id, "adjudication_evidence"
    )

    restarted = BlindEvaluationController(controller.loaded, controller.run_root)
    result = BlindScorer.from_case(
        restarted.artifacts, restarted.case_id
    ).evaluate_case()
    assert result.passed is True
    assert result.adjudication is not None
    assert result.adjudication.third_score.score.score_sheet_ref == third_ref
    assert result.adjudication.verdict_ref == final_ref


def test_revision_supersedes_third_and_final_and_old_state_cannot_score(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    _complete_adjudication(controller)
    paths = controller.artifacts.paths(controller.case_id)

    controller.revise_source(
        source_sheet("pilot-001", generation=2), revision_id="score-r2"
    )

    assert controller.artifacts.lifecycle.current_envelope(
        paths.third_score
    ).validation_status is ArtifactLifecycle.SUPERSEDED
    assert controller.artifacts.lifecycle.current_envelope(
        paths.adjudication
    ).validation_status is ArtifactLifecycle.SUPERSEDED
    for path in (
        paths.expert_one_evidence,
        paths.expert_two_evidence,
        paths.third_score_evidence,
        paths.adjudication_evidence,
    ):
        assert controller.artifacts.lifecycle.current_envelope(
            path
        ).validation_status is ArtifactLifecycle.SUPERSEDED
    with pytest.raises(ValueError, match="current|validated|generation|order"):
        BlindScorer.from_case(controller.artifacts, controller.case_id).evaluate_case()


@pytest.mark.parametrize("attack", ("expert-input", "wrong-capability", "wrong-id"))
def test_scorer_rejects_forged_initial_adjudicator_order(
    tmp_path: Path, attack: str
) -> None:
    controller = _controller(tmp_path)
    controller.accept_expert_score(1, _score_for(controller, 1))
    controller.accept_expert_score(2, _score_for(controller, 2, conflict=True))
    queue = controller._queue("adjudicator", controller.case_id)
    controller.adjudicator_queue = queue
    adjudicator = controller._human(PrincipalKind.ADJUDICATOR, 1)
    assignment = (
        controller._human(PrincipalKind.EXPERT, 1)
        if attack == "wrong-capability"
        else adjudicator
    )
    paths = controller.artifacts.paths(controller.case_id)
    inputs = (
        controller.artifacts.ref(controller.case_id, "blinded_brief"),
        controller.artifacts.ref(controller.case_id, "recommendation"),
        controller.artifacts.lifecycle.artifact_ref(
            paths.source_sheet.parent / "expert-rubric.json"
        ),
    )
    if attack == "expert-input":
        inputs = (*inputs, controller.artifacts.ref(controller.case_id, "expert_one"))
    order_id = "forged-adjudicator-score" if attack == "wrong-id" else "adjudicator-score"
    order = controller._order(
        order_id,
        WorkerRole.BENCHMARK_ADJUDICATOR,
        inputs,
        "envresearch.ExpertScoreSheet",
        "adjudicator-score.yaml",
        assignment,
    )
    queue.issue(order)
    controller._submit_payload(
        queue,
        order_id,
        expert_sheet(
            controller.artifacts.ref(controller.case_id, "recommendation"),
            assignment.principal_id,
        ),
    )

    with pytest.raises(ValueError, match="contract|principal|generation|capability"):
        BlindScorer.from_case(controller.artifacts, controller.case_id).evaluate_case()


def test_scorer_rejects_unsealed_final_verdict_artifact(tmp_path: Path) -> None:
    import yaml

    controller = _controller(tmp_path)
    _complete_adjudication(controller)
    path = controller.artifacts.paths(controller.case_id).adjudication
    current = controller.run_root / path
    document = yaml.safe_load(current.read_text(encoding="utf-8"))
    document["envelope"]["content_hash"] = None
    current.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match="unsealed"):
        BlindScorer.from_case(controller.artifacts, controller.case_id).evaluate_case()

"""Offline blind evaluation keeps every worker inside its disclosure boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from blind_artifact_helpers import expert_sheet, source_sheet
from blind_signing_helpers import enroll_controller, signed_candidate
from blind_workflow_helpers import (
    raw_score_for as _raw_score_for,
)
from blind_workflow_helpers import (
    ready_for_expert_scoring,
    ready_for_recommendation,
    valid_recommendation,
)
from blind_workflow_helpers import (
    score_for as _score_for,
)
from test_blind_registry_security import write_case

from envresearch.benchmarks.blind_workflow import (
    BlindEvaluationController,
    build_blind_graph,
)
from envresearch.models.benchmark_evaluation import AdjudicationVerdict
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.principal import PrincipalKind
from envresearch.workers import WorkerRole

__all__ = [
    "_raw_score_for",
    "_score_for",
    "ready_for_expert_scoring",
    "ready_for_recommendation",
    "valid_recommendation",
]


def test_blind_graph_reuses_citation_node_and_declares_parallel_experts() -> None:
    """Dropping citation or joining expert branches would weaken the workflow."""
    graph = build_blind_graph("pilot-001")

    assert tuple(node.node_id for node in graph.nodes) == (
        "curate-source",
        "mask-brief",
        "validate-leakage",
        "recommend-method",
        "verify-citations",
        "expert-score-1",
        "expert-score-2",
    )
    assert graph.nodes[4].dependencies == ("recommend-method",)
    assert graph.nodes[5].dependencies == ("recommend-method",)
    assert graph.nodes[6].dependencies == ("recommend-method",)


def test_recommendation_order_exposes_only_three_allowed_inputs(
    tmp_path: Path,
) -> None:
    """Adding any curator-side artifact to the order breaks the blind boundary."""
    controller = ready_for_recommendation(tmp_path)
    order = controller.queue.read_order("recommend-method")

    assert order.role is WorkerRole.BENCHMARK_RECOMMENDER
    assert order.input_artifacts == controller.allowed_recommendation_refs()
    assert "No network or connector access" in order.policy_constraints
    assert len(order.input_artifacts) == 3
    assert all("source-sheet" not in ref.artifact_id for ref in order.input_artifacts)
    assert controller.recommender_workspace_files() == (
        "blinded-brief.yaml",
        "leakage-report.yaml",
        "method-profiles.json",
        "work-order.json",
    )
    assert controller.queue.require_producer_context is True
    assert not controller.queue.control_root.is_relative_to(controller.queue.root)


def test_recommender_workspace_has_no_curator_side_channel(tmp_path: Path) -> None:
    """Identity, unrestricted paths, and hidden artifacts must not enter isolation."""
    controller = ready_for_recommendation(tmp_path)
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(controller.recommender_workspace.iterdir())
    ).casefold()

    for forbidden in (
        "source",
        "curator-source-sheet",
        "claim-fact-map",
        "zotero",
        str(controller.run_root).casefold(),
        "https://",
        "http://",
    ):
        assert forbidden not in text
    assert not [
        path for path in controller.recommender_workspace.rglob("*") if path.is_dir()
    ]


def test_recommender_handoff_uses_opaque_case_identity_everywhere(
    tmp_path: Path,
) -> None:
    """The emitted brief, order, principal, queues, and paths share one opaque ID."""
    opaque_id = "pilot-case-001"
    source_facing_id = "pilot-rct-energy-feedback"
    case = write_case(tmp_path / "case", case_id=opaque_id)
    controller = BlindEvaluationController.from_case(case, tmp_path / "run")

    enroll_controller(controller)
    controller.replay_calibration()

    visible = "\n".join(
        (
            json.dumps(controller._public_brief(), sort_keys=True),
            controller.queue.read_order("recommend-method").model_dump_json(),
            str(controller.recommender_workspace),
            str(controller.queue.root),
            str(controller.queue.control_root),
            *(
                path.read_text(encoding="utf-8")
                for path in sorted(controller.recommender_workspace.iterdir())
            ),
        )
    ).casefold()

    assert opaque_id in visible
    assert source_facing_id not in visible


def test_recommender_submission_with_source_ref_fails(tmp_path: Path) -> None:
    """A valid model with a curator ref must fail before queue publication."""
    controller = ready_for_recommendation(tmp_path)
    forged = valid_recommendation(controller).model_copy(
        update={"blinded_brief_ref": controller.source_ref()}
    )

    with pytest.raises(ValueError, match="prohibited recommendation input"):
        controller.accept_recommendation(forged)


def test_experts_cannot_see_identity_or_each_others_scores(tmp_path: Path) -> None:
    """Each expert starts from the same frozen inputs in a different empty root."""
    controller = ready_for_expert_scoring(tmp_path)
    controller.issue_expert_orders()
    expected = (
        "blinded-brief.yaml",
        "expert-rubric.yaml",
        "method-recommendation.yaml",
        "work-order.json",
    )

    assert controller.expert_workspace_files(1) == expected
    assert controller.expert_workspace_files(2) == expected
    controller.accept_expert_score(1, _score_for(controller, 1))
    assert "expert-score.yaml" in controller.expert_workspace_files(1)
    assert "expert-score.yaml" not in controller.expert_workspace_files(2)
    assert not any(
        "score-1" in path.name for path in controller.expert_workspaces[2].rglob("*")
    )


def test_adjudicator_is_created_only_after_trigger_and_locks_blind_score_first(
    tmp_path: Path,
) -> None:
    """Disagreement reasons cannot influence the independent third score."""
    controller = ready_for_expert_scoring(tmp_path)
    with pytest.raises(ValueError, match="two expert scores"):
        controller.issue_adjudication_order()
    assert not controller.adjudicator_workspace.exists()

    controller.issue_expert_orders()
    controller.accept_expert_score(1, _score_for(controller, 1))
    controller.accept_expert_score(2, _score_for(controller, 2, conflict=True))
    controller.issue_adjudication_order()
    assert (
        "disagreement-rationales.json" not in controller.adjudicator_workspace_files()
    )

    order = controller.adjudicator_queue.read_order("adjudicator-score")
    assignment = order.principal_assignment
    assert assignment is not None
    third = expert_sheet(order.input_artifacts[1], assignment.principal_id)
    third_ref = controller.accept_adjudication(
        signed_candidate(controller, order, third, PrincipalKind.ADJUDICATOR, 1)
    )
    assert third_ref.artifact_id == "adjudicator-score"
    assert "disagreement-rationales.json" in controller.adjudicator_workspace_files()

    expert_one_ref = controller.artifacts.ref(controller.case_id, "expert_one")
    verdict = AdjudicationVerdict(
        score_sheet_ref=expert_one_ref,
        verdict="accept",
        rationale="The locked blind score resolves the disagreement.",
        adjudicator_principal=assignment.principal_id,
    )
    final_order = controller.adjudicator_queue.read_order("adjudicate")
    controller.accept_adjudication(
        signed_candidate(
            controller, final_order, verdict, PrincipalKind.ADJUDICATOR, 1
        )
    )
    assert controller.status().adjudication_completed is True


def test_source_revision_invalidates_descendants_and_archives_stale_orders(
    tmp_path: Path,
) -> None:
    """A source generation change must revoke every issued downstream generation."""
    controller = ready_for_expert_scoring(tmp_path)
    controller.issue_expert_orders()
    prior = controller.queue.read_order("recommend-method")

    controller.revise_source(
        source_sheet("pilot-001", generation=2), revision_id="source-r2"
    )

    status = controller.status()
    assert set(status.stale_nodes) >= {
        "mask-brief",
        "validate-leakage",
        "recommend-method",
        "verify-citations",
    }
    assert not controller.queue.has_generation("recommend-method")
    paths = controller.artifacts.paths(controller.case_id)
    assert (
        controller.artifacts.lifecycle.current_envelope(
            paths.recommendation
        ).validation_status
        is ArtifactLifecycle.SUPERSEDED
    )
    archived = controller.queue.exchange.read_file(
        Path("revisions/source-r2/worker/work-orders/recommend-method.json"),
        description="archived recommendation order",
    )
    assert json.loads(archived)["order_hash"] == prior.order_hash


def test_superseded_inputs_cannot_issue_fresh_orders(tmp_path: Path) -> None:
    """A passing payload is unusable after Task 8 supersedes its envelope."""
    controller = ready_for_expert_scoring(tmp_path)
    controller.issue_expert_orders()
    controller.accept_expert_score(1, _score_for(controller, 1))
    controller.accept_expert_score(2, _score_for(controller, 2, conflict=True))
    controller.revise_source(
        source_sheet("pilot-001", generation=2), revision_id="stale-r2"
    )

    with pytest.raises(ValueError, match="current|validated|stale"):
        controller.issue_ready()
    with pytest.raises(ValueError, match="current|validated|stale"):
        controller.issue_adjudication_order()
    assert not controller.adjudicator_workspace.exists()


def test_run_root_symlink_cannot_alias_case_root(tmp_path: Path) -> None:
    """Canonical root separation must reject a symlink into the case."""
    case = write_case(tmp_path / "case")
    alias = tmp_path / "run-alias"
    alias.symlink_to(case, target_is_directory=True)

    with pytest.raises(ValueError, match="separate"):
        BlindEvaluationController.from_case(case, alias / "nested-run")


def test_repeated_source_revisions_reuse_current_authenticated_curator(
    tmp_path: Path,
) -> None:
    """A replacement keeps the authenticated signer required by Task 8."""
    controller = ready_for_recommendation(tmp_path)
    controller.revise_source(
        source_sheet("pilot-001", generation=2), revision_id="repeat-r2"
    )
    controller.recompute_ready()

    result = controller.revise_source(
        source_sheet("pilot-001", generation=3), revision_id="repeat-r3"
    )

    assert result.artifact_id == controller.source_ref().artifact_id


def test_contaminated_workspace_never_leaves_a_live_order(tmp_path: Path) -> None:
    """Workspace validation must finish before queue publication."""
    controller = BlindEvaluationController.from_case(
        write_case(tmp_path / "case"), tmp_path / "run"
    )
    enroll_controller(controller)
    controller.recommender_workspace.mkdir(parents=True)
    (controller.recommender_workspace / "intruder.txt").write_text(
        "unexpected", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="unexpected"):
        controller.replay_calibration()

    assert not controller.queue.has_generation("recommend-method")
    assert controller.recommender_workspace_files() == ("intruder.txt",)


def test_adjudication_lock_and_final_order_survive_restart(tmp_path: Path) -> None:
    """The authenticated third-score receipt, not process memory, unlocks review."""
    case = write_case(tmp_path / "case")
    controller = BlindEvaluationController.from_case(case, tmp_path / "run")
    enroll_controller(controller)
    controller.replay_calibration()
    controller.accept_recommendation(valid_recommendation(controller))
    controller.issue_expert_orders()
    controller.accept_expert_score(1, _score_for(controller, 1))
    controller.accept_expert_score(2, _score_for(controller, 2, conflict=True))
    controller.issue_adjudication_order()
    assert controller.adjudicator_queue is not None
    order = controller.adjudicator_queue.read_order("adjudicator-score")
    assignment = order.principal_assignment
    assert assignment is not None
    third = expert_sheet(order.input_artifacts[1], assignment.principal_id)
    controller.accept_adjudication(
        signed_candidate(controller, order, third, PrincipalKind.ADJUDICATOR, 1)
    )

    restarted = BlindEvaluationController.from_case(case, tmp_path / "run")
    verdict = AdjudicationVerdict(
        score_sheet_ref=restarted.artifacts.ref(restarted.case_id, "expert_one"),
        verdict="accept",
        rationale="The durable blind score resolves the disagreement.",
        adjudicator_principal=assignment.principal_id,
    )
    adjudicator_queue, _ = restarted._restore_adjudicator()
    final_order = adjudicator_queue.read_order("adjudicate")
    restarted.accept_adjudication(
        signed_candidate(
            restarted, final_order, verdict, PrincipalKind.ADJUDICATOR, 1
        )
    )

    assert restarted.adjudicator_queue is not None
    assert restarted.adjudicator_queue.has_generation("adjudicate")
    assert len(restarted.adjudicator_queue.collect("adjudicate")) == 1


def test_source_dependent_statement_requires_its_opaque_fact_binding(
    tmp_path: Path,
) -> None:
    """Task 5 must not silently assign an unbound statement to the first claim."""
    controller = ready_for_recommendation(tmp_path)
    forged = valid_recommendation(controller).model_copy(
        update={"diagnostics": ("The estimated effect is 25%.",)}
    )

    with pytest.raises(ValueError, match="citation integrity"):
        controller.accept_recommendation(forged)

    assert not (
        controller.run_root
        / controller.artifacts.paths(controller.case_id).recommendation
    ).exists()

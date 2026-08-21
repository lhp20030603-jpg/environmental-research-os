"""Restart and fail-closed regressions for the blind workflow."""

from pathlib import Path

import pytest
from blind_artifact_helpers import expert_sheet, source_sheet
from blind_signing_helpers import enroll_controller, signed_candidate
from test_blind_registry_security import write_case
from test_blind_workflow import (
    _score_for,
    ready_for_expert_scoring,
    ready_for_recommendation,
    valid_recommendation,
)

from envresearch.benchmarks.blind_signed_submission import import_signed_submission
from envresearch.benchmarks.blind_workflow import BlindEvaluationController
from envresearch.models.benchmark_claims import ClaimFactMap, ClaimFactMappingEntry
from envresearch.models.benchmark_evaluation import ExpertScoreSheet
from envresearch.models.principal import PrincipalKind
from envresearch.research.order_policy import blind_claim_usages
from envresearch.workers.contracts import WorkOrder
from envresearch.workers.control import serialize_model
from envresearch.workers.filesystem import PinnedRoot
from envresearch.workers.queue import FilesystemWorkerQueue


def _ready_for_final_order(tmp_path: Path) -> BlindEvaluationController:
    controller = ready_for_expert_scoring(tmp_path)
    controller.issue_expert_orders()
    controller.accept_expert_score(1, _score_for(controller, 1))
    controller.accept_expert_score(2, _score_for(controller, 2, conflict=True))
    controller.issue_adjudication_order()
    assert controller.adjudicator_queue is not None
    order = controller.adjudicator_queue.read_order("adjudicator-score")
    assignment = order.principal_assignment
    assert assignment is not None
    third = expert_sheet(order.input_artifacts[1], assignment.principal_id)
    signed = signed_candidate(controller, order, third, assignment.kind, 1)
    import_signed_submission(
        controller,
        controller.adjudicator_queue,
        "adjudicator-score",
        signed,
        ExpertScoreSheet,
        PrincipalKind.ADJUDICATOR,
        1,
    )
    return controller


def test_restart_revision_archives_every_role_queue_before_recompute(
    tmp_path: Path,
) -> None:
    """Restart must not lose expert or adjudicator generations during revision."""
    case, run = write_case(tmp_path / "case"), tmp_path / "run"
    controller = BlindEvaluationController.from_case(case, run)
    enroll_controller(controller)
    controller.replay_calibration()
    controller.accept_recommendation(valid_recommendation(controller))
    controller.issue_expert_orders()
    controller.accept_expert_score(1, _score_for(controller, 1))
    controller.accept_expert_score(2, _score_for(controller, 2, conflict=True))
    controller.issue_adjudication_order()
    assert controller.adjudicator_queue is not None
    adjudicator = controller.adjudicator_queue.read_order("adjudicator-score")
    assignment = adjudicator.principal_assignment
    assert assignment is not None
    third = expert_sheet(adjudicator.input_artifacts[1], assignment.principal_id)
    controller.accept_adjudication(
        signed_candidate(controller, adjudicator, third, assignment.kind, 1)
    )
    stale = (*controller.expert_queues.values(), controller.adjudicator_queue)

    restarted = BlindEvaluationController.from_case(case, run)
    restarted.revise_source(
        source_sheet("pilot-001", generation=2), revision_id="restart-r2"
    )

    assert all(not queue.has_generation(order) for queue, order in (
        (stale[0], "expert-score-1"), (stale[1], "expert-score-2"),
        (stale[2], "adjudicator-score"), (stale[2], "adjudicate"),
    ))
    restarted.recompute_ready()
    restarted.accept_recommendation(valid_recommendation(restarted))
    with pytest.raises(ValueError, match="principal assignment authentication"):
        restarted.issue_expert_orders()


def test_structural_recommendation_fields_never_fabricate_claim_usage(
    tmp_path: Path,
) -> None:
    """Hashes and identifiers are source-independent, not claim statements."""
    controller = ready_for_recommendation(tmp_path)
    mapping = controller.artifacts.lifecycle.read_payload(
        controller.artifacts.paths(controller.case_id).claim_fact_map, ClaimFactMap
    )
    usages = blind_claim_usages(
        valid_recommendation(controller).model_dump(mode="json"), mapping
    )

    assert "/method_profile_registry_sha256" not in {
        usage.json_pointer for usage in usages
    }


@pytest.mark.parametrize(
    "statement",
    ("Effect is 25% using fact-001 and fact-002.", "Effect is 25% using fact-999."),
)
def test_claim_usage_rejects_ambiguous_or_missing_fact(
    tmp_path: Path, statement: str
) -> None:
    """Every dependent statement must resolve exactly one mapped opaque fact."""
    controller = ready_for_recommendation(tmp_path)
    mapping = controller.artifacts.lifecycle.read_payload(
        controller.artifacts.paths(controller.case_id).claim_fact_map, ClaimFactMap
    ).model_copy(update={"entries": (
        *controller.loaded.claim_fact_map.entries,
        ClaimFactMappingEntry(claim_id="claim-002", fact_id="fact-002"),
    )})

    with pytest.raises(ValueError, match="citation integrity"):
        blind_claim_usages({"finding": statement}, mapping)


def test_contaminated_adjudicator_root_never_leaves_final_order_live(
    tmp_path: Path,
) -> None:
    """Final-order workspace validation must precede queue publication."""
    controller = _ready_for_final_order(tmp_path)
    assert controller.adjudicator_queue is not None
    (controller.adjudicator_workspace / "intruder.txt").write_text(
        "unexpected", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="unexpected"):
        controller.accept_adjudication()

    assert not controller.adjudicator_queue.has_generation("adjudicate")


def test_tampered_adjudicator_input_never_leaves_final_order_live(
    tmp_path: Path,
) -> None:
    """Same-name content replacement must fail before final queue publication."""
    controller = _ready_for_final_order(tmp_path)
    assert controller.adjudicator_queue is not None
    (controller.adjudicator_workspace / "blinded-brief.yaml").write_text(
        "{}", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="unexpected"):
        controller.accept_adjudication()

    assert not controller.adjudicator_queue.has_generation("adjudicate")


def test_final_order_write_failure_never_leaves_generation_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed workspace append cannot expose the authenticated final order."""
    controller = _ready_for_final_order(tmp_path)
    assert controller.adjudicator_queue is not None
    original = PinnedRoot.write_file_noreplace

    def injected(
        root: PinnedRoot, path: Path, data: bytes, *, mode: int = 0o600
    ) -> None:
        if path.name == "adjudication-work-order.json":
            raise OSError("injected extension write failure")
        original(root, path, data, mode=mode)

    monkeypatch.setattr(PinnedRoot, "write_file_noreplace", injected)
    with pytest.raises(OSError, match="injected extension"):
        controller.accept_adjudication()

    assert not controller.adjudicator_queue.has_generation("adjudicate")


def test_post_append_input_tamper_never_leaves_generation_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All seven workspace bytes must remain exact through queue publication."""
    controller = _ready_for_final_order(tmp_path)
    assert controller.adjudicator_queue is not None
    original = PinnedRoot.write_file_noreplace

    def tamper_after_write(
        root: PinnedRoot, path: Path, data: bytes, *, mode: int = 0o600
    ) -> None:
        original(root, path, data, mode=mode)
        if path.name == "adjudication-work-order.json":
            (root.path / "blinded-brief.yaml").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(PinnedRoot, "write_file_noreplace", tamper_after_write)
    with pytest.raises(ValueError, match="unexpected"):
        controller.accept_adjudication()

    assert not controller.adjudicator_queue.has_generation("adjudicate")


def test_validate_then_publish_tamper_revokes_final_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publication-time contamination must revoke the just-issued order."""
    controller = _ready_for_final_order(tmp_path)
    assert controller.adjudicator_queue is not None
    original = FilesystemWorkerQueue.issue

    def issue_then_tamper(
        queue: FilesystemWorkerQueue, order: object
    ) -> object:
        record = original(queue, order)  # type: ignore[arg-type]
        if getattr(order, "order_id", None) == "adjudicate":
            (controller.adjudicator_workspace / "blinded-brief.yaml").write_text(
                "{}", encoding="utf-8"
            )
        return record

    monkeypatch.setattr(FilesystemWorkerQueue, "issue", issue_then_tamper)
    with pytest.raises(ValueError, match="unexpected"):
        controller.accept_adjudication()

    assert not controller.adjudicator_queue.has_generation("adjudicate")


def test_repeated_publish_then_raise_revokes_each_committed_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every retry must revoke its committed order into a fresh archive."""
    controller = _ready_for_final_order(tmp_path)
    assert controller.adjudicator_queue is not None
    original = FilesystemWorkerQueue.issue

    def issue_then_raise(
        queue: FilesystemWorkerQueue, order: object
    ) -> object:
        record = original(queue, order)  # type: ignore[arg-type]
        if getattr(order, "order_id", None) == "adjudicate":
            raise OSError("injected post-publication failure")
        return record

    monkeypatch.setattr(FilesystemWorkerQueue, "issue", issue_then_raise)
    for _ in range(2):
        with pytest.raises(OSError, match="post-publication"):
            controller.accept_adjudication()
        assert not controller.adjudicator_queue.has_generation("adjudicate")


def test_partial_publish_then_raise_reconciles_and_revokes_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A protected-only publication half must be completed only for archival."""
    controller = _ready_for_final_order(tmp_path)
    assert controller.adjudicator_queue is not None

    def anchor_then_raise(queue: FilesystemWorkerQueue, order: WorkOrder) -> None:
        queue.control.ensure_order(order, serialize_model(order))
        raise OSError("injected partial publication failure")

    monkeypatch.setattr(FilesystemWorkerQueue, "issue", anchor_then_raise)
    with pytest.raises(OSError, match="partial publication"):
        controller.accept_adjudication()

    assert not controller.adjudicator_queue.has_generation("adjudicate")

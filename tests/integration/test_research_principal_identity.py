"""Trusted assignments defeat caller-selected producer and human labels."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from orchestrator_fixtures import (
    broad_brief,
    candidate_payload,
    config,
    revision_capability,
)
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.kernel.gates import GateDecision
from envresearch.models.artifact import ProducerIdentity
from envresearch.models.enums import GateStatus
from envresearch.models.intake import ResearchIntakeMode
from envresearch.models.principal import PrincipalAssignment
from envresearch.research.cli_adapter import _open_run
from envresearch.research.orchestrator import ResearchOrchestrator

CLI = CliRunner()


def _candidate(root: Path) -> Path:
    path = root / "incoming/candidate-charters.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(candidate_payload().model_dump_json(), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "producer",
    (
        ProducerIdentity(
            component="assigned-research-framer",
            version="0.2.0",
            context_id="caller-changed-context",
        ),
        ProducerIdentity(
            component="caller-changed-component",
            version="0.2.0",
            context_id="caller-changed-context",
        ),
    ),
)
def test_caller_strings_cannot_impersonate_assigned_worker(
    tmp_path: Path, producer: ProducerIdentity
) -> None:
    """Trusting receipt strings instead of the sealed assignment is the bug."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    order = orchestrator.queue.read_order("frame-charters")
    assert order.principal_assignment is not None

    with pytest.raises(ValueError, match="assigned principal"):
        orchestrator.queue.submit(
            "frame-charters",
            _candidate(tmp_path),
            producer=producer,
            expected_order_hash=order.order_hash,
        )
    assert orchestrator.queue.collect("frame-charters") == ()


def test_queue_stamps_sealed_assignment_and_gate_rejects_actor_relabel(
    tmp_path: Path,
) -> None:
    """Actor relabeling must not create worker or human independence."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    order = orchestrator.queue.read_order("frame-charters")
    assert order.principal_assignment is not None
    orchestrator.queue.submit(
        "frame-charters",
        _candidate(tmp_path),
        expected_order_hash=order.order_hash,
    )
    orchestrator.accept_submission("frame-charters")
    artifact = orchestrator.lifecycle.read_artifact(
        Path("artifacts/candidate-charters.json")
    )
    assert artifact.envelope.producer == order.principal_assignment.producer

    orchestrator.advance()
    context = orchestrator.bound_gates.active_context("gate-1")
    assert context is not None
    orchestrator.gates.decide(
        context.gate_id,
        GateDecision(
            status=GateStatus.APPROVED,
            decided_by="caller-invented-human",
            rationale="Relabel the same caller.",
            conditions={
                **orchestrator.bound_gates.decision_conditions("gate-1"),
                "selected_candidate_id": "charter-air",
            },
        ),
    )
    with pytest.raises(ValueError, match="authenticated gate principal"):
        orchestrator.advance()


def test_exact_gate_actor_string_without_control_record_is_not_authenticated(
    tmp_path: Path,
) -> None:
    """Knowing the assigned actor label must not grant the owner capability."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    order = orchestrator.queue.read_order("frame-charters")
    orchestrator.queue.submit(
        "frame-charters",
        _candidate(tmp_path),
        expected_order_hash=order.order_hash,
    )
    orchestrator.accept_submission("frame-charters")
    orchestrator.advance()
    context = orchestrator.bound_gates.active_context("gate-1")
    assert context is not None
    orchestrator.gates.decide(
        context.gate_id,
        GateDecision(
            status=GateStatus.APPROVED,
            decided_by="human-reviewer",
            rationale="Only the public label is known.",
            conditions={
                **orchestrator.bound_gates.decision_conditions("gate-1"),
                "selected_candidate_id": "charter-air",
            },
        ),
    )

    with pytest.raises(ValueError, match="gate decision authentication"):
        orchestrator.advance()


def test_gate_api_rejects_missing_owner_capability(tmp_path: Path) -> None:
    """The trusted API must require more than an exact public actor label."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    order = orchestrator.queue.read_order("frame-charters")
    orchestrator.queue.submit(
        "frame-charters", _candidate(tmp_path), expected_order_hash=order.order_hash
    )
    orchestrator.accept_submission("frame-charters")
    orchestrator.advance()
    decision = GateDecision(
        status=GateStatus.APPROVED,
        decided_by="human-reviewer",
        rationale="Public actor label is insufficient.",
        conditions={
            **orchestrator.bound_gates.decision_conditions("gate-1"),
            "selected_candidate_id": "charter-air",
        },
    )

    with pytest.raises(ValueError, match="gate principal capability"):
        orchestrator.decide_gate("gate-1", decision, principal_capability="00")


def test_research_gate_cli_requires_owner_capability(tmp_path: Path) -> None:
    """The research CLI must mint protected decisions, not generic gate labels."""
    run_root = tmp_path / "run"
    initialized = CLI.invoke(
        app,
        [
            "research",
            "init",
            "benchmarks/design/fixtures/broad-topic/brief.yaml",
            "--config",
            "configs/research-default.yaml",
            "--run-root",
            str(run_root),
            "--json",
        ],
    )
    assert initialized.exit_code == 0
    orchestrator, _ = _open_run(run_root)
    order = orchestrator.queue.read_order("frame-charters")
    orchestrator.queue.submit(
        "frame-charters", _candidate(run_root), expected_order_hash=order.order_hash
    )
    orchestrator.accept_submission("frame-charters")
    orchestrator.advance()
    context = orchestrator.bound_gates.active_context("gate-1")
    assert context is not None
    capability_file = orchestrator.queue.control.path / "principals/gate.capability"
    conditions = tmp_path / "conditions.json"
    conditions.write_text(
        json.dumps(
            {
                **orchestrator.bound_gates.decision_conditions("gate-1"),
                "selected_candidate_id": "charter-air",
            }
        )
    )
    orchestrator.close()
    arguments = [
        "research",
        "gate-decide",
        str(run_root),
        context.gate_id,
        "--approve",
        "--rationale",
        "Approve current charter.",
        "--conditions-json",
        str(conditions),
        "--json",
    ]

    missing = CLI.invoke(app, arguments)
    accepted = CLI.invoke(
        app,
        [
            *arguments[:-1],
            "--principal-capability-file",
            str(capability_file),
            "--json",
        ],
    )

    assert missing.exit_code == 2
    assert "--principal-capability-file" in missing.output
    assert accepted.exit_code == 0
    reopened, _ = _open_run(run_root)
    assert reopened.advance().phase.value == "waiting_for_agent"


def test_revision_actor_is_scheduler_assigned_not_caller_selected(
    tmp_path: Path,
) -> None:
    """Changing only actor text must not change the authenticated principal."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    order = orchestrator.queue.read_order("frame-charters")
    orchestrator.queue.submit(
        "frame-charters",
        _candidate(tmp_path),
        expected_order_hash=order.order_hash,
    )
    orchestrator.accept_submission("frame-charters")

    with pytest.raises(ValueError, match="revision principal capability"):
        orchestrator.request_revision(
            "frame-charters", "Change framing", "invented-researcher", "00"
        )
    revision = orchestrator.request_revision(
        "frame-charters",
        "Change framing",
        "invented-researcher",
        revision_capability(orchestrator),
    )

    assert revision.actor == "human-reviewer"
    assert revision.principal_assignment.principal_id == "human-reviewer"
    assert revision.principal_assignment.verification == "owner_control"


def test_revision_rejects_a_worker_assignment_relabelled_as_human(
    tmp_path: Path,
) -> None:
    """Passing an authenticated worker assignment must not grant human control."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    order = orchestrator.queue.read_order("frame-charters")
    orchestrator.queue.submit(
        "frame-charters",
        _candidate(tmp_path),
        expected_order_hash=order.order_hash,
    )
    orchestrator.accept_submission("frame-charters")
    assert order.principal_assignment is not None
    forged = PrincipalAssignment.model_validate(
        order.principal_assignment.model_dump(mode="json")
    )

    with pytest.raises(ValueError, match="authenticated revision principal"):
        orchestrator.revisions.request(
            "frame-charters",
            reason="Impersonate human control",
            actor="human-reviewer",
            principal=forged,
        )


def test_revision_cli_requires_explicit_owner_capability(tmp_path: Path) -> None:
    """An arbitrary CLI actor label must not receive owner-control authority."""
    run_root = tmp_path / "run"
    initialized = CLI.invoke(
        app,
        [
            "research",
            "init",
            "benchmarks/design/fixtures/broad-topic/brief.yaml",
            "--config",
            "configs/research-default.yaml",
            "--run-root",
            str(run_root),
            "--json",
        ],
    )
    assert initialized.exit_code == 0
    orchestrator, _ = _open_run(run_root)
    order = orchestrator.queue.read_order("frame-charters")
    orchestrator.queue.submit(
        "frame-charters", _candidate(run_root), expected_order_hash=order.order_hash
    )
    orchestrator.accept_submission("frame-charters")
    orchestrator.close()
    arguments = [
        "research",
        "revise",
        str(run_root),
        "frame-charters",
        "--actor",
        "human-reviewer",
        "--reason",
        "Change framing",
        "--json",
    ]

    missing = CLI.invoke(app, arguments)
    wrong = CLI.invoke(
        app,
        [*arguments[:-1], "--principal-capability-file", str(tmp_path / "wrong"), "--json"],
    )

    assert missing.exit_code == 2
    assert "--principal-capability-file" in missing.output
    assert wrong.exit_code == 2
    assert "capability" in wrong.output
    capability_file = (
        run_root.parent / ".run.worker-queue-control/principals/revision.capability"
    )
    accepted = CLI.invoke(
        app,
        [
            *arguments[:-1],
            "--principal-capability-file",
            str(capability_file),
            "--json",
        ],
    )
    assert accepted.exit_code == 0

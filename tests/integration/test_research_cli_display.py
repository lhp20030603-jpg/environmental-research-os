"""Human-readable CLI output for owner-authenticated research gates."""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator_fixtures import broad_brief, candidate_payload, submit
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.kernel.gates import GateRequest
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.run_config import load_explicit_config
from envresearch.research.workflow import ResearchRunConfig

CLI = CliRunner()


def test_non_json_research_gate_decision_reports_gate_and_result(
    tmp_path: Path,
) -> None:
    """The human display must not collapse a gate decision to generic dashes."""
    explicit = load_explicit_config(Path("configs/research-default.yaml"))
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        ResearchRunConfig(
            workspace=tmp_path,
            run_id="cli-display-run",
            input_mode=broad_brief().intake_mode,
            ranking_policy=explicit.ranking_policy,
            acquisition_budget=explicit.acquisition_budget,
            config_sha256=explicit.sha256,
        ),
        broad_brief(),
        explicit_config=explicit.data,
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    context = orchestrator.bound_gates.active_context("gate-1")
    assert context is not None
    conditions = tmp_path / "gate-conditions.json"
    conditions.write_text(
        json.dumps(
            {
                **orchestrator.bound_gates.decision_conditions("gate-1"),
                "selected_candidate_id": "charter-air",
            }
        ),
        encoding="utf-8",
    )
    capability = orchestrator.queue.control.path / "principals/gate.capability"
    orchestrator.close()

    result = CLI.invoke(
        app,
        [
            "research",
            "gate-decide",
            str(tmp_path),
            context.gate_id,
            "--approve",
            "--rationale",
            "Selected the strongest current charter.",
            "--conditions-json",
            str(conditions),
            "--principal-capability-file",
            str(capability),
        ],
    )

    assert result.exit_code == 0, result.output
    assert context.gate_id in result.output
    assert "APPROVED" in result.output
    assert "human-reviewer" in result.output
    assert "- / -" not in result.output


def test_json_research_gate_decision_matches_the_complete_durable_gate(
    tmp_path: Path,
) -> None:
    """The display fix must leave the machine-readable contract unchanged."""
    explicit = load_explicit_config(Path("configs/research-default.yaml"))
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        ResearchRunConfig(
            workspace=tmp_path,
            run_id="cli-json-contract-run",
            input_mode=broad_brief().intake_mode,
            ranking_policy=explicit.ranking_policy,
            acquisition_budget=explicit.acquisition_budget,
            config_sha256=explicit.sha256,
        ),
        broad_brief(),
        explicit_config=explicit.data,
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    context = orchestrator.bound_gates.active_context("gate-1")
    assert context is not None
    conditions = tmp_path / "gate-conditions.json"
    conditions.write_text(
        json.dumps(
            {
                **orchestrator.bound_gates.decision_conditions("gate-1"),
                "selected_candidate_id": "charter-air",
            }
        ),
        encoding="utf-8",
    )
    capability = orchestrator.queue.control.path / "principals/gate.capability"
    orchestrator.close()

    result = CLI.invoke(
        app,
        [
            "research",
            "gate-decide",
            str(tmp_path),
            context.gate_id,
            "--approve",
            "--rationale",
            "Selected the strongest current charter.",
            "--conditions-json",
            str(conditions),
            "--principal-capability-file",
            str(capability),
            "--json",
        ],
    )

    durable = GateRequest.model_validate_json(
        (tmp_path / "gates" / f"{context.gate_id}.json").read_bytes()
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == durable.model_dump(mode="json")

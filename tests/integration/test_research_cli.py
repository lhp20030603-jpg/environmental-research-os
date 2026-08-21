"""Command-line contracts for local Discover/Design research runs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.workflow import ResearchRunPhase

CLI = CliRunner()
DEFAULT_CONFIG = Path("configs/research-default.yaml")


def _invoke(*arguments: str) -> tuple[int, dict[str, object]]:
    result = CLI.invoke(app, list(arguments))
    assert result.stdout, result.exception
    return result.exit_code, json.loads(result.stdout)


def _order_hash(run_root: Path, order_id: str) -> str:
    payload = json.loads((run_root / "work-orders" / f"{order_id}.json").read_text())
    return str(payload["order_hash"])


def test_research_submit_requires_producer_context_before_opening_run() -> None:
    """The CLI must reject contextless workers before any queue publication."""
    result = CLI.invoke(
        app,
        ["research", "submit", "missing", "unknown", "none.json", "--json"],
    )

    assert result.exit_code == 2
    assert "--order-hash" in result.output


def test_research_init_emits_summary_and_copies_exact_config(tmp_path: Path) -> None:
    """Removing initialization or changing the config copy breaks this contract."""
    run_root = tmp_path / "run"

    code, body = _invoke(
        "research",
        "init",
        "benchmarks/design/fixtures/broad-topic/brief.yaml",
        "--config",
        str(DEFAULT_CONFIG),
        "--run-root",
        str(run_root),
        "--json",
    )

    assert code == 0
    assert body["phase"] == "waiting_for_agent"
    assert body["pending_work_order_nodes"] == ["frame-charters"]
    assert (run_root / "research-run-config.yaml").read_bytes() == (
        DEFAULT_CONFIG.read_bytes()
    )


def test_research_status_recovers_the_initialized_run(tmp_path: Path) -> None:
    """Status must reopen durable state rather than depend on a live process."""
    run_root = tmp_path / "run"
    init_code, initialized = _invoke(
        "research",
        "init",
        "benchmarks/design/fixtures/structured-brief/brief.yaml",
        "--config",
        str(DEFAULT_CONFIG),
        "--run-root",
        str(run_root),
        "--json",
    )
    assert init_code == 0

    status_code, recovered = _invoke("research", "status", str(run_root), "--json")

    assert status_code == 0
    assert recovered == initialized
    assert recovered["pending_work_order_nodes"] == ["normalize-brief"]


def test_research_submit_promotes_one_authenticated_candidate(tmp_path: Path) -> None:
    """Submit must pass through the queue and lifecycle before completion."""
    run_root = tmp_path / "run"
    code, _ = _invoke(
        "research",
        "init",
        "benchmarks/design/fixtures/structured-brief/brief.yaml",
        "--config",
        str(DEFAULT_CONFIG),
        "--run-root",
        str(run_root),
        "--json",
    )
    assert code == 0
    candidate = run_root / "incoming" / "research-brief.yaml"
    candidate.parent.mkdir()
    candidate.write_bytes(
        Path(
            "benchmarks/design/fixtures/structured-brief/research-brief.yaml"
        ).read_bytes()
    )

    submit_code, submitted = _invoke(
        "research",
        "submit",
        str(run_root),
        "normalize-brief",
        str(candidate),
        "--order-hash",
        _order_hash(run_root, "normalize-brief"),
        "--producer-context",
        "cli-normalize-context",
        "--json",
    )

    assert submit_code == 0
    assert submitted["completed_nodes"] == ["normalize-brief"]
    assert submitted["phase"] == "degraded"


def test_research_submit_binds_cli_provider_identity_to_artifact(
    tmp_path: Path,
) -> None:
    """Dropping authenticated CLI identity options must fail this provenance check."""
    run_root = tmp_path / "run"
    _invoke(
        "research",
        "init",
        "benchmarks/design/fixtures/structured-brief/brief.yaml",
        "--config",
        str(DEFAULT_CONFIG),
        "--run-root",
        str(run_root),
        "--json",
    )
    candidate = run_root / "incoming" / "research-brief.yaml"
    candidate.parent.mkdir()
    candidate.write_bytes(
        Path(
            "benchmarks/design/fixtures/structured-brief/research-brief.yaml"
        ).read_bytes()
    )

    code, _ = _invoke(
        "research",
        "submit",
        str(run_root),
        "normalize-brief",
        str(candidate),
        "--order-hash",
        _order_hash(run_root, "normalize-brief"),
        "--producer-component",
        "cli-framer",
        "--producer-version",
        "2.0.0",
        "--producer-model",
        "model-local",
        "--producer-runtime",
        "codex-cli",
        "--producer-context",
        "context-cli-framer",
        "--json",
    )

    assert code == 0
    lifecycle = ResearchArtifactLifecycle(run_root, "unused-read-id")
    producer = lifecycle.read_artifact(
        Path("artifacts/research-brief.yaml")
    ).envelope.producer
    order = json.loads((run_root / "work-orders/normalize-brief.json").read_text())
    assert producer.model_dump(mode="json") == order["principal_assignment"]["producer"]


def test_research_advance_reports_a_required_gate_with_stable_code(
    tmp_path: Path,
) -> None:
    """A pending human gate must not look like a successful autonomous advance."""
    run_root = tmp_path / "run"
    _invoke(
        "research",
        "init",
        "benchmarks/design/fixtures/structured-brief/brief.yaml",
        "--config",
        str(DEFAULT_CONFIG),
        "--run-root",
        str(run_root),
        "--json",
    )
    candidate = run_root / "incoming" / "research-brief.yaml"
    candidate.parent.mkdir()
    candidate.write_bytes(
        Path(
            "benchmarks/design/fixtures/structured-brief/research-brief.yaml"
        ).read_bytes()
    )
    _invoke(
        "research",
        "submit",
        str(run_root),
        "normalize-brief",
        str(candidate),
        "--order-hash",
        _order_hash(run_root, "normalize-brief"),
        "--producer-context",
        "cli-normalize-context",
        "--json",
    )

    code, body = _invoke("research", "advance", str(run_root), "--json")

    assert code == 2
    assert body["error"]["code"] == "GATE_REQUIRED"


@pytest.mark.parametrize(
    ("command", "expected_code"),
    (
        (("research", "status", "missing", "--json"), "RESEARCH_RUN_INVALID"),
        (
            (
                "research",
                "submit",
                "missing",
                "unknown",
                "none.json",
                "--order-hash",
                "0" * 64,
                "--producer-context",
                "cli-invalid-context",
                "--json",
            ),
            "RESEARCH_RUN_INVALID",
        ),
    ),
)
def test_research_commands_emit_stable_application_error_codes(
    tmp_path: Path,
    command: tuple[str, ...],
    expected_code: str,
) -> None:
    """Missing durable state must not leak changing internal exception text."""
    adjusted = tuple(
        str(tmp_path / item) if item == "missing" else item for item in command
    )

    code, body = _invoke(*adjusted)

    assert code == 2
    assert body["error"]["code"] == expected_code


def test_research_init_rejects_an_invalid_brief_with_stable_code(
    tmp_path: Path,
) -> None:
    """Malformed intake is an application validation error, not a parser error."""
    brief = tmp_path / "brief.yaml"
    brief.write_text(
        "intake_mode: broad_topic\nstructured_brief: wrong\n", encoding="utf-8"
    )

    code, body = _invoke(
        "research",
        "init",
        str(brief),
        "--config",
        str(DEFAULT_CONFIG),
        "--run-root",
        str(tmp_path / "run"),
        "--json",
    )

    assert code == 2
    assert body["error"]["code"] == "RESEARCH_BRIEF_INVALID"


def test_research_submit_separates_unknown_order_from_bad_candidate(
    tmp_path: Path,
) -> None:
    """Order identity failures and candidate failures retain distinct codes."""
    run_root = tmp_path / "run"
    _invoke(
        "research",
        "init",
        "benchmarks/design/fixtures/broad-topic/brief.yaml",
        "--config",
        str(DEFAULT_CONFIG),
        "--run-root",
        str(run_root),
        "--json",
    )
    candidate = run_root / "incoming" / "candidate-charters.json"
    candidate.parent.mkdir()
    candidate.write_text("{}", encoding="utf-8")

    unknown_code, unknown = _invoke(
        "research",
        "submit",
        str(run_root),
        "not-issued",
        str(candidate),
        "--order-hash",
        "0" * 64,
        "--producer-context",
        "cli-invalid-context",
        "--json",
    )
    invalid_code, invalid = _invoke(
        "research",
        "submit",
        str(run_root),
        "frame-charters",
        str(candidate),
        "--order-hash",
        _order_hash(run_root, "frame-charters"),
        "--producer-context",
        "cli-invalid-context",
        "--json",
    )

    assert unknown_code == 2
    assert unknown["error"]["code"] == "WORK_ORDER_INVALID"
    assert invalid_code == 2
    assert invalid["error"]["code"] == "SUBMISSION_INVALID"


def test_default_config_exposes_all_rank_and_acquisition_limits() -> None:
    """A missing explicit threshold would reintroduce a hidden global default."""
    payload = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))

    assert set(payload["ranking_weights"]) == {
        "contribution_potential",
        "literature_gap",
        "data_feasibility",
        "identification_plausibility",
        "policy_relevance",
        "scope_manageability",
    }
    assert payload["acquisition_budget"] == {
        "max_download_bytes": 104857600,
        "max_local_storage_bytes": 536870912,
        "max_api_calls": 1000,
        "max_external_cost": "0",
        "max_elapsed_seconds": 1800,
    }


def test_research_advance_succeeds_while_agent_work_remains(tmp_path: Path) -> None:
    """A normal non-gate advance returns the current durable summary."""
    run_root = tmp_path / "run"
    _invoke(
        "research",
        "init",
        "benchmarks/design/fixtures/broad-topic/brief.yaml",
        "--config",
        str(DEFAULT_CONFIG),
        "--run-root",
        str(run_root),
        "--json",
    )

    code, body = _invoke("research", "advance", str(run_root), "--json")

    assert code == 0
    assert body["phase"] == "waiting_for_agent"


def test_research_advance_maps_blocked_phase_to_stable_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blocked application branch has the declared public error code."""
    run_root = tmp_path / "run"
    _invoke(
        "research",
        "init",
        "benchmarks/design/fixtures/broad-topic/brief.yaml",
        "--config",
        str(DEFAULT_CONFIG),
        "--run-root",
        str(run_root),
        "--json",
    )
    original = ResearchOrchestrator.advance

    def blocked(orchestrator: ResearchOrchestrator) -> object:
        summary = original(orchestrator)
        return summary.model_copy(update={"phase": ResearchRunPhase.BLOCKED})

    monkeypatch.setattr(ResearchOrchestrator, "advance", blocked)
    code, body = _invoke("research", "advance", str(run_root), "--json")

    assert code == 2
    assert body["error"]["code"] == "RESEARCH_RUN_BLOCKED"

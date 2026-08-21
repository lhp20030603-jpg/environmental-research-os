"""Adversarial filesystem and lifecycle contracts for the research CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.models.intake import ResearchBriefPayload, ResearchIntakeMode
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.workflow import ResearchRunConfig

CLI = CliRunner()
BRIEF = Path("benchmarks/design/fixtures/broad-topic/brief.yaml")
CONFIG = Path("configs/research-default.yaml")


def _init(run_root: Path) -> None:
    result = CLI.invoke(
        app,
        [
            "research",
            "init",
            str(BRIEF),
            "--config",
            str(CONFIG),
            "--run-root",
            str(run_root),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout


def _json(result: Any) -> dict[str, object]:
    stdout = result.stdout
    assert isinstance(stdout, str) and stdout
    return json.loads(stdout)


def test_cli_rejects_filesystem_root_before_orchestrator_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root validation must precede every orchestrator write."""
    called = False

    def forbidden_initialize(*_: object, **__: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("unsafe root reached orchestrator")

    monkeypatch.setattr(ResearchOrchestrator, "initialize", forbidden_initialize)

    result = CLI.invoke(
        app,
        [
            "research",
            "init",
            str(BRIEF),
            "--config",
            str(CONFIG),
            "--run-root",
            str(Path("/")),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert _json(result)["error"]["code"] == "RESEARCH_RUN_INVALID"
    assert called is False


def test_orchestrator_rejects_filesystem_root_before_workspace_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct callers receive the same pre-write workspace boundary."""
    config = ResearchRunConfig(
        workspace=Path("/"),
        run_id="unsafe-root",
        input_mode=ResearchIntakeMode.BROAD_TOPIC,
    )
    brief = ResearchBriefPayload(
        intake_mode=ResearchIntakeMode.BROAD_TOPIC,
        broad_topic="Unsafe root must never be initialized",
    )

    original_mkdir = Path.mkdir

    def guarded_mkdir(path: Path, *args: object, **kwargs: object) -> None:
        if path == Path(path.anchor):
            pytest.fail("filesystem root reached workspace creation")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", guarded_mkdir)
    with pytest.raises(ValueError, match="filesystem root"):
        ResearchOrchestrator().initialize(config, brief)


@pytest.mark.parametrize(
    "order_id",
    ("../../probe", "/absolute", "bad/name", "bad\\name", "x" * 300),
)
@pytest.mark.parametrize("outside_exists", (False, True))
def test_submit_rejects_unsafe_order_id_before_any_path_probe(
    tmp_path: Path,
    order_id: str,
    outside_exists: bool,
) -> None:
    """Unsafe IDs have one stable result independent of outside filesystem state."""
    run_root = tmp_path / "run"
    _init(run_root)
    if outside_exists:
        (tmp_path / "probe.json").write_text("{}", encoding="utf-8")
    candidate = run_root / "incoming" / "candidate-charters.json"
    candidate.parent.mkdir()
    candidate.write_text("{}", encoding="utf-8")

    result = CLI.invoke(
        app,
        [
            "research",
            "submit",
            str(run_root),
            order_id,
            str(candidate),
            "--producer-context",
            "cli-security-context",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert _json(result)["error"]["code"] == "WORK_ORDER_INVALID"


def test_advance_failure_closes_all_opened_descriptors_and_remains_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An adapter error cannot defer queue/checkpoint cleanup to GC."""
    run_root = tmp_path / "run"
    _init(run_root)
    captured: list[ResearchOrchestrator] = []

    def fail_advance(orchestrator: ResearchOrchestrator) -> object:
        captured.append(orchestrator)
        raise RuntimeError("injected advance failure")

    monkeypatch.setattr(ResearchOrchestrator, "advance", fail_advance)
    failed = CLI.invoke(app, ["research", "advance", str(run_root), "--json"])

    assert failed.exit_code == 2
    assert captured[0].queue.exchange.fd == -1
    assert captured[0].queue.control.storage.fd == -1
    assert captured[0].checkpoints._closed is True

    monkeypatch.undo()
    recovered = CLI.invoke(app, ["research", "status", str(run_root), "--json"])
    assert recovered.exit_code == 0


def test_typer_parser_errors_remain_framework_default() -> None:
    """Parser failures must not be rewritten as application JSON errors."""
    result = CLI.invoke(app, ["research", "init"])

    assert result.exit_code == 2
    assert "Missing argument" in result.output
    assert '"error"' not in result.output

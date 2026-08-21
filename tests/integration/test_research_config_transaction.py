"""Atomic two-file identity and recovery invariants for research runs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.models.intake import ResearchBriefPayload
from envresearch.research import config_publication
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.config_publication import (
    CONFIG_COPY,
    INTERNAL_CONFIG,
    RunConfigPublication,
)
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.run_config import load_explicit_config
from envresearch.research.workflow import ResearchRunConfig

CLI = CliRunner()
BRIEF = Path("benchmarks/design/fixtures/broad-topic/brief.yaml")
DEFAULT = Path("configs/research-default.yaml")


def _brief() -> ResearchBriefPayload:
    return ResearchBriefPayload.model_validate(
        yaml.safe_load(BRIEF.read_text(encoding="utf-8"))
    )


def _config(run_root: Path) -> ResearchRunConfig:
    explicit = load_explicit_config(DEFAULT)
    target = run_root.resolve()
    return ResearchRunConfig(
        workspace=target,
        run_id=f"research-{hashlib.sha256(str(target).encode()).hexdigest()[:16]}",
        input_mode=_brief().intake_mode,
        ranking_policy=explicit.ranking_policy,
        acquisition_budget=explicit.acquisition_budget,
        config_sha256=explicit.sha256,
    )


def _internal_bytes(config: ResearchRunConfig) -> bytes:
    return json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _wrong_explicit() -> bytes:
    payload = yaml.safe_load(DEFAULT.read_text(encoding="utf-8"))
    payload["acquisition_budget"]["max_api_calls"] = 999
    return yaml.safe_dump(payload, sort_keys=False).encode("utf-8")


def _init(run_root: Path) -> object:
    return CLI.invoke(
        app,
        [
            "research",
            "init",
            str(BRIEF),
            "--config",
            str(DEFAULT),
            "--run-root",
            str(run_root),
            "--json",
        ],
    )


def test_recovery_replacement_race_cannot_return_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery must finally verify the safely read copied bytes under its lock."""
    run_root = tmp_path / "run"
    assert _init(run_root).exit_code == 0
    original = ResearchArtifactLifecycle.persist_structured
    injected = False

    def replace_copy(
        lifecycle: ResearchArtifactLifecycle,
        path: Path,
        payload: object,
        component: str,
        inputs: object,
        **kwargs: object,
    ) -> Any:
        nonlocal injected
        result = original(
            lifecycle,
            path,
            payload,
            component,
            inputs,
            **kwargs,  # type: ignore[arg-type]
        )
        if not injected:
            injected = True
            staged = tmp_path / "replacement.yaml"
            staged.write_bytes(_wrong_explicit())
            os.replace(staged, run_root / CONFIG_COPY)
        return result

    monkeypatch.setattr(ResearchArtifactLifecycle, "persist_structured", replace_copy)

    result = CLI.invoke(app, ["research", "status", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "RESEARCH_RUN_INVALID"
    assert "phase" not in json.loads(result.stdout)
    monkeypatch.undo()
    assert (
        CLI.invoke(app, ["research", "status", str(run_root), "--json"]).exit_code == 2
    )


def test_direct_digest_requires_explicit_bytes_before_any_publication(
    tmp_path: Path,
) -> None:
    """A non-null digest without its exact source bytes cannot create a run."""
    run_root = tmp_path / "run"
    orchestrator = ResearchOrchestrator()

    with pytest.raises(ValueError, match="explicit config"):
        orchestrator.initialize(_config(run_root), _brief())

    assert orchestrator._closed is True
    assert not (run_root / INTERNAL_CONFIG).exists()
    assert not (run_root / CONFIG_COPY).exists()
    assert not (run_root / "artifacts").exists()
    assert not (run_root / "work-orders").exists()


def test_direct_mismatch_is_rejected_before_resources_or_authority(
    tmp_path: Path,
) -> None:
    """Schema-valid mismatched bytes fail before locks, configs, or run artifacts."""
    run_root = tmp_path / "run"
    orchestrator = ResearchOrchestrator()

    with pytest.raises(ValueError, match="digest"):
        orchestrator.initialize(
            _config(run_root), _brief(), explicit_config=_wrong_explicit()
        )

    assert orchestrator._closed is True
    assert not run_root.exists()


@pytest.mark.parametrize("preexisting_internal", (False, True))
def test_second_identity_conflict_retains_exact_internal_for_safe_retry(
    tmp_path: Path, preexisting_internal: bool
) -> None:
    """A failed second bind leaves exact residue that a same-identity retry reuses."""
    run_root = tmp_path / "run"
    run_root.mkdir()
    config = _config(run_root)
    if preexisting_internal:
        (run_root / INTERNAL_CONFIG).write_bytes(_internal_bytes(config))
    wrong = _wrong_explicit()
    (run_root / CONFIG_COPY).write_bytes(wrong)
    orchestrator = ResearchOrchestrator()

    with pytest.raises(ValueError, match="research config copy") as raised:
        orchestrator.initialize(config, _brief(), explicit_config=DEFAULT.read_bytes())

    assert (run_root / CONFIG_COPY).read_bytes() == wrong
    assert (run_root / INTERNAL_CONFIG).read_bytes() == _internal_bytes(config)
    assert orchestrator._closed is True
    if not preexisting_internal:
        notes = "\n".join(getattr(raised.value, "__notes__", ()))
        assert "retained without deletion" in notes
        assert "retry only the same run identity" in notes

    (run_root / CONFIG_COPY).unlink()
    summary = orchestrator.initialize(
        config, _brief(), explicit_config=DEFAULT.read_bytes()
    )

    assert summary.run_id == config.run_id
    orchestrator.close()

    conflicting = config.model_copy(update={"requested_by": "conflicting-identity"})
    with pytest.raises(ValueError, match="internal run config"):
        ResearchOrchestrator().initialize(
            conflicting, _brief(), explicit_config=DEFAULT.read_bytes()
        )
    assert (run_root / INTERNAL_CONFIG).read_bytes() == _internal_bytes(config)


def test_failed_publication_never_deletes_concurrently_replaced_internal_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replacement before failure remains untouched by fail-closed retention."""
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / CONFIG_COPY).write_bytes(_wrong_explicit())
    replacement = b'{"replacement":true}'
    original = RunConfigPublication.read_optional
    injected = False

    def replace_internal(
        publication: RunConfigPublication, relative: Path, *, description: str
    ) -> bytes | None:
        nonlocal injected
        if relative == CONFIG_COPY and not injected:
            injected = True
            staged = tmp_path / "replacement.json"
            staged.write_bytes(replacement)
            os.replace(staged, run_root / INTERNAL_CONFIG)
        return original(publication, relative, description=description)

    monkeypatch.setattr(RunConfigPublication, "read_optional", replace_internal)

    with pytest.raises(ValueError, match="research config copy"):
        ResearchOrchestrator().initialize(
            _config(run_root), _brief(), explicit_config=DEFAULT.read_bytes()
        )

    assert (run_root / INTERNAL_CONFIG).read_bytes() == replacement


def test_replacement_after_retention_identity_observation_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure handling preserves a replacement installed after observation."""
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / CONFIG_COPY).write_bytes(_wrong_explicit())
    replacement = b'{"replacement":"after-stat"}'
    staged = tmp_path / "replacement-after-stat.json"
    staged.write_bytes(replacement)
    original_retain = config_publication._retain_partial_bindings
    race_triggered = False

    def replace_after_observation(
        error: BaseException, created: tuple[Path, ...]
    ) -> None:
        nonlocal race_triggered
        metadata = os.stat(run_root / INTERNAL_CONFIG, follow_symlinks=False)
        assert metadata.st_nlink == 1
        race_triggered = True
        os.replace(staged, run_root / INTERNAL_CONFIG)
        original_retain(error, created)

    monkeypatch.setattr(
        config_publication, "_retain_partial_bindings", replace_after_observation
    )

    with pytest.raises(ValueError, match="research config copy"):
        ResearchOrchestrator().initialize(
            _config(run_root), _brief(), explicit_config=DEFAULT.read_bytes()
        )

    assert race_triggered is True
    assert (run_root / INTERNAL_CONFIG).read_bytes() == replacement

"""Durable explicit-config binding for CLI-created research runs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
from test_blind_registry_security import write_case
from typer.testing import CliRunner

from envresearch.benchmarks import design_replay
from envresearch.cli import app
from envresearch.models.intake import ResearchBriefPayload
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.run_config import load_explicit_config
from envresearch.research.workflow import ResearchRunConfig
from envresearch.workers.contracts import WorkOrder
from envresearch.workers.filesystem import PinnedRoot

CLI = CliRunner()
BRIEF = Path("benchmarks/design/fixtures/broad-topic/brief.yaml")
DEFAULT = Path("configs/research-default.yaml")
INTERNAL = Path("research-run-config.json")


def _init(run_root: Path, config: Path = DEFAULT) -> object:
    return CLI.invoke(
        app,
        [
            "research",
            "init",
            str(BRIEF),
            "--config",
            str(config),
            "--run-root",
            str(run_root),
            "--json",
        ],
    )


def _changed_config(tmp_path: Path, field: str) -> Path:
    payload = yaml.safe_load(DEFAULT.read_text(encoding="utf-8"))
    if field == "budget":
        payload["acquisition_budget"]["max_api_calls"] = 999
    else:
        payload["ranking_weights"]["contribution_potential"] = 0.25
        payload["ranking_weights"]["literature_gap"] = 1 / 12
    path = tmp_path / f"changed-{field}.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_internal_config_binds_exact_yaml_digest_and_ranking_policy(
    tmp_path: Path,
) -> None:
    """The operational config receipt must commit to bytes and all six weights."""
    run_root = tmp_path / "run"
    result = _init(run_root)
    assert result.exit_code == 0

    internal = json.loads((run_root / "research-run-config.json").read_text())
    expected_digest = hashlib.sha256(DEFAULT.read_bytes()).hexdigest()
    assert internal["config_sha256"] == expected_digest
    assert (
        internal["ranking_policy"]["weights"]
        == yaml.safe_load(DEFAULT.read_text(encoding="utf-8"))["ranking_weights"]
    )
    order = WorkOrder.model_validate_json(
        (run_root / "work-orders/frame-charters.json").read_bytes()
    )
    assert f"Explicit run config SHA-256: {expected_digest}" in order.policy_constraints
    assert any(
        item.startswith("Ranking weights: ") for item in order.policy_constraints
    )


def test_strict_citation_policy_survives_cli_and_design_replay_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A strict YAML flag must reach both durable run-config construction paths."""
    payload = yaml.safe_load(DEFAULT.read_text(encoding="utf-8"))
    payload["require_claim_verified_citations"] = True
    catalog = write_case(tmp_path / "authorized-blind-catalog")
    payload["citation_catalog_roots"] = [str(catalog)]
    strict = tmp_path / "strict.yaml"
    strict.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    run_root = tmp_path / "cli-run"
    assert _init(run_root, strict).exit_code == 0
    internal = json.loads((run_root / INTERNAL).read_text(encoding="utf-8"))
    assert internal["require_claim_verified_citations"] is True
    assert internal["citation_catalog_roots"] == [str(catalog.resolve())]

    monkeypatch.setattr(design_replay, "_DEFAULT_CONFIG", strict)
    brief = ResearchBriefPayload.model_validate(yaml.safe_load(BRIEF.read_text()))
    replay_config = design_replay._run_config(tmp_path / "replay-run", brief)
    assert replay_config.require_claim_verified_citations is True
    assert replay_config.citation_catalog_roots == (catalog.resolve(),)


def test_legacy_config_without_citation_policy_defaults_false(tmp_path: Path) -> None:
    """Adding the opt-in gate must not invalidate exact V0.1/V0.2 config bytes."""
    payload = yaml.safe_load(DEFAULT.read_text(encoding="utf-8"))
    payload.pop("require_claim_verified_citations")
    payload.pop("citation_catalog_roots", None)
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    explicit = load_explicit_config(legacy)

    assert explicit.require_claim_verified_citations is False
    assert explicit.citation_catalog_roots == ()
    assert _init(tmp_path / "legacy-run", legacy).exit_code == 0


def test_strict_citation_policy_requires_prebound_catalog(tmp_path: Path) -> None:
    """Strict initialization must fail before any caller can select a first catalog."""
    payload = yaml.safe_load(DEFAULT.read_text(encoding="utf-8"))
    payload["require_claim_verified_citations"] = True
    payload["citation_catalog_roots"] = []
    strict = tmp_path / "strict-without-catalog.yaml"
    strict.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    result = _init(tmp_path / "run", strict)

    assert result.exit_code == 2
    assert "catalog" in json.loads(result.stdout)["error"]["message"]


def test_same_exact_config_init_is_idempotent(tmp_path: Path) -> None:
    """An exact retry reuses the run while preserving its immutable order identity."""
    run_root = tmp_path / "run"
    first = _init(run_root)
    order_before = (run_root / "work-orders/frame-charters.json").read_bytes()

    second = _init(run_root)

    assert first.exit_code == second.exit_code == 0
    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert (run_root / "work-orders/frame-charters.json").read_bytes() == order_before


def test_valid_copied_config_tampering_is_rejected_on_reopen(tmp_path: Path) -> None:
    """Schema-valid byte drift cannot alter the displayed explicit policy."""
    run_root = tmp_path / "run"
    assert _init(run_root).exit_code == 0
    copied = run_root / "research-run-config.yaml"
    payload = yaml.safe_load(copied.read_text(encoding="utf-8"))
    payload["acquisition_budget"]["max_api_calls"] = 999
    copied.write_text(yaml.safe_dump(payload), encoding="utf-8")

    result = CLI.invoke(app, ["research", "status", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "RESEARCH_RUN_INVALID"


def test_changed_weight_and_budget_configs_reject_existing_run(
    tmp_path: Path,
) -> None:
    """Every operational policy change requires a distinct run root."""
    run_root = tmp_path / "run"
    assert _init(run_root).exit_code == 0

    for field in ("weights", "budget"):
        result = _init(run_root, _changed_config(tmp_path, field))
        assert result.exit_code == 2
        assert json.loads(result.stdout)["error"]["code"] == "RESEARCH_RUN_INVALID"


@pytest.mark.parametrize("preexisting", (False, True))
def test_init_rejects_conflicting_config_created_or_replaced_during_initialize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preexisting: bool,
) -> None:
    """A publication race must never return a summary for an unreopenable run."""
    run_root = tmp_path / "run"
    copied = run_root / "research-run-config.yaml"
    if preexisting:
        run_root.mkdir()
        copied.write_bytes(DEFAULT.read_bytes())
    wrong = _changed_config(tmp_path, "budget").read_bytes()
    original = ResearchArtifactLifecycle.persist_structured
    injected = False

    def inject_conflict(
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
            staged = tmp_path / "racing-config.yaml"
            staged.write_bytes(wrong)
            os.replace(staged, copied)
        return result

    monkeypatch.setattr(
        ResearchArtifactLifecycle, "persist_structured", inject_conflict
    )

    result = _init(run_root)

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "RESEARCH_RUN_INVALID"
    assert "phase" not in payload
    monkeypatch.undo()
    reopened = CLI.invoke(app, ["research", "status", str(run_root), "--json"])
    assert reopened.exit_code == 2


@pytest.mark.parametrize("alias", ("symlink", "hardlink"))
def test_init_rejects_aliased_existing_config_copy(tmp_path: Path, alias: str) -> None:
    """Existing config bytes must come from one regular unaliased inode."""
    run_root = tmp_path / "run"
    run_root.mkdir()
    copied = run_root / "research-run-config.yaml"
    if alias == "symlink":
        copied.symlink_to(DEFAULT.resolve())
    else:
        os.link(DEFAULT, copied)

    result = _init(run_root)

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "RESEARCH_RUN_INVALID"


@pytest.mark.parametrize("weight", (10**400, -(10**400)))
def test_extreme_integer_weight_uses_stable_json_error_contract(
    tmp_path: Path, weight: int
) -> None:
    """Float overflow is a validation error, never an uncaught CLI exception."""
    payload = yaml.safe_load(DEFAULT.read_text(encoding="utf-8"))
    payload["ranking_weights"]["contribution_potential"] = weight
    config = tmp_path / "extreme.yaml"
    config.write_text(yaml.safe_dump(payload), encoding="utf-8")

    result = _init(tmp_path / "run", config)

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "RESEARCH_RUN_INVALID"


def _expected_internal(
    run_root: Path, tmp_path: Path
) -> tuple[ResearchRunConfig, bytes, Path]:
    explicit = load_explicit_config(DEFAULT)
    target = run_root.resolve()
    config = ResearchRunConfig(
        workspace=target,
        run_id=f"research-{hashlib.sha256(str(target).encode()).hexdigest()[:16]}",
        input_mode=ResearchBriefPayload.model_validate(
            yaml.safe_load(BRIEF.read_text(encoding="utf-8"))
        ).intake_mode,
        ranking_policy=explicit.ranking_policy,
        acquisition_budget=explicit.acquisition_budget,
        config_sha256=explicit.sha256,
    )
    data = json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    external = tmp_path / "external-internal.json"
    external.write_bytes(data)
    run_root.mkdir()
    return config, data, external


def test_exact_regular_internal_config_precreation_is_recoverable(
    tmp_path: Path,
) -> None:
    """An exact regular precreated binding may resume under the same transaction."""
    run_root = tmp_path / "run"
    _, data, _ = _expected_internal(run_root, tmp_path)
    (run_root / INTERNAL).write_bytes(data)

    result = _init(run_root)

    assert result.exit_code == 0
    assert (
        CLI.invoke(app, ["research", "status", str(run_root), "--json"]).exit_code == 0
    )


@pytest.mark.parametrize("alias", ("symlink", "hardlink"))
def test_init_rejects_aliased_existing_internal_config(
    tmp_path: Path, alias: str
) -> None:
    """Internal identity must come from one regular single-link inode."""
    run_root = tmp_path / "run"
    _, _, external = _expected_internal(run_root, tmp_path)
    if alias == "symlink":
        (run_root / INTERNAL).symlink_to(external)
    else:
        os.link(external, run_root / INTERNAL)

    result = _init(run_root)

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "RESEARCH_RUN_INVALID"


def test_internal_config_create_race_fails_without_success_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conflicting entry created at no-replace publication must fail closed."""
    run_root = tmp_path / "run"
    original = PinnedRoot.write_file_noreplace

    def inject_create(
        storage: PinnedRoot, relative: Path, data: bytes, *, mode: int
    ) -> None:
        if relative == INTERNAL:
            (run_root / INTERNAL).write_text("{}", encoding="utf-8")
        original(storage, relative, data, mode=mode)

    monkeypatch.setattr(PinnedRoot, "write_file_noreplace", inject_create)

    result = _init(run_root)

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "RESEARCH_RUN_INVALID"
    assert "phase" not in payload
    monkeypatch.undo()
    assert (
        CLI.invoke(app, ["research", "status", str(run_root), "--json"]).exit_code == 2
    )


def test_internal_config_replace_race_fails_final_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacement during run initialization must be caught before success."""
    run_root = tmp_path / "run"
    original = ResearchArtifactLifecycle.persist_structured
    injected = False

    def inject_replace(
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
            staged = tmp_path / "wrong-internal.json"
            staged.write_text("{}", encoding="utf-8")
            os.replace(staged, run_root / INTERNAL)
        return result

    monkeypatch.setattr(ResearchArtifactLifecycle, "persist_structured", inject_replace)

    result = _init(run_root)

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "RESEARCH_RUN_INVALID"
    assert "phase" not in payload
    monkeypatch.undo()
    assert (
        CLI.invoke(app, ["research", "status", str(run_root), "--json"]).exit_code == 2
    )


def test_direct_orchestrator_rejects_hardlinked_internal_config(
    tmp_path: Path,
) -> None:
    """Direct initialization uses the same single-link internal binding boundary."""
    run_root = tmp_path / "run"
    config, _, external = _expected_internal(run_root, tmp_path)
    os.link(external, run_root / INTERNAL)
    brief = ResearchBriefPayload.model_validate(
        yaml.safe_load(BRIEF.read_text(encoding="utf-8"))
    )

    with pytest.raises(ValueError, match="link count"):
        ResearchOrchestrator().initialize(
            config, brief, explicit_config=DEFAULT.read_bytes()
        )

"""Offline acceptance boundaries for V0.2 design benchmarks."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from typer.testing import CliRunner

from envresearch.benchmarks.design_registry import (
    DesignBenchmarkRegistry,
    replay_design_fixture,
)
from envresearch.benchmarks.design_scoring import (
    RESEARCH_QUALITY_DIMENSIONS,
    RESEARCH_QUALITY_RUBRIC_VERSION,
)
from envresearch.cli import app
from envresearch.connectors.contracts import ConnectorCoverage
from envresearch.connectors.gateway import LiteratureGateway
from envresearch.kernel.events import EventLog
from envresearch.kernel.gates import GateRequest, GateStore
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.workflow import ResearchRunPhase
from envresearch.storage.artifacts import ArtifactStore

FIXTURE_ROOT = Path("benchmarks/design/fixtures")
FIXTURES = (
    "broad-topic",
    "structured-brief",
    "structured-connector-outage",
    "restricted-data",
    "connector-outage",
    "blocking-review",
    "interrupted-run",
)
CLI = CliRunner()


@pytest.mark.parametrize("fixture", FIXTURES)
def test_design_fixture_has_auditable_expected_terminal_state(fixture: str) -> None:
    """Changing a scenario signal or adding authority outside its inventory fails."""
    result = replay_design_fixture(FIXTURE_ROOT / fixture)

    assert result.actual_phase == result.expected_phase
    assert result.missing_authoritative_files == ()
    assert result.unexpected_authoritative_files == ()
    assert result.replayed_operations > 0


def _write_design_manifest(
    root: Path,
    *,
    tier: int,
    executes_replication_package: bool,
    source: str = "repository-owned synthetic fixture",
) -> None:
    payload = {
        "id": "design-boundary",
        "version": "1.0",
        "tier": tier,
        "source": source,
        "license": "CC0-1.0",
        "input_fixture": "brief.yaml",
        "replay_fixture": "replay.yaml",
        "expected_phase": "waiting_for_agent",
        "expected_artifacts": ["brief.yaml"],
        "rubric_version": RESEARCH_QUALITY_RUBRIC_VERSION,
        "rubric_thresholds": {
            dimension: 3 for dimension in RESEARCH_QUALITY_DIMENSIONS
        },
        "executes_replication_package": executes_replication_package,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "benchmark.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=True), encoding="utf-8"
    )


def test_v02_design_registry_discovers_only_tier_zero_and_one() -> None:
    """The shipped catalog must remain inside the approved V0.2 boundary."""
    catalog = DesignBenchmarkRegistry.discover(FIXTURE_ROOT)

    assert set(catalog) == set(FIXTURES)
    assert {manifest.tier for manifest in catalog.values()} <= {0, 1}
    assert not any(
        manifest.executes_replication_package for manifest in catalog.values()
    )


def test_v02_design_registry_rejects_tier_two_before_execution(
    tmp_path: Path,
) -> None:
    """Allowing Tier 2 would authorize a real replication package by mistake."""
    _write_design_manifest(tmp_path, tier=2, executes_replication_package=True)

    with pytest.raises(ValueError, match="Tier 2 is not allowed in v0.2"):
        DesignBenchmarkRegistry.discover(tmp_path)


def test_v02_design_registry_rejects_execution_flag_at_any_tier(
    tmp_path: Path,
) -> None:
    """A Tier 0 label must not disguise an executable package."""
    _write_design_manifest(tmp_path, tier=0, executes_replication_package=True)

    with pytest.raises(ValueError, match="replication package execution"):
        DesignBenchmarkRegistry.discover(tmp_path)


def test_tier_one_is_metadata_and_evidence_only(tmp_path: Path) -> None:
    """Tier 1 sources must identify an open paper without gaining execution fields."""
    _write_design_manifest(
        tmp_path,
        tier=1,
        executes_replication_package=False,
        source="https://example.org/open-paper",
    )

    catalog = DesignBenchmarkRegistry.discover(tmp_path)

    assert catalog["design-boundary"].source == "https://example.org/open-paper"


def test_replay_reports_a_declared_but_missing_authoritative_artifact(
    tmp_path: Path,
) -> None:
    """The inventory audit is exact in both the missing and unexpected directions."""
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT / "broad-topic", fixture)
    manifest_path = fixture / "benchmark.yaml"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    payload["expected_artifacts"].append("artifacts/not-produced.json")
    manifest_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    result = replay_design_fixture(fixture)

    assert result.missing_authoritative_files == (Path("artifacts/not-produced.json"),)
    assert result.overall_pass is False


def test_replay_phase_depends_on_real_orchestrator_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A production phase mutation must be visible to the acceptance replay."""
    original = ResearchOrchestrator._summarize

    def mutated(orchestrator: ResearchOrchestrator) -> object:
        summary = original(orchestrator)
        return summary.model_copy(update={"phase": ResearchRunPhase.DEGRADED})

    monkeypatch.setattr(ResearchOrchestrator, "_summarize", mutated)

    result = replay_design_fixture(FIXTURE_ROOT / "broad-topic")

    assert result.actual_phase is ResearchRunPhase.DEGRADED
    assert result.actual_phase is not result.expected_phase


@pytest.mark.parametrize("fixture", ("connector-outage", "structured-connector-outage"))
def test_connector_outage_replay_retains_gateway_degradation_as_bound_input(
    fixture: str,
) -> None:
    """The outage fixture must retain the real gateway result in worker provenance."""
    result = replay_design_fixture(FIXTURE_ROOT / fixture)

    assert result.connector_coverage == ConnectorCoverage(
        connector_id="repository-local-literature",
        connector_version="1.0",
        status="degraded",
        records=(),
        reason_code="CONNECTOR_UNAVAILABLE",
        connector_reason_code="EXPORT_MISSING",
        diagnostic="repository literature export is intentionally unavailable",
    )
    assert result.connector_coverage_bound is True
    assert Path("connector-receipts/local-export-unavailable.json") in (
        result.actual_authoritative_files
    )


def test_connector_outage_replay_fails_if_gateway_identity_is_mutated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bypassing the declared gateway identity must invalidate the fixture replay."""

    def mutated(
        _gateway: LiteratureGateway, _connector: object, _query: object
    ) -> ConnectorCoverage:
        return ConnectorCoverage(
            connector_id="mutated-connector",
            connector_version="9.9",
            status="degraded",
            records=(),
            reason_code="CONNECTOR_UNAVAILABLE",
            connector_reason_code="EXPORT_MISSING",
            diagnostic="mutated outage",
        )

    monkeypatch.setattr(LiteratureGateway, "literature_search", mutated)

    with pytest.raises(ValueError, match="connector identity"):
        replay_design_fixture(FIXTURE_ROOT / "connector-outage")


def test_ci_does_not_reference_external_replication_sources() -> None:
    """PR automation must never acquire or execute external replication assets."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    forbidden = ("openicpsr", "icpsr.org", "wget ", "curl ", "benchmark/cases")

    assert not any(token in workflow.casefold() for token in forbidden)


def test_research_docs_state_v02_stop_boundary() -> None:
    """The operator guide must not imply empirical execution is part of V0.2."""
    text = Path("docs/research-workflow.md").read_text(encoding="utf-8")

    assert "approved analysis-plan.yaml" in text
    assert "does not execute empirical analysis" in text


@pytest.mark.parametrize(
    ("gate_id", "base_gate_id", "specific_conditions"),
    (
        ("gate-1-r2", "gate-1", {"selected_candidate_id": "charter-air"}),
        (
            "data-gate-r2",
            "data-gate",
            {"approved_risk_reasons": ["private-air: credentials required"]},
        ),
        (
            "final-gate-r2",
            "final-gate",
            {"accepted_major_ids": ["major-spillover-risk"]},
        ),
    ),
)
def test_documented_gate_decision_shape_uses_the_public_cli(
    tmp_path: Path,
    gate_id: str,
    base_gate_id: str,
    specific_conditions: dict[str, object],
) -> None:
    """The operator contract must remain executable through the supported CLI."""
    store = GateStore(ArtifactStore(tmp_path), EventLog(tmp_path / "events.jsonl"))
    store.request(
        GateRequest(id=gate_id, name="Research charter", requested_by="agent")
    )
    conditions = {
        "gate_context": {
            "base_gate_id": base_gate_id,
            "gate_id": gate_id,
            "revision": 2,
        },
        **specific_conditions,
    }
    conditions_path = tmp_path / "gate-1-conditions.json"
    conditions_path.write_text(json.dumps(conditions), encoding="utf-8")

    result = CLI.invoke(
        app,
        [
            "gate",
            "decide",
            str(tmp_path),
            gate_id,
            "--approve",
            "--actor",
            "human-reviewer",
            "--rationale",
            "Selected the strongest current charter.",
            "--conditions-json",
            str(conditions_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    decision = json.loads(result.stdout)["decision"]
    assert decision["conditions"] == conditions
    assert set(json.loads(conditions_path.read_text(encoding="utf-8"))) == {
        "gate_context",
        *specific_conditions,
    }


def test_research_guide_documents_each_runnable_gate_transition() -> None:
    """Removing any public gate step would strand an operator mid-run."""
    text = Path("docs/research-workflow.md").read_text(encoding="utf-8")

    assert text.count(
        'uv run envresearch research gate-decide "$RUN_ROOT" "$CURRENT_GATE_ID"'
    ) >= 3
    assert text.count("--principal-capability-file") >= 3
    assert text.count("--conditions-json") >= 3
    assert text.count('uv run envresearch research advance "$RUN_ROOT" --json') >= 3
    assert "conditions-only JSON" in text
    assert "gate-1-r2" in text
    assert "final-gate-rN" in text
    assert "pending_gate_ids" in text
    assert "selected_candidate_id" in text
    assert "accepted_major_ids" in text


def test_research_guide_states_connector_preissuance_boundary() -> None:
    """A provider swap must not be promised after literature work is issued."""
    text = Path("docs/research-workflow.md").read_text(encoding="utf-8")

    assert "API/library-only in V0.2" in text
    assert "ResearchOrchestrator.bind_literature_coverage()" in text
    assert "BEFORE" in text
    assert "work-orders/map-literature.json" in text
    assert "post-issuance hot-swap is unsupported" in text


def test_ci_retains_every_required_quality_command() -> None:
    """A green workflow must still exercise the complete repository gate."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    for command in (
        "uv sync --locked --dev",
        "uv run ruff check .",
        "uv run mypy src",
        "uv run pytest --cov=envresearch --cov-report=term-missing --cov-fail-under=80",
    ):
        assert command in workflow


def test_docs_preserve_tier_and_method_schema_contracts() -> None:
    """Benchmark authority and profile authoring must not silently widen."""
    tiers = Path("docs/benchmark-onboarding.md").read_text(encoding="utf-8")
    methods = Path("docs/method-profile-authoring.md").read_text(encoding="utf-8")

    for phrase in (
        "Tier 0: repository-owned synthetic fixtures, allowed in CI.",
        "Tier 1: open published-paper design benchmarks, metadata/evidence only in v0.2.",
        "Tier 2: real replication packages, prohibited until v0.3 approval.",
    ):
        assert phrase in tiers
    for field in (
        "profile_id",
        "version",
        "family",
        "compatible_estimands",
        "required_data_structures",
        "required_features",
        "identifying_assumptions",
        "incompatibility_rules",
        "mandatory_diagnostics",
        "falsification_checks",
        "fallback_profiles",
        "analysis_plan_fields",
        "methodological_references",
        "estimator_entrypoint",
    ):
        assert f"`{field}`" in methods


def test_readme_local_markdown_links_resolve() -> None:
    """The primary handoff must not send operators to a missing local guide."""
    readme = Path("README.md")
    links = re.findall(r"\[[^]]+\]\(([^)]+)\)", readme.read_text(encoding="utf-8"))
    local_links = [target.split("#", 1)[0] for target in links if "://" not in target]

    assert local_links
    assert all((readme.parent / target).is_file() for target in local_links)


def test_agent_protocol_assigns_hash_binding_to_the_trusted_queue() -> None:
    """The adapter must not be documented as authoring a trusted receipt."""
    text = Path("docs/agent-work-order-protocol.md").read_text(encoding="utf-8")

    assert "unchanged order_id and candidate bytes" in text
    assert "trusted queue verifies the anchored order_hash" in text
    assert "same-order/same-bytes retry is idempotent" in text
    assert "conflicting duplicate is rejected" in text

"""Actual offline orchestration replay for repository-owned design fixtures."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]
from pydantic import JsonValue

from envresearch.benchmarks import design_scenarios as scenarios
from envresearch.benchmarks.blind_registry import (
    BlindBenchmarkRegistry,
    LoadedBlindCase,
)
from envresearch.benchmarks.blind_scoring_contracts import CaseEvaluation
from envresearch.benchmarks.design_connectors import (
    RepositoryUnavailableLiteratureConnector,
)
from envresearch.benchmarks.design_contract import (
    DesignReplayScenario,
    DesignReplaySpec,
)
from envresearch.benchmarks.design_files import PinnedFixtureRoot
from envresearch.benchmarks.design_inventory import authoritative_inventory
from envresearch.benchmarks.design_registry import (
    DesignBenchmarkManifest,
    DesignBenchmarkRegistry,
)
from envresearch.benchmarks.design_result import DesignFixtureReplay
from envresearch.benchmarks.design_scoring import ResearchQualityScorer
from envresearch.connectors.contracts import ConnectorCoverage, LiteratureQuery
from envresearch.connectors.gateway import literature_gateway
from envresearch.kernel.gates import GateDecision
from envresearch.models.enums import GateStatus
from envresearch.models.intake import ResearchBriefPayload, ResearchIntakeMode
from envresearch.research.node_inputs import LITERATURE_COVERAGE_PATH
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.run_config import load_explicit_config
from envresearch.research.workflow import (
    ARTIFACT_PATHS,
    ResearchRunConfig,
    data_risk_reasons,
)
from envresearch.workers.contracts import WorkOrder

_DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "configs/research-default.yaml"


def replay_design_fixture(root: Path) -> DesignFixtureReplay:
    """Drive one fixture through the real orchestrator and reopen durable state."""
    with PinnedFixtureRoot(root) as pinned:
        manifest = DesignBenchmarkRegistry.load_pinned(pinned, Path("benchmark.yaml"))
        brief = _read_brief(pinned, manifest)
        replay_spec = _read_replay_spec(pinned, manifest)
        blind_case = _read_blind_descriptor(pinned, manifest)
    with tempfile.TemporaryDirectory(prefix=f"envresearch-{manifest.id}-") as temp:
        run_root = Path(temp) / "run"
        blind_evaluation = _replay_blind_descriptor(
            blind_case, Path(temp) / "blind-run"
        )
        config = _run_config(run_root, brief)
        explicit = load_explicit_config(_DEFAULT_CONFIG)
        orchestrator = ResearchOrchestrator()
        operations = 1
        try:
            orchestrator.initialize(config, brief, explicit_config=explicit.data)
            scenario_coverage = (
                _bind_connector_outage(orchestrator, brief)
                if replay_spec.scenario is DesignReplayScenario.CONNECTOR_DEGRADATION
                else None
            )
            operations += _enter_and_approve(orchestrator, brief)
            if replay_spec.scenario is DesignReplayScenario.INTERRUPTED_RECOVERY:
                orchestrator.close()
                orchestrator = ResearchOrchestrator()
                orchestrator.initialize(config, brief, explicit_config=explicit.data)
                operations += 1
            operations += _drive_design(
                replay_spec, orchestrator, coverage=scenario_coverage
            )
        finally:
            orchestrator.close()
        recovered = ResearchOrchestrator()
        try:
            summary = recovered.initialize(config, brief, explicit_config=explicit.data)
            operations += 1
            coverage, coverage_bound = _recovered_coverage(
                recovered,
                expected=(
                    replay_spec.scenario is DesignReplayScenario.CONNECTOR_DEGRADATION
                ),
            )
            quality = ResearchQualityScorer(
                run_root, recovered.lifecycle, recovered.semantics
            ).evaluate(manifest.rubric_thresholds)
        finally:
            recovered.close()
        actual = authoritative_inventory(run_root)
        expected = tuple(sorted(manifest.expected_artifacts))
        missing = tuple(sorted(set(expected) - set(actual)))
        unexpected = tuple(sorted(set(actual) - set(expected)))
        return DesignFixtureReplay(
            benchmark_id=manifest.id,
            expected_phase=manifest.expected_phase,
            actual_phase=summary.phase,
            completed_nodes=summary.completed_nodes,
            actual_authoritative_files=actual,
            missing_authoritative_files=missing,
            unexpected_authoritative_files=unexpected,
            replayed_operations=operations,
            connector_coverage=coverage,
            connector_coverage_bound=coverage_bound,
            quality_scores=quality.scores,
            threshold_results=quality.threshold_results,
            open_blockers=quality.open_blockers,
            blind_case_evaluation=blind_evaluation,
            overall_pass=(
                quality.overall_pass
                and summary.phase is manifest.expected_phase
                and not missing
                and not unexpected
            ),
        )


def _replay_blind_descriptor(
    loaded: LoadedBlindCase | None,
    _run_root: Path,
) -> CaseEvaluation | None:
    """Validate opt-in descriptor loading without unauthenticated blind writes."""
    if loaded is None:
        return None
    return None


def _read_blind_descriptor(
    pinned: PinnedFixtureRoot, manifest: DesignBenchmarkManifest
) -> LoadedBlindCase | None:
    if manifest.blind_manifest is None:
        return None
    blind_manifest, loaded = BlindBenchmarkRegistry.load_pinned_case(
        pinned, manifest.blind_manifest
    )
    if blind_manifest.rubric_version != manifest.blind_rubric_version:
        raise ValueError("blind descriptor rubric does not match design manifest")
    return loaded


def _read_brief(
    pinned: PinnedFixtureRoot, manifest: DesignBenchmarkManifest
) -> ResearchBriefPayload:
    data = pinned.read(manifest.input_fixture, description="design input fixture")
    payload = yaml.safe_load(data)
    if not isinstance(payload, dict):
        raise TypeError("design input fixture must contain one YAML mapping")
    return ResearchBriefPayload.model_validate(payload)


def _read_replay_spec(
    pinned: PinnedFixtureRoot, manifest: DesignBenchmarkManifest
) -> DesignReplaySpec:
    data = pinned.read(manifest.replay_fixture, description="design replay fixture")
    payload = yaml.safe_load(data)
    if not isinstance(payload, dict):
        raise TypeError("design replay fixture must contain one YAML mapping")
    return DesignReplaySpec.model_validate(payload)


def _run_config(run_root: Path, brief: ResearchBriefPayload) -> ResearchRunConfig:
    explicit = load_explicit_config(_DEFAULT_CONFIG)
    return ResearchRunConfig(
        workspace=run_root,
        run_id="design-fixture-run",
        input_mode=brief.intake_mode,
        ranking_policy=explicit.ranking_policy,
        acquisition_budget=explicit.acquisition_budget,
        require_claim_verified_citations=explicit.require_claim_verified_citations,
        citation_catalog_roots=explicit.citation_catalog_roots,
        config_sha256=explicit.sha256,
    )


def _drive_design(
    replay_spec: DesignReplaySpec,
    orchestrator: ResearchOrchestrator,
    *,
    coverage: ConnectorCoverage | None,
) -> int:
    operations = 0
    scenario = replay_spec.scenario
    _submit(
        orchestrator,
        "map-literature",
        scenarios.literature(coverage=coverage),
    )
    feasibility = scenarios.feasibility(
        restricted=scenario is DesignReplayScenario.CONDITIONAL_DATA_APPROVAL
    )
    _submit(orchestrator, "inspect-data", feasibility)
    orchestrator.advance()
    operations += 3
    if scenario is DesignReplayScenario.CONDITIONAL_DATA_APPROVAL:
        _approve_gate(
            orchestrator,
            "data-gate",
            approved_risk_reasons=list(
                data_risk_reasons(feasibility, orchestrator.config.acquisition_budget)
            ),
        )
        orchestrator.advance()
        operations += 2
    _submit(orchestrator, "define-estimand", scenarios.estimand())
    orchestrator.advance()
    operations += 2
    ref = orchestrator.lifecycle.artifact_ref(Path("artifacts/estimand-spec.yaml"))
    estimand_ref = (
        f"artifact:{ref.artifact_id}@{ref.artifact_version}#sha256:{ref.content_hash}"
    )
    for node_id, payload in (
        ("rank-methods", scenarios.methods(estimand_ref)),
        ("draft-identification", scenarios.memo(estimand_ref)),
    ):
        _submit(orchestrator, node_id, payload)
        orchestrator.advance()
        operations += 2
    review = (
        scenarios.blocking_review()
        if scenario is DesignReplayScenario.BLOCKING_REVIEW_REVISION
        else scenarios.resolved_review()
    )
    _submit(orchestrator, "review-design", review)
    orchestrator.advance()
    operations += 2
    if scenario is DesignReplayScenario.BLOCKING_REVIEW_REVISION:
        orchestrator.request_revision(
            "review-design",
            reason="Close the blocking benchmark finding",
            actor="fixture-human-reviewer",
            principal_capability=orchestrator.queue.control.storage.read_file(
                Path("principals/revision.capability"),
                description="benchmark revision capability",
                required_mode=0o600,
            ).decode(),
        )
        _submit(orchestrator, "review-design", scenarios.resolved_blocking_review())
        orchestrator.advance()
        operations += 3
    _submit(orchestrator, "compose-plan", scenarios.analysis_plan(estimand_ref))
    orchestrator.advance()
    _approve_gate(orchestrator, "final-gate", accepted_major_ids=[])
    orchestrator.advance()
    return operations + 4


def _bind_connector_outage(
    orchestrator: ResearchOrchestrator, brief: ResearchBriefPayload
) -> ConnectorCoverage:
    connector = RepositoryUnavailableLiteratureConnector()
    query = LiteratureQuery(text=brief.broad_topic or "structured research brief")
    coverage = literature_gateway().literature_search(connector, query)
    if (
        coverage.connector_id != connector.connector_id
        or coverage.connector_version != connector.connector_version
    ):
        raise ValueError("connector identity does not match outage fixture")
    if coverage.status != "degraded" or coverage.reason_code != "CONNECTOR_UNAVAILABLE":
        raise ValueError("outage fixture requires degraded connector coverage")
    orchestrator.bind_literature_coverage(coverage)
    return coverage


def _recovered_coverage(
    orchestrator: ResearchOrchestrator, *, expected: bool
) -> tuple[ConnectorCoverage | None, bool]:
    if not expected:
        return None, False
    coverage = orchestrator.lifecycle.read_payload(
        LITERATURE_COVERAGE_PATH, ConnectorCoverage
    )
    reference = orchestrator.lifecycle.artifact_ref(LITERATURE_COVERAGE_PATH)
    order = WorkOrder.model_validate_json(
        (orchestrator.workspace / "work-orders/map-literature.json").read_bytes()
    )
    literature = orchestrator.lifecycle.read_artifact(
        Path("artifacts/literature-map.json")
    )
    bound = (
        reference in order.input_artifacts
        and reference in literature.envelope.input_artifacts
    )
    if not bound:
        raise ValueError("connector coverage is not bound to literature work")
    return coverage, bound


def _enter_and_approve(
    orchestrator: ResearchOrchestrator, brief: ResearchBriefPayload
) -> int:
    if brief.intake_mode is ResearchIntakeMode.BROAD_TOPIC:
        node_id = "frame-charters"
        selected = "charter-air"
        payload: object = scenarios.candidate_charters(brief)
    else:
        node_id = "normalize-brief"
        selected = "charter-structured"
        payload = scenarios.normalized_charter(brief)
    _submit(orchestrator, node_id, payload)
    orchestrator.advance()
    _approve_gate(
        orchestrator,
        "gate-1",
        selected_candidate_id=selected,
    )
    orchestrator.advance()
    return 4


def _approve_gate(
    orchestrator: ResearchOrchestrator, gate_id: str, **specific: object
) -> None:
    conditions = orchestrator.bound_gates.decision_conditions(gate_id)
    context = orchestrator.bound_gates.active_context(gate_id)
    if context is None:
        raise RuntimeError(f"{gate_id} context was not created")
    orchestrator.decide_gate(
        gate_id,
        GateDecision(
            status=GateStatus.APPROVED,
            decided_by="human-reviewer",
            rationale="Repository-owned synthetic fixture approval.",
            conditions=cast(dict[str, JsonValue], {**conditions, **specific}),
        ),
        orchestrator.queue.control.storage.read_file(
            Path("principals/gate.capability"),
            description="benchmark gate capability",
            required_mode=0o600,
        ).decode(),
    )


def _submit(orchestrator: ResearchOrchestrator, node_id: str, payload: object) -> None:
    filename = ARTIFACT_PATHS[node_id][0].name
    source = orchestrator.workspace / "fixture-inputs" / node_id / filename
    source.parent.mkdir(parents=True, exist_ok=True)
    value = (
        payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    )
    source.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    order = orchestrator.queue.read_order(node_id)
    orchestrator.queue.submit(node_id, source, expected_order_hash=order.order_hash)
    orchestrator.accept_submission(node_id)

"""Real Research and Factory composition for completed canonical cases."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from envresearch.benchmarks import design_scenarios as scenarios
from envresearch.factory.design_resolver import V02ApprovedDesignResolver
from envresearch.factory.service import FactoryRunService
from envresearch.kernel.gates import GateDecision
from envresearch.models.artifact import ArtifactRef
from envresearch.models.design import (
    AnalysisPlanPayload,
    ClaimMode,
    DesignReviewPayload,
    EstimandSpecPayload,
    EstimandType,
    IdentificationMemoMetadata,
    MethodCandidate,
    MethodCandidateRole,
    MethodCandidatesPayload,
)
from envresearch.models.enums import GateStatus
from envresearch.models.evidence import DataFeasibilityPayload, DatasetCandidate
from envresearch.models.intake import ResearchBriefPayload, ResearchIntakeMode
from envresearch.models.method_screening import (
    MethodRejectionEvidence,
    MethodRequirementKind,
)
from envresearch.models.principal import PrincipalKind
from envresearch.paper.release import PaperReleaseService
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.stop_contracts import ResearchStopInspection
from envresearch.research.stop_inspection import inspect_research_stop
from envresearch.research.workflow import (
    ARTIFACT_PATHS,
    ResearchRunConfig,
    ResearchRunPhase,
)

if TYPE_CHECKING:
    from envresearch.personal_validation.canonical_cases import CaseExecutionContext

_PLAN = Path("artifacts/analysis-plan.yaml")
_ESTIMAND = Path("artifacts/estimand-spec.yaml")
_LIMITATION = (
    "Model-conditional marginal implicit price; residual confounding and "
    "housing-market sorting remain outside the registered design."
)


@dataclass(frozen=True, slots=True)
class ResearchCaseServices:
    """Production-owned builder over a disposable Research authority."""

    design_root: Path
    factory_root: Path
    clock: Callable[[], datetime]
    _state: dict[str, object] = field(default_factory=dict, init=False, repr=False)

    def build_hedonic_approved_design(self, include_rdd_rejection: bool) -> ArtifactRef:
        prior = self._state.get("design_ref")
        if prior is not None:
            if self._state.get("with_rdd") is not include_rdd_rejection:
                raise ValueError("one research root cannot change method semantics")
            return cast(ArtifactRef, prior)
        orchestrator = self._orchestrator()
        summary = orchestrator.initialize(_config(self.design_root), _brief())
        if summary.phase is not ResearchRunPhase.COMPLETE:
            _drive_to_review(orchestrator, include_rdd_rejection)
            _submit(
                orchestrator,
                "review-design",
                DesignReviewPayload(review_id="personal-review-clear", findings=()),
            )
            orchestrator.advance()
            _submit(orchestrator, "compose-plan", _plan(_estimand_token(orchestrator)))
            orchestrator.advance()
            _approve(orchestrator, "final-gate", accepted_major_ids=[])
            if orchestrator.advance().phase is not ResearchRunPhase.COMPLETE:
                raise RuntimeError("canonical research design did not complete")
        resolver = V02ApprovedDesignResolver(orchestrator, self.factory_root)
        current = resolver.build(
            orchestrator.lifecycle.artifact_ref(_PLAN), _final_context(orchestrator)
        )
        self._state.update(
            orchestrator=orchestrator,
            resolver=resolver,
            design_ref=current,
            with_rdd=include_rdd_rejection,
        )
        return current

    def execute_until_blocking_review(self) -> None:
        if self._state.get("blocked"):
            return
        orchestrator = self._orchestrator()
        summary = orchestrator.initialize(_config(self.design_root), _brief())
        if summary.phase is not ResearchRunPhase.BLOCKED:
            _drive_to_review(orchestrator, False)
            _submit(orchestrator, "review-design", scenarios.blocking_review())
            if orchestrator.advance().phase is not ResearchRunPhase.BLOCKED:
                raise RuntimeError("canonical correct-stop did not block")
        self._state.update(orchestrator=orchestrator, blocked=True)

    def inspect_research_stop(self) -> ResearchStopInspection:
        if not self._state.get("blocked"):
            raise RuntimeError("research stop is unavailable before execution")
        return inspect_research_stop(self.design_root)

    @property
    def design_ref(self) -> ArtifactRef:
        reference = self._state.get("design_ref")
        if not isinstance(reference, ArtifactRef):
            raise TypeError("approved design is not built")
        return reference

    @property
    def estimand_ref(self) -> ArtifactRef:
        orchestrator = cast(ResearchOrchestrator, self._state["orchestrator"])
        return orchestrator.lifecycle.artifact_ref(_ESTIMAND)

    def factory_service(
        self, release_service: PaperReleaseService
    ) -> FactoryRunService:
        resolver = self._state.get("resolver")
        if not isinstance(resolver, V02ApprovedDesignResolver):
            raise TypeError("approved design resolver is not built")
        return FactoryRunService(
            design_resolver=resolver, release_service=release_service
        )

    def close(self) -> None:
        orchestrator = self._state.get("orchestrator")
        if isinstance(orchestrator, ResearchOrchestrator):
            orchestrator.close()

    def _orchestrator(self) -> ResearchOrchestrator:
        current = self._state.get("orchestrator")
        return (
            current
            if isinstance(current, ResearchOrchestrator)
            else ResearchOrchestrator(self.clock)
        )


def run_success_case(context: CaseExecutionContext) -> ArtifactRef:
    design_ref = context.research.build_hedonic_approved_design(False)
    release_ref = context.paper.build_clean_hedonic_release()
    return context.research.factory_service(context.paper.release_service).assemble(
        design_ref, release_ref
    )


def run_incompatibility_case(context: CaseExecutionContext) -> ArtifactRef:
    design_ref = context.research.build_hedonic_approved_design(True)
    release_ref = context.paper.build_clean_hedonic_release()
    return context.research.factory_service(context.paper.release_service).assemble(
        design_ref, release_ref
    )


def _drive_to_review(orchestrator: ResearchOrchestrator, with_rdd: bool) -> None:
    _submit(orchestrator, "frame-charters", scenarios.candidate_charters(_brief()))
    orchestrator.advance()
    _approve(orchestrator, "gate-1", selected_candidate_id="charter-air")
    orchestrator.advance()
    _submit(orchestrator, "map-literature", scenarios.literature())
    _submit(orchestrator, "inspect-data", _feasibility())
    orchestrator.advance()
    _submit(orchestrator, "define-estimand", _estimand())
    orchestrator.advance()
    token = _estimand_token(orchestrator)
    _submit(orchestrator, "rank-methods", _methods(token, with_rdd))
    orchestrator.advance()
    _submit(orchestrator, "draft-identification", _memo(token))
    orchestrator.advance()


def _brief() -> ResearchBriefPayload:
    return ResearchBriefPayload(
        intake_mode=ResearchIntakeMode.BROAD_TOPIC,
        broad_topic="Repository-owned synthetic hedonic valuation",
    )


def _config(root: Path) -> ResearchRunConfig:
    return ResearchRunConfig(
        workspace=root,
        run_id="personal-hedonic-design",
        input_mode=ResearchIntakeMode.BROAD_TOPIC,
    )


def _feasibility() -> DataFeasibilityPayload:
    candidate = DatasetCandidate(
        dataset_id="repository-hedonic",
        source="benchmarks/econometrics/valuation-core/runner/data/hedonic.csv",
        public_access=True,
        requires_credentials=False,
        clear_license=True,
        license="repository-owned-synthetic",
        estimated_download_bytes=0,
        estimated_local_storage_bytes=4096,
        estimated_api_calls=0,
        estimated_external_cost=Decimal(0),
        estimated_elapsed_seconds=1,
        suitable_for_design=True,
        suitability_reason="Contains market prices and environmental attributes.",
        access_reason="Repository-owned synthetic fixture.",
        data_structures=("panel",),
        available_features=(
            "environmental_attribute",
            "georeferenced_measurement",
            "market_prices",
        ),
    )
    return DataFeasibilityPayload(
        research_design="Hedonic panel comparison",
        candidates=(candidate,),
        recommendation="Use the local synthetic Hedonic fixture.",
        evidence_reason="Access, license, structure, and features are explicit.",
    )


def _estimand() -> EstimandSpecPayload:
    return EstimandSpecPayload(
        estimand_id="implicit-price",
        estimand_type=EstimandType.CAUSAL,
        population="sample",
        unit="cny",
        exposure_or_treatment="environmental attribute",
        outcome="market price",
        comparison_or_counterfactual="same market absent the attribute change",
        time_horizon="annual",
        target_parameter="implicit-price",
        evidence_refs=("evidence-1",),
        assumption_refs=("market-equilibrium",),
    )


def _methods(token: str, with_rdd: bool) -> MethodCandidatesPayload:
    candidates = [
        MethodCandidate(
            method_profile_ref="hedonic@0.2.0",
            role=MethodCandidateRole.PRIMARY,
            rank=1,
            estimand_compatible=True,
            required_assumption_refs=("market-equilibrium",),
            required_data_structure=("panel",),
            diagnostics=("registered diagnostics",),
            fallback_or_limitations=(_LIMITATION,),
        ),
        MethodCandidate(
            method_profile_ref="spatiotemporal@0.2.0",
            role=MethodCandidateRole.ALTERNATIVE,
            rank=2,
            estimand_compatible=True,
            required_assumption_refs=("market-equilibrium",),
            required_data_structure=("panel",),
            diagnostics=("registered diagnostics",),
            fallback_or_limitations=(_LIMITATION,),
        ),
    ]
    if with_rdd:
        candidates.append(
            MethodCandidate(
                method_profile_ref="rdd@0.2.0",
                role=MethodCandidateRole.REJECTED,
                rank=3,
                estimand_compatible=False,
                required_assumption_refs=("continuity-at-cutoff",),
                required_data_structure=("panel",),
                diagnostics=("cutoff support diagnostic",),
                fallback_or_limitations=("RDD requires its assignment variables.",),
                rejection_evidence=MethodRejectionEvidence(
                    requirement_kind=MethodRequirementKind.FEATURE_SET,
                    requirement_refs=("known_cutoff", "running_variable"),
                    explanation=(
                        "The fixture contains neither a known cutoff nor a running variable."
                    ),
                ),
            )
        )
    return MethodCandidatesPayload(estimand_ref=token, candidates=tuple(candidates))


def _memo(token: str) -> dict[str, object]:
    metadata = IdentificationMemoMetadata(
        estimand_ref=token,
        primary_method_profile_ref="hedonic@0.2.0",
        alternative_method_profile_refs=("spatiotemporal@0.2.0",),
        assumption_refs=("market-equilibrium",),
        threat_refs=("sorting",),
        diagnostic_refs=("registered diagnostics",),
        evidence_refs=("evidence-1",),
        residual_risks=("Residual confounding remains possible.",),
    )
    return {
        "metadata": metadata.model_dump(mode="json"),
        "body": "# Identification\n\nEstimate the registered implicit-price relationship.\n",
    }


def _plan(token: str) -> AnalysisPlanPayload:
    return AnalysisPlanPayload(
        estimand_ref=token,
        estimand_type=EstimandType.CAUSAL,
        estimand=_estimand(),
        primary_method_profile_ref="hedonic@0.2.0",
        alternative_method_profile_refs=("spatiotemporal@0.2.0",),
        data_boundaries=("price-base:p2025", "repository-owned synthetic data"),
        assumptions=("market-equilibrium",),
        diagnostics=("registered diagnostics",),
        exclusion_rules=("exclude incomplete records",),
        robustness_plan=("registered sensitivity",),
        fallback_rules=(_LIMITATION,),
        claim_mode=ClaimMode.CAUSAL,
    )


def _submit(orchestrator: ResearchOrchestrator, node_id: str, payload: object) -> None:
    source = (
        orchestrator.workspace
        / "fixture-inputs"
        / node_id
        / ARTIFACT_PATHS[node_id][0].name
    )
    source.parent.mkdir(parents=True, exist_ok=True)
    value = (
        payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    )
    source.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    order = orchestrator.queue.read_order(node_id)
    orchestrator.queue.submit(node_id, source, expected_order_hash=order.order_hash)
    orchestrator.accept_submission(node_id)


def _approve(orchestrator: ResearchOrchestrator, gate_id: str, **extra: object) -> None:
    conditions = {**orchestrator.bound_gates.decision_conditions(gate_id), **extra}
    orchestrator.decide_gate(
        gate_id,
        GateDecision(
            status=GateStatus.APPROVED,
            decided_by=orchestrator.principals.human(PrincipalKind.GATE).principal_id,
            rationale="Repository-owned synthetic fixture approval.",
            conditions=cast(dict[str, JsonValue], conditions),
            decided_at=orchestrator._clock(),
        ),
        orchestrator.queue.control.storage.read_file(
            Path("principals/gate.capability"),
            description="personal validation gate capability",
            required_mode=0o600,
        ).decode(),
    )


def _estimand_token(orchestrator: ResearchOrchestrator) -> str:
    reference = orchestrator.lifecycle.artifact_ref(_ESTIMAND)
    return (
        f"artifact:{reference.artifact_id}@{reference.artifact_version}"
        f"#sha256:{reference.content_hash}"
    )


def _final_context(orchestrator: ResearchOrchestrator) -> ArtifactRef:
    context = orchestrator.bound_gates.active_context("final-gate")
    if context is None or context.context_hash is None:
        raise RuntimeError("final gate context is absent")
    return ArtifactRef(
        artifact_id="final-gate-context",
        artifact_version=context.revision,
        content_hash=context.context_hash,
    )


__all__ = ["ResearchCaseServices", "run_incompatibility_case", "run_success_case"]

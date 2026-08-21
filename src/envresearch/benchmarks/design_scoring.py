"""Versioned research-quality scoring from recovered authoritative state."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from envresearch.kernel.decision_log import DecisionLog
from envresearch.kernel.gates import GateRequest
from envresearch.models.design import (
    AnalysisPlanPayload,
    DesignReviewPayload,
    EstimandSpecPayload,
    IdentificationMemoMetadata,
    MethodCandidateRole,
    MethodCandidatesPayload,
    ResearchQualityScores,
    ReviewSeverity,
)
from envresearch.models.enums import GateStatus
from envresearch.models.evidence import DataFeasibilityPayload, LiteratureMapPayload
from envresearch.models.intake import CandidateCharter
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.audit_state import DECISION_LOG_PATH, MANIFEST_PATH
from envresearch.research.gate_context import BoundGateContext
from envresearch.research.semantic_validation import SemanticSubmissionValidator
from envresearch.research.workflow import ResearchRunConfig, data_risk_reasons

RESEARCH_QUALITY_RUBRIC_VERSION = "research-quality-v1"
RESEARCH_QUALITY_DIMENSIONS = (
    "contribution_clarity",
    "evidence_coverage",
    "data_feasibility",
    "estimand_precision",
    "identification_credibility",
    "uncertainty_disclosure",
)
CONTROLLED_MINIMUM_SCORE = 3


class QualityThresholdResult(BaseModel):
    """One declared threshold evaluated with the controlled minimum floor."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dimension: str
    score: int = Field(ge=1, le=5)
    threshold: int = Field(ge=1, le=5)
    passed: bool


class ResearchQualityEvaluation(BaseModel):
    """Auditable scores, blockers, and aggregate policy result."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scores: ResearchQualityScores
    threshold_results: tuple[QualityThresholdResult, ...]
    open_blockers: tuple[str, ...]
    overall_pass: bool


class ResearchQualityScorer:
    """Compute the fixed rubric from recovered artifacts and decision records."""

    def __init__(
        self,
        workspace: Path,
        lifecycle: ResearchArtifactLifecycle,
        semantics: SemanticSubmissionValidator,
    ) -> None:
        self.workspace = workspace
        self.lifecycle = lifecycle
        self.semantics = semantics

    def evaluate(self, thresholds: dict[str, int]) -> ResearchQualityEvaluation:
        """Revalidate terminal state, calculate scores, and apply thresholds."""
        self._require_authoritative_audit_state()
        self.semantics.validate_final()
        scores = ResearchQualityScores(
            contribution_clarity=self._score_contribution_clarity(),
            evidence_coverage=self._score_evidence_coverage(),
            data_feasibility=self._score_data_feasibility(),
            estimand_precision=self._score_estimand_precision(),
            identification_credibility=self._score_identification_credibility(),
            uncertainty_disclosure=self._score_uncertainty_disclosure(),
        )
        values = scores.model_dump()
        results = tuple(
            QualityThresholdResult(
                dimension=dimension,
                score=values[dimension],
                threshold=max(thresholds[dimension], CONTROLLED_MINIMUM_SCORE),
                passed=(
                    values[dimension]
                    >= max(thresholds[dimension], CONTROLLED_MINIMUM_SCORE)
                ),
            )
            for dimension in RESEARCH_QUALITY_DIMENSIONS
        )
        review = self._review()
        blockers = tuple(
            finding.finding_id
            for finding in review.findings
            if finding.severity is ReviewSeverity.BLOCKING and not finding.resolved
        )
        return ResearchQualityEvaluation(
            scores=scores,
            threshold_results=results,
            open_blockers=blockers,
            overall_pass=all(item.passed for item in results) and not blockers,
        )

    def _require_authoritative_audit_state(self) -> None:
        manifest = self.workspace / MANIFEST_PATH
        if not manifest.is_file() or manifest.is_symlink():
            raise ValueError("research quality requires a durable run manifest")
        entries = DecisionLog(self.workspace / DECISION_LOG_PATH).read_all()
        if not any(
            entry.decision_kind == "terminal_approval" and entry.status == "approved"
            for entry in entries
        ):
            raise ValueError("research quality requires an audited terminal approval")

    def _score_contribution_clarity(self) -> int:
        charter = self.lifecycle.read_payload(
            Path("artifacts/research-charter.yaml"), CandidateCharter
        )
        distinct = sum(
            claim.different_exposure_or_policy or claim.different_outcome_or_mechanism
            for claim in charter.distinctness_claims
        )
        return 5 if charter.research_question.strip() and distinct >= 2 else 3

    def _score_evidence_coverage(self) -> int:
        literature = self._literature()
        estimand = self._estimand()
        source_ids = {source.source_id for source in literature.sources}
        rows = {row.evidence_id: row for row in literature.evidence_rows}
        linked = all(row.source_id in source_ids for row in rows.values())
        supports_estimand = set(estimand.evidence_refs) <= rows.keys()
        return 5 if source_ids and rows and linked and supports_estimand else 2

    def _score_data_feasibility(self) -> int:
        feasibility = self.lifecycle.read_payload(
            Path("artifacts/data-feasibility.yaml"), DataFeasibilityPayload
        )
        suitable = tuple(
            item for item in feasibility.candidates if item.suitable_for_design
        )
        if not suitable:
            return 1
        complete = all(
            item.source.strip()
            and item.access_reason
            and item.access_reason.strip()
            and item.data_structures
            and item.available_features
            for item in suitable
        )
        if not complete:
            return 2
        restricted = any(
            not item.public_access
            or item.requires_credentials
            or not item.clear_license
            for item in suitable
        )
        if not restricted:
            return 5
        return 4 if self._has_exact_data_approval(feasibility) else 2

    def _has_exact_data_approval(self, feasibility: DataFeasibilityPayload) -> bool:
        config = ResearchRunConfig.model_validate_json(
            (self.workspace / "research-run-config.json").read_bytes()
        )
        expected = data_risk_reasons(feasibility, config.acquisition_budget)
        contexts = sorted((self.workspace / "gate-contexts/data-gate").glob("*.json"))
        if not contexts:
            return False
        context = BoundGateContext.model_validate_json(contexts[-1].read_bytes())
        gate_id = context.gate_id
        gate = GateRequest.model_validate_json(
            (self.workspace / "gates" / f"{gate_id}.json").read_bytes()
        )
        if gate.status is not GateStatus.APPROVED or gate.decision is None:
            return False
        actual = gate.decision.conditions.get("approved_risk_reasons")
        return (
            isinstance(actual, list)
            and all(isinstance(item, str) for item in actual)
            and len(actual) == len(set(actual))
            and tuple(actual) == expected
        )

    def _score_estimand_precision(self) -> int:
        estimand = self._estimand()
        fields = (
            estimand.population,
            estimand.unit,
            estimand.exposure_or_treatment,
            estimand.outcome,
            estimand.comparison_or_counterfactual,
            estimand.time_horizon,
            estimand.target_parameter,
        )
        return 5 if all(value is not None and value.strip() for value in fields) else 2

    def _score_identification_credibility(self) -> int:
        methods = self.lifecycle.read_payload(
            Path("artifacts/method-candidates.json"), MethodCandidatesPayload
        )
        memo = self._memo()
        plan = self._plan()
        retained = tuple(
            item
            for item in methods.candidates
            if item.role is not MethodCandidateRole.REJECTED
        )
        rejected = tuple(
            item
            for item in methods.candidates
            if item.role is MethodCandidateRole.REJECTED
        )
        credible = (
            len(methods.candidates) >= 2
            and all(item.estimand_compatible for item in retained)
            and all(
                not item.estimand_compatible and item.rejection_evidence is not None
                for item in rejected
            )
            and bool(memo.assumption_refs)
            and bool(memo.threat_refs)
            and bool(memo.diagnostic_refs)
            and bool(plan.diagnostics)
            and bool(plan.robustness_plan)
        )
        return 5 if credible else 2

    def _score_uncertainty_disclosure(self) -> int:
        memo = self._memo()
        plan = self._plan()
        review = self._review()
        disclosed = (
            bool(memo.residual_risks)
            and bool(plan.data_boundaries)
            and bool(plan.fallback_rules)
            and all(
                finding.resolved or finding.residual_risk is not None
                for finding in review.findings
            )
        )
        return 5 if disclosed else 2

    def _literature(self) -> LiteratureMapPayload:
        return cast(
            LiteratureMapPayload,
            self.lifecycle.read_payload(
                Path("artifacts/literature-map.json"), LiteratureMapPayload
            ),
        )

    def _estimand(self) -> EstimandSpecPayload:
        return cast(
            EstimandSpecPayload,
            self.lifecycle.read_payload(
                Path("artifacts/estimand-spec.yaml"), EstimandSpecPayload
            ),
        )

    def _memo(self) -> IdentificationMemoMetadata:
        envelope, _body = self.lifecycle.store.read_markdown(
            Path("artifacts/identification-memo.md")
        )
        return IdentificationMemoMetadata.model_validate(
            envelope.provenance.get("identification")
        )

    def _review(self) -> DesignReviewPayload:
        return cast(
            DesignReviewPayload,
            self.lifecycle.read_payload(
                Path("artifacts/design-review-findings.json"), DesignReviewPayload
            ),
        )

    def _plan(self) -> AnalysisPlanPayload:
        return cast(
            AnalysisPlanPayload,
            self.lifecycle.read_payload(
                Path("artifacts/analysis-plan.yaml"), AnalysisPlanPayload
            ),
        )

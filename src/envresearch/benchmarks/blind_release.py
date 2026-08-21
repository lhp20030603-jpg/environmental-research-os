"""Exact held-out family and integrity gates for benchmark release."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from envresearch.benchmarks.blind_scoring_contracts import (
    CaseEvaluation,
    StrictScoringModel,
)
from envresearch.models.artifact import ArtifactRef

_PASS_THRESHOLD = Decimal("3.0")
CANONICAL_RELEASE_BLOCKER = "verified authority enrollment is required"
CANONICAL_METHOD_FAMILIES = frozenset(
    {
        "rct",
        "did_event_study",
        "rdd",
        "iv",
        "synthetic_control",
        "hedonic",
        "measurement",
        "systematic_review_meta_analysis",
    }
)


class ReleaseCohort(StrEnum):
    PILOT = "pilot"
    HELD_OUT = "held_out"


class CaseForRelease(StrictScoringModel):
    case_id: str
    method_family: str
    recommendation_ref: ArtifactRef
    evaluation: CaseEvaluation
    cohort: ReleaseCohort
    leakage_passed: bool
    citation_passed: bool
    unresolved: bool

    @field_validator("case_id", "method_family")
    @classmethod
    def require_case_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("case ID and method family must be nonblank")
        return value

    @model_validator(mode="after")
    def require_evaluation_binding(self) -> CaseForRelease:
        if self.recommendation_ref != self.evaluation.recommendation_ref:
            raise ValueError("case recommendation ref must match its evaluation")
        return self


class ReleaseReadinessReport(StrictScoringModel):
    released: bool
    held_out_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    family_means: tuple[tuple[str, Decimal], ...]
    blockers: tuple[str, ...]


class ReleaseEvaluator:
    def evaluate(
        self,
        cases: tuple[CaseForRelease, ...],
    ) -> ReleaseReadinessReport:
        self._validate_cases(cases)
        held_out = tuple(
            case for case in cases if case.cohort is ReleaseCohort.HELD_OUT
        )
        families = _group_by_family(held_out)
        means = tuple(
            (family, _mean(case.evaluation.weighted_score for case in members))
            for family, members in sorted(families.items())
        )
        mean_map = dict(means)
        passed = sum(_case_passed(case) for case in held_out)
        blockers: list[str] = []
        blockers.append(CANONICAL_RELEASE_BLOCKER)
        if len(held_out) < 16:
            blockers.append("requires at least 16 held-out cases")
        if any(case.cohort is ReleaseCohort.PILOT for case in cases):
            blockers.append("Pilot8 calibration cases are never release eligible")
        if passed < 14:
            blockers.append("requires at least 14 passed cases")
        for family in CANONICAL_METHOD_FAMILIES:
            members = families.get(family, ())
            if len(members) < 2:
                blockers.append(f"family {family} requires at least two cases")
            if not any(_case_passed(case) for case in members):
                blockers.append(f"family {family} requires at least one passing case")
            if members and mean_map[family] < _PASS_THRESHOLD:
                blockers.append(f"family {family} mean must be at least 3.0")
        if any(not case.leakage_passed for case in cases):
            blockers.append("all cases require passing leakage validation")
        if any(not case.citation_passed for case in cases):
            blockers.append("all cases require passing citation validation")
        if any(case.unresolved for case in cases):
            blockers.append("all cases must be resolved")
        return ReleaseReadinessReport(
            released=not blockers,
            held_out_cases=len(held_out),
            passed_cases=passed,
            family_means=means,
            blockers=tuple(blockers),
        )

    @staticmethod
    def _validate_cases(cases: tuple[CaseForRelease, ...]) -> None:
        ids = tuple(case.case_id for case in cases)
        refs = tuple(case.recommendation_ref for case in cases)
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate case IDs are not allowed")
        if len(refs) != len(set(refs)):
            raise ValueError("duplicate case recommendation refs are not allowed")
        if any(
            case.recommendation_ref != case.evaluation.recommendation_ref
            for case in cases
        ):
            raise ValueError("case recommendation ref must match its evaluation")
        held_out_families = {
            case.method_family
            for case in cases
            if case.cohort is ReleaseCohort.HELD_OUT
        }
        if held_out_families and held_out_families != CANONICAL_METHOD_FAMILIES:
            raise ValueError(
                "held-out cases must use exactly the canonical method families"
            )


def _group_by_family(
    cases: tuple[CaseForRelease, ...],
) -> dict[str, tuple[CaseForRelease, ...]]:
    grouped: dict[str, list[CaseForRelease]] = {}
    for case in cases:
        grouped.setdefault(case.method_family, []).append(case)
    return {family: tuple(members) for family, members in grouped.items()}


def _case_passed(case: CaseForRelease) -> bool:
    """Revalidate the minimum weighted score at the release boundary."""
    return case.evaluation.passed and case.evaluation.weighted_score >= _PASS_THRESHOLD


def _mean(values: Iterable[Decimal]) -> Decimal:
    scores = tuple(values)
    return sum(scores, Decimal(0)) / Decimal(len(scores))

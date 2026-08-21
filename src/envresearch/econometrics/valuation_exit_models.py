"""Frozen contracts for the compact V0.3.1 Valuation Core exit."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.exit_models import ExpectedComparison, _name
from envresearch.econometrics.report import LocalAnalysisReference
from envresearch.models.artifact import ArtifactRef

STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)
ValuationCaseRole = Literal["green", "scientific-failure", "integrity-failure"]
ValuationFamily = Literal[
    "hedonic-pricing", "travel-cost", "contingent-valuation", "dce-clogit"
]
GREEN_FAMILIES = frozenset(
    {"hedonic-pricing", "travel-cost", "contingent-valuation", "dce-clogit"}
)
EXACT_CASES = frozenset(
    {
        ("green-hedonic", "hedonic-pricing", "green"),
        ("green-travel-cost", "travel-cost", "green"),
        ("green-cv", "contingent-valuation", "green"),
        ("green-dce", "dce-clogit", "green"),
        ("fail-hedonic-sensitivity", "hedonic-pricing", "scientific-failure"),
        ("fail-travel-dispersion", "travel-cost", "scientific-failure"),
        ("fail-cv-wtp", "contingent-valuation", "scientific-failure"),
        ("fail-dce-choice-set", "dce-clogit", "scientific-failure"),
        ("integrity-output-tamper", "hedonic-pricing", "integrity-failure"),
    }
)


class ValuationExitCase(BaseModel):
    model_config = STRICT
    case_id: str
    family: ValuationFamily
    role: ValuationCaseRole
    case_ref: ArtifactRef
    data_ref: ArtifactRef

    @model_validator(mode="after")
    def canonical_case(self) -> ValuationExitCase:
        _name(self.case_id, "case id")
        return self


class ValuationExitCaseInput(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.valuation-exit-case.v1"]
    case_id: str
    family: ValuationFamily
    data_ref: ArtifactRef
    spec: AnalysisSpec
    integrity_mutation: Literal["none", "output-byte"] = "none"

    @model_validator(mode="after")
    def method_matches(self) -> ValuationExitCaseInput:
        if self.spec.method_id != self.family:
            raise ValueError("valuation exit family does not match its analysis spec")
        return self


class ValuationExitManifest(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.valuation-exit-manifest.v1"]
    manifest_id: str
    cases: tuple[ValuationExitCase, ...] = Field(min_length=9, max_length=9)

    @model_validator(mode="after")
    def exact_matrix(self) -> ValuationExitManifest:
        _name(self.manifest_id, "manifest id")
        ids = tuple(item.case_id for item in self.cases)
        refs = tuple(item.case_ref for item in self.cases)
        data = tuple(item.data_ref for item in self.cases)
        if len(set(ids)) != 9 or len(set(refs)) != 9 or len(set(data)) != 9:
            raise ValueError("valuation cases and references must be unique")
        matrix = {(item.case_id, item.family, item.role) for item in self.cases}
        if matrix != EXACT_CASES:
            raise ValueError("valuation manifest must contain the exact nine-case matrix")
        return self


class ValuationCaseExpectation(BaseModel):
    model_config = STRICT
    case_id: str
    role: ValuationCaseRole
    expected_code: str | None = None
    comparisons: tuple[ExpectedComparison, ...] = ()

    @model_validator(mode="after")
    def coherent(self) -> ValuationCaseExpectation:
        if self.role == "green" and (self.expected_code or not self.comparisons):
            raise ValueError("green valuation case requires comparisons only")
        if self.role != "green" and (not self.expected_code or self.comparisons):
            raise ValueError("failed valuation case requires one exact code only")
        keys = tuple((item.output_name, item.selector) for item in self.comparisons)
        if len(keys) != len(set(keys)):
            raise ValueError("valuation comparisons must be unique")
        return self


class ValuationExitExpectationCatalog(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.valuation-exit-expectations.v1"]
    manifest_id: str
    cases: tuple[ValuationCaseExpectation, ...] = Field(min_length=9, max_length=9)

    @model_validator(mode="after")
    def unique_cases(self) -> ValuationExitExpectationCatalog:
        if len({item.case_id for item in self.cases}) != 9:
            raise ValueError("valuation expectation case ids must be unique")
        return self


class ValuationExitCatalogBinding(BaseModel):
    """Evaluator-owned catalog authorization for one exact runner manifest."""

    model_config = STRICT
    schema_version: Literal["econometrics.valuation-exit-catalog-binding.v1"]
    manifest_ref: ArtifactRef
    catalog_ref: ArtifactRef


class ValuationExitCaseReceipt(BaseModel):
    model_config = STRICT
    case_id: str
    role: ValuationCaseRole
    analysis_ref: LocalAnalysisReference


class ValuationExitRun(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.valuation-exit-run.v1"]
    manifest_ref: ArtifactRef
    receipts: tuple[ValuationExitCaseReceipt, ...]


class ValuationExitAnalysisBinding(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.valuation-exit-analysis-binding.v1"]
    case_ref: ArtifactRef
    analysis_ref: LocalAnalysisReference
    data_sha256: str


class ValuationExitCaseOutcome(BaseModel):
    model_config = STRICT
    case_id: str
    role: ValuationCaseRole
    status: Literal["matched", "unresolved"]
    analysis_ref: LocalAnalysisReference
    findings: tuple[str, ...] = ()


class ValuationExitReport(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.valuation-exit-report.v1"]
    status: Literal["passed", "failed"]
    run_ref: ArtifactRef
    catalog_ref: ArtifactRef
    outcomes: tuple[ValuationExitCaseOutcome, ...] = Field(min_length=9, max_length=9)

    @model_validator(mode="after")
    def all_or_nothing(self) -> ValuationExitReport:
        matched = all(item.status == "matched" and not item.findings for item in self.outcomes)
        if (self.status == "passed") != matched:
            raise ValueError("valuation report status conflicts with case outcomes")
        if len({item.case_id for item in self.outcomes}) != 9:
            raise ValueError("valuation report case ids must be unique")
        return self

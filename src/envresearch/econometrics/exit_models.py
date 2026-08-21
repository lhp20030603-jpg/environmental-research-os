"""Frozen contracts for the blinded V0.3 evidence-runner exit."""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.report import LocalAnalysisReference
from envresearch.models.artifact import ArtifactRef

STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)
CaseRole = Literal["green", "assumption-failure", "integrity-failure"]
Family = Literal[
    "rct-itt",
    "did-event-study",
    "rdd-local-linear",
    "iv-2sls",
    "synthetic-control",
    "environmental-measurement",
    "meta-analysis",
    "panel-fe",
]
GREEN_FAMILIES = frozenset(
    {
        "rct-itt",
        "did-event-study",
        "rdd-local-linear",
        "iv-2sls",
        "synthetic-control",
        "environmental-measurement",
        "meta-analysis",
        "panel-fe",
    }
)
FAILURE_FAMILIES = GREEN_FAMILIES - {"panel-fe"}
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,95}$")


def _name(value: str, label: str) -> str:
    if not _NAME.fullmatch(value):
        raise ValueError(f"{label} is not canonical")
    return value


class ExitCase(BaseModel):
    model_config = STRICT
    case_id: str
    family: Family
    role: CaseRole
    case_ref: ArtifactRef
    data_ref: ArtifactRef

    @field_validator("case_id")
    @classmethod
    def case_name(cls, value: str) -> str:
        return _name(value, "case id")


class ExitCaseInput(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.v03-exit-case.v1"]
    case_id: str
    family: Family
    data_ref: ArtifactRef
    spec: AnalysisSpec
    integrity_mutation: Literal["none", "output-byte"] = "none"

    @model_validator(mode="after")
    def method_matches(self) -> ExitCaseInput:
        if self.spec.method_id != self.family:
            raise ValueError("exit case family does not match its analysis spec")
        return self


class V03ExitManifest(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.v03-exit-manifest.v1"]
    manifest_id: str
    cases: tuple[ExitCase, ...] = Field(min_length=16, max_length=16)
    expectation_catalog_ref: ArtifactRef

    @field_validator("manifest_id")
    @classmethod
    def manifest_name(cls, value: str) -> str:
        return _name(value, "manifest id")

    @model_validator(mode="after")
    def exact_blind_matrix(self) -> V03ExitManifest:
        ids = tuple(item.case_id for item in self.cases)
        refs = tuple(item.case_ref for item in self.cases)
        data_refs = tuple(item.data_ref for item in self.cases)
        if (
            len(set(ids)) != len(ids)
            or len({item.content_hash for item in refs}) != len(refs)
            or len(set(data_refs)) != len(data_refs)
        ):
            raise ValueError("exit cases and references must be unique")
        green = {item.family for item in self.cases if item.role == "green"}
        failures = {
            item.family for item in self.cases if item.role == "assumption-failure"
        }
        integrity = tuple(
            item for item in self.cases if item.role == "integrity-failure"
        )
        if (
            green != GREEN_FAMILIES
            or failures != FAILURE_FAMILIES
            or len(integrity) != 1
        ):
            raise ValueError("exit manifest must contain the exact V0.3 matrix")
        return self


class ExpectedComparison(BaseModel):
    model_config = STRICT
    comparison_type: Literal["exact", "json", "csv"]
    output_name: str
    selector: str | None = None
    expected: str | int | float | bool
    atol: float = Field(default=0.0, ge=0.0)
    rtol: float = Field(default=0.0, ge=0.0)

    @field_validator("output_name")
    @classmethod
    def output(cls, value: str) -> str:
        return _name(value, "output name")

    @field_validator("atol", "rtol")
    @classmethod
    def finite_tolerance(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("comparison tolerances must be finite")
        return value

    @model_validator(mode="after")
    def selector_shape(self) -> ExpectedComparison:
        if (self.comparison_type == "exact") != (self.selector is None):
            raise ValueError("only exact comparisons omit a selector")
        if isinstance(self.expected, float) and not math.isfinite(self.expected):
            raise ValueError("expected numeric values must be finite")
        if self.comparison_type == "exact" and (
            not isinstance(self.expected, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.expected)
        ):
            raise ValueError("exact comparison requires a SHA-256 digest")
        return self


def numeric_matches(observed: float, comparison: ExpectedComparison) -> bool:
    """Apply the registered inclusive absolute-plus-relative tolerance."""
    if (
        not isinstance(comparison.expected, (int, float))
        or isinstance(comparison.expected, bool)
        or not math.isfinite(observed)
    ):
        return False
    expected = float(comparison.expected)
    return abs(observed - expected) <= comparison.atol + comparison.rtol * abs(expected)


class CaseExpectation(BaseModel):
    model_config = STRICT
    case_id: str
    role: CaseRole
    expected_code: str | None = None
    comparisons: tuple[ExpectedComparison, ...] = ()

    @model_validator(mode="after")
    def coherent(self) -> CaseExpectation:
        if self.role == "green" and (
            self.expected_code is not None or not self.comparisons
        ):
            raise ValueError("green case requires comparisons only")
        if self.role != "green" and (not self.expected_code or self.comparisons):
            raise ValueError("failure case requires one exact code only")
        keys = tuple((item.output_name, item.selector) for item in self.comparisons)
        if len(keys) != len(set(keys)):
            raise ValueError("case comparisons must be unique")
        return self


class ExitExpectationCatalog(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.v03-exit-expectations.v1"]
    manifest_id: str
    cases: tuple[CaseExpectation, ...] = Field(min_length=16, max_length=16)

    @model_validator(mode="after")
    def unique_cases(self) -> ExitExpectationCatalog:
        if len({item.case_id for item in self.cases}) != len(self.cases):
            raise ValueError("expectation case ids must be unique")
        return self


class ExitCaseReceipt(BaseModel):
    model_config = STRICT
    case_id: str
    role: CaseRole
    analysis_ref: LocalAnalysisReference


class ExitAnalysisBinding(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.v03-exit-analysis-binding.v1"]
    case_ref: ArtifactRef
    analysis_ref: LocalAnalysisReference


class V03ExitRun(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.v03-exit-run.v1"]
    manifest_ref: ArtifactRef
    receipts: tuple[ExitCaseReceipt, ...]


class ExitCaseOutcome(BaseModel):
    model_config = STRICT
    case_id: str
    role: CaseRole
    status: Literal["matched", "unresolved"]
    analysis_ref: LocalAnalysisReference
    findings: tuple[str, ...] = ()


class V03ExitReport(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.v03-exit-report.v1"]
    status: Literal["passed", "failed"]
    run_ref: ArtifactRef
    catalog_ref: ArtifactRef
    outcomes: tuple[ExitCaseOutcome, ...] = Field(min_length=16, max_length=16)

    @model_validator(mode="after")
    def all_or_nothing(self) -> V03ExitReport:
        matched = all(
            item.status == "matched" and not item.findings for item in self.outcomes
        )
        if (self.status == "passed") != matched:
            raise ValueError("exit report status conflicts with case outcomes")
        if len({item.case_id for item in self.outcomes}) != len(self.outcomes):
            raise ValueError("exit report case ids must be unique")
        return self

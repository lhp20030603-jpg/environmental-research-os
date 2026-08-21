"""R-first DiD planning with separate author and derived-result namespaces."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from envresearch.models.artifact import ArtifactRef, ResearchArtifact, verify_artifact
from envresearch.replication.container import ContainerPlan, allocate_output_namespace
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ApprovedTier2Intake,
    ContainerRuntimeProfile,
    ReplicationBudget,
    Tier2ExpectedOutput,
)

_FROZEN_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True)
_R_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_REPORT_SCHEMA_VERSION = "derived-did-event-study-v1"


def _require_r_name(value: str, field_name: str) -> str:
    """Permit only a literal R identifier, never executable source text."""
    if not _R_NAME.fullmatch(value):
        raise ValueError(f"{field_name} must be an R-safe column name")
    return value


class AuthorReproductionMapping(BaseModel):
    """Approved author script, expected-output mapping, and isolated workspace."""

    model_config = _FROZEN_STRICT

    script_path: Path
    output_mappings: tuple[Tier2ExpectedOutput, ...]
    input_root: Path
    output_root: Path
    budget: ReplicationBudget

    @field_validator("script_path")
    @classmethod
    def require_relative_script(cls, value: Path) -> Path:
        if (
            value.is_absolute()
            or ".." in value.parts
            or not value.as_posix().endswith(".R")
        ):
            raise ValueError("author script path must be a safe relative R file")
        return value

    @field_validator("output_mappings")
    @classmethod
    def require_output_mapping(
        cls, value: tuple[Tier2ExpectedOutput, ...]
    ) -> tuple[Tier2ExpectedOutput, ...]:
        if not value:
            raise ValueError("author output mappings must be nonempty")
        if len({item.path for item in value}) != len(value):
            raise ValueError("author output mappings must be unique")
        return value


class DidEventStudySpec(BaseModel):
    """Validated data layout and identifier-only configuration for derived DiD."""

    model_config = _FROZEN_STRICT

    data_path: Path
    unit_column: str
    time_column: str
    treatment_column: str
    cohort_column: str
    outcome_column: str
    reference_period: int
    input_root: Path
    output_root: Path
    budget: ReplicationBudget

    @field_validator("data_path")
    @classmethod
    def require_relative_data(cls, value: Path) -> Path:
        if value.is_absolute() or ".." in value.parts or value.suffix.lower() != ".csv":
            raise ValueError(
                "analysis-ready data path must be a safe relative CSV file"
            )
        return value

    @field_validator(
        "unit_column",
        "time_column",
        "treatment_column",
        "cohort_column",
        "outcome_column",
    )
    @classmethod
    def require_safe_columns(cls, value: str, info: object) -> str:
        return _require_r_name(value, getattr(info, "field_name", "column"))

    @model_validator(mode="after")
    def require_distinct_columns(self) -> DidEventStudySpec:
        columns = (
            self.unit_column,
            self.time_column,
            self.treatment_column,
            self.cohort_column,
            self.outcome_column,
        )
        if len(set(columns)) != len(columns):
            raise ValueError("DiD column names must be distinct")
        return self


class CallawaySantAnnaEstimate(BaseModel):
    """One finite group-time ATT estimate from Callaway-Sant'Anna."""

    model_config = _FROZEN_STRICT

    group: int
    time: int
    estimate: float

    @field_validator("estimate")
    @classmethod
    def require_finite_estimate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Callaway-Sant'Anna estimate must be finite")
        return value


class CallawaySantAnnaConfidenceInterval(BaseModel):
    """One finite, ordered group-time confidence interval."""

    model_config = _FROZEN_STRICT

    group: int
    time: int
    lower: float
    upper: float

    @field_validator("lower", "upper")
    @classmethod
    def require_finite_bound(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Callaway-Sant'Anna confidence bound must be finite")
        return value

    @model_validator(mode="after")
    def require_ordered_bounds(self) -> CallawaySantAnnaConfidenceInterval:
        if self.lower > self.upper:
            raise ValueError("Callaway-Sant'Anna confidence interval is inverted")
        return self


class CallawaySantAnnaConfiguration(BaseModel):
    """Required, fixed estimator configuration accompanying completed results."""

    model_config = _FROZEN_STRICT

    estimator: Literal["did::att_gt"]
    control_group: Literal["nevertreated"]


class CallawaySantAnnaResult(BaseModel):
    """Explicit status for the optional group-time ATT diagnostic."""

    model_config = _FROZEN_STRICT

    status: Literal["completed", "unsupported"]
    estimates: tuple[CallawaySantAnnaEstimate, ...] = ()
    confidence_intervals: tuple[CallawaySantAnnaConfidenceInterval, ...] = ()
    configuration: CallawaySantAnnaConfiguration | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def require_reasoned_status(self) -> CallawaySantAnnaResult:
        if self.status == "unsupported" and not self.reason:
            raise ValueError("unsupported Callaway-Sant'Anna result requires a reason")
        if self.status == "completed" and self.reason is not None:
            raise ValueError(
                "completed Callaway-Sant'Anna result cannot include a reason"
            )
        if self.status == "completed" and (
            not self.estimates
            or not self.confidence_intervals
            or self.configuration is None
        ):
            raise ValueError(
                "completed Callaway-Sant'Anna result requires estimates, confidence intervals, and configuration"
            )
        estimate_keys = tuple(
            (estimate.group, estimate.time) for estimate in self.estimates
        )
        interval_keys = tuple(
            (interval.group, interval.time) for interval in self.confidence_intervals
        )
        if self.status == "completed" and (
            len(estimate_keys) != len(set(estimate_keys))
            or len(interval_keys) != len(set(interval_keys))
        ):
            raise ValueError("Callaway-Sant'Anna group-time evidence is duplicate")
        if self.status == "completed" and (
            len(estimate_keys) != len(interval_keys)
            or set(estimate_keys) != set(interval_keys)
        ):
            raise ValueError(
                "Callaway-Sant'Anna estimates and confidence intervals must align"
            )
        return self


class DidComparisonReport(BaseModel):
    """Machine-readable derived diagnostics, deliberately not a reproduction result."""

    model_config = _FROZEN_STRICT

    schema_version: Literal["derived-did-event-study-v1"]
    treatment_timing: dict[str, object]
    support: dict[str, object]
    balance: dict[str, object]
    event_time: dict[str, object]
    twfe_event_study: dict[str, object]
    callaway_santanna: CallawaySantAnnaResult
    configuration: dict[str, object]
    reproduction_result: None = None


class RDidAdapter:
    """Build isolated author and derived R plans from approved inventory only."""

    def __init__(
        self,
        profile: ContainerRuntimeProfile,
        approved_intake_artifact: ResearchArtifact[ApprovedTier2Intake],
    ) -> None:
        if not isinstance(approved_intake_artifact.payload, ApprovedTier2Intake):
            raise TypeError("approved intake must be a resolved ApprovedTier2Intake")
        verify_artifact(cast(ResearchArtifact[object], approved_intake_artifact))
        approved_intake_ref = _artifact_ref(approved_intake_artifact)
        if approved_intake_ref.artifact_id != "approved-tier2-intake":
            raise ValueError(
                "approved intake reference must identify an approved intake"
            )
        self._profile = profile
        self._approved_intake_ref = approved_intake_ref

    def author_plan(
        self, package: AcquiredPackageInventory, mapping: AuthorReproductionMapping
    ) -> ContainerPlan:
        """Plan untouched author execution only for a reviewed script and outputs."""
        self._require_approved_package(package)
        inventory_paths = {item.path.as_posix() for item in package.files}
        script_path = mapping.script_path.as_posix()
        if script_path not in inventory_paths:
            raise ValueError("author script is not present in acquired inventory")
        _require_declared_comparators(mapping.output_mappings)
        return ContainerPlan(
            image_digest=self._profile.image_digest,
            user=self._profile.nonroot_uid_gid,
            input_root=mapping.input_root,
            output_root=allocate_output_namespace(
                mapping.output_root, "author-reproduction"
            ),
            argv=("Rscript", f"/input/{script_path}"),
            output_namespace="author-reproduction",
            budget=mapping.budget,
        )

    def derived_plan(
        self, package: AcquiredPackageInventory, spec: DidEventStudySpec
    ) -> ContainerPlan:
        """Plan a separately labelled derived DiD/event-study diagnostic."""
        self._require_approved_package(package)
        if spec.data_path.as_posix() not in {
            item.path.as_posix() for item in package.files
        }:
            raise ValueError("analysis-ready data is not present in acquired inventory")
        return ContainerPlan(
            image_digest=self._profile.image_digest,
            user=self._profile.nonroot_uid_gid,
            input_root=spec.input_root,
            output_root=allocate_output_namespace(
                spec.output_root, "derived-did-event-study"
            ),
            argv=("Rscript", "/input/.generated/derived_did.R"),
            output_namespace="derived-did-event-study",
            budget=spec.budget,
            generated_files={"derived_did.R": render_derived_r_script(spec)},
        )

    def _require_approved_package(self, package: AcquiredPackageInventory) -> None:
        if package.approved_intake_ref != self._approved_intake_ref:
            raise ValueError("acquired inventory is not bound to the approved intake")


def _require_declared_comparators(outputs: tuple[Tier2ExpectedOutput, ...]) -> None:
    """Defend against forged models before their output mappings become executable."""
    for output in outputs:
        if output.comparator not in {"exact", "json_numeric", "csv_numeric"}:
            raise ValueError("author output mapping lacks a predeclared comparator")
        try:
            tolerances_are_finite = math.isfinite(
                output.absolute_tolerance
            ) and math.isfinite(output.relative_tolerance)
        except TypeError as error:
            raise ValueError(
                "author output mapping has an invalid tolerance"
            ) from error
        if (
            not tolerances_are_finite
            or output.absolute_tolerance < 0
            or output.relative_tolerance < 0
        ):
            raise ValueError("author output mapping has an invalid tolerance")


def _artifact_ref(
    artifact: ResearchArtifact[ApprovedTier2Intake],
) -> ArtifactRef:
    """Derive the sole admitted approval reference from verified sealed evidence."""
    content_hash = artifact.envelope.content_hash
    if content_hash is None:
        raise ValueError("approved intake artifact is unsealed")
    return ArtifactRef(
        artifact_id=artifact.envelope.artifact_id,
        artifact_version=artifact.envelope.artifact_version,
        content_hash=content_hash,
    )


def render_derived_r_script(spec: DidEventStudySpec) -> str:
    """Render literal R code using identifier-only config and JSON-quoted values."""
    values = {
        "data_path": f"/input/{spec.data_path.as_posix()}",
        "unit_column": spec.unit_column,
        "time_column": spec.time_column,
        "treatment_column": spec.treatment_column,
        "cohort_column": spec.cohort_column,
        "outcome_column": spec.outcome_column,
        "reference_period": spec.reference_period,
        "output_path": "/output/derived-did-event-study/derived-did-event-study-v1.json",
    }
    literal = {name: json.dumps(value) for name, value in values.items()}
    return "\n".join(
        (
            "library(fixest)",
            "library(did)",
            "library(jsonlite)",
            "# Machine-readable diagnostics only; this is not author reproduction.",
            f"data_path <- {literal['data_path']}",
            f"unit_column <- {literal['unit_column']}",
            f"time_column <- {literal['time_column']}",
            f"treatment_column <- {literal['treatment_column']}",
            f"cohort_column <- {literal['cohort_column']}",
            f"outcome_column <- {literal['outcome_column']}",
            f"reference_period <- {literal['reference_period']}",
            f"output_path <- {literal['output_path']}",
            "data <- utils::read.csv(data_path)",
            "event_time <- data[[time_column]] - data[[cohort_column]]",
            "treated_rows <- data[data[[treatment_column]] == 1, , drop = FALSE]",
            "first_treated_period <- if (nrow(treated_rows) == 0) NA_integer_ else min(treated_rows[[time_column]], na.rm = TRUE)",
            "group_time_supported <- all(!is.na(data[[cohort_column]])) && length(unique(data[[cohort_column]])) > 1",
            "twfe_formula <- stats::as.formula(sprintf('%s ~ fixest::i(event_time, ref = %d) | %s + %s', outcome_column, reference_period, unit_column, time_column))",
            "twfe <- fixest::feols(twfe_formula, data = data)",
            "callaway_santanna <- if (group_time_supported) did::att_gt(yname = outcome_column, tname = time_column, idname = unit_column, gname = cohort_column, data = data) else NULL",
            "callaway_estimates <- if (is.null(callaway_santanna)) NULL else data.frame(group = callaway_santanna$group, time = callaway_santanna$t, estimate = callaway_santanna$att)",
            "callaway_confidence_intervals <- if (is.null(callaway_santanna)) NULL else data.frame(group = callaway_santanna$group, time = callaway_santanna$t, lower = callaway_santanna$att - 1.96 * callaway_santanna$se, upper = callaway_santanna$att + 1.96 * callaway_santanna$se)",
            "# did::att_gt is intentionally skipped when cohort support is absent.",
            "report <- list(schema_version = 'derived-did-event-study-v1', treatment_timing = list(first_treated_period = first_treated_period), support = list(group_time_supported = group_time_supported, cohort_count = length(unique(data[[cohort_column]]))), balance = list(units = length(unique(data[[unit_column]])), observations = nrow(data), treated_observations = nrow(treated_rows)), event_time = list(reference_period = reference_period, minimum = min(event_time, na.rm = TRUE), maximum = max(event_time, na.rm = TRUE)), twfe_event_study = list(estimates = as.data.frame(fixest::coeftable(twfe)), confidence_intervals = as.data.frame(stats::confint(twfe))), callaway_santanna = if (is.null(callaway_santanna)) list(status = 'unsupported', reason = 'group-time ATT requires multiple non-missing cohorts') else list(status = 'completed', estimates = callaway_estimates, confidence_intervals = callaway_confidence_intervals, configuration = list(estimator = 'did::att_gt', control_group = 'nevertreated')), configuration = list(unit_column = unit_column, time_column = time_column, treatment_column = treatment_column, cohort_column = cohort_column, outcome_column = outcome_column, reference_period = reference_period))",
            "dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)",
            "jsonlite::write_json(report, output_path, auto_unbox = TRUE)",
        )
    )


def parse_derived_report(payload: object) -> DidComparisonReport:
    """Parse a derived diagnostic while rejecting any reproduction-success field."""
    if not isinstance(payload, dict):
        raise TypeError("derived report must be an object")
    if any("reproduction" in str(key) for key in payload):
        raise ValueError("derived report cannot contain reproduction result fields")
    normalized = dict(payload)
    callaway = normalized.get("callaway_santanna")
    if isinstance(callaway, dict):
        normalized_callaway = dict(callaway)
        for field in ("estimates", "confidence_intervals"):
            if isinstance(normalized_callaway.get(field), list):
                normalized_callaway[field] = tuple(normalized_callaway[field])
        normalized["callaway_santanna"] = normalized_callaway
    return DidComparisonReport.model_validate(normalized)

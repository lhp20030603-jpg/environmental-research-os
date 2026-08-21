"""Wave-1 row semantics for authenticated local CSV snapshots."""

from __future__ import annotations

import math

from envresearch.econometrics.data_snapshot import LocalDataInvalid
from envresearch.econometrics.wave1_contracts import (
    EnvironmentalMeasurementSpec,
    MetaAnalysisSpec,
    RctSpec,
    SyntheticControlSpec,
)

Rows = tuple[tuple[str, ...], ...]


def validate_rct(rows: Rows, positions: dict[str, int], spec: RctSpec) -> None:
    units: set[str] = set()
    assignments: set[str] = set()
    missing_outcomes = 0
    baseline: dict[str, dict[str, list[float]]] = {
        arm: {name: [] for name in spec.columns.baseline_covariates}
        for arm in ("0", "1")
    }
    for row in rows:
        unit = row[positions[spec.columns.unit]]
        assignment = row[positions[spec.columns.assignment]]
        if not unit or unit in units:
            raise LocalDataInvalid("RCT units must be nonmissing and unique")
        if assignment not in {"0", "1"}:
            raise LocalDataInvalid("RCT assignment must be exactly binary")
        outcome = row[positions[spec.columns.outcome]]
        if outcome:
            _finite(outcome, "RCT outcome values")
        else:
            missing_outcomes += 1
        for name in spec.columns.baseline_covariates:
            baseline[assignment][name].append(
                _finite(row[positions[name]], "RCT baseline values")
            )
        units.add(unit)
        assignments.add(assignment)
    if assignments != {"0", "1"}:
        raise LocalDataInvalid("RCT requires both treatment and control assignments")
    if missing_outcomes / len(rows) > spec.max_attrition_rate:
        raise LocalDataInvalid(
            "RCT attrition exceeds its declared threshold",
            code="RCT_ATTRITION_EXCEEDED",
        )
    balance = max(
        _standardized_difference(baseline["0"][name], baseline["1"][name])
        for name in spec.columns.baseline_covariates
    )
    if balance > spec.balance_smd_threshold:
        raise LocalDataInvalid(
            "RCT balance exceeds its declared threshold", code="RCT_BALANCE_EXCEEDED"
        )
    _reject_assignment_leakage(rows, positions, spec)


def _reject_assignment_leakage(
    rows: Rows, positions: dict[str, int], spec: RctSpec
) -> None:
    maximum_levels = max(2, math.isqrt(len(rows)))
    candidates = tuple(
        name
        for name in spec.columns.baseline_covariates
        if 1 < len({row[positions[name]] for row in rows}) <= maximum_levels
    )
    if not candidates:
        return
    profiles: dict[tuple[str, ...], set[str]] = {}
    for row in rows:
        profile = tuple(row[positions[name]] for name in candidates)
        profiles.setdefault(profile, set()).add(row[positions[spec.columns.assignment]])
    if len(profiles) < len(rows) and all(len(arms) == 1 for arms in profiles.values()):
        raise LocalDataInvalid(
            "RCT assignment is deterministic from low-cardinality baselines",
            code="RCT_ASSIGNMENT_LEAKAGE",
        )


def validate_scm(
    rows: Rows, positions: dict[str, int], spec: SyntheticControlSpec
) -> None:
    support: dict[str, set[float]] = {}
    keys: set[tuple[str, float]] = set()
    for row in rows:
        unit = row[positions[spec.columns.unit]]
        time = _finite(row[positions[spec.columns.time]], "SCM time values")
        if not unit or (unit, time) in keys:
            raise LocalDataInvalid("SCM unit-time rows must be unique")
        _finite(row[positions[spec.columns.outcome]], "SCM outcome values")
        for name in spec.columns.predictors:
            _finite(row[positions[name]], "SCM predictor values")
        support.setdefault(unit, set()).add(time)
        keys.add((unit, time))
    if spec.treated_unit not in support or len(support) < 3:
        raise LocalDataInvalid("SCM requires one treated unit and two donors")
    periods = {tuple(sorted(values)) for values in support.values()}
    if len(periods) != 1:
        raise LocalDataInvalid("SCM panel must be balanced")
    times = next(iter(periods))
    if sum(time < spec.intervention_time for time in times) < 2 or not any(
        time >= spec.intervention_time for time in times
    ):
        raise LocalDataInvalid("SCM requires two pre-periods and one post-period")


def validate_measurement(
    rows: Rows, positions: dict[str, int], spec: EnvironmentalMeasurementSpec
) -> None:
    keys: set[tuple[str, str]] = set()
    missing = 0
    allowed_flags = {"", "valid", "below-detection", "above-detection"}
    for row in rows:
        monitor = row[positions[spec.columns.monitor]]
        timestamp = row[positions[spec.columns.timestamp]]
        if not monitor or not timestamp or (monitor, timestamp) in keys:
            raise LocalDataInvalid("measurement monitor-time rows must be unique")
        if row[positions[spec.columns.unit]] != spec.declared_unit:
            raise LocalDataInvalid(
                "measurement unit does not match its declaration",
                code="MEASUREMENT_UNIT_MISMATCH",
            )
        value = row[positions[spec.columns.value]]
        if value:
            numeric = _finite(value, "measurement values")
            if not spec.valid_min <= numeric <= spec.valid_max:
                raise LocalDataInvalid(
                    "measurement value is outside its declared range",
                    code="MEASUREMENT_RANGE_INVALID",
                )
        else:
            missing += 1
        if spec.columns.detection_flag is not None:
            flag = row[positions[spec.columns.detection_flag]]
            if (
                flag not in allowed_flags
                or (value and flag not in {"", "valid"})
                or (not value and flag not in {"below-detection", "above-detection"})
            ):
                raise LocalDataInvalid(
                    "measurement detection flag conflicts with its value",
                    code="MEASUREMENT_DETECTION_FLAG_INVALID",
                )
        keys.add((monitor, timestamp))
    if missing / len(rows) > spec.max_missing_rate:
        raise LocalDataInvalid(
            "measurement missingness exceeds its declared threshold",
            code="MEASUREMENT_MISSINGNESS_EXCEEDED",
        )


def validate_meta(
    rows: Rows, positions: dict[str, int], spec: MetaAnalysisSpec
) -> None:
    studies: set[str] = set()
    for row in rows:
        study = row[positions[spec.columns.study]]
        if not study or study in studies:
            raise LocalDataInvalid("meta-analysis study keys must be unique")
        _finite(row[positions[spec.columns.effect]], "meta-analysis effects")
        variance = _finite(
            row[positions[spec.columns.variance]], "meta-analysis variances"
        )
        if variance <= 0:
            raise LocalDataInvalid("meta-analysis variances must be positive")
        studies.add(study)
    if len(studies) < 3:
        raise LocalDataInvalid(
            "meta-analysis requires at least three studies for sensitivity analysis"
        )


def _finite(value: str, label: str) -> float:
    try:
        numeric = float(value)
    except ValueError as error:
        raise LocalDataInvalid(f"{label} must be finite numeric values") from error
    if not math.isfinite(numeric):
        raise LocalDataInvalid(f"{label} must be finite numeric values")
    return numeric


def _standardized_difference(control: list[float], treated: list[float]) -> float:
    control_mean = sum(control) / len(control)
    treated_mean = sum(treated) / len(treated)
    variances = tuple(
        0.0
        if len(values) < 2
        else sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        for values, mean in ((control, control_mean), (treated, treated_mean))
    )
    pooled = math.sqrt(sum(variances) / 2)
    difference = treated_mean - control_mean
    if pooled == 0:
        return 0.0 if difference == 0 else math.inf
    return abs(difference / pooled)

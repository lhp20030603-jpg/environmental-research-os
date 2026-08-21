"""Reconstruct RCT and measurement evidence from authenticated CSV bytes."""

from __future__ import annotations

import csv
import io
import math

from envresearch.econometrics.wave1_contracts import (
    EnvironmentalMeasurementSpec,
    MetaAnalysisSpec,
    RctSpec,
    SyntheticControlSpec,
)
from envresearch.econometrics.wave1_results import (
    MeasurementResult,
    MetaAnalysisResult,
    RctResult,
    SyntheticControlResult,
)


def wave_result_matches_snapshot(
    data: bytes,
    spec: RctSpec
    | EnvironmentalMeasurementSpec
    | SyntheticControlSpec
    | MetaAnalysisSpec,
    result: RctResult | MeasurementResult | SyntheticControlResult | MetaAnalysisResult,
) -> bool:
    """Recompute raw-data-derived result fields used by the verifier."""
    try:
        rows = tuple(csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))
        if isinstance(spec, RctSpec) and isinstance(result, RctResult):
            return _rct_matches(rows, spec, result)
        if isinstance(spec, EnvironmentalMeasurementSpec) and isinstance(
            result, MeasurementResult
        ):
            return _measurement_matches(rows, spec, result)
        if isinstance(spec, SyntheticControlSpec) and isinstance(
            result, SyntheticControlResult
        ):
            return _scm_matches(rows, spec, result)
        if isinstance(spec, MetaAnalysisSpec) and isinstance(
            result, MetaAnalysisResult
        ):
            return _meta_matches(rows, spec, result)
    except (UnicodeDecodeError, KeyError, TypeError, ValueError, ZeroDivisionError):
        return False
    return False


def _rct_matches(
    rows: tuple[dict[str, str], ...], spec: RctSpec, result: RctResult
) -> bool:
    assignments = tuple(int(row[spec.columns.assignment]) for row in rows)
    outcomes = tuple(row[spec.columns.outcome] for row in rows)
    control_observed = sum(
        arm == 0 and bool(outcome)
        for arm, outcome in zip(assignments, outcomes, strict=True)
    )
    treated_observed = sum(
        arm == 1 and bool(outcome)
        for arm, outcome in zip(assignments, outcomes, strict=True)
    )
    support = result.support
    expected = (
        len(rows),
        assignments.count(0),
        assignments.count(1),
        control_observed,
        treated_observed,
        control_observed + treated_observed,
        len(rows) - control_observed - treated_observed,
    )
    actual = (
        support.total,
        support.assigned_control,
        support.assigned_treated,
        support.control_outcomes_observed,
        support.treated_outcomes_observed,
        support.outcomes_observed,
        support.outcomes_missing,
    )
    expected_balance = tuple(
        (column, _signed_smd(rows, assignments, column))
        for column in spec.columns.baseline_covariates
    )
    return (
        actual == expected
        and math.isclose(result.attrition_rate, expected[-1] / len(rows), abs_tol=1e-12)
        and tuple(item.term for item in result.balance)
        == spec.columns.baseline_covariates
        and all(
            math.isclose(item.smd, expected_smd, abs_tol=1e-10)
            for item, (_, expected_smd) in zip(
                result.balance, expected_balance, strict=True
            )
        )
    )


def _signed_smd(
    rows: tuple[dict[str, str], ...], assignments: tuple[int, ...], column: str
) -> float:
    control = tuple(
        float(row[column])
        for row, arm in zip(rows, assignments, strict=True)
        if arm == 0
    )
    treated = tuple(
        float(row[column])
        for row, arm in zip(rows, assignments, strict=True)
        if arm == 1
    )
    difference = sum(treated) / len(treated) - sum(control) / len(control)
    pooled = math.sqrt((_variance(control) + _variance(treated)) / 2)
    return 0.0 if pooled == 0 and difference == 0 else difference / pooled


def _variance(values: tuple[float, ...]) -> float:
    mean = sum(values) / len(values)
    return sum((item - mean) ** 2 for item in values) / (len(values) - 1)


def _measurement_matches(
    rows: tuple[dict[str, str], ...],
    spec: EnvironmentalMeasurementSpec,
    result: MeasurementResult,
) -> bool:
    values = tuple(
        float(row[spec.columns.value]) for row in rows if row[spec.columns.value] != ""
    )
    missing = len(rows) - len(values)
    support = result.support
    temporal: dict[str, list[float]] = {}
    monitors: dict[str, tuple[int, int]] = {}
    for row in rows:
        raw = row[spec.columns.value]
        if raw:
            temporal.setdefault(row[spec.columns.timestamp], []).append(float(raw))
        monitor = row[spec.columns.monitor]
        total, valid = monitors.get(monitor, (0, 0))
        monitors[monitor] = (total + 1, valid + bool(raw))
    expected_temporal = tuple(
        (date, sum(items) / len(items)) for date, items in sorted(temporal.items())
    )
    expected_coverage = tuple(
        (monitor, total, valid, total - valid)
        for monitor, (total, valid) in sorted(monitors.items())
    )
    ordered_values = tuple(sorted(values))
    return (
        support.total == len(rows)
        and support.valid == len(values)
        and support.missing == missing
        and support.monitors == len({row[spec.columns.monitor] for row in rows})
        and math.isclose(result.mean, sum(values) / len(values), abs_tol=1e-10)
        and result.minimum == min(values)
        and result.maximum == max(values)
        and math.isclose(
            result.quantiles.q25, _quantile(ordered_values, 0.25), abs_tol=1e-10
        )
        and math.isclose(
            result.quantiles.median, _quantile(ordered_values, 0.5), abs_tol=1e-10
        )
        and math.isclose(
            result.quantiles.q75, _quantile(ordered_values, 0.75), abs_tol=1e-10
        )
        and result.exceedances
        == sum(value > spec.exceedance_threshold for value in values)
        and math.isclose(result.missing_rate, missing / len(rows), abs_tol=1e-12)
        and len(result.temporal) == len(expected_temporal)
        and all(
            item.date == date and math.isclose(item.mean, mean, abs_tol=1e-10)
            for item, (date, mean) in zip(
                result.temporal, expected_temporal, strict=True
            )
        )
        and tuple(
            (item.monitor, item.total, item.valid, item.missing)
            for item in result.monitor_coverage
        )
        == expected_coverage
    )


def _quantile(values: tuple[float, ...], probability: float) -> float:
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] + fraction * (values[upper] - values[lower])


def _scm_matches(
    rows: tuple[dict[str, str], ...],
    spec: SyntheticControlSpec,
    result: SyntheticControlResult,
) -> bool:
    units = {row[spec.columns.unit] for row in rows}
    times = sorted({float(row[spec.columns.time]) for row in rows})
    donors = units - {spec.treated_unit}
    outcomes = {
        (row[spec.columns.unit], float(row[spec.columns.time])): float(
            row[spec.columns.outcome]
        )
        for row in rows
    }
    weights = {item.donor: item.weight for item in result.donor_weights}
    expected_keys = {
        (row[spec.columns.unit], float(row[spec.columns.time])) for row in rows
    }
    return (
        len(expected_keys) == len(units) * len(times)
        and {item.donor for item in result.donor_weights} == donors
        and {item.unit for item in result.placebos} == donors
        and result.pre_periods == sum(time < spec.intervention_time for time in times)
        and result.post_periods == sum(time >= spec.intervention_time for time in times)
        and tuple(item.time for item in result.gaps) == tuple(times)
        and all(
            item.period == ("pre" if item.time < spec.intervention_time else "post")
            for item in result.gaps
        )
        and all(
            math.isclose(
                item.treated,
                outcomes[(spec.treated_unit, item.time)],
                abs_tol=1e-10,
            )
            and math.isclose(
                item.synthetic,
                sum(weights[donor] * outcomes[(donor, item.time)] for donor in donors),
                abs_tol=1e-10,
            )
            for item in result.gaps
        )
    )


def _meta_matches(
    rows: tuple[dict[str, str], ...], spec: MetaAnalysisSpec, result: MetaAnalysisResult
) -> bool:
    studies = tuple(row[spec.columns.study] for row in rows)
    effects = {row[spec.columns.study]: float(row[spec.columns.effect]) for row in rows}
    variances = {
        row[spec.columns.study]: float(row[spec.columns.variance]) for row in rows
    }
    evidence = {item.study: item for item in result.study_weights}
    funnel = {item.study: item for item in result.funnel}
    raw_weights = {key: 1 / (variances[key] + result.tau_squared) for key in studies}
    weight_total = sum(raw_weights.values())
    return (
        result.studies == len(rows)
        and set(evidence) == set(studies) == set(funnel)
        and math.isclose(
            result.inverse_variance_support,
            sum(1 / value for value in variances.values()),
            rel_tol=1e-10,
        )
        and all(
            math.isclose(evidence[key].effect, effects[key], abs_tol=1e-12)
            and math.isclose(
                evidence[key].std_error, math.sqrt(variances[key]), rel_tol=1e-8
            )
            for key in studies
        )
        and all(
            math.isclose(
                evidence[key].weight,
                raw_weights[key] / weight_total,
                rel_tol=1e-8,
            )
            for key in studies
        )
        and all(
            math.isclose(funnel[key].effect, effects[key], abs_tol=1e-12)
            and math.isclose(
                funnel[key].std_error, math.sqrt(variances[key]), rel_tol=1e-8
            )
            for key in studies
        )
    )

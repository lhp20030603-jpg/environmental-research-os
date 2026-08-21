"""Method-specific row semantics for already bounded local CSV bytes."""

from __future__ import annotations

import math

from envresearch.econometrics._wave1_csv import (
    validate_measurement as _validate_measurement,
)
from envresearch.econometrics._wave1_csv import (
    validate_meta as _validate_meta,
)
from envresearch.econometrics._wave1_csv import (
    validate_rct as _validate_rct,
)
from envresearch.econometrics._wave1_csv import (
    validate_scm as _validate_scm,
)
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.causal_contracts import Iv2slsSpec, PanelFeSpec, RddSpec
from envresearch.econometrics.contracts import LocalAnalysisSpec
from envresearch.econometrics.valuation_contracts import (
    ContingentValuationSpec,
    DiscreteChoiceSpec,
    HedonicSpec,
    TravelCostSpec,
)
from envresearch.econometrics.wave1_contracts import (
    EnvironmentalMeasurementSpec,
    MetaAnalysisSpec,
    RctSpec,
    SyntheticControlSpec,
)


def validate_rows(
    header: tuple[str, ...], rows: tuple[tuple[str, ...], ...], spec: AnalysisSpec
) -> None:
    """Apply the selected method's declared numeric and support rules."""
    from envresearch.econometrics.data_snapshot import LocalDataInvalid

    if not rows:
        raise LocalDataInvalid("local CSV must contain data rows")
    positions = {name: index for index, name in enumerate(header)}
    if isinstance(spec, LocalAnalysisSpec):
        _validate_did(rows, positions, spec)
    elif isinstance(spec, PanelFeSpec):
        _validate_panel(rows, positions, spec)
    elif isinstance(spec, Iv2slsSpec):
        _validate_iv(rows, positions, spec)
    elif isinstance(spec, RddSpec):
        _validate_rdd(rows, positions, spec)
    elif isinstance(spec, RctSpec):
        _validate_rct(rows, positions, spec)
    elif isinstance(spec, SyntheticControlSpec):
        _validate_scm(rows, positions, spec)
    elif isinstance(spec, EnvironmentalMeasurementSpec):
        _validate_measurement(rows, positions, spec)
    elif isinstance(spec, MetaAnalysisSpec):
        _validate_meta(rows, positions, spec)
    elif isinstance(spec, HedonicSpec):
        from envresearch.econometrics._valuation_csv import validate_hedonic

        validate_hedonic(rows, positions, spec)
    elif isinstance(spec, TravelCostSpec):
        from envresearch.econometrics._valuation_csv import validate_travel_cost

        validate_travel_cost(rows, positions, spec)
    elif isinstance(spec, ContingentValuationSpec):
        from envresearch.econometrics._valuation_csv import validate_cv

        validate_cv(rows, positions, spec)
    elif isinstance(spec, DiscreteChoiceSpec):
        from envresearch.econometrics._valuation_csv import validate_dce

        validate_dce(rows, positions, spec)
    else:  # pragma: no cover - the discriminated union is closed
        raise LocalDataInvalid("analysis method is not registered")


def _validate_did(
    rows: tuple[tuple[str, ...], ...],
    positions: dict[str, int],
    spec: LocalAnalysisSpec,
) -> None:
    from envresearch.econometrics.data_snapshot import LocalDataInvalid

    keys: set[tuple[str, str]] = set()
    cohorts: dict[str, float | None] = {}
    for row in rows:
        unit = row[positions[spec.columns.unit]]
        time = row[positions[spec.columns.time]]
        if not unit or not time:
            raise LocalDataInvalid("panel unit and time values must be nonmissing")
        _finite(time, "time values")
        _finite(row[positions[spec.columns.outcome]], "outcome values")
        cohort = row[positions[spec.columns.treatment_cohort]]
        cohort_value = (
            None if not cohort else _finite(cohort, "treatment cohort values")
        )
        if unit in cohorts and cohorts[unit] != cohort_value:
            raise LocalDataInvalid("treatment cohort must be constant by unit")
        cohorts[unit] = cohort_value
        for covariate in spec.columns.covariates:
            _finite(row[positions[covariate]], "covariate values")
        key = (unit, time)
        if key in keys:
            raise LocalDataInvalid("panel unit-time rows must be unique")
        keys.add(key)


def _validate_panel(
    rows: tuple[tuple[str, ...], ...],
    positions: dict[str, int],
    spec: PanelFeSpec,
) -> None:
    from envresearch.econometrics.data_snapshot import LocalDataInvalid

    keys: set[tuple[str, str]] = set()
    for row in rows:
        unit = row[positions[spec.columns.unit]]
        time = row[positions[spec.columns.time]]
        if not unit or not time:
            raise LocalDataInvalid("panel unit and time values must be nonmissing")
        _finite(time, "time values")
        _finite(row[positions[spec.columns.outcome]], "outcome values")
        for regressor in spec.columns.regressors:
            _finite(row[positions[regressor]], "regressor values")
        _require_groups(
            row, positions, spec.columns.fixed_effects, "fixed-effect values"
        )
        if spec.inference.cluster_column is not None:
            _require_groups(
                row, positions, (spec.inference.cluster_column,), "cluster values"
            )
        key = (unit, time)
        if key in keys:
            raise LocalDataInvalid("panel unit-time rows must be unique")
        keys.add(key)


def _validate_iv(
    rows: tuple[tuple[str, ...], ...],
    positions: dict[str, int],
    spec: Iv2slsSpec,
) -> None:
    for row in rows:
        _finite(row[positions[spec.columns.outcome]], "outcome values")
        for name in spec.columns.endogenous:
            _finite(row[positions[name]], "endogenous values")
        for name in spec.columns.instruments:
            _finite(row[positions[name]], "instrument values")
        for name in spec.columns.controls:
            _finite(row[positions[name]], "control values")
        _require_groups(
            row, positions, spec.columns.fixed_effects, "fixed-effect values"
        )
        if spec.inference.cluster_column is not None:
            _require_groups(
                row, positions, (spec.inference.cluster_column,), "cluster values"
            )


def _validate_rdd(
    rows: tuple[tuple[str, ...], ...],
    positions: dict[str, int],
    spec: RddSpec,
) -> None:
    running: list[float] = []
    for row in rows:
        _finite(row[positions[spec.columns.outcome]], "outcome values")
        running.append(_finite(row[positions[spec.columns.running]], "running values"))
        for name in spec.columns.covariates:
            _finite(row[positions[name]], "covariate values")
        if spec.inference.cluster_column is not None:
            _require_groups(
                row, positions, (spec.inference.cluster_column,), "cluster values"
            )
    cutoff = spec.design.cutoff
    bandwidth = spec.design.bandwidth
    _require_cutoff_support(running, cutoff, bandwidth, "main bandwidth window")
    _require_cutoff_support(running, cutoff, bandwidth * 0.5, "half-bandwidth window")
    _require_cutoff_support(
        running, cutoff, bandwidth * 1.5, "one-and-a-half-bandwidth window"
    )
    if spec.design.donut_radius > 0:
        donut = [
            value
            for value in running
            if abs(value - cutoff) >= spec.design.donut_radius
        ]
        _require_cutoff_support(donut, cutoff, bandwidth, "donut window")


def _require_cutoff_support(
    values: list[float], cutoff: float, bandwidth: float, label: str
) -> None:
    from envresearch.econometrics.data_snapshot import LocalDataInvalid

    inside = [value for value in values if abs(value - cutoff) <= bandwidth]
    left = {value for value in inside if value < cutoff}
    right = {value for value in inside if value >= cutoff}
    if not left or not right:
        if label == "main bandwidth window":
            raise LocalDataInvalid(
                "RDD requires observations on both sides of the cutoff",
                code="RDD_SUPPORT_INSUFFICIENT",
            )
        raise LocalDataInvalid(
            f"RDD {label} requires support on both sides",
            code="RDD_SUPPORT_INSUFFICIENT",
        )
    if len(left) < 4 or len(right) < 4:
        raise LocalDataInvalid(
            f"RDD {label} requires four unique running values per side",
            code="RDD_SUPPORT_INSUFFICIENT",
        )


def _finite(value: str, label: str) -> float:
    from envresearch.econometrics.data_snapshot import LocalDataInvalid

    try:
        numeric = float(value)
    except ValueError as error:
        raise LocalDataInvalid(f"{label} must be finite numeric values") from error
    if not math.isfinite(numeric):
        raise LocalDataInvalid(f"{label} must be finite numeric values")
    return numeric


def _require_groups(
    row: tuple[str, ...],
    positions: dict[str, int],
    names: tuple[str, ...],
    label: str,
) -> None:
    from envresearch.econometrics.data_snapshot import LocalDataInvalid

    if any(not row[positions[name]] for name in names):
        raise LocalDataInvalid(f"{label} must be nonmissing")

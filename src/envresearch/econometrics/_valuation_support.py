"""Independent support reconstruction for valuation data shapes."""

from __future__ import annotations

import csv
import io
import math

from envresearch.econometrics.valuation_contracts import (
    ContingentValuationSpec,
    DiscreteChoiceSpec,
    HedonicSpec,
    TravelCostSpec,
)
from envresearch.econometrics.valuation_results import (
    ContingentValuationResult,
    DiscreteChoiceResult,
    HedonicResult,
    TravelCostResult,
    ValuationSupport,
)

ValuationSpec = (
    HedonicSpec | TravelCostSpec | ContingentValuationSpec | DiscreteChoiceSpec
)
Rows = tuple[tuple[str, ...], ...]


def valuation_result_matches_snapshot(
    data: bytes,
    spec: ValuationSpec,
    result: HedonicResult
    | TravelCostResult
    | ContingentValuationResult
    | DiscreteChoiceResult,
) -> bool:
    """Reconstruct raw-data-derived valuation evidence from exact CSV bytes."""
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8"), newline=""))
        header = tuple(next(reader))
        rows = tuple(tuple(row) for row in reader)
        expected = reconstruct_support(header, rows, spec)
        if result.support != expected:
            return False
        if isinstance(spec, HedonicSpec) and isinstance(result, HedonicResult):
            positions = {name: index for index, name in enumerate(header)}
            prices = tuple(float(row[positions[spec.columns.price]]) for row in rows)
            environments = tuple(
                float(row[positions[spec.columns.environmental_attribute]])
                for row in rows
            )
            return math.isclose(
                result.reference_price, sum(prices) / len(prices), abs_tol=1e-12
            ) and math.isclose(
                result.reference_environment,
                sum(environments) / len(environments),
                abs_tol=1e-12,
            )
        return (
            isinstance(spec, TravelCostSpec)
            and isinstance(result, TravelCostResult)
            or isinstance(spec, ContingentValuationSpec)
            and isinstance(result, ContingentValuationResult)
            or isinstance(spec, DiscreteChoiceSpec)
            and isinstance(result, DiscreteChoiceResult)
        )
    except (
        UnicodeDecodeError,
        KeyError,
        StopIteration,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ):
        return False


def reconstruct_support(
    header: tuple[str, ...], rows: Rows, spec: ValuationSpec
) -> ValuationSupport:
    """Recompute typed support from authenticated raw rows."""
    positions = {name: index for index, name in enumerate(header)}
    if isinstance(spec, HedonicSpec):
        units = {row[positions[spec.columns.transaction]] for row in rows}
        groups = (
            None
            if not spec.columns.fixed_effects
            else len(
                {
                    tuple(
                        row[positions[column]] for column in spec.columns.fixed_effects
                    )
                    for row in rows
                }
            )
        )
        return ValuationSupport(
            observations=len(rows), primary_units=len(units), groups=groups
        )
    if isinstance(spec, TravelCostSpec):
        units = {row[positions[spec.columns.unit]] for row in rows}
        sites = {row[positions[spec.columns.site]] for row in rows}
        zeros = sum(float(row[positions[spec.columns.visits]]) == 0 for row in rows)
        return ValuationSupport(
            observations=len(rows),
            primary_units=len(units),
            groups=len(sites),
            zero_or_no_count=zeros,
        )
    if isinstance(spec, ContingentValuationSpec):
        units = {row[positions[spec.columns.respondent]] for row in rows}
        no_count = sum(row[positions[spec.columns.response]] == "0" for row in rows)
        bids = {float(row[positions[spec.columns.bid]]) for row in rows}
        if not all(math.isfinite(value) for value in bids):
            raise ValueError("CV bid support is not finite")
        return ValuationSupport(
            observations=len(rows),
            primary_units=len(units),
            groups=len(bids),
            zero_or_no_count=no_count,
        )
    units = {row[positions[spec.columns.respondent]] for row in rows}
    choice_sets = {
        (
            row[positions[spec.columns.respondent]],
            row[positions[spec.columns.choice_set]],
        )
        for row in rows
    }
    return ValuationSupport(
        observations=len(rows),
        primary_units=len(units),
        groups=len(choice_sets),
        zero_or_no_count=sum(
            row[positions[spec.columns.chosen]] == "0" for row in rows
        ),
    )

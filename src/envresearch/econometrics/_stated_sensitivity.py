"""Exact sensitivity parser for stated-preference WTP ratios."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from envresearch.econometrics._causal_outputs import CausalOutputInvalid, read_rows
from envresearch.econometrics.valuation_results import SensitivityEstimate


def stated_sensitivity(
    path: Path,
) -> tuple[tuple[SensitivityEstimate, ...], float, float, float, str]:
    """Parse one fully reconstructible sensitivity-ratio row."""
    rows = read_rows(
        path,
        (
            "label",
            "estimate",
            "baseline_estimate",
            "absolute_change",
            "max_sensitivity_change",
            "numerator_coefficient",
            "denominator_coefficient",
            "model_form",
        ),
    )
    if len(rows) != 1:
        raise CausalOutputInvalid("stated-preference sensitivity requires one row")
    try:
        row = rows[0]
        item = SensitivityEstimate(
            label=row["label"],
            estimate=float(row["estimate"]),
            baseline_estimate=float(row["baseline_estimate"]),
            absolute_change=float(row["absolute_change"]),
        )
        return (
            (item,),
            float(row["max_sensitivity_change"]),
            float(row["numerator_coefficient"]),
            float(row["denominator_coefficient"]),
            row["model_form"],
        )
    except (KeyError, ValueError, ValidationError) as error:
        raise CausalOutputInvalid(
            "stated-preference sensitivity output is invalid"
        ) from error

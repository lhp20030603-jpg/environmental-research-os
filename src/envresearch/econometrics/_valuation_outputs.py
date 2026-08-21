"""Exact-schema parsers shared by valuation recipes."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from envresearch.econometrics._causal_outputs import CausalOutputInvalid, read_rows
from envresearch.econometrics._valuation_evidence import BidYesShare
from envresearch.econometrics.valuation_results import (
    CoefficientEstimate,
    CovarianceEvidence,
    SensitivityEstimate,
    ValuationConfiguration,
    ValuationSupport,
    WelfareEstimate,
)
from envresearch.models.artifact import ArtifactRef


def coefficients(path: Path) -> tuple[CoefficientEstimate, ...]:
    rows = read_rows(
        path, ("term", "estimate", "std_error", "confidence_low", "confidence_high")
    )
    try:
        result = tuple(
            CoefficientEstimate(
                term=row["term"],
                estimate=float(row["estimate"]),
                std_error=float(row["std_error"]),
                confidence_low=float(row["confidence_low"]),
                confidence_high=float(row["confidence_high"]),
            )
            for row in rows
        )
    except (KeyError, ValueError, ValidationError) as error:
        raise CausalOutputInvalid("valuation coefficients are invalid") from error
    if len({item.term for item in result}) != len(result):
        raise CausalOutputInvalid("valuation coefficient terms must be unique")
    return result


def covariance(path: Path, terms: tuple[str, ...]) -> CovarianceEvidence:
    rows = read_rows(path, ("row_term", "column_term", "value"))
    cells: dict[tuple[str, str], float] = {}
    try:
        for row in rows:
            key = (row["row_term"], row["column_term"])
            if key in cells:
                raise CausalOutputInvalid("covariance cells must be unique")
            cells[key] = float(row["value"])
        expected = {(left, right) for left in terms for right in terms}
        if set(cells) != expected:
            raise CausalOutputInvalid(
                "covariance does not cover exact coefficient terms"
            )
        return CovarianceEvidence(
            terms=terms,
            values=tuple(
                tuple(cells[(left, right)] for right in terms) for left in terms
            ),
        )
    except (KeyError, ValueError, ValidationError) as error:
        raise CausalOutputInvalid("valuation covariance is invalid") from error


def welfare(path: Path) -> tuple[WelfareEstimate, ...]:
    rows = read_rows(
        path,
        (
            "name",
            "estimate",
            "std_error",
            "confidence_low",
            "confidence_high",
            "currency",
            "price_base",
            "time_basis",
            "population_basis",
            "transformation",
            "numerator_term",
            "denominator_term",
        ),
    )
    try:
        return tuple(
            WelfareEstimate(
                name=row["name"],
                estimate=float(row["estimate"]),
                std_error=float(row["std_error"]),
                confidence_low=float(row["confidence_low"]),
                confidence_high=float(row["confidence_high"]),
                currency=row["currency"],
                price_base=row["price_base"],
                time_basis=row["time_basis"],
                population_basis=row["population_basis"],
                transformation=row["transformation"],  # type: ignore[arg-type]
                numerator_term=row["numerator_term"] or None,
                denominator_term=row["denominator_term"],
            )
            for row in rows
        )
    except (KeyError, ValueError, ValidationError) as error:
        raise CausalOutputInvalid("valuation welfare output is invalid") from error


def support(path: Path) -> ValuationSupport:
    rows = read_rows(
        path, ("observations", "primary_units", "groups", "zero_or_no_count")
    )
    if len(rows) != 1:
        raise CausalOutputInvalid("valuation support must contain one row")
    try:
        row = rows[0]
        return ValuationSupport(
            observations=int(row["observations"]),
            primary_units=int(row["primary_units"]),
            groups=None if row["groups"] == "" else int(row["groups"]),
            zero_or_no_count=int(row["zero_or_no_count"]),
        )
    except (KeyError, ValueError, ValidationError) as error:
        raise CausalOutputInvalid("valuation support is invalid") from error


def bid_yes_shares(path: Path) -> tuple[BidYesShare, ...]:
    """Parse typed observed yes shares for each CV bid."""
    rows = read_rows(path, ("bid", "yes_count", "observations", "yes_share"))
    try:
        return tuple(
            BidYesShare(
                bid=float(row["bid"]),
                yes_count=int(row["yes_count"]),
                observations=int(row["observations"]),
                yes_share=float(row["yes_share"]),
            )
            for row in rows
        )
    except (KeyError, ValueError, ValidationError) as error:
        raise CausalOutputInvalid("CV bid yes-share output is invalid") from error


def sensitivities(
    path: Path,
) -> tuple[tuple[SensitivityEstimate, ...], float, float, str]:
    rows = read_rows(
        path,
        (
            "label",
            "estimate",
            "baseline_estimate",
            "absolute_change",
            "max_sensitivity_change",
            "raw_coefficient",
            "model_form",
        ),
    )
    try:
        result = tuple(
            SensitivityEstimate(
                label=row["label"],
                estimate=float(row["estimate"]),
                baseline_estimate=float(row["baseline_estimate"]),
                absolute_change=float(row["absolute_change"]),
            )
            for row in rows
        )
        thresholds = {float(row["max_sensitivity_change"]) for row in rows}
        if len(thresholds) != 1:
            raise CausalOutputInvalid("sensitivity threshold must be constant")
        raw = {float(row["raw_coefficient"]) for row in rows}
        forms = {row["model_form"] for row in rows}
        if len(raw) != 1 or len(forms) != 1:
            raise CausalOutputInvalid("sensitivity model evidence must be constant")
        return result, thresholds.pop(), raw.pop(), forms.pop()
    except (KeyError, ValueError, ValidationError) as error:
        raise CausalOutputInvalid("valuation sensitivity output is invalid") from error


def configuration(
    path: Path, package_authorities: tuple[ArtifactRef, ...] = ()
) -> ValuationConfiguration:
    rows = read_rows(
        path,
        (
            "method_id",
            "r_version",
            "confidence_level",
            "cluster_column",
            "fixed_effects",
            "functional_form",
            "family",
            "link",
        ),
    )
    if len(rows) != 1:
        raise CausalOutputInvalid("valuation configuration must contain one row")
    row = rows[0]
    try:
        return ValuationConfiguration(
            method_id=row["method_id"],  # type: ignore[arg-type]
            r_version=row["r_version"],
            confidence_level=float(row["confidence_level"]),
            cluster_column=row["cluster_column"] or None,
            fixed_effects=tuple(
                item for item in row["fixed_effects"].split(";") if item
            ),
            functional_form=row["functional_form"] or None,  # type: ignore[arg-type]
            family=row["family"] or None,  # type: ignore[arg-type]
            link=row["link"] or None,  # type: ignore[arg-type]
            package_authorities=package_authorities,
        )
    except (KeyError, ValueError, ValidationError) as error:
        raise CausalOutputInvalid("valuation configuration is invalid") from error

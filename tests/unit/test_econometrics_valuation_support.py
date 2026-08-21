from pathlib import Path

import pytest
from test_econometrics_contingent_valuation import (
    _write_outputs as _write_cv_outputs,
)
from test_econometrics_discrete_choice import _write_outputs as _write_dce_outputs
from test_econometrics_hedonic import _write_hedonic_outputs
from test_econometrics_travel_cost import _write_outputs as _write_travel_outputs
from test_econometrics_valuation_contracts import (
    contingent_valuation,
    dce,
    hedonic,
    travel_cost,
)

from envresearch.econometrics._valuation_support import reconstruct_support
from envresearch.econometrics.contingent_valuation import ContingentValuationRecipe
from envresearch.econometrics.discrete_choice import DiscreteChoiceRecipe
from envresearch.econometrics.hedonic import HedonicRecipe
from envresearch.econometrics.travel_cost import TravelCostRecipe
from envresearch.econometrics.valuation_results import (
    ContingentValuationResult,
    DiscreteChoiceResult,
    HedonicResult,
    TravelCostResult,
    ValuationSupport,
    WelfareEstimate,
)


def test_reconstructs_hedonic_support_from_raw_rows(tmp_path: Path) -> None:
    header = ("sale_id", "price", "pm25", "area", "age", "district", "year")
    rows = (
        ("s1", "100", "10", "80", "5", "north", "2024"),
        ("s2", "120", "9", "90", "3", "north", "2024"),
        ("s3", "110", "8", "85", "4", "south", "2024"),
    )
    assert reconstruct_support(header, rows, hedonic(tmp_path / "hedonic.csv")) == (
        ValuationSupport(observations=3, primary_units=3, groups=2)
    )


def test_reconstructs_travel_cost_support_from_raw_rows(tmp_path: Path) -> None:
    header = (
        "person_id",
        "visits",
        "travel_cost",
        "exposure",
        "site_id",
        "substitute_cost",
    )
    rows = (("p1", "0", "10", "1", "park", "15"), ("p2", "2", "8", "1", "park", "12"))
    assert reconstruct_support(
        header, rows, travel_cost(tmp_path / "travel.csv")
    ) == ValuationSupport(observations=2, primary_units=2, groups=1, zero_or_no_count=1)


def test_reconstructs_cv_support_from_raw_rows(tmp_path: Path) -> None:
    header = ("respondent_id", "yes", "bid", "income")
    rows = (
        ("r1", "1", "10", "50"),
        ("r2", "0", "10", "40"),
        ("r3", "0", "20", "60"),
    )
    assert reconstruct_support(
        header, rows, contingent_valuation(tmp_path / "cv.csv")
    ) == ValuationSupport(observations=3, primary_units=3, groups=2, zero_or_no_count=2)


def test_cv_support_uses_numeric_bid_levels(tmp_path: Path) -> None:
    header = ("respondent_id", "yes", "bid", "income")
    rows = (
        ("r1", "1", "10", "50"),
        ("r2", "0", "10.0", "40"),
        ("r3", "1", "20", "60"),
        ("r4", "0", "20.00", "45"),
    )

    assert reconstruct_support(
        header, rows, contingent_valuation(tmp_path / "cv.csv")
    ) == ValuationSupport(observations=4, primary_units=4, groups=2, zero_or_no_count=2)


def test_reconstructs_dce_support_from_raw_rows(tmp_path: Path) -> None:
    header = (
        "respondent_id",
        "choice_set_id",
        "alternative_id",
        "chosen",
        "cost",
        "air_quality",
        "green_space",
    )
    rows = (
        ("r1", "s1", "a", "1", "10", "2", "1"),
        ("r1", "s1", "b", "0", "5", "1", "0"),
        ("r2", "s2", "a", "0", "10", "2", "1"),
        ("r2", "s2", "b", "1", "5", "1", "0"),
    )
    assert reconstruct_support(
        header, rows, dce(tmp_path / "dce.csv")
    ) == ValuationSupport(observations=4, primary_units=2, groups=2, zero_or_no_count=2)


ValuationResult = (
    HedonicResult | TravelCostResult | ContingentValuationResult | DiscreteChoiceResult
)


def _legitimate_results(tmp_path: Path) -> tuple[tuple[object, ValuationResult], ...]:
    hedonic_root = tmp_path / "hedonic"
    travel_root = tmp_path / "travel"
    cv_root = tmp_path / "cv"
    dce_root = tmp_path / "dce"
    _write_hedonic_outputs(hedonic_root)
    _write_travel_outputs(travel_root)
    _write_cv_outputs(cv_root)
    _write_dce_outputs(dce_root)
    return (
        (
            hedonic(tmp_path / "hedonic.csv"),
            HedonicRecipe(tmp_path / "hedonic-work").parse(hedonic_root),
        ),
        (
            travel_cost(tmp_path / "travel.csv"),
            TravelCostRecipe(tmp_path / "travel-work").parse(travel_root),
        ),
        (
            contingent_valuation(tmp_path / "cv.csv"),
            ContingentValuationRecipe(tmp_path / "cv-work").parse(cv_root),
        ),
        (
            dce(tmp_path / "dce.csv"),
            DiscreteChoiceRecipe(tmp_path / "dce-work").parse(dce_root),
        ),
    )


def _assert_welfare_matches(
    reconstructed: tuple[WelfareEstimate, ...],
    expected: tuple[WelfareEstimate, ...],
) -> None:
    assert len(reconstructed) == len(expected)
    for actual, declared in zip(reconstructed, expected, strict=True):
        assert actual.estimate == pytest.approx(declared.estimate, abs=1e-10)
        assert actual.std_error == pytest.approx(declared.std_error, abs=1e-10)
        assert actual.confidence_low == pytest.approx(
            declared.confidence_low, abs=1e-10
        )
        assert actual.confidence_high == pytest.approx(
            declared.confidence_high, abs=1e-10
        )
        assert actual.model_dump(
            exclude={"estimate", "std_error", "confidence_low", "confidence_high"}
        ) == declared.model_dump(
            exclude={"estimate", "std_error", "confidence_low", "confidence_high"}
        )


def test_reconstructs_welfare_independently_for_every_valuation_method(
    tmp_path: Path,
) -> None:
    from envresearch.econometrics._valuation_welfare import reconstruct_welfare

    for spec, result in _legitimate_results(tmp_path):
        legitimate = result.welfare
        forged_rows = tuple(
            item.model_copy(
                update={
                    "estimate": item.estimate + 999.0,
                    "std_error": item.std_error + 999.0,
                    "confidence_low": item.confidence_low - 999.0,
                    "confidence_high": item.confidence_high + 999.0,
                    "currency": "FORGED",
                }
            )
            for item in legitimate
        )
        forged_result = result.model_copy(update={"welfare": forged_rows})

        _assert_welfare_matches(
            reconstruct_welfare(spec, forged_result),  # type: ignore[arg-type]
            legitimate,
        )


def test_rejects_forged_derived_sensitivity_for_every_valuation_method(
    tmp_path: Path,
) -> None:
    from envresearch.econometrics._valuation_welfare import valuation_evidence_matches

    for spec, result in _legitimate_results(tmp_path):
        original = result.sensitivities[0]
        forged_item = original.model_copy(
            update={
                "estimate": original.estimate + 1.0,
                "absolute_change": abs(
                    original.estimate + 1.0 - original.baseline_estimate
                ),
            }
        )
        forged = result.model_copy(update={"sensitivities": (forged_item,)})

        assert not valuation_evidence_matches(spec, forged)  # type: ignore[arg-type]

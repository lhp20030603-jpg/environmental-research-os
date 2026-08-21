from pathlib import Path

import pytest
from test_econometrics_valuation_contracts import (
    contingent_valuation,
    dce,
    hedonic,
    travel_cost,
)

from envresearch.econometrics._causal_csv import validate_rows
from envresearch.econometrics.data_snapshot import LocalDataInvalid


def test_valid_valuation_rows_pass(tmp_path: Path) -> None:
    validate_rows(
        ("sale_id", "price", "pm25", "area", "age", "district", "year"),
        (
            ("s1", "100", "12", "80", "5", "a", "2024"),
            ("s2", "120", "8", "90", "2", "b", "2025"),
        ),
        hedonic(tmp_path / "hedonic.csv"),
    )
    validate_rows(
        (
            "person_id",
            "visits",
            "travel_cost",
            "exposure",
            "site_id",
            "substitute_cost",
        ),
        (("p1", "0", "10", "1", "park", "15"), ("p2", "2", "8", "1", "park", "12")),
        travel_cost(tmp_path / "travel.csv"),
    )
    validate_rows(
        ("respondent_id", "yes", "bid", "income"),
        (("r1", "1", "10", "50"), ("r2", "0", "20", "60")),
        contingent_valuation(tmp_path / "cv.csv"),
    )
    validate_rows(
        (
            "respondent_id",
            "choice_set_id",
            "alternative_id",
            "chosen",
            "cost",
            "air_quality",
            "green_space",
        ),
        (
            ("r1", "s1", "a", "1", "10", "2", "1"),
            ("r1", "s1", "b", "0", "5", "1", "0"),
            ("r2", "s2", "a", "0", "10", "2", "1"),
            ("r2", "s2", "b", "1", "5", "1", "0"),
        ),
        dce(tmp_path / "dce.csv"),
    )


def test_hedonic_requires_unique_transactions_and_valid_log_domain(
    tmp_path: Path,
) -> None:
    spec = hedonic(tmp_path / "hedonic.csv")
    header = ("sale_id", "price", "pm25", "area", "age", "district", "year")
    with pytest.raises(LocalDataInvalid, match="unique"):
        validate_rows(header, (("s1", "100", "1", "2", "3", "a", "2024"),) * 2, spec)
    with pytest.raises(LocalDataInvalid, match="positive price") as price_error:
        validate_rows(header, (("s1", "0", "1", "2", "3", "a", "2024"),), spec)
    assert price_error.value.code == "HEDONIC_LOG_DOMAIN_INVALID"


def test_hedonic_validates_sensitivity_log_domain(tmp_path: Path) -> None:
    spec = hedonic(tmp_path / "hedonic.csv").model_copy(
        update={"functional_form": "level-level", "sensitivity_form": "log-log"}
    )
    header = ("sale_id", "price", "pm25", "area", "age", "district", "year")
    with pytest.raises(LocalDataInvalid, match="logged environmental") as caught:
        validate_rows(
            header,
            (("s1", "100", "0", "2", "3", "a", "2024"),),
            spec,
        )
    assert caught.value.code == "HEDONIC_LOG_DOMAIN_INVALID"


def test_travel_cost_requires_counts_and_positive_exposure(tmp_path: Path) -> None:
    spec = travel_cost(tmp_path / "travel.csv")
    header = (
        "person_id",
        "visits",
        "travel_cost",
        "exposure",
        "site_id",
        "substitute_cost",
    )
    with pytest.raises(LocalDataInvalid, match="nonnegative integers"):
        validate_rows(header, (("p1", "1.5", "10", "1", "park", "5"),), spec)
    with pytest.raises(LocalDataInvalid, match="positive exposure") as caught:
        validate_rows(header, (("p1", "1", "10", "0", "park", "5"),), spec)
    assert caught.value.code == "TRAVEL_COST_OFFSET_INVALID"


def test_cv_requires_both_responses_and_positive_bids(tmp_path: Path) -> None:
    spec = contingent_valuation(tmp_path / "cv.csv")
    header = ("respondent_id", "yes", "bid", "income")
    with pytest.raises(LocalDataInvalid, match="both yes and no"):
        validate_rows(header, (("r1", "1", "10", "50"), ("r2", "1", "20", "60")), spec)
    with pytest.raises(LocalDataInvalid, match="positive bids"):
        validate_rows(header, (("r1", "1", "0", "50"), ("r2", "0", "20", "60")), spec)


def test_dce_requires_unique_alternatives_and_one_choice(tmp_path: Path) -> None:
    spec = dce(tmp_path / "dce.csv")
    header = (
        "respondent_id",
        "choice_set_id",
        "alternative_id",
        "chosen",
        "cost",
        "air_quality",
        "green_space",
    )
    duplicate = (
        ("r1", "s1", "a", "1", "10", "2", "1"),
        ("r1", "s1", "a", "0", "5", "1", "0"),
    )
    with pytest.raises(LocalDataInvalid, match="unique"):
        validate_rows(header, duplicate, spec)
    none_chosen = (
        ("r1", "s1", "a", "0", "10", "2", "1"),
        ("r1", "s1", "b", "0", "5", "1", "0"),
    )
    with pytest.raises(LocalDataInvalid, match="exactly one chosen"):
        validate_rows(header, none_chosen, spec)


def test_dce_requires_within_set_variation_for_every_term(tmp_path: Path) -> None:
    spec = dce(tmp_path / "dce.csv")
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
        ("r1", "s1", "b", "0", "5", "2", "0"),
        ("r2", "s2", "a", "0", "10", "3", "1"),
        ("r2", "s2", "b", "1", "5", "3", "0"),
    )
    with pytest.raises(LocalDataInvalid, match="air_quality"):
        validate_rows(header, rows, spec)


def test_hedonic_rejects_nonfinite_missing_and_invariant_rows(tmp_path: Path) -> None:
    spec = hedonic(tmp_path / "hedonic.csv")
    header = ("sale_id", "price", "pm25", "area", "age", "district", "year")
    with pytest.raises(LocalDataInvalid, match="finite"):
        validate_rows(header, (("s1", "NaN", "1", "2", "3", "a", "2024"),), spec)
    with pytest.raises(LocalDataInvalid, match="nonmissing"):
        validate_rows(header, (("s1", "100", "1", "2", "3", "", "2024"),), spec)
    with pytest.raises(LocalDataInvalid, match="requires variation"):
        validate_rows(
            header,
            (
                ("s1", "100", "1", "2", "3", "a", "2024"),
                ("s2", "120", "1", "3", "4", "b", "2025"),
            ),
            spec,
        )


def test_travel_rejects_missing_duplicate_and_negative_cost(tmp_path: Path) -> None:
    spec = travel_cost(tmp_path / "travel.csv")
    header = (
        "person_id",
        "visits",
        "travel_cost",
        "exposure",
        "site_id",
        "substitute_cost",
    )
    with pytest.raises(LocalDataInvalid, match="nonmissing"):
        validate_rows(header, (("", "1", "10", "1", "park", "5"),), spec)
    row = ("p1", "1", "10", "1", "park", "5")
    with pytest.raises(LocalDataInvalid, match="keys must be unique"):
        validate_rows(header, (row, row), spec)
    with pytest.raises(LocalDataInvalid, match="nonnegative"):
        validate_rows(header, (("p1", "1", "-1", "1", "park", "5"),), spec)


def test_cv_rejects_duplicate_nonbinary_and_single_bid_level(tmp_path: Path) -> None:
    spec = contingent_valuation(tmp_path / "cv.csv")
    header = ("respondent_id", "yes", "bid", "income")
    row = ("r1", "1", "10", "50")
    with pytest.raises(LocalDataInvalid, match="keys must be nonmissing and unique"):
        validate_rows(header, (row, row), spec)
    with pytest.raises(LocalDataInvalid, match="exactly binary"):
        validate_rows(header, (("r1", "yes", "10", "50"),), spec)
    with pytest.raises(LocalDataInvalid, match="two bid levels"):
        validate_rows(
            header,
            (("r1", "1", "10", "50"), ("r2", "0", "10", "60")),
            spec,
        )


def test_dce_rejects_missing_nonbinary_and_singleton_set(tmp_path: Path) -> None:
    spec = dce(tmp_path / "dce.csv")
    header = (
        "respondent_id",
        "choice_set_id",
        "alternative_id",
        "chosen",
        "cost",
        "air_quality",
        "green_space",
    )
    with pytest.raises(LocalDataInvalid, match="identifiers must be nonmissing"):
        validate_rows(header, (("", "s1", "a", "1", "10", "2", "1"),), spec)
    with pytest.raises(LocalDataInvalid, match="exactly binary"):
        validate_rows(header, (("r1", "s1", "a", "yes", "10", "2", "1"),), spec)
    with pytest.raises(LocalDataInvalid, match="at least two alternatives"):
        validate_rows(header, (("r1", "s1", "a", "1", "10", "2", "1"),), spec)

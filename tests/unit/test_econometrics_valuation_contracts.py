from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER
from envresearch.econometrics.contracts import ResourceBudget
from envresearch.econometrics.valuation_contracts import (
    ContingentValuationSpec,
    DiscreteChoiceSpec,
    HedonicSpec,
    TravelCostSpec,
)


def _budget() -> ResourceBudget:
    return ResourceBudget(
        inactivity_seconds=30,
        max_output_bytes=1_000_000,
        max_workspace_bytes=10_000_000,
    )


def _base(path: Path) -> dict[str, object]:
    return {
        "data_path": path,
        "budget": _budget(),
        "currency": "USD",
        "price_base": "2025",
        "time_basis": "per-year",
        "population_basis": "sample-household",
    }


def hedonic(path: Path) -> HedonicSpec:
    return HedonicSpec(
        **_base(path),
        schema_version="econometrics.hedonic-pricing.v1",
        method_id="hedonic-pricing",
        columns={
            "transaction": "sale_id",
            "price": "price",
            "environmental_attribute": "pm25",
            "controls": ("area", "age"),
            "fixed_effects": ("district", "year"),
        },
        confidence_level=0.95,
        cluster_column="district",
        functional_form="log-level",
        sensitivity_form="level-level",
        max_condition_number=30.0,
        max_sensitivity_change=0.25,
    )


def travel_cost(path: Path) -> TravelCostSpec:
    return TravelCostSpec(
        **_base(path),
        schema_version="econometrics.travel-cost.v1",
        method_id="travel-cost",
        columns={
            "unit": "person_id",
            "visits": "visits",
            "travel_cost": "travel_cost",
            "exposure": "exposure",
            "site": "site_id",
            "substitute_controls": ("substitute_cost",),
        },
        confidence_level=0.95,
        cluster_column=None,
        family="negative-binomial",
        sensitivity="exclude-substitute-controls",
        max_dispersion=2.0,
        max_sensitivity_change=0.25,
    )


def contingent_valuation(path: Path) -> ContingentValuationSpec:
    return ContingentValuationSpec(
        **_base(path),
        schema_version="econometrics.contingent-valuation.v1",
        method_id="contingent-valuation",
        columns={
            "respondent": "respondent_id",
            "response": "yes",
            "bid": "bid",
            "covariates": ("income",),
        },
        confidence_level=0.95,
        link="logit",
        sensitivity="exclude-covariates",
        max_extreme_probability_share=0.10,
        max_sensitivity_change=0.25,
    )


def dce(path: Path) -> DiscreteChoiceSpec:
    return DiscreteChoiceSpec(
        **_base(path),
        schema_version="econometrics.dce-clogit.v1",
        method_id="dce-clogit",
        columns={
            "respondent": "respondent_id",
            "choice_set": "choice_set_id",
            "alternative": "alternative_id",
            "chosen": "chosen",
            "cost": "cost",
            "attributes": ("air_quality", "green_space"),
        },
        confidence_level=0.95,
        cluster_column="respondent_id",
        sensitivity="include-alternative-specific-constants",
        min_abs_cost_coefficient=0.001,
        max_sensitivity_change=0.25,
    )


def test_valuation_specs_join_closed_analysis_union(tmp_path: Path) -> None:
    specs = (
        hedonic(tmp_path / "hedonic.csv"),
        travel_cost(tmp_path / "travel.csv"),
        contingent_valuation(tmp_path / "cv.csv"),
        dce(tmp_path / "dce.csv"),
    )
    parsed = tuple(
        ANALYSIS_SPEC_ADAPTER.validate_python(spec.model_dump()) for spec in specs
    )
    assert tuple(item.method_id for item in parsed) == (
        "hedonic-pricing",
        "travel-cost",
        "contingent-valuation",
        "dce-clogit",
    )
    assert all(item.required_columns() for item in parsed)


@pytest.mark.parametrize(
    ("factory", "field", "value"),
    (
        (hedonic, "functional_form", "automatic"),
        (travel_cost, "family", "best-aic"),
        (contingent_valuation, "link", "automatic"),
        (dce, "cluster_column", "choice_set_id"),
    ),
)
def test_valuation_specs_reject_unregistered_design_choices(
    tmp_path: Path, factory: object, field: str, value: str
) -> None:
    spec = factory(tmp_path / "data.csv")  # type: ignore[operator]
    payload = spec.model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        ANALYSIS_SPEC_ADAPTER.validate_python(payload)


def test_valuation_specs_reject_duplicate_column_roles(tmp_path: Path) -> None:
    payload = dce(tmp_path / "dce.csv").model_dump()
    payload["columns"]["attributes"] = ("cost",)  # type: ignore[index]
    with pytest.raises(ValidationError, match="column roles must be unique"):
        DiscreteChoiceSpec.model_validate(payload)


@pytest.mark.parametrize("field", ("currency", "price_base", "time_basis"))
def test_valuation_specs_require_canonical_welfare_units(
    tmp_path: Path, field: str
) -> None:
    payload = hedonic(tmp_path / "hedonic.csv").model_dump()
    payload[field] = " "
    with pytest.raises(ValidationError):
        HedonicSpec.model_validate(payload)


def test_valuation_specs_require_absolute_csv_path(tmp_path: Path) -> None:
    payload = travel_cost(tmp_path / "travel.csv").model_dump()
    payload["data_path"] = Path("relative.csv")
    with pytest.raises(ValidationError, match="absolute CSV"):
        TravelCostSpec.model_validate(payload)


def test_hedonic_sensitivity_must_differ_from_primary_form(tmp_path: Path) -> None:
    payload = hedonic(tmp_path / "hedonic.csv").model_dump()
    payload["sensitivity_form"] = payload["functional_form"]
    with pytest.raises(ValidationError, match="sensitivity form"):
        HedonicSpec.model_validate(payload)


def test_travel_cost_requires_registered_substitute_sensitivity(
    tmp_path: Path,
) -> None:
    payload = travel_cost(tmp_path / "travel.csv").model_dump()
    payload["columns"]["substitute_controls"] = ()  # type: ignore[index]
    with pytest.raises(ValidationError, match="substitute"):
        TravelCostSpec.model_validate(payload)


def test_negative_binomial_rejects_unimplemented_cluster_inference(
    tmp_path: Path,
) -> None:
    payload = travel_cost(tmp_path / "travel.csv").model_dump()
    payload["cluster_column"] = "site_id"
    with pytest.raises(ValidationError, match="negative-binomial.*cluster"):
        TravelCostSpec.model_validate(payload)

"""Owned deterministic fixtures for valuation service integration tests."""

from __future__ import annotations

from pathlib import Path

from envresearch.econometrics.contracts import ResourceBudget
from envresearch.econometrics.valuation_contracts import (
    ContingentValuationSpec,
    DiscreteChoiceSpec,
    HedonicSpec,
    TravelCostSpec,
)


def cv_spec(path: Path) -> ContingentValuationSpec:
    return ContingentValuationSpec(
        schema_version="econometrics.contingent-valuation.v1",
        method_id="contingent-valuation",
        data_path=path,
        budget=_budget(),
        currency="USD",
        price_base="2025",
        time_basis="per-year",
        population_basis="sample-household",
        columns={
            "respondent": "respondent_id",
            "response": "yes",
            "bid": "bid",
            "covariates": ("income",),
        },
        confidence_level=0.95,
        link="logit",
        sensitivity="exclude-covariates",
        max_extreme_probability_share=0.2,
        max_sensitivity_change=100.0,
    )


def dce_spec(path: Path) -> DiscreteChoiceSpec:
    return DiscreteChoiceSpec(
        schema_version="econometrics.dce-clogit.v1",
        method_id="dce-clogit",
        data_path=path,
        budget=_budget(),
        currency="USD",
        price_base="2025",
        time_basis="per-year",
        population_basis="sample-household",
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
        min_abs_cost_coefficient=0.0001,
        max_sensitivity_change=100.0,
    )


def hedonic_spec(path: Path) -> HedonicSpec:
    return HedonicSpec(
        schema_version="econometrics.hedonic-pricing.v1",
        method_id="hedonic-pricing",
        data_path=path,
        budget=_budget(),
        currency="USD",
        price_base="2025",
        time_basis="per-year",
        population_basis="sample-household",
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
        max_condition_number=300.0,
        max_sensitivity_change=0.25,
    )


def travel_spec(path: Path) -> TravelCostSpec:
    return TravelCostSpec(
        schema_version="econometrics.travel-cost.v1",
        method_id="travel-cost",
        data_path=path,
        budget=_budget(),
        currency="USD",
        price_base="2025",
        time_basis="per-year",
        population_basis="sample-household",
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


def write_hedonic_outputs(root: Path) -> None:
    _write(
        root,
        "coefficients.csv",
        "term,estimate,std_error,confidence_low,confidence_high\n"
        "pm25,-0.5,0.1,-0.6959963984540054,-0.3040036015459946\n",
    )
    _write(root, "covariance.csv", "row_term,column_term,value\npm25,pm25,0.01\n")
    _write(
        root,
        "implicit_price.csv",
        "name,estimate,std_error,confidence_low,confidence_high,currency,price_base,time_basis,population_basis,transformation,numerator_term,denominator_term\n"
        "implicit-price,-84645.83333333335,16929.16666666667,-117826.39028827602,-51465.27637839068,USD,2025,per-year,sample-household,marginal-implicit-price,pm25,price\n",
    )
    _write(
        root,
        "support.csv",
        "observations,primary_units,groups,zero_or_no_count\n24,24,6,0\n",
    )
    _write(
        root,
        "collinearity.csv",
        "condition_number,max_condition_number,max_vif,reference_price,reference_environment\n"
        "145.505300137852,300,71.6041001906775,169291.6666666667,25.9166666666667\n",
    )
    _write(
        root,
        "sensitivity.csv",
        "label,estimate,baseline_estimate,absolute_change,max_sensitivity_change,raw_coefficient,model_form\n"
        "alternative-functional-form,-84645.73333333335,-84645.83333333335,0.1,0.25,-84645.73333333335,level-level\n",
    )
    _write(
        root,
        "package_configuration.csv",
        "method_id,r_version,confidence_level,cluster_column,fixed_effects,functional_form,family,link\nhedonic-pricing,R version 4.4.3,0.95,district,district;year,log-level,,\n",
    )
    _write(
        root,
        "hedonic_plot.svg",
        '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"><text>0</text></g></svg>\n',
    )


def write_travel_outputs(root: Path) -> None:
    _write(
        root,
        "coefficients.csv",
        "term,estimate,std_error,confidence_low,confidence_high\n"
        "travel_cost,-0.5,0.1,-0.6959963984540054,-0.3040036015459946\n",
    )
    _write(
        root,
        "covariance.csv",
        "row_term,column_term,value\ntravel_cost,travel_cost,0.01\n",
    )
    _write(
        root,
        "consumer_surplus.csv",
        "name,estimate,std_error,confidence_low,confidence_high,currency,price_base,time_basis,population_basis,transformation,numerator_term,denominator_term\n"
        "consumer-surplus,2,0.4,1.21601440618398,2.78398559381602,USD,2025,per-year,sample-household,negative-inverse-cost,,travel_cost\n",
    )
    _write(
        root,
        "support.csv",
        "observations,primary_units,groups,zero_or_no_count\n30,30,3,5\n",
    )
    _write(
        root,
        "dispersion.csv",
        "dispersion,max_dispersion,log_likelihood,deviance,residual_df,theta\n"
        "0.14817879396209943,2,-48.86315757515838,6.337752108678604,25,10\n",
    )
    visits = (
        12,
        4,
        9,
        3,
        7,
        1,
        4,
        0,
        2,
        0,
        15,
        6,
        11,
        4,
        8,
        2,
        5,
        1,
        3,
        0,
        10,
        3,
        8,
        2,
        6,
        1,
        3,
        0,
        1,
        0,
    )
    _write(
        root,
        "fit_evidence.csv",
        "row_index,observed,fitted\n"
        + "".join(
            f"{index},{observed},{observed + 0.5}\n"
            for index, observed in enumerate(visits, start=1)
        ),
    )
    _write(
        root,
        "sensitivity.csv",
        "label,estimate,baseline_estimate,absolute_change,max_sensitivity_change,raw_coefficient,model_form\n"
        "exclude-substitute-controls,2.1,2,0.1,0.25,-0.4761904761904762,negative-binomial\n",
    )
    _write(
        root,
        "package_configuration.csv",
        "method_id,r_version,confidence_level,cluster_column,fixed_effects,functional_form,family,link\ntravel-cost,R version 4.4.3,0.95,,site_id,,negative-binomial,\n",
    )
    _write(
        root,
        "travel_cost_plot.svg",
        '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"><text>0</text></g></svg>\n',
    )


def _budget() -> ResourceBudget:
    return ResourceBudget(
        inactivity_seconds=30,
        max_output_bytes=1_000_000,
        max_workspace_bytes=10_000_000,
    )


def _write(root: Path, name: str, data: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(data, encoding="utf-8")

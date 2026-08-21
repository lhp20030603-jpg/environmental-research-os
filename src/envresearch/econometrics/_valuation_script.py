"""Deterministic rendering for repository-owned valuation R scripts."""

from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files

from envresearch.econometrics.valuation_contracts import (
    ContingentValuationSpec,
    DiscreteChoiceSpec,
    HedonicSpec,
    TravelCostSpec,
)

HEDONIC_TEMPLATE_ID = "hedonic-pricing-v1"
TRAVEL_COST_TEMPLATE_ID = "travel-cost-v1"
CV_TEMPLATE_ID = "contingent-valuation-v1"
DCE_TEMPLATE_ID = "dce-clogit-v1"


def expected_hedonic_script(spec: HedonicSpec) -> tuple[bytes, str]:
    return _render(
        "hedonic_pricing.R",
        {
            "__PRICE__": _string(spec.columns.price),
            "__ENVIRONMENT__": _string(spec.columns.environmental_attribute),
            "__CONTROLS__": _vector(spec.columns.controls),
            "__FIXED_EFFECTS__": _vector(spec.columns.fixed_effects),
            "__CLUSTER__": _optional(spec.cluster_column),
            "__FORM__": _string(spec.functional_form),
            "__SENSITIVITY_FORM__": _string(spec.sensitivity_form),
            "__CONFIDENCE__": repr(spec.confidence_level),
            "__MAX_CONDITION__": repr(spec.max_condition_number),
            "__MAX_SENSITIVITY__": repr(spec.max_sensitivity_change),
            "__CURRENCY__": _string(spec.currency),
            "__PRICE_BASE__": _string(spec.price_base),
            "__TIME_BASIS__": _string(spec.time_basis),
            "__POPULATION_BASIS__": _string(spec.population_basis),
        },
    )


def expected_travel_cost_script(spec: TravelCostSpec) -> tuple[bytes, str]:
    return _render(
        "travel_cost.R",
        {
            "__UNIT__": _string(spec.columns.unit),
            "__VISITS__": _string(spec.columns.visits),
            "__COST__": _string(spec.columns.travel_cost),
            "__EXPOSURE__": _string(spec.columns.exposure),
            "__SITE__": _string(spec.columns.site),
            "__SUBSTITUTES__": _vector(spec.columns.substitute_controls),
            "__CLUSTER__": _optional(spec.cluster_column),
            "__FAMILY__": _string(spec.family),
            "__CONFIDENCE__": repr(spec.confidence_level),
            "__MAX_DISPERSION__": repr(spec.max_dispersion),
            "__MAX_SENSITIVITY__": repr(spec.max_sensitivity_change),
            "__CURRENCY__": _string(spec.currency),
            "__PRICE_BASE__": _string(spec.price_base),
            "__TIME_BASIS__": _string(spec.time_basis),
            "__POPULATION_BASIS__": _string(spec.population_basis),
        },
    )


def expected_cv_script(spec: ContingentValuationSpec) -> tuple[bytes, str]:
    return _render(
        "contingent_valuation.R",
        {
            "__RESPONDENT__": _string(spec.columns.respondent),
            "__RESPONSE__": _string(spec.columns.response),
            "__BID__": _string(spec.columns.bid),
            "__COVARIATES__": _vector(spec.columns.covariates),
            "__LINK__": _string(spec.link),
            "__CONFIDENCE__": repr(spec.confidence_level),
            "__MAX_EXTREME__": repr(spec.max_extreme_probability_share),
            "__MAX_SENSITIVITY__": repr(spec.max_sensitivity_change),
            "__CURRENCY__": _string(spec.currency),
            "__PRICE_BASE__": _string(spec.price_base),
            "__TIME_BASIS__": _string(spec.time_basis),
            "__POPULATION_BASIS__": _string(spec.population_basis),
        },
    )


def expected_dce_script(spec: DiscreteChoiceSpec) -> tuple[bytes, str]:
    return _render(
        "dce_clogit.R",
        {
            "__RESPONDENT__": _string(spec.columns.respondent),
            "__CHOICE_SET__": _string(spec.columns.choice_set),
            "__ALTERNATIVE__": _string(spec.columns.alternative),
            "__CHOSEN__": _string(spec.columns.chosen),
            "__COST__": _string(spec.columns.cost),
            "__ATTRIBUTES__": _vector(spec.columns.attributes),
            "__CLUSTER__": _string(spec.cluster_column),
            "__CONFIDENCE__": repr(spec.confidence_level),
            "__MIN_COST__": repr(spec.min_abs_cost_coefficient),
            "__MAX_SENSITIVITY__": repr(spec.max_sensitivity_change),
            "__CURRENCY__": _string(spec.currency),
            "__PRICE_BASE__": _string(spec.price_base),
            "__TIME_BASIS__": _string(spec.time_basis),
            "__POPULATION_BASIS__": _string(spec.population_basis),
        },
    )


def _render(name: str, replacements: dict[str, str]) -> tuple[bytes, str]:
    template = (
        files("envresearch.econometrics")
        .joinpath("templates", name)
        .read_text(encoding="utf-8")
    )
    tokens = set(re.findall(r"__[A-Z][A-Z0-9_]*__", template))
    if tokens != set(replacements):
        raise ValueError("R template token contract is incomplete")
    for token, value in replacements.items():
        template = template.replace(token, value)
    data = template.encode("utf-8")
    return data, hashlib.sha256(data).hexdigest()


def _string(value: str) -> str:
    return json.dumps(value)


def _vector(values: tuple[str, ...]) -> str:
    return "c(" + ", ".join(_string(value) for value in values) + ")"


def _optional(value: str | None) -> str:
    return "NULL" if value is None else _string(value)

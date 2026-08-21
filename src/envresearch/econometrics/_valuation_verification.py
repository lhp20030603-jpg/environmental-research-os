"""Independent configuration binding for local valuation results."""

from __future__ import annotations

import re
from pathlib import Path

from envresearch.econometrics._valuation_authority import package_authorities_match
from envresearch.econometrics._valuation_diagnostics import (
    cv_diagnostics_match,
    hedonic_diagnostics_match,
    travel_diagnostics_match,
)
from envresearch.econometrics._valuation_support import (
    valuation_result_matches_snapshot,
)
from envresearch.econometrics._valuation_welfare import valuation_evidence_matches
from envresearch.econometrics.r_evidence import PackageAuthority
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
)
from envresearch.models.artifact import ArtifactRef


def valuation_configuration_matches(
    data: bytes | None,
    spec: HedonicSpec | TravelCostSpec | ContingentValuationSpec | DiscreteChoiceSpec,
    result: HedonicResult
    | TravelCostResult
    | ContingentValuationResult
    | DiscreteChoiceResult,
    runtime_version: str | None,
    package_authorities: tuple[ArtifactRef, ...] = (),
    package_authority_records: tuple[PackageAuthority, ...] = (),
    output_root: Path | None = None,
) -> bool:
    """Bind output design, units, support, and runtime to approved authority."""
    if data is None or runtime_version is None or not is_valuation_pair(spec, result):
        return False
    configuration = result.configuration
    expected_fixed_effects = (
        spec.columns.fixed_effects
        if isinstance(spec, HedonicSpec)
        else (spec.columns.site,)
        if isinstance(spec, TravelCostSpec)
        else ()
    )
    expected_form = spec.functional_form if isinstance(spec, HedonicSpec) else None
    expected_family = spec.family if isinstance(spec, TravelCostSpec) else None
    expected_link = spec.link if isinstance(spec, ContingentValuationSpec) else None
    expected_cluster = getattr(spec, "cluster_column", None)
    units = {
        (item.currency, item.price_base, item.time_basis, item.population_basis)
        for item in result.welfare
    }
    common = (
        result.max_sensitivity_change == spec.max_sensitivity_change
        and valuation_result_matches_snapshot(data, spec, result)
        and configuration.method_id == spec.method_id
        and configuration.confidence_level == spec.confidence_level
        and configuration.cluster_column == expected_cluster
        and configuration.fixed_effects == expected_fixed_effects
        and configuration.functional_form == expected_form
        and configuration.family == expected_family
        and configuration.link == expected_link
        and units
        == {(spec.currency, spec.price_base, spec.time_basis, spec.population_basis)}
        and same_r_version(configuration.r_version, runtime_version)
        and configuration.package_authorities == package_authorities
        and package_authorities_match(spec, package_authority_records)
        and valuation_evidence_matches(spec, result)
    )
    if isinstance(spec, HedonicSpec) and isinstance(result, HedonicResult):
        return common and (
            result.environmental_term == spec.columns.environmental_attribute
            and result.price_term == spec.columns.price
            and result.max_condition_number == spec.max_condition_number
            and hedonic_diagnostics_match(data, spec, result)
        )
    if isinstance(spec, TravelCostSpec) and isinstance(result, TravelCostResult):
        return common and (
            result.cost_term == spec.columns.travel_cost
            and result.max_dispersion == spec.max_dispersion
            and output_root is not None
            and travel_diagnostics_match(data, output_root, spec, result)
        )
    if isinstance(spec, ContingentValuationSpec) and isinstance(
        result, ContingentValuationResult
    ):
        return common and (
            result.bid_term == spec.columns.bid
            and result.max_extreme_probability_share
            == spec.max_extreme_probability_share
            and cv_diagnostics_match(data, spec, result)
        )
    return (
        common
        and isinstance(spec, DiscreteChoiceSpec)
        and isinstance(result, DiscreteChoiceResult)
        and result.cost_term == spec.columns.cost
        and result.attribute_terms == spec.columns.attributes
        and result.min_abs_cost_coefficient == spec.min_abs_cost_coefficient
    )


def is_valuation_pair(spec: object, result: object) -> bool:
    """Return whether one spec/result pair selects the same valuation method."""
    return (
        isinstance(spec, HedonicSpec)
        and isinstance(result, HedonicResult)
        or isinstance(spec, TravelCostSpec)
        and isinstance(result, TravelCostResult)
        or isinstance(spec, ContingentValuationSpec)
        and isinstance(result, ContingentValuationResult)
        or isinstance(spec, DiscreteChoiceSpec)
        and isinstance(result, DiscreteChoiceResult)
    )


def same_r_version(emitted: str, runtime: str) -> bool:
    pattern = re.compile(r"\bversion\s+([0-9]+(?:\.[0-9]+){1,3})\b", re.IGNORECASE)
    emitted_match = pattern.search(emitted)
    runtime_match = pattern.search(runtime)
    return (
        emitted_match is not None
        and runtime_match is not None
        and emitted_match.group(1) == runtime_match.group(1)
    )

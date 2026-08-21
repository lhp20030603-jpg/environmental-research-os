"""Closed discriminated union for every trusted local econometrics method."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, TypeAdapter

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

AnalysisSpec = Annotated[
    LocalAnalysisSpec
    | PanelFeSpec
    | Iv2slsSpec
    | RddSpec
    | RctSpec
    | SyntheticControlSpec
    | EnvironmentalMeasurementSpec
    | MetaAnalysisSpec
    | HedonicSpec
    | TravelCostSpec
    | ContingentValuationSpec
    | DiscreteChoiceSpec,
    Field(discriminator="method_id"),
]
ANALYSIS_SPEC_ADAPTER: TypeAdapter[AnalysisSpec] = TypeAdapter(AnalysisSpec)


def required_columns_for(spec: AnalysisSpec) -> tuple[str, ...]:
    """Return declared input columns for any registered analysis authority."""
    if isinstance(spec, LocalAnalysisSpec):
        return spec.columns.required()
    return spec.required_columns()

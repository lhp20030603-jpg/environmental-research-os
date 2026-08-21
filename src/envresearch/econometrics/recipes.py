"""Method-neutral econometrics recipe registry."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.causal_contracts import Iv2slsSpec, PanelFeSpec, RddSpec
from envresearch.econometrics.causal_models import (
    Iv2slsResult,
    PanelFeResult,
    RddResult,
)
from envresearch.econometrics.contracts import LocalAnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.did_models import DidResult
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.econometrics.valuation_results import (
    ContingentValuationResult,
    DiscreteChoiceResult,
    HedonicResult,
    TravelCostResult,
)
from envresearch.econometrics.wave1_results import (
    EnvironmentalMeasurementResult,
    MetaAnalysisResult,
    RctResult,
    SyntheticControlResult,
)
from envresearch.models.artifact import ArtifactRef

AnalysisResult = (
    DidResult
    | PanelFeResult
    | Iv2slsResult
    | RddResult
    | RctResult
    | SyntheticControlResult
    | EnvironmentalMeasurementResult
    | MetaAnalysisResult
    | HedonicResult
    | TravelCostResult
    | ContingentValuationResult
    | DiscreteChoiceResult
)


def expected_script_for(spec: AnalysisSpec) -> tuple[bytes, str, str]:
    """Rebuild the registered template bytes, digest, and template identity."""
    if isinstance(spec, LocalAnalysisSpec):
        from envresearch.econometrics._did_script import (
            TEMPLATE_ID,
            expected_did_script,
        )

        data, digest = expected_did_script(spec)
        return data, digest, TEMPLATE_ID
    from envresearch.econometrics._causal_script import (
        IV_TEMPLATE_ID,
        PANEL_TEMPLATE_ID,
        RDD_TEMPLATE_ID,
        expected_iv_script,
        expected_panel_script,
        expected_rdd_script,
    )

    if isinstance(spec, PanelFeSpec):
        data, digest = expected_panel_script(spec)
        return data, digest, PANEL_TEMPLATE_ID
    if isinstance(spec, Iv2slsSpec):
        data, digest = expected_iv_script(spec)
        return data, digest, IV_TEMPLATE_ID
    if isinstance(spec, RddSpec):
        data, digest = expected_rdd_script(spec)
        return data, digest, RDD_TEMPLATE_ID
    from envresearch.econometrics._wave1_script import (
        MEASUREMENT_TEMPLATE_ID,
        META_TEMPLATE_ID,
        RCT_TEMPLATE_ID,
        SCM_TEMPLATE_ID,
        expected_measurement_script,
        expected_meta_script,
        expected_rct_script,
        expected_scm_script,
    )
    from envresearch.econometrics.wave1_contracts import (
        EnvironmentalMeasurementSpec,
        MetaAnalysisSpec,
        RctSpec,
        SyntheticControlSpec,
    )

    if isinstance(spec, RctSpec):
        data, digest = expected_rct_script(spec)
        return data, digest, RCT_TEMPLATE_ID
    if isinstance(spec, EnvironmentalMeasurementSpec):
        data, digest = expected_measurement_script(spec)
        return data, digest, MEASUREMENT_TEMPLATE_ID
    if isinstance(spec, SyntheticControlSpec):
        data, digest = expected_scm_script(spec)
        return data, digest, SCM_TEMPLATE_ID
    if isinstance(spec, MetaAnalysisSpec):
        data, digest = expected_meta_script(spec)
        return data, digest, META_TEMPLATE_ID
    from envresearch.econometrics._valuation_script import (
        CV_TEMPLATE_ID,
        DCE_TEMPLATE_ID,
        HEDONIC_TEMPLATE_ID,
        TRAVEL_COST_TEMPLATE_ID,
        expected_cv_script,
        expected_dce_script,
        expected_hedonic_script,
        expected_travel_cost_script,
    )
    from envresearch.econometrics.valuation_contracts import (
        ContingentValuationSpec,
        DiscreteChoiceSpec,
        HedonicSpec,
        TravelCostSpec,
    )

    if isinstance(spec, HedonicSpec):
        data, digest = expected_hedonic_script(spec)
        return data, digest, HEDONIC_TEMPLATE_ID
    if isinstance(spec, TravelCostSpec):
        data, digest = expected_travel_cost_script(spec)
        return data, digest, TRAVEL_COST_TEMPLATE_ID
    if isinstance(spec, ContingentValuationSpec):
        data, digest = expected_cv_script(spec)
        return data, digest, CV_TEMPLATE_ID
    if isinstance(spec, DiscreteChoiceSpec):
        data, digest = expected_dce_script(spec)
        return data, digest, DCE_TEMPLATE_ID
    raise KeyError("analysis method is not registered")


class EconometricsRecipe(Protocol):
    """One registered method implementation with typed output."""

    method_id: str
    expected_outputs: frozenset[str]

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None: ...

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript: ...

    def parse(
        self,
        output_root: Path,
        package_authorities: tuple[ArtifactRef, ...] = (),
    ) -> AnalysisResult: ...


def recipe_for(method_id: str, *, workspace: Path) -> EconometricsRecipe:
    """Construct one registered recipe without branching in callers."""
    if method_id == "did-event-study":
        from envresearch.econometrics.did import DidEventStudyRecipe

        return DidEventStudyRecipe(workspace)
    if method_id == "panel-fe":
        from envresearch.econometrics.panel_fe import PanelFeRecipe

        return PanelFeRecipe(workspace)
    if method_id == "iv-2sls":
        from envresearch.econometrics.iv_2sls import Iv2slsRecipe

        return Iv2slsRecipe(workspace)
    if method_id == "rdd-local-linear":
        from envresearch.econometrics.rdd import RddRecipe

        return RddRecipe(workspace)
    if method_id == "rct-itt":
        from envresearch.econometrics.rct import RctRecipe

        return RctRecipe(workspace)
    if method_id == "environmental-measurement":
        from envresearch.econometrics.measurement import MeasurementRecipe

        return MeasurementRecipe(workspace)
    if method_id == "synthetic-control":
        from envresearch.econometrics.synthetic_control import SyntheticControlRecipe

        return SyntheticControlRecipe(workspace)
    if method_id == "meta-analysis":
        from envresearch.econometrics.meta_analysis import MetaAnalysisRecipe

        return MetaAnalysisRecipe(workspace)
    if method_id == "hedonic-pricing":
        from envresearch.econometrics.hedonic import HedonicRecipe

        return HedonicRecipe(workspace)
    if method_id == "travel-cost":
        from envresearch.econometrics.travel_cost import TravelCostRecipe

        return TravelCostRecipe(workspace)
    if method_id == "contingent-valuation":
        from envresearch.econometrics.contingent_valuation import (
            ContingentValuationRecipe,
        )

        return ContingentValuationRecipe(workspace)
    if method_id == "dce-clogit":
        from envresearch.econometrics.discrete_choice import DiscreteChoiceRecipe

        return DiscreteChoiceRecipe(workspace)
    raise KeyError(f"unknown econometrics recipe: {method_id}")

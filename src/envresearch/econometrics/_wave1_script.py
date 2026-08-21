"""Deterministic rendering for RCT and measurement R scripts."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files

from envresearch.econometrics.wave1_contracts import (
    EnvironmentalMeasurementSpec,
    MetaAnalysisSpec,
    RctSpec,
    SyntheticControlSpec,
)

RCT_TEMPLATE_ID = "rct-itt-v1"
MEASUREMENT_TEMPLATE_ID = "environmental-measurement-v1"
SCM_TEMPLATE_ID = "synthetic-control-v1"
META_TEMPLATE_ID = "meta-analysis-v1"


def expected_rct_script(spec: RctSpec) -> tuple[bytes, str]:
    return _render(
        "rct_itt.R",
        {
            "__UNIT__": _string(spec.columns.unit),
            "__ASSIGNMENT__": _string(spec.columns.assignment),
            "__OUTCOME__": _string(spec.columns.outcome),
            "__BASELINES__": _vector(spec.columns.baseline_covariates),
            "__CONFIDENCE__": repr(spec.inference.confidence_level),
            "__MAX_ATTRITION__": repr(spec.max_attrition_rate),
            "__BALANCE_THRESHOLD__": repr(spec.balance_smd_threshold),
        },
    )


def expected_measurement_script(
    spec: EnvironmentalMeasurementSpec,
) -> tuple[bytes, str]:
    return _render(
        "environmental_measurement.R",
        {
            "__MONITOR__": _string(spec.columns.monitor),
            "__TIMESTAMP__": _string(spec.columns.timestamp),
            "__VALUE__": _string(spec.columns.value),
            "__UNIT__": _string(spec.columns.unit),
            "__DETECTION_FLAG__": _optional(spec.columns.detection_flag),
            "__DECLARED_UNIT__": _string(spec.declared_unit),
            "__MAX_MISSING__": repr(spec.max_missing_rate),
            "__VALID_MIN__": repr(spec.valid_min),
            "__VALID_MAX__": repr(spec.valid_max),
            "__EXCEEDANCE__": repr(spec.exceedance_threshold),
        },
    )


def expected_scm_script(spec: SyntheticControlSpec) -> tuple[bytes, str]:
    return _render(
        "synthetic_control.R",
        {
            "__UNIT__": _string(spec.columns.unit),
            "__TIME__": _string(spec.columns.time),
            "__OUTCOME__": _string(spec.columns.outcome),
            "__TREATED_UNIT__": _string(spec.treated_unit),
            "__INTERVENTION__": repr(spec.intervention_time),
            "__MAX_PRE_RMSPE__": repr(spec.max_pre_rmspe),
            "__MAX_LOO__": repr(spec.max_leave_one_out_change),
        },
    )


def expected_meta_script(spec: MetaAnalysisSpec) -> tuple[bytes, str]:
    return _render(
        "meta_analysis.R",
        {
            "__STUDY__": _string(spec.columns.study),
            "__EFFECT__": _string(spec.columns.effect),
            "__VARIANCE__": _string(spec.columns.variance),
            "__CONFIDENCE__": repr(spec.confidence_level),
            "__MAX_LOO__": repr(spec.max_leave_one_out_change),
        },
    )


def _render(name: str, replacements: dict[str, str]) -> tuple[bytes, str]:
    template = (
        files("envresearch.econometrics")
        .joinpath("templates", name)
        .read_text(encoding="utf-8")
    )
    for token, value in replacements.items():
        template = template.replace(token, value)
    if "__" in template:
        raise ValueError("R template contains an unresolved token")
    data = template.encode("utf-8")
    return data, hashlib.sha256(data).hexdigest()


def _string(value: str) -> str:
    return json.dumps(value)


def _vector(values: tuple[str, ...]) -> str:
    return "c(" + ", ".join(_string(value) for value in values) + ")"


def _optional(value: str | None) -> str:
    return "NULL" if value is None else _string(value)

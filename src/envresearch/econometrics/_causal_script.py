"""Deterministic rendering for repository-owned causal-policy R scripts."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files

from envresearch.econometrics.causal_contracts import Iv2slsSpec, PanelFeSpec, RddSpec

PANEL_TEMPLATE_ID = "panel-fe-v1"
IV_TEMPLATE_ID = "iv-2sls-v1"
RDD_TEMPLATE_ID = "rdd-local-linear-v1"


def expected_panel_script(spec: PanelFeSpec) -> tuple[bytes, str]:
    replacements = {
        "__OUTCOME__": _r_string(spec.columns.outcome),
        "__REGRESSORS__": _r_vector(spec.columns.regressors),
        "__FIXED_EFFECTS__": _r_vector(spec.columns.fixed_effects),
        "__UNIT__": _r_string(spec.columns.unit),
        "__TIME__": _r_string(spec.columns.time),
        "__CLUSTER__": _r_optional(spec.inference.cluster_column),
        "__CONFIDENCE__": repr(spec.inference.confidence_level),
    }
    return _render("panel_fe.R", replacements)


def expected_iv_script(spec: Iv2slsSpec) -> tuple[bytes, str]:
    replacements = {
        "__OUTCOME__": _r_string(spec.columns.outcome),
        "__ENDOGENOUS__": _r_vector(spec.columns.endogenous),
        "__INSTRUMENTS__": _r_vector(spec.columns.instruments),
        "__CONTROLS__": _r_vector(spec.columns.controls),
        "__FIXED_EFFECTS__": _r_vector(spec.columns.fixed_effects),
        "__CLUSTER__": _r_optional(spec.inference.cluster_column),
        "__CONFIDENCE__": repr(spec.inference.confidence_level),
        "__WEAK_THRESHOLD__": repr(spec.weak_instrument_f_threshold),
    }
    return _render("iv_2sls.R", replacements)


def expected_rdd_script(spec: RddSpec) -> tuple[bytes, str]:
    replacements = {
        "__OUTCOME__": _r_string(spec.columns.outcome),
        "__RUNNING__": _r_string(spec.columns.running),
        "__COVARIATES__": _r_vector(spec.columns.covariates),
        "__CUTOFF__": repr(spec.design.cutoff),
        "__BANDWIDTH__": repr(spec.design.bandwidth),
        "__DONUT_RADIUS__": repr(spec.design.donut_radius),
        "__CLUSTER__": _r_optional(spec.inference.cluster_column),
        "__CONFIDENCE__": repr(spec.inference.confidence_level),
    }
    return _render("rdd_local_linear.R", replacements)


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


def _r_string(value: str) -> str:
    return json.dumps(value)


def _r_vector(values: tuple[str, ...]) -> str:
    return "c(" + ", ".join(_r_string(value) for value in values) + ")"


def _r_optional(value: str | None) -> str:
    return "NULL" if value is None else _r_string(value)

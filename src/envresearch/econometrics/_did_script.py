"""Pure deterministic rendering for the repository-owned DiD script."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files

from envresearch.econometrics.contracts import LocalAnalysisSpec

TEMPLATE_ID = "did-event-study-v1"


def expected_did_script(spec: LocalAnalysisSpec) -> tuple[bytes, str]:
    """Return exact repository template bytes and their content digest."""
    template = (
        files("envresearch.econometrics")
        .joinpath("templates/did_event_study.R")
        .read_text(encoding="utf-8")
    )
    replacements = {
        "__UNIT__": _r_string(spec.columns.unit),
        "__TIME__": _r_string(spec.columns.time),
        "__OUTCOME__": _r_string(spec.columns.outcome),
        "__COHORT__": _r_string(spec.columns.treatment_cohort),
        "__COVARIATES__": _r_vector(spec.columns.covariates),
        "__COMPARISON__": _r_string(spec.comparison_group),
        "__REFERENCE__": str(spec.reference_period),
        "__SEED__": str(spec.inference.bootstrap_seed),
        "__CONFIDENCE__": repr(spec.inference.confidence_level),
        "__INTERVAL_MODE__": _r_string(spec.inference.interval_mode),
        "__CLUSTER__": _r_string(spec.inference.cluster_column),
    }
    script = template
    for token, value in replacements.items():
        script = script.replace(token, value)
    if "__" in script:
        raise ValueError("R template contains an unresolved token")
    data = script.encode("utf-8")
    return data, hashlib.sha256(data).hexdigest()


def _r_string(value: str) -> str:
    return json.dumps(value)


def _r_vector(values: tuple[str, ...]) -> str:
    return "c(" + ", ".join(_r_string(value) for value in values) + ")"

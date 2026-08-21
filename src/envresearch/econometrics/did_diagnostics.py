"""Predeclared DiD diagnostics shared by execution and verification."""

from envresearch.econometrics.contracts import LocalAnalysisSpec
from envresearch.econometrics.did_models import DidResult


def pretrend_exceeded(spec: LocalAnalysisSpec, result: DidResult) -> bool:
    """Return whether any varying-base pre-treatment pseudo-ATT exceeds authority."""
    leads = tuple(
        abs(item.estimate)
        for item in result.dynamic.estimates
        if item.event_time is not None and item.event_time < 0
    )
    return bool(leads) and max(leads) > spec.max_pretrend_abs

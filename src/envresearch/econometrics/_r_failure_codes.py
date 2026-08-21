"""Closed mapping from generated-R markers to scientific failure codes."""

from __future__ import annotations

_REGISTERED = {
    "hedonic-pricing-v1": frozenset({"HEDONIC_TERM_UNIDENTIFIED"}),
    "travel-cost-v1": frozenset({"TRAVEL_COST_SLOPE_INVALID"}),
    "contingent-valuation-v1": frozenset(
        {
            "CV_BID_SLOPE_INVALID",
            "CV_MONOTONICITY_FAILED",
            "CV_SEPARATION_DETECTED",
            "CV_WTP_UNIDENTIFIED",
        }
    ),
    "dce-clogit-v1": frozenset({"DCE_TERM_UNIDENTIFIED", "DCE_COST_SLOPE_INVALID"}),
}


def registered_failure_code(template_id: str, output: str) -> str | None:
    """Return one standalone marker authorized for the exact template."""
    markers = {
        line for line in output.splitlines() if line.startswith("ENVRESEARCH_CODE:")
    }
    return next(
        (
            code
            for code in _REGISTERED.get(template_id, ())
            if f"ENVRESEARCH_CODE:{code}" in markers
        ),
        None,
    )

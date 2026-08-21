"""Reconstruct causal-method support from authenticated snapshot bytes."""

from __future__ import annotations

import csv
import io

from envresearch.econometrics.causal_contracts import Iv2slsSpec, PanelFeSpec, RddSpec
from envresearch.econometrics.causal_models import RddSupport, RegressionSupport


def support_matches_snapshot(
    data: bytes,
    spec: PanelFeSpec | Iv2slsSpec | RddSpec,
    support: RegressionSupport | RddSupport,
) -> bool:
    """Compare every emitted support field with the authenticated CSV rows."""
    try:
        rows = tuple(csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))
        expected = _expected_support(rows, spec)
    except (UnicodeDecodeError, KeyError, TypeError, ValueError):
        return False
    return expected == support


def _expected_support(
    rows: tuple[dict[str, str], ...], spec: PanelFeSpec | Iv2slsSpec | RddSpec
) -> RegressionSupport | RddSupport:
    if isinstance(spec, PanelFeSpec):
        return RegressionSupport(
            observations=len(rows),
            clusters=_unique(rows, spec.inference.cluster_column),
            units=_unique(rows, spec.columns.unit),
            time_periods=_unique(rows, spec.columns.time),
        )
    if isinstance(spec, Iv2slsSpec):
        return RegressionSupport(
            observations=len(rows),
            clusters=_unique(rows, spec.inference.cluster_column),
        )
    if isinstance(spec, RddSpec):
        running = tuple(float(row[spec.columns.running]) for row in rows)
        centered = tuple(value - spec.design.cutoff for value in running)
        main = tuple(value for value in centered if abs(value) < spec.design.bandwidth)
        donut = tuple(value for value in main if abs(value) >= spec.design.donut_radius)
        return RddSupport(
            observations=len(main),
            left_observations=sum(value < 0 for value in main),
            right_observations=sum(value >= 0 for value in main),
            left_unique_running=len({value for value in main if value < 0}),
            right_unique_running=len({value for value in main if value >= 0}),
            donut_left_observations=sum(value < 0 for value in donut),
            donut_right_observations=sum(value >= 0 for value in donut),
        )
    raise TypeError("support reconstruction requires a causal specification")


def _unique(rows: tuple[dict[str, str], ...], column: str | None) -> int | None:
    return None if column is None else len({row[column] for row in rows})

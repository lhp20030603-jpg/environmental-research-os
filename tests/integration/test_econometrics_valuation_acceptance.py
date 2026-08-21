"""Formal V0.3.1 exit acceptance and post-release capability boundary."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from itertools import pairwise
from pathlib import Path
from typing import NoReturn

import pytest

from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.service import LocalAnalysisService
from envresearch.storage.research_artifacts import ResearchArtifactStore

RESERVED_GATES = {
    "spatial",
    "exposure",
    "forecasting-wave3",
    "stata",
}
RESERVED_METHODS = {
    "spatial-lag",
    "spatial-error",
    "exposure-assignment",
    "environmental-forecasting",
    "wave3-structural",
    "stata-adapter",
}


class _NeverBackend:
    def execute(self, *args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("invalid DCE input must fail before estimator execution")


def test_checked_green_corpus_has_estimable_support() -> None:
    """Collapsing checked fixtures to saturated or separated samples must fail."""
    root = (
        Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core/runner/data"
    )
    with (root / "cv.csv").open(newline="", encoding="utf-8") as source:
        cv_rows = tuple(csv.DictReader(source))
    bid_responses: dict[str, set[str]] = {}
    for row in cv_rows:
        bid_responses.setdefault(row["bid"], set()).add(row["yes"])
    assert len(cv_rows) >= 30
    assert all(responses == {"0", "1"} for responses in bid_responses.values())

    with (root / "travel-cost.csv").open(newline="", encoding="utf-8") as source:
        travel_rows = tuple(csv.DictReader(source))
    assert len(travel_rows) >= 20
    assert len({row["exposure"] for row in travel_rows}) > 1
    assert len({row["site_id"] for row in travel_rows}) >= 3

    with (root / "hedonic.csv").open(newline="", encoding="utf-8") as source:
        hedonic_rows = tuple(csv.DictReader(source))
    assert len(hedonic_rows) >= 20

    with (root / "dce.csv").open(newline="", encoding="utf-8") as source:
        dce_rows = tuple(csv.DictReader(source))
    assert len(dce_rows) >= 40


def test_green_sensitivity_thresholds_follow_input_scale_not_estimates() -> None:
    """Green tolerances are fixed from checked input support before execution."""
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core/runner"
    with (root / "data/hedonic.csv").open(newline="", encoding="utf-8") as source:
        prices = sorted({float(row["price"]) for row in csv.DictReader(source)})
    increments = tuple(right - left for left, right in pairwise(prices))
    hedonic = json.loads((root / "green-hedonic.yaml").read_text(encoding="utf-8"))
    with (root / "data/dce.csv").open(newline="", encoding="utf-8") as source:
        costs = tuple(float(row["cost"]) for row in csv.DictReader(source))
    dce = json.loads((root / "green-dce.yaml").read_text(encoding="utf-8"))

    assert hedonic["spec"]["max_sensitivity_change"] == min(increments) / 2
    assert dce["spec"]["max_sensitivity_change"] == (max(costs) - min(costs)) / 2


def test_v031_extension_registry_is_frozen_and_reserved_methods_are_gated(
    tmp_path: Path,
) -> None:
    """Making a reserved post-V0.3.1 method executable must fail this test."""
    from envresearch.econometrics.extension_registry import (
        FROZEN_EXTENSION_REGISTRY,
    )

    registry = FROZEN_EXTENSION_REGISTRY
    assert registry.is_frozen
    assert set(registry.reserved_extensions) == RESERVED_GATES
    assert all(
        item.status == "capability-gated"
        for item in registry.reserved_extensions.values()
    )
    declared_methods = {
        method_id for gate in registry.gates for method_id in gate.method_ids
    }
    assert declared_methods == RESERVED_METHODS
    for method_id in declared_methods:
        assert not registry.can_execute(method_id)
        with pytest.raises(KeyError, match="unknown econometrics recipe"):
            recipe_for(method_id, workspace=tmp_path / method_id)


def test_v031_exit_is_current_complete_and_v04_ready() -> None:
    """Reopening anything except the exact current 9/9 real-R exit must fail."""
    run_root_value = os.getenv("ENVRESEARCH_V031_ACCEPTANCE_ROOT")
    if run_root_value is None:
        pytest.skip("set ENVRESEARCH_V031_ACCEPTANCE_ROOT for the sealed real-R exit")

    from envresearch.econometrics.valuation_transition import (
        V031ExitHarness,
    )

    exit_harness = V031ExitHarness(Path(run_root_value).resolve())
    report = exit_harness.run_and_evaluate()

    assert report.status == "passed"
    assert len(report.outcomes) == 9
    assert all(item.status == "matched" for item in report.outcomes)
    assert exit_harness.extension_registry.is_frozen
    assert set(exit_harness.extension_registry.reserved_extensions) == RESERVED_GATES
    assert all(
        not exit_harness.extension_registry.can_execute(method_id)
        for method_id in RESERVED_METHODS
    )


def test_invalid_exact_input_reference_is_bound_to_data_bytes(tmp_path: Path) -> None:
    """Reusing an invalid-input report for changed exact bytes must fail this test."""
    spec = ANALYSIS_SPEC_ADAPTER.validate_python(
        {
            "schema_version": "econometrics.dce-clogit.v1",
            "method_id": "dce-clogit",
            "data_path": tmp_path / "invalid.csv",
            "columns": {
                "respondent": "id",
                "choice_set": "set",
                "alternative": "alt",
                "chosen": "chosen",
                "cost": "cost",
                "attributes": ("air",),
            },
            "budget": {
                "inactivity_seconds": 10,
                "max_output_bytes": 100_000,
                "max_workspace_bytes": 1_000_000,
            },
            "currency": "cny",
            "price_base": "p2025",
            "time_basis": "annual",
            "population_basis": "sample",
            "confidence_level": 0.95,
            "cluster_column": "id",
            "sensitivity": "include-alternative-specific-constants",
            "min_abs_cost_coefficient": 0.01,
            "max_sensitivity_change": 0.01,
        }
    )
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "analysis"), _NeverBackend()
    )
    first = b"id,set,alt,chosen,cost,air\n1,1,a,1,1,1\n1,1,b,1,2,2\n"
    second = first.replace(b"1,2,2", b"1,3,2")

    first_ref = service.run_exact(spec, first, hashlib.sha256(first).hexdigest())
    second_ref = service.run_exact(spec, second, hashlib.sha256(second).hexdigest())

    assert first_ref != second_ref
    assert service.status(first_ref).code == "DCE_CHOICE_SET_INVALID"
    assert service.status(second_ref).code == "DCE_CHOICE_SET_INVALID"

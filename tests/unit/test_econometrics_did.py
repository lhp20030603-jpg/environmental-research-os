"""Strict DiD/event-study recipe tests."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.econometrics.contracts import LocalAnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot, snapshot_csv
from envresearch.econometrics.did import DidEventStudyRecipe, DidOutputInvalid
from envresearch.econometrics.did_models import DidResult, EstimateRow
from envresearch.econometrics.r_runtime import FORBIDDEN_R
from envresearch.econometrics.recipes import recipe_for
from envresearch.models.artifact import ArtifactRef
from envresearch.storage.research_artifacts import ResearchArtifactStore


def _spec(tmp_path: Path) -> LocalAnalysisSpec:
    return LocalAnalysisSpec.model_validate(
        {
            "schema_version": "econometrics.local-analysis.v1",
            "method_id": "did-event-study",
            "data_path": tmp_path / "local.csv",
            "columns": {
                "unit": "unit",
                "time": "year",
                "outcome": "outcome",
                "treatment_cohort": "cohort",
                "covariates": ("x1",),
            },
            "comparison_group": "never-treated",
            "reference_period": -1,
            "inference": {
                "confidence_level": 0.95,
                "cluster_column": "unit",
                "interval_mode": "pointwise",
                "bootstrap_seed": 20260811,
            },
            "budget": {
                "inactivity_seconds": 30,
                "max_output_bytes": 100_000,
                "max_workspace_bytes": 1_000_000,
            },
        }
    )


def _snapshot() -> LocalDataSnapshot:
    return LocalDataSnapshot(
        reference=ArtifactRef(
            artifact_id="local-data-1234567890abcdef",
            artifact_version=1,
            content_hash="a" * 64,
        ),
        relative_path=Path("artifacts/econometrics/data") / f"{'a' * 64}.csv",
        sha256="a" * 64,
        size_bytes=100,
        row_count=12,
        columns=("unit", "year", "outcome", "cohort", "x1"),
        missing_values=(),
    )


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _valid_outputs(root: Path) -> Path:
    estimates = [
        {
            "term": "event_time_-2",
            "event_time": -2,
            "group": "",
            "time": "",
            "estimate": -0.1,
            "std_error": 0.1,
            "conf_low": -0.3,
            "conf_high": 0.1,
        },
        {
            "term": "event_time_0",
            "event_time": 0,
            "group": "",
            "time": "",
            "estimate": 1.2,
            "std_error": 0.2,
            "conf_low": 0.8,
            "conf_high": 1.6,
        },
    ]
    _write_csv(root / "baseline.csv", estimates)
    _write_csv(
        root / "group_time_att.csv",
        [
            {
                **estimates[1],
                "term": "att_2020_2020",
                "event_time": 0,
                "group": 2020,
                "time": 2020,
            }
        ],
    )
    _write_csv(root / "dynamic.csv", estimates)
    _write_csv(
        root / "support.csv",
        [
            {
                "observations": 12,
                "units": 3,
                "treated_units": 2,
                "comparison_units": 1,
                "cohorts": 1,
                "dropped_observations": 0,
                "duplicate_panel_keys": 0,
                "removal_rule": "complete-declared-columns",
            }
        ],
    )
    _write_csv(
        root / "package_configuration.csv",
        [
            {
                "r_version": "4.4.3",
                "fixest_version": "0.13.2",
                "did_version": "2.1.2",
                "bootstrap_seed": 20260811,
                "comparison_group": "never-treated",
                "reference_period": -1,
                "base_period": "varying",
                "anticipation": 0,
                "confidence_level": 0.95,
                "interval_mode": "pointwise",
                "baseline_interval_method": "pointwise-normal",
                "did_interval_method": "pointwise-normal",
                "cluster_column": "unit",
            }
        ],
    )
    _write_csv(
        root / "support_by_group_time.csv",
        [
            {
                "group": 2020,
                "time": 2020,
                "event_time": 0,
                "treated_observations": 1,
                "comparison_observations": 1,
                "treated_units": 1,
                "comparison_units": 1,
            }
        ],
    )
    _write_csv(
        root / "cohort_timing.csv",
        [{"cohort": 2020, "units": 1, "first_period": 2018, "last_period": 2021}],
    )
    _write_csv(
        root / "covariate_balance.csv",
        [
            {
                "covariate": "x1",
                "treated_mean": 2.5,
                "comparison_mean": 2.5,
                "standardized_difference": 0.0,
                "treated_n": 8,
                "comparison_n": 4,
            }
        ],
    )
    (root / "event_study.svg").write_text("<svg></svg>\n", encoding="utf-8")
    return root


def test_recipe_registry_and_render_are_method_neutral(tmp_path: Path) -> None:
    """The registry selects one recipe and renders only declared inputs."""
    recipe = recipe_for("did-event-study", workspace=tmp_path / "work")

    script = recipe.render(_spec(tmp_path), _snapshot())

    assert script.template_id == "did-event-study-v1"
    text = script.path.read_text(encoding="utf-8")
    assert "fixest::feols" in text
    assert "did::att_gt" in text
    assert "did::aggte" in text
    assert 'base_period = "varying"' in text
    assert "treatment_boundary <- -0.5" in text
    assert "scale_x(treatment_boundary)" in text
    assert "group_time_model$c" in text
    assert "degenerate covariate overlap" in text
    assert "input/data.csv" in text
    assert '"baseline.csv"' in text
    assert '"group_time_att.csv"' in text
    assert '"dynamic.csv"' in text
    assert '"support.csv"' in text
    assert '"package_configuration.csv"' in text
    assert str(_spec(tmp_path).data_path) not in text
    assert FORBIDDEN_R.search(text) is None


def test_did_script_allows_only_the_known_small_group_warning(
    tmp_path: Path,
) -> None:
    """Unknown scientific warnings stay fatal while small cohorts stay visible."""
    script = recipe_for("did-event-study", workspace=tmp_path / "work").render(
        _spec(tmp_path), _snapshot()
    )

    text = script.path.read_text(encoding="utf-8")

    assert "options(warn = 2)" in text
    assert "withCallingHandlers" in text
    assert "some small groups in your dataset" in text
    assert 'invokeRestart("muffleWarning")' in text


def test_checked_staggered_fixture_snapshots_and_renders(tmp_path: Path) -> None:
    """The checked never-treated fixture reaches the actual generated template."""
    fixture = (
        Path(__file__).parents[1] / "fixtures" / "econometrics" / "staggered_panel.csv"
    ).resolve()
    spec = _spec(tmp_path).model_copy(update={"data_path": fixture})
    snapshot = snapshot_csv(spec, ResearchArtifactStore(tmp_path / "store"))

    script = DidEventStudyRecipe(tmp_path / "work").render(spec, snapshot)

    text = script.path.read_text(encoding="utf-8")
    assert "treated_ever_internal" in text
    assert "ifelse(is.na(did_data[[cohort_column]]), 0" in text
    assert snapshot.row_count == 12


def test_parser_requires_all_three_estimands_and_support(tmp_path: Path) -> None:
    """A useful result contains baseline, group-time, and dynamic estimates."""
    result = DidEventStudyRecipe(tmp_path / "work").parse(
        _valid_outputs(tmp_path / "output")
    )

    assert result.baseline.estimates
    assert result.group_time_att.estimates
    assert result.dynamic.estimates
    assert result.packages.base_period == "varying"
    assert result.support.comparison_units == 1
    assert all(
        row.conf_low <= row.estimate <= row.conf_high for row in result.all_estimates()
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-estimates", "estimate table"),
        ("nonfinite", "finite"),
        ("reversed-ci", "confidence interval"),
        ("duplicate-event", "duplicate event time"),
        ("dropped", "dropped observations"),
        ("no-comparison", "comparison support"),
        ("missing-package", "package configuration"),
        ("extra-config", "invalid schema"),
        ("mismatched-support", "must match group-time estimate keys"),
    ],
)
def test_parser_rejects_nonuseful_or_undeclared_outputs(
    tmp_path: Path, mutation: str, message: str
) -> None:
    """Malformed or scientifically incomplete outputs fail closed."""
    root = _valid_outputs(tmp_path / "output")
    if mutation == "missing-estimates":
        (root / "baseline.csv").write_text("", encoding="utf-8")
    elif mutation == "nonfinite":
        text = (root / "dynamic.csv").read_text(encoding="utf-8")
        (root / "dynamic.csv").write_text(
            text.replace("1.2", "NaN", 1), encoding="utf-8"
        )
    elif mutation == "reversed-ci":
        text = (root / "baseline.csv").read_text(encoding="utf-8")
        (root / "baseline.csv").write_text(
            text.replace("-0.3,0.1", "0.2,0.1"), encoding="utf-8"
        )
    elif mutation == "duplicate-event":
        rows = list(csv.DictReader((root / "dynamic.csv").open(encoding="utf-8")))
        rows[1]["event_time"] = rows[0]["event_time"]
        _write_csv(root / "dynamic.csv", rows)
    elif mutation == "dropped":
        support = list(csv.DictReader((root / "support.csv").open(encoding="utf-8")))
        support[0]["dropped_observations"] = "1"
        _write_csv(root / "support.csv", support)
    elif mutation == "no-comparison":
        support = list(csv.DictReader((root / "support.csv").open(encoding="utf-8")))
        support[0]["comparison_units"] = "0"
        _write_csv(root / "support.csv", support)
    elif mutation == "missing-package":
        (root / "package_configuration.csv").unlink()
    elif mutation == "extra-config":
        rows = list(csv.DictReader((root / "package_configuration.csv").open()))
        rows[0]["unreviewed"] = "true"
        _write_csv(root / "package_configuration.csv", rows)
    else:
        rows = list(csv.DictReader((root / "support_by_group_time.csv").open()))
        rows[0]["time"], rows[0]["event_time"] = "2021", "1"
        _write_csv(root / "support_by_group_time.csv", rows)

    with pytest.raises(DidOutputInvalid, match=message):
        DidEventStudyRecipe(tmp_path / "work").parse(root)


def test_models_reject_empty_maps_and_invalid_panel_keys() -> None:
    """Strict typed results reject extra fields and invalid numeric rows."""
    with pytest.raises(ValidationError):
        DidResult.model_validate({})
    with pytest.raises(ValidationError, match="confidence interval"):
        EstimateRow.model_validate(
            {
                "term": "x",
                "event_time": 0,
                "group": None,
                "time": None,
                "estimate": 2.0,
                "std_error": 0.1,
                "conf_low": 3.0,
                "conf_high": 4.0,
            }
        )
    payload = _valid_outputs_payload()
    payload["group_time_att"]["estimates"][0]["event_time"] = 2
    with pytest.raises(ValidationError, match="event key is inconsistent"):
        DidResult.model_validate(payload)


def test_parser_rejects_symlinked_output(tmp_path: Path) -> None:
    """Estimator outputs cannot redirect parsing outside the result root."""
    root = _valid_outputs(tmp_path / "output")
    outside = tmp_path / "outside.csv"
    outside.write_bytes((root / "baseline.csv").read_bytes())
    (root / "baseline.csv").unlink()
    (root / "baseline.csv").symlink_to(outside)

    with pytest.raises(DidOutputInvalid, match="bounded regular file"):
        DidEventStudyRecipe(tmp_path / "work").parse(root)


def _valid_outputs_payload() -> dict[str, object]:
    """Return one direct-model payload for cross-table invariant tests."""
    row = {
        "term": "event_time_0",
        "event_time": 0,
        "group": None,
        "time": None,
        "estimate": 1.0,
        "std_error": 0.1,
        "conf_low": 0.8,
        "conf_high": 1.2,
    }
    return {
        "baseline": {"estimator": "fixest::feols", "estimates": (row,)},
        "group_time_att": {
            "estimator": "did::att_gt",
            "estimates": ({**row, "group": 2020, "time": 2020},),
        },
        "dynamic": {"estimator": "did::aggte", "estimates": (row,)},
        "support": {
            "observations": 12,
            "units": 3,
            "treated_units": 2,
            "comparison_units": 1,
            "cohorts": 1,
            "dropped_observations": 0,
            "duplicate_panel_keys": 0,
            "removal_rule": "complete-declared-columns",
        },
        "support_cells": (
            {
                "group": 2020,
                "time": 2020,
                "event_time": 0,
                "treated_observations": 1,
                "comparison_observations": 1,
                "treated_units": 1,
                "comparison_units": 1,
            },
        ),
        "cohort_timing": (
            {"cohort": 2020, "units": 1, "first_period": 2018, "last_period": 2021},
        ),
        "covariate_balance": (
            {
                "covariate": "x1",
                "treated_mean": 2.5,
                "comparison_mean": 2.5,
                "standardized_difference": 0.0,
                "treated_n": 8,
                "comparison_n": 4,
            },
        ),
        "packages": {
            "r_version": "4.4.3",
            "fixest_version": "0.13.2",
            "did_version": "2.1.2",
            "bootstrap_seed": 20260811,
            "comparison_group": "never-treated",
            "reference_period": -1,
            "base_period": "varying",
            "anticipation": 0,
            "confidence_level": 0.95,
            "interval_mode": "pointwise",
            "baseline_interval_method": "pointwise-normal",
            "did_interval_method": "pointwise-normal",
            "cluster_column": "unit",
        },
        "figure_sha256": "a" * 64,
    }

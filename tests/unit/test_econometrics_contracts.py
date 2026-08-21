"""Contract tests for trusted local econometrics analyses."""

from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from envresearch.econometrics.contracts import LocalAnalysisSpec


def valid_spec(data_path: Path) -> dict[str, object]:
    """Return one strict, fully declared local DiD specification."""
    return {
        "schema_version": "econometrics.local-analysis.v1",
        "method_id": "did-event-study",
        "data_path": data_path,
        "columns": {
            "unit": "unit",
            "time": "year",
            "outcome": "emissions",
            "treatment_cohort": "first_treated",
            "covariates": ("population",),
        },
        "comparison_group": "not-yet-treated",
        "reference_period": -1,
        "inference": {
            "confidence_level": 0.95,
            "cluster_column": "unit",
            "interval_mode": "simultaneous",
            "bootstrap_seed": 20260811,
        },
        "budget": {
            "inactivity_seconds": 120,
            "max_output_bytes": 2_000_000,
            "max_workspace_bytes": 20_000_000,
        },
    }


def test_local_analysis_spec_requires_explicit_panel_columns(tmp_path: Path) -> None:
    """A method ID without its data contract cannot reach execution."""
    with pytest.raises(ValidationError, match="columns"):
        LocalAnalysisSpec.model_validate(
            {
                "schema_version": "econometrics.local-analysis.v1",
                "method_id": "did-event-study",
                "data_path": tmp_path / "panel.csv",
            }
        )


def test_local_analysis_spec_accepts_a_serialized_local_path(tmp_path: Path) -> None:
    """Strict YAML loading still needs one explicit filesystem path string."""
    payload = valid_spec(tmp_path / "panel.csv")
    payload["data_path"] = str(payload["data_path"])

    spec = LocalAnalysisSpec.model_validate(payload)

    assert spec.data_path == tmp_path / "panel.csv"


@pytest.mark.parametrize("field", ["script", "url", "download", "r_expression"])
def test_local_analysis_spec_rejects_execution_and_download_fields(
    tmp_path: Path, field: str
) -> None:
    """The local lane cannot smuggle scripts or acquisition instructions."""
    payload = valid_spec(tmp_path / "panel.csv")
    payload[field] = "source('author.R')"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LocalAnalysisSpec.model_validate(payload)


def test_local_analysis_spec_rejects_duplicate_column_roles(tmp_path: Path) -> None:
    """Unit, time, outcome, and cohort roles must be unambiguous."""
    payload = valid_spec(tmp_path / "panel.csv")
    columns = dict(cast(dict[str, object], payload["columns"]))
    columns["time"] = "unit"
    payload["columns"] = columns

    with pytest.raises(ValidationError, match="column roles must be unique"):
        LocalAnalysisSpec.model_validate(payload)


def test_local_analysis_spec_is_frozen_and_method_registry_is_closed(
    tmp_path: Path,
) -> None:
    """V0.3-A installs only the reviewed DiD recipe."""
    spec = LocalAnalysisSpec.model_validate(valid_spec(tmp_path / "panel.csv"))

    with pytest.raises(ValidationError, match="frozen"):
        spec.reference_period = 0

    payload = valid_spec(tmp_path / "panel.csv")
    payload["method_id"] = "iv-2sls"
    with pytest.raises(ValidationError, match="did-event-study"):
        LocalAnalysisSpec.model_validate(payload)

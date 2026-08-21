"""DiD rendering tests for valid non-syntactic local column names."""

from pathlib import Path

from envresearch.econometrics.contracts import (
    ColumnMapping,
    InferenceSpec,
    LocalAnalysisSpec,
    ResourceBudget,
)
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.did import DidEventStudyRecipe
from envresearch.models.artifact import ArtifactRef


def test_render_safely_quotes_nonsyntactic_column_names(tmp_path: Path) -> None:
    """Spaces and R operators in declared names cannot break formula parsing."""
    columns = ColumnMapping(
        unit="unit id",
        time="calendar year",
        outcome="air pollution",
        treatment_cohort="first policy year",
        covariates=("income + trend",),
    )
    spec = LocalAnalysisSpec(
        schema_version="econometrics.local-analysis.v1",
        method_id="did-event-study",
        data_path=tmp_path / "local data.csv",
        columns=columns,
        comparison_group="never-treated",
        reference_period=-1,
        inference=InferenceSpec(
            confidence_level=0.95,
            cluster_column="unit id",
            interval_mode="pointwise",
            bootstrap_seed=20260811,
        ),
        budget=ResourceBudget(
            inactivity_seconds=30,
            max_output_bytes=100_000,
            max_workspace_bytes=1_000_000,
        ),
    )
    snapshot = LocalDataSnapshot(
        reference=ArtifactRef(
            artifact_id="local-data-1234567890abcdef",
            artifact_version=1,
            content_hash="a" * 64,
        ),
        relative_path=Path("artifacts/econometrics/data") / f"{'a' * 64}.csv",
        sha256="a" * 64,
        size_bytes=100,
        row_count=12,
        columns=columns.required(),
        missing_values=(),
    )

    text = (
        DidEventStudyRecipe(tmp_path / "work").render(spec, snapshot).path.read_text()
    )

    assert 'quote_identifier <- function(value) paste0("`", value, "`")' in text
    assert 'outcome_column <- "air pollution"' in text
    assert 'covariate_columns <- c("income + trend")' in text

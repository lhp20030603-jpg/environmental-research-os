"""Repository-owned sharp local-linear RDD recipe and strict parser."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import ValidationError

from envresearch.econometrics._causal_outputs import (
    CausalOutputInvalid,
    figure_digest,
    parse_coefficients,
    parse_configuration,
    read_rows,
)
from envresearch.econometrics._causal_script import (
    RDD_TEMPLATE_ID,
    expected_rdd_script,
)
from envresearch.econometrics._r_owned_files import publish_owned_file
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.causal_contracts import RddSpec
from envresearch.econometrics.causal_models import (
    BandwidthEstimate,
    RddResult,
    RddSupport,
    RegressionCoefficient,
)
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.models.artifact import ArtifactRef

INFERENCE_LIMITATION: Final = (
    "local-linear conventional inference; rdrobust RBC not included"
)


class RddRecipe:
    """Render and parse one bounded sharp local-linear RDD."""

    method_id = "rdd-local-linear"
    expected_outputs = frozenset(
        {
            "main.csv",
            "bandwidth_sensitivity.csv",
            "donut.csv",
            "covariate_continuity.csv",
            "support.csv",
            "package_configuration.csv",
            "rdd_plot.svg",
        }
    )

    def __init__(self, workspace: Path) -> None:
        if not workspace.is_absolute():
            raise ValueError("recipe workspace must be absolute")
        self.workspace = workspace.resolve()

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None:
        if not isinstance(spec, RddSpec):
            raise CausalOutputInvalid("analysis spec selects a different method")
        missing = tuple(
            name for name in spec.required_columns() if name not in snapshot.columns
        )
        if missing or snapshot.row_count < 8:
            raise CausalOutputInvalid("snapshot does not satisfy the RDD design")

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript:
        self.validate(spec, snapshot)
        assert isinstance(spec, RddSpec)
        data, digest = expected_rdd_script(spec)
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = publish_owned_file(
            self.workspace, "generated", f"rdd-{digest}.R", data, 0o444
        )
        return GeneratedRScript(template_id=RDD_TEMPLATE_ID, path=path, sha256=digest)

    def parse(
        self,
        output_root: Path,
        package_authorities: tuple[ArtifactRef, ...] = (),
    ) -> RddResult:
        try:
            sensitivity = _parse_sensitivity(output_root / "bandwidth_sensitivity.csv")
            result = RddResult(
                method_id="rdd-local-linear",
                main=_one_coefficient(output_root / "main.csv", "main"),
                bandwidth_sensitivity=sensitivity,
                donut=_one_coefficient(output_root / "donut.csv", "donut"),
                covariate_continuity=parse_coefficients(
                    output_root / "covariate_continuity.csv", allow_empty=True
                ),
                support=_parse_support(output_root / "support.csv"),
                configuration=parse_configuration(
                    output_root / "package_configuration.csv"
                ),
                figure_sha256=figure_digest(output_root / "rdd_plot.svg"),
                inference_limitation=INFERENCE_LIMITATION,
            )
        except (OSError, ValueError, ValidationError) as error:
            raise CausalOutputInvalid(str(error) or "RDD output is invalid") from error
        return result


def _one_coefficient(path: Path, label: str) -> RegressionCoefficient:
    coefficients = parse_coefficients(path)
    if len(coefficients) != 1:
        raise CausalOutputInvalid(f"{label} output must contain one coefficient")
    return coefficients[0]


def _parse_sensitivity(path: Path) -> tuple[BandwidthEstimate, ...]:
    rows = read_rows(
        path,
        ("multiplier", "term", "estimate", "std_error", "conf_low", "conf_high"),
    )
    estimates = tuple(
        BandwidthEstimate(
            multiplier=float(row["multiplier"]),
            coefficient=RegressionCoefficient(
                term=row["term"],
                estimate=float(row["estimate"]),
                std_error=float(row["std_error"]),
                conf_low=float(row["conf_low"]),
                conf_high=float(row["conf_high"]),
            ),
        )
        for row in rows
    )
    if tuple(item.multiplier for item in estimates) != (0.5, 1.0, 1.5):
        raise CausalOutputInvalid("bandwidth sensitivity is incomplete")
    return estimates


def _parse_support(path: Path) -> RddSupport:
    rows = read_rows(
        path,
        (
            "observations",
            "left_observations",
            "right_observations",
            "left_unique_running",
            "right_unique_running",
            "donut_left_observations",
            "donut_right_observations",
        ),
    )
    if len(rows) != 1:
        raise CausalOutputInvalid("RDD support output must contain one row")
    row = rows[0]
    try:
        return RddSupport(**{name: int(value) for name, value in row.items()})
    except (TypeError, ValueError, ValidationError) as error:
        raise CausalOutputInvalid("RDD support is invalid") from error

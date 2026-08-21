"""Repository-owned descriptive environmental-measurement recipe."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from envresearch.econometrics._causal_outputs import (
    CausalOutputInvalid,
    figure_digest,
    read_rows,
)
from envresearch.econometrics._r_owned_files import publish_owned_file
from envresearch.econometrics._wave1_evidence_models import (
    MeasurementQuantiles,
    MonitorCoverage,
    TemporalMean,
)
from envresearch.econometrics._wave1_script import (
    MEASUREMENT_TEMPLATE_ID,
    expected_measurement_script,
)
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.econometrics.wave1_contracts import EnvironmentalMeasurementSpec
from envresearch.econometrics.wave1_results import (
    MeasurementResult,
    MeasurementSupport,
    WavePackageConfiguration,
)
from envresearch.models.artifact import ArtifactRef


class MeasurementRecipe:
    """Render and parse one non-causal environmental measurement summary."""

    method_id = "environmental-measurement"
    expected_outputs = frozenset(
        {
            "summary.csv",
            "completeness.csv",
            "exceedances.csv",
            "temporal.csv",
            "monitor_coverage.csv",
            "package_configuration.csv",
            "measurement_plot.svg",
        }
    )

    def __init__(self, workspace: Path) -> None:
        if not workspace.is_absolute():
            raise ValueError("recipe workspace must be absolute")
        self.workspace = workspace.resolve()

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None:
        if not isinstance(spec, EnvironmentalMeasurementSpec):
            raise CausalOutputInvalid("analysis spec selects a different method")
        missing = tuple(
            name for name in spec.required_columns() if name not in snapshot.columns
        )
        if missing or snapshot.row_count < 1:
            raise CausalOutputInvalid("snapshot does not satisfy measurement design")

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript:
        self.validate(spec, snapshot)
        assert isinstance(spec, EnvironmentalMeasurementSpec)
        data, digest = expected_measurement_script(spec)
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = publish_owned_file(
            self.workspace, "generated", f"measurement-{digest}.R", data, 0o444
        )
        return GeneratedRScript(
            template_id=MEASUREMENT_TEMPLATE_ID, path=path, sha256=digest
        )

    def parse(
        self,
        output_root: Path,
        package_authorities: tuple[ArtifactRef, ...] = (),
    ) -> MeasurementResult:
        try:
            summary = _one(
                output_root / "summary.csv",
                (
                    "mean",
                    "minimum",
                    "q25",
                    "median",
                    "q75",
                    "maximum",
                    "exceedances",
                ),
            )
            complete = _one(
                output_root / "completeness.csv",
                (
                    "total",
                    "valid",
                    "missing",
                    "monitors",
                    "missing_rate",
                    "max_missing_rate",
                ),
            )
            config = _one(
                output_root / "package_configuration.csv",
                ("method_id", "r_version", "declared_unit"),
            )
            if config["method_id"] != "environmental-measurement":
                raise CausalOutputInvalid(
                    "measurement configuration selects another method"
                )
            exceedance = _one(output_root / "exceedances.csv", ("threshold", "count"))
            temporal = read_rows(output_root / "temporal.csv", ("date", "mean"))
            coverage = read_rows(
                output_root / "monitor_coverage.csv",
                ("monitor", "total", "valid", "missing"),
            )
            _validate_detail(summary, exceedance, temporal, coverage, complete)
            result = MeasurementResult(
                method_id="environmental-measurement",
                support=MeasurementSupport(
                    total=int(complete["total"]),
                    valid=int(complete["valid"]),
                    missing=int(complete["missing"]),
                    monitors=int(complete["monitors"]),
                ),
                quantiles=MeasurementQuantiles(
                    q25=float(summary["q25"]),
                    median=float(summary["median"]),
                    q75=float(summary["q75"]),
                ),
                temporal=tuple(
                    TemporalMean(date=row["date"], mean=float(row["mean"]))
                    for row in temporal
                ),
                monitor_coverage=tuple(
                    MonitorCoverage(
                        monitor=row["monitor"],
                        total=int(row["total"]),
                        valid=int(row["valid"]),
                        missing=int(row["missing"]),
                    )
                    for row in coverage
                ),
                mean=float(summary["mean"]),
                minimum=float(summary["minimum"]),
                maximum=float(summary["maximum"]),
                exceedances=int(summary["exceedances"]),
                exceedance_threshold=float(exceedance["threshold"]),
                missing_rate=float(complete["missing_rate"]),
                max_missing_rate=float(complete["max_missing_rate"]),
                declared_unit=config["declared_unit"],
                configuration=WavePackageConfiguration(
                    method_id="environmental-measurement",
                    r_version=config["r_version"],
                    package_authorities=package_authorities,
                ),
                figure_sha256=figure_digest(output_root / "measurement_plot.svg"),
            )
        except (OSError, ValueError, ValidationError) as error:
            raise CausalOutputInvalid(
                str(error) or "measurement output is invalid"
            ) from error
        return result


def _one(path: Path, header: tuple[str, ...]) -> dict[str, str]:
    rows = read_rows(path, header)
    if len(rows) != 1:
        raise CausalOutputInvalid("measurement output must contain one row")
    return rows[0]


def _validate_detail(
    summary: dict[str, str],
    exceedance: dict[str, str],
    temporal: list[dict[str, str]],
    coverage: list[dict[str, str]],
    complete: dict[str, str],
) -> None:
    if int(exceedance["count"]) != int(summary["exceedances"]):
        raise CausalOutputInvalid("measurement exceedance evidence conflicts")
    if len({row["date"] for row in temporal}) != len(temporal):
        raise CausalOutputInvalid("measurement temporal keys must be unique")
    if len({row["monitor"] for row in coverage}) != len(coverage):
        raise CausalOutputInvalid("measurement monitor keys must be unique")
    totals = tuple(
        sum(int(row[key]) for row in coverage) for key in ("total", "valid", "missing")
    )
    expected = tuple(int(complete[key]) for key in ("total", "valid", "missing"))
    if totals != expected or len(coverage) != int(complete["monitors"]):
        raise CausalOutputInvalid("measurement coverage does not reconcile")

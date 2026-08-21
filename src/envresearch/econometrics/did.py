"""Repository-owned DiD/event-study recipe and strict result parser."""

from __future__ import annotations

import csv
import hashlib
import math
import os
import stat
from pathlib import Path

from pydantic import ValidationError

from envresearch.econometrics._did_script import TEMPLATE_ID, expected_did_script
from envresearch.econometrics._r_owned_files import publish_owned_file
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.contracts import LocalAnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.did_models import (
    CohortTiming,
    CovariateBalance,
    DidResult,
    EstimateRow,
    EstimateTable,
    PackageConfiguration,
    SupportCell,
    SupportDiagnostic,
)
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.models.artifact import ArtifactRef


class DidOutputInvalid(ValueError):
    """The estimator output is malformed or scientifically incomplete."""


class DidEventStudyRecipe:
    """Render and parse the V0.3-A staggered-adoption DiD recipe."""

    method_id = "did-event-study"
    expected_outputs = frozenset(
        {
            "baseline.csv",
            "group_time_att.csv",
            "dynamic.csv",
            "support.csv",
            "support_by_group_time.csv",
            "cohort_timing.csv",
            "covariate_balance.csv",
            "package_configuration.csv",
            "event_study.svg",
        }
    )

    def __init__(self, workspace: Path) -> None:
        if not workspace.is_absolute():
            raise ValueError("recipe workspace must be absolute")
        self.workspace = workspace.resolve()

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None:
        """Require the exact registered method and snapshotted declared columns."""
        if not isinstance(spec, LocalAnalysisSpec):
            raise DidOutputInvalid("analysis spec selects a different method")
        missing = tuple(
            name for name in spec.columns.required() if name not in snapshot.columns
        )
        if missing:
            raise DidOutputInvalid(f"snapshot is missing declared columns: {missing}")
        if snapshot.row_count < 4:
            raise DidOutputInvalid("DiD requires a nontrivial panel")

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript:
        """Render the exact repository template with declared identifiers only."""
        self.validate(spec, snapshot)
        assert isinstance(spec, LocalAnalysisSpec)
        try:
            data, digest = expected_did_script(spec)
        except ValueError as error:
            raise DidOutputInvalid(str(error)) from error
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = publish_owned_file(
            self.workspace, "generated", f"did-event-study-{digest}.R", data, 0o444
        )
        return GeneratedRScript(template_id=TEMPLATE_ID, path=path, sha256=digest)

    def parse(
        self,
        output_root: Path,
        package_authorities: tuple[ArtifactRef, ...] = (),
    ) -> DidResult:
        """Parse exact bounded machine outputs into strict scientific models."""
        package_path = output_root / "package_configuration.csv"
        if not package_path.exists():
            raise DidOutputInvalid("package configuration is missing or invalid")
        try:
            result = DidResult(
                baseline=_estimate_table(output_root / "baseline.csv", "fixest::feols"),
                group_time_att=_estimate_table(
                    output_root / "group_time_att.csv", "did::att_gt"
                ),
                dynamic=_estimate_table(output_root / "dynamic.csv", "did::aggte"),
                support=SupportDiagnostic.model_validate(
                    _support_row(output_root / "support.csv")
                ),
                support_cells=tuple(
                    SupportCell.model_validate(row)
                    for row in _typed_rows(
                        output_root / "support_by_group_time.csv",
                        (
                            "group",
                            "time",
                            "event_time",
                            "treated_observations",
                            "comparison_observations",
                            "treated_units",
                            "comparison_units",
                        ),
                        int_fields=(
                            "group",
                            "time",
                            "event_time",
                            "treated_observations",
                            "comparison_observations",
                            "treated_units",
                            "comparison_units",
                        ),
                    )
                ),
                cohort_timing=tuple(
                    CohortTiming.model_validate(row)
                    for row in _typed_rows(
                        output_root / "cohort_timing.csv",
                        ("cohort", "units", "first_period", "last_period"),
                        int_fields=("cohort", "units", "first_period", "last_period"),
                    )
                ),
                covariate_balance=tuple(
                    CovariateBalance.model_validate(row)
                    for row in _typed_rows(
                        output_root / "covariate_balance.csv",
                        (
                            "covariate",
                            "treated_mean",
                            "comparison_mean",
                            "standardized_difference",
                            "treated_n",
                            "comparison_n",
                        ),
                        int_fields=("treated_n", "comparison_n"),
                        float_fields=(
                            "treated_mean",
                            "comparison_mean",
                            "standardized_difference",
                        ),
                        allow_empty=True,
                    )
                ),
                packages=PackageConfiguration.model_validate(
                    _package_row(package_path)
                ),
                figure_sha256=hashlib.sha256(
                    _read_regular(output_root / "event_study.svg", 4 * 1024 * 1024)
                ).hexdigest(),
            )
        except (OSError, csv.Error, KeyError, ValidationError, ValueError) as error:
            message = str(error)
            if "package_configuration" in str(getattr(error, "filename", "")):
                message = "package configuration is missing or invalid"
            raise DidOutputInvalid(message or "DiD output is invalid") from error
        return result


def _estimate_table(path: Path, estimator: str) -> EstimateTable:
    """Parse one exact finite estimate CSV under a fixed schema."""
    rows = _read_csv(path)
    if not rows:
        raise DidOutputInvalid("estimate table must not be empty")
    estimates: list[EstimateRow] = []
    for row in rows:
        estimates.append(
            EstimateRow(
                term=row["term"],
                event_time=_optional_int(row["event_time"]),
                group=_optional_int(row["group"]),
                time=_optional_int(row["time"]),
                estimate=_finite(row["estimate"]),
                std_error=_finite(row["std_error"]),
                conf_low=_finite(row["conf_low"]),
                conf_high=_finite(row["conf_high"]),
            )
        )
    return EstimateTable(estimator=estimator, estimates=tuple(estimates))


def _read_csv(path: Path) -> list[dict[str, str]]:
    """Read one regular bounded CSV with an exact header."""
    expected = (
        "term",
        "event_time",
        "group",
        "time",
        "estimate",
        "std_error",
        "conf_low",
        "conf_high",
    )
    data = _read_regular(path, 4 * 1024 * 1024).decode("utf-8")
    reader = csv.DictReader(data.splitlines())
    if tuple(reader.fieldnames or ()) != expected:
        raise DidOutputInvalid("estimate table has an invalid schema")
    return list(reader)


def _support_row(path: Path) -> dict[str, object]:
    """Parse the exact one-row support diagnostic."""
    row = _single_row(
        path,
        (
            "observations",
            "units",
            "treated_units",
            "comparison_units",
            "cohorts",
            "dropped_observations",
            "duplicate_panel_keys",
            "removal_rule",
        ),
    )
    return {
        "observations": int(row["observations"]),
        "units": int(row["units"]),
        "treated_units": int(row["treated_units"]),
        "comparison_units": int(row["comparison_units"]),
        "cohorts": int(row["cohorts"]),
        "dropped_observations": int(row["dropped_observations"]),
        "duplicate_panel_keys": int(row["duplicate_panel_keys"]),
        "removal_rule": row["removal_rule"],
    }


def _package_row(path: Path) -> dict[str, object]:
    """Parse the exact one-row package and inference configuration."""
    row = _single_row(
        path,
        (
            "r_version",
            "fixest_version",
            "did_version",
            "bootstrap_seed",
            "comparison_group",
            "reference_period",
            "base_period",
            "anticipation",
            "confidence_level",
            "interval_mode",
            "baseline_interval_method",
            "did_interval_method",
            "cluster_column",
        ),
    )
    return {
        "r_version": row["r_version"],
        "fixest_version": row["fixest_version"],
        "did_version": row["did_version"],
        "bootstrap_seed": int(row["bootstrap_seed"]),
        "comparison_group": row["comparison_group"],
        "reference_period": int(row["reference_period"]),
        "base_period": row["base_period"],
        "anticipation": int(row["anticipation"]),
        "confidence_level": float(row["confidence_level"]),
        "interval_mode": row["interval_mode"],
        "baseline_interval_method": row["baseline_interval_method"],
        "did_interval_method": row["did_interval_method"],
        "cluster_column": row["cluster_column"],
    }


def _single_row(path: Path, expected: tuple[str, ...]) -> dict[str, str]:
    """Read exactly one nonempty CSV record."""
    reader = csv.DictReader(
        _read_regular(path, 1024 * 1024).decode("utf-8").splitlines()
    )
    if tuple(reader.fieldnames or ()) != expected:
        raise DidOutputInvalid("configuration output has an invalid schema")
    rows = list(reader)
    if (
        len(rows) != 1
        or None in rows[0]
        or any(value is None for value in rows[0].values())
    ):
        raise DidOutputInvalid(
            "configuration output must contain exactly one complete row"
        )
    return rows[0]


def _typed_rows(
    path: Path,
    expected: tuple[str, ...],
    *,
    int_fields: tuple[str, ...] = (),
    float_fields: tuple[str, ...] = (),
    allow_empty: bool = False,
) -> list[dict[str, object]]:
    """Parse one diagnostic CSV with an exact schema and typed cells."""
    reader = csv.DictReader(
        _read_regular(path, 4 * 1024 * 1024).decode("utf-8").splitlines()
    )
    if tuple(reader.fieldnames or ()) != expected:
        raise DidOutputInvalid("diagnostic output has an invalid schema")
    parsed: list[dict[str, object]] = []
    for raw in reader:
        if None in raw or any(value is None for value in raw.values()):
            raise DidOutputInvalid("diagnostic output contains incomplete rows")
        row: dict[str, object] = dict(raw)
        for field in int_fields:
            row[field] = int(raw[field])
        for field in float_fields:
            row[field] = _finite(raw[field])
        parsed.append(row)
    if not parsed and not allow_empty:
        raise DidOutputInvalid("diagnostic output must not be empty")
    return parsed


def _read_regular(path: Path, max_bytes: int) -> bytes:
    """Reject links, devices, and oversized estimator outputs."""
    try:
        lexical = path.lstat()
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as error:
        raise DidOutputInvalid(
            "estimator output must be a bounded regular file"
        ) from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lexical.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (lexical.st_dev, lexical.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > max_bytes
        ):
            raise DidOutputInvalid("estimator output must be a bounded regular file")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, min(1024 * 1024, max_bytes - total + 1)):
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise DidOutputInvalid("estimator output exceeds its size limit")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _finite(value: str) -> float:
    """Parse one finite floating-point spelling."""
    parsed = float(value)
    if not math.isfinite(parsed):
        raise DidOutputInvalid("estimate values must be finite")
    return parsed


def _optional_int(value: str) -> int | None:
    """Parse an optional integer key without float coercion."""
    return None if value == "" else int(value)

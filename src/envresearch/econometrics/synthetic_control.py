"""Repository-owned synthetic-control recipe."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from envresearch.econometrics._causal_outputs import (
    CausalOutputInvalid,
    figure_digest,
    parse_coefficients,
    read_rows,
)
from envresearch.econometrics._r_owned_files import publish_owned_file
from envresearch.econometrics._wave1_script import SCM_TEMPLATE_ID, expected_scm_script
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.econometrics.wave1_contracts import SyntheticControlSpec
from envresearch.econometrics.wave1_results import (
    DonorWeight,
    LeaveOneOutEffect,
    PlaceboEffect,
    SyntheticControlResult,
    SyntheticGap,
    WavePackageConfiguration,
)
from envresearch.models.artifact import ArtifactRef


class SyntheticControlRecipe:
    """Render and parse one single-treated-unit synthetic control."""

    method_id = "synthetic-control"
    expected_outputs = frozenset(
        {
            "effect.csv",
            "weights.csv",
            "gaps.csv",
            "rmspe.csv",
            "placebo.csv",
            "leave_one_out.csv",
            "package_configuration.csv",
            "synthetic_control.svg",
        }
    )

    def __init__(self, workspace: Path) -> None:
        if not workspace.is_absolute():
            raise ValueError("recipe workspace must be absolute")
        self.workspace = workspace.resolve()

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None:
        if not isinstance(spec, SyntheticControlSpec):
            raise CausalOutputInvalid("analysis spec selects a different method")
        if snapshot.row_count < 9 or any(
            name not in snapshot.columns for name in spec.required_columns()
        ):
            raise CausalOutputInvalid(
                "snapshot does not satisfy synthetic-control design"
            )

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript:
        self.validate(spec, snapshot)
        assert isinstance(spec, SyntheticControlSpec)
        data, digest = expected_scm_script(spec)
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = publish_owned_file(
            self.workspace, "generated", f"scm-{digest}.R", data, 0o444
        )
        return GeneratedRScript(template_id=SCM_TEMPLATE_ID, path=path, sha256=digest)

    def parse(
        self, output_root: Path, package_authorities: tuple[ArtifactRef, ...] = ()
    ) -> SyntheticControlResult:
        try:
            effect = parse_coefficients(output_root / "effect.csv")
            if len(effect) != 1 or effect[0].term != "ATT":
                raise CausalOutputInvalid("synthetic-control effect must contain ATT")
            weights = read_rows(output_root / "weights.csv", ("donor", "weight"))
            gaps = read_rows(
                output_root / "gaps.csv",
                ("time", "treated", "synthetic", "gap", "period"),
            )
            placebo = read_rows(output_root / "placebo.csv", ("unit", "effect"))
            leave = read_rows(
                output_root / "leave_one_out.csv",
                ("omitted", "effect", "absolute_change"),
            )
            rmspe = _one(
                output_root / "rmspe.csv",
                (
                    "pre_periods",
                    "post_periods",
                    "pre_rmspe",
                    "post_rmspe",
                    "max_pre_rmspe",
                    "post_pre_ratio",
                ),
            )
            config = _one(
                output_root / "package_configuration.csv",
                (
                    "method_id",
                    "r_version",
                    "package_version",
                    "intervention_time",
                    "leave_one_out_threshold",
                ),
            )
            if config["method_id"] != "synthetic-control":
                raise CausalOutputInvalid("SCM configuration selects another method")
            _authority_version(
                package_authorities, "synthdid", config["package_version"]
            )
            result = SyntheticControlResult(
                method_id="synthetic-control",
                effect=effect[0],
                donor_weights=tuple(
                    DonorWeight(donor=row["donor"], weight=float(row["weight"]))
                    for row in weights
                ),
                gaps=tuple(
                    SyntheticGap(
                        time=float(row["time"]),
                        treated=float(row["treated"]),
                        synthetic=float(row["synthetic"]),
                        gap=float(row["gap"]),
                        period=_period(row["period"]),
                    )
                    for row in gaps
                ),
                placebos=tuple(
                    PlaceboEffect(unit=row["unit"], effect=float(row["effect"]))
                    for row in placebo
                ),
                leave_one_out=tuple(
                    LeaveOneOutEffect(
                        omitted=row["omitted"],
                        effect=float(row["effect"]),
                        absolute_change=float(row["absolute_change"]),
                    )
                    for row in leave
                ),
                pre_periods=int(rmspe["pre_periods"]),
                post_periods=int(rmspe["post_periods"]),
                pre_rmspe=float(rmspe["pre_rmspe"]),
                post_rmspe=float(rmspe["post_rmspe"]),
                post_pre_ratio=float(rmspe["post_pre_ratio"]),
                intervention_time=float(config["intervention_time"]),
                package_version=config["package_version"],
                max_pre_rmspe=float(rmspe["max_pre_rmspe"]),
                max_leave_one_out_change=max(
                    float(row["absolute_change"]) for row in leave
                ),
                leave_one_out_threshold=float(config["leave_one_out_threshold"]),
                configuration=WavePackageConfiguration(
                    method_id="synthetic-control",
                    r_version=config["r_version"],
                    package_authorities=package_authorities,
                ),
                figure_sha256=figure_digest(output_root / "synthetic_control.svg"),
            )
        except (OSError, ValueError, ValidationError) as error:
            message = str(error) or "synthetic-control output is invalid"
            code = (
                "SCM_PREFIT_EXCEEDED"
                if "pre-fit exceeds" in message
                else "SCM_SENSITIVITY_EXCEEDED"
                if "sensitivity exceeds" in message
                else "OUTPUT_INVALID"
            )
            raise CausalOutputInvalid(message, code=code) from error
        return result


def _one(path: Path, header: tuple[str, ...]) -> dict[str, str]:
    rows = read_rows(path, header)
    if len(rows) != 1:
        raise CausalOutputInvalid("synthetic-control output must contain one row")
    return rows[0]


def _period(value: str) -> Literal["pre", "post"]:
    if value not in {"pre", "post"}:
        raise CausalOutputInvalid("synthetic-control period is invalid")
    return "pre" if value == "pre" else "post"


def _authority_version(
    authorities: tuple[ArtifactRef, ...], package: str, version: str
) -> None:
    prefix = f"r-package-authority-{package}-"
    expected = _numeric_version(version)
    if expected is None or not any(
        item.artifact_id.startswith(prefix)
        and _numeric_version(item.artifact_id.removeprefix(prefix)) == expected
        for item in authorities
    ):
        raise CausalOutputInvalid(f"{package} package authority/version is invalid")


def _numeric_version(value: str) -> tuple[int, ...] | None:
    if re.fullmatch(r"[0-9]+(?:[.-][0-9]+)+", value) is None:
        return None
    return tuple(int(item) for item in re.split(r"[.-]", value))

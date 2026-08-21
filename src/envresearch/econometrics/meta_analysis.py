"""Repository-owned fixed and random-effects meta-analysis recipe."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from envresearch.econometrics._causal_outputs import (
    CausalOutputInvalid,
    figure_digest,
    parse_coefficients,
    read_rows,
)
from envresearch.econometrics._r_owned_files import publish_owned_file
from envresearch.econometrics._wave1_script import (
    META_TEMPLATE_ID,
    expected_meta_script,
)
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.econometrics.synthetic_control import _authority_version
from envresearch.econometrics.wave1_contracts import MetaAnalysisSpec
from envresearch.econometrics.wave1_results import (
    FunnelPoint,
    LeaveOneOutEffect,
    MetaAnalysisResult,
    StudyEvidence,
    WavePackageConfiguration,
)
from envresearch.models.artifact import ArtifactRef


class MetaAnalysisRecipe:
    """Render and parse fixed plus DL random-effects evidence."""

    method_id = "meta-analysis"
    expected_outputs = frozenset(
        {
            "fixed.csv",
            "random.csv",
            "heterogeneity.csv",
            "study_weights.csv",
            "leave_one_out.csv",
            "funnel.csv",
            "package_configuration.csv",
            "forest_funnel.svg",
        }
    )

    def __init__(self, workspace: Path) -> None:
        if not workspace.is_absolute():
            raise ValueError("recipe workspace must be absolute")
        self.workspace = workspace.resolve()

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None:
        if not isinstance(spec, MetaAnalysisSpec):
            raise CausalOutputInvalid("analysis spec selects a different method")
        if snapshot.row_count < 2 or any(
            name not in snapshot.columns for name in spec.required_columns()
        ):
            raise CausalOutputInvalid("snapshot does not satisfy meta-analysis design")

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript:
        self.validate(spec, snapshot)
        assert isinstance(spec, MetaAnalysisSpec)
        data, digest = expected_meta_script(spec)
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = publish_owned_file(
            self.workspace, "generated", f"meta-{digest}.R", data, 0o444
        )
        return GeneratedRScript(template_id=META_TEMPLATE_ID, path=path, sha256=digest)

    def parse(
        self, output_root: Path, package_authorities: tuple[ArtifactRef, ...] = ()
    ) -> MetaAnalysisResult:
        try:
            fixed = parse_coefficients(output_root / "fixed.csv")
            random = parse_coefficients(output_root / "random.csv")
            if (
                len(fixed) != 1
                or len(random) != 1
                or fixed[0].term != "fixed"
                or random[0].term != "random"
            ):
                raise CausalOutputInvalid("meta-analysis summaries are incomplete")
            heterogeneity = _one(
                output_root / "heterogeneity.csv",
                (
                    "studies",
                    "q",
                    "i_squared",
                    "tau_squared",
                    "inverse_variance_support",
                    "prediction_low",
                    "prediction_high",
                ),
            )
            weights = read_rows(
                output_root / "study_weights.csv",
                ("study", "effect", "std_error", "weight"),
            )
            leave = read_rows(
                output_root / "leave_one_out.csv",
                ("omitted", "effect", "absolute_change"),
            )
            funnel = read_rows(
                output_root / "funnel.csv", ("study", "effect", "std_error")
            )
            config = _one(
                output_root / "package_configuration.csv",
                (
                    "method_id",
                    "r_version",
                    "package_version",
                    "confidence_level",
                    "model",
                    "leave_one_out_threshold",
                ),
            )
            if (
                config["method_id"] != "meta-analysis"
                or config["model"] != "fixed-and-dl-random"
            ):
                raise CausalOutputInvalid("meta-analysis model is invalid")
            _authority_version(
                package_authorities, "metafor", config["package_version"]
            )
            result = MetaAnalysisResult(
                method_id="meta-analysis",
                fixed=fixed[0],
                random=random[0],
                study_weights=tuple(
                    StudyEvidence(
                        study=row["study"],
                        effect=float(row["effect"]),
                        std_error=float(row["std_error"]),
                        weight=float(row["weight"]),
                    )
                    for row in weights
                ),
                funnel=tuple(
                    FunnelPoint(
                        study=row["study"],
                        effect=float(row["effect"]),
                        std_error=float(row["std_error"]),
                    )
                    for row in funnel
                ),
                leave_one_out=tuple(
                    LeaveOneOutEffect(
                        omitted=row["omitted"],
                        effect=float(row["effect"]),
                        absolute_change=float(row["absolute_change"]),
                    )
                    for row in leave
                ),
                studies=int(heterogeneity["studies"]),
                q=float(heterogeneity["q"]),
                i_squared=float(heterogeneity["i_squared"]),
                tau_squared=float(heterogeneity["tau_squared"]),
                inverse_variance_support=float(
                    heterogeneity["inverse_variance_support"]
                ),
                prediction_low=float(heterogeneity["prediction_low"]),
                prediction_high=float(heterogeneity["prediction_high"]),
                package_version=config["package_version"],
                model="fixed-and-dl-random",
                max_leave_one_out_change=max(
                    float(row["absolute_change"]) for row in leave
                ),
                leave_one_out_threshold=float(config["leave_one_out_threshold"]),
                configuration=WavePackageConfiguration(
                    method_id="meta-analysis",
                    r_version=config["r_version"],
                    confidence_level=float(config["confidence_level"]),
                    package_authorities=package_authorities,
                ),
                figure_sha256=figure_digest(output_root / "forest_funnel.svg"),
            )
        except (OSError, ValueError, ValidationError) as error:
            message = str(error) or "meta-analysis output is invalid"
            code = (
                "META_INFLUENCE_EXCEEDED"
                if "influence exceeds" in message
                else "OUTPUT_INVALID"
            )
            raise CausalOutputInvalid(message, code=code) from error
        return result


def _one(path: Path, header: tuple[str, ...]) -> dict[str, str]:
    rows = read_rows(path, header)
    if len(rows) != 1:
        raise CausalOutputInvalid("meta-analysis output must contain one row")
    return rows[0]

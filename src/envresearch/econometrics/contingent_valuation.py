"""Repository-owned single-bounded Contingent Valuation recipe."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from pydantic import ValidationError

from envresearch.econometrics._causal_outputs import (
    CausalOutputInvalid,
    figure_digest,
    read_rows,
)
from envresearch.econometrics._r_owned_files import publish_owned_file
from envresearch.econometrics._stated_sensitivity import stated_sensitivity
from envresearch.econometrics._valuation_outputs import (
    bid_yes_shares,
    coefficients,
    configuration,
    covariance,
    support,
    welfare,
)
from envresearch.econometrics._valuation_script import (
    CV_TEMPLATE_ID,
    expected_cv_script,
)
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.econometrics.valuation_contracts import ContingentValuationSpec
from envresearch.econometrics.valuation_results import ContingentValuationResult
from envresearch.models.artifact import ArtifactRef


class ContingentValuationRecipe:
    method_id = "contingent-valuation"
    expected_outputs = frozenset(
        {
            "coefficients.csv",
            "covariance.csv",
            "wtp.csv",
            "bid_support.csv",
            "bid_yes_shares.csv",
            "probabilities.csv",
            "sensitivity.csv",
            "package_configuration.csv",
            "cv_plot.svg",
        }
    )

    def __init__(self, workspace: Path) -> None:
        if not workspace.is_absolute():
            raise ValueError("recipe workspace must be absolute")
        self.workspace = workspace.resolve()

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None:
        if not isinstance(spec, ContingentValuationSpec):
            raise CausalOutputInvalid("analysis spec selects a different method")
        if snapshot.row_count < 4 or any(
            name not in snapshot.columns for name in spec.required_columns()
        ):
            raise CausalOutputInvalid("snapshot does not satisfy the CV design")

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript:
        self.validate(spec, snapshot)
        assert isinstance(spec, ContingentValuationSpec)
        data, digest = expected_cv_script(spec)
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = publish_owned_file(
            self.workspace, "generated", f"cv-{digest}.R", data, 0o444
        )
        return GeneratedRScript(template_id=CV_TEMPLATE_ID, path=path, sha256=digest)

    def parse(
        self, output_root: Path, package_authorities: tuple[ArtifactRef, ...] = ()
    ) -> ContingentValuationResult:
        try:
            estimates = coefficients(output_root / "coefficients.csv")
            (
                sensitivity,
                max_change,
                sensitivity_intercept,
                sensitivity_bid,
                sensitivity_link,
            ) = stated_sensitivity(output_root / "sensitivity.csv")
            if sensitivity_link not in {"logit", "probit"}:
                raise CausalOutputInvalid("CV sensitivity link is not registered")
            probabilities = read_rows(
                output_root / "probabilities.csv",
                ("minimum", "maximum", "extreme_share", "max_extreme_share"),
            )
            if len(probabilities) != 1:
                raise CausalOutputInvalid(
                    "CV probability evidence must contain one row"
                )
            row = probabilities[0]
            welfare_rows = welfare(output_root / "wtp.csv")
            result = ContingentValuationResult(
                method_id="contingent-valuation",
                coefficients=estimates,
                covariance=covariance(
                    output_root / "covariance.csv",
                    tuple(item.term for item in estimates),
                ),
                welfare=welfare_rows,
                support=support(output_root / "bid_support.csv"),
                bid_yes_shares=bid_yes_shares(output_root / "bid_yes_shares.csv"),
                sensitivities=sensitivity,
                max_sensitivity_change=max_change,
                configuration=configuration(
                    output_root / "package_configuration.csv", package_authorities
                ),
                figure_sha256=figure_digest(output_root / "cv_plot.svg"),
                bid_term=welfare_rows[0].denominator_term,
                intercept_term="(Intercept)",
                probability_min=float(row["minimum"]),
                probability_max=float(row["maximum"]),
                extreme_probability_share=float(row["extreme_share"]),
                max_extreme_probability_share=float(row["max_extreme_share"]),
                sensitivity_bid_coefficient=sensitivity_bid,
                sensitivity_intercept_coefficient=sensitivity_intercept,
                sensitivity_link=cast(Literal["logit", "probit"], sensitivity_link),
            )
        except (OSError, ValueError, ValidationError) as error:
            text = str(error)
            code = "OUTPUT_INVALID"
            if "bid coefficient" in text:
                code = "CV_BID_SLOPE_INVALID"
            elif "extreme probability" in text:
                code = "CV_SEPARATION_DETECTED"
            elif "welfare" in text or "intercept" in text:
                code = "CV_WTP_UNIDENTIFIED"
            raise CausalOutputInvalid(
                text or "CV output is invalid", code=code
            ) from error
        return result

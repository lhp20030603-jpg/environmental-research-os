"""Repository-owned long-format conditional-logit DCE recipe."""

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
    coefficients,
    configuration,
    covariance,
    welfare,
)
from envresearch.econometrics._valuation_script import (
    DCE_TEMPLATE_ID,
    expected_dce_script,
)
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.econometrics.valuation_contracts import DiscreteChoiceSpec
from envresearch.econometrics.valuation_results import (
    DiscreteChoiceResult,
    ValuationSupport,
)
from envresearch.models.artifact import ArtifactRef


class DiscreteChoiceRecipe:
    method_id = "dce-clogit"
    expected_outputs = frozenset(
        {
            "coefficients.csv",
            "covariance.csv",
            "wtp.csv",
            "choice_support.csv",
            "sensitivity.csv",
            "package_configuration.csv",
            "dce_plot.svg",
        }
    )

    def __init__(self, workspace: Path) -> None:
        if not workspace.is_absolute():
            raise ValueError("recipe workspace must be absolute")
        self.workspace = workspace.resolve()

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None:
        if not isinstance(spec, DiscreteChoiceSpec):
            raise CausalOutputInvalid("analysis spec selects a different method")
        if snapshot.row_count < 4 or any(
            name not in snapshot.columns for name in spec.required_columns()
        ):
            raise CausalOutputInvalid("snapshot does not satisfy the DCE design")

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript:
        self.validate(spec, snapshot)
        assert isinstance(spec, DiscreteChoiceSpec)
        data, digest = expected_dce_script(spec)
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = publish_owned_file(
            self.workspace, "generated", f"dce-{digest}.R", data, 0o444
        )
        return GeneratedRScript(template_id=DCE_TEMPLATE_ID, path=path, sha256=digest)

    def parse(
        self, output_root: Path, package_authorities: tuple[ArtifactRef, ...] = ()
    ) -> DiscreteChoiceResult:
        try:
            estimates = coefficients(output_root / "coefficients.csv")
            (
                sensitivity,
                max_change,
                sensitivity_attribute,
                sensitivity_cost,
                sensitivity_form,
            ) = stated_sensitivity(output_root / "sensitivity.csv")
            if sensitivity_form != "conditional-logit":
                raise CausalOutputInvalid("DCE sensitivity form is not registered")
            welfare_rows = welfare(output_root / "wtp.csv")
            cost_term = welfare_rows[0].denominator_term
            attributes = tuple(
                item.numerator_term for item in welfare_rows if item.numerator_term
            )
            support_rows = read_rows(
                output_root / "choice_support.csv",
                (
                    "observations",
                    "primary_units",
                    "groups",
                    "zero_or_no_count",
                    "min_abs_cost_coefficient",
                ),
            )
            if len(support_rows) != 1:
                raise CausalOutputInvalid("DCE support must contain one row")
            support_row = support_rows[0]
            result = DiscreteChoiceResult(
                method_id="dce-clogit",
                coefficients=estimates,
                covariance=covariance(
                    output_root / "covariance.csv",
                    tuple(item.term for item in estimates),
                ),
                welfare=welfare_rows,
                support=ValuationSupport(
                    observations=int(support_row["observations"]),
                    primary_units=int(support_row["primary_units"]),
                    groups=int(support_row["groups"]),
                    zero_or_no_count=int(support_row["zero_or_no_count"]),
                ),
                sensitivities=sensitivity,
                max_sensitivity_change=max_change,
                configuration=configuration(
                    output_root / "package_configuration.csv", package_authorities
                ),
                figure_sha256=figure_digest(output_root / "dce_plot.svg"),
                cost_term=cost_term,
                attribute_terms=attributes,
                min_abs_cost_coefficient=float(support_row["min_abs_cost_coefficient"]),
                sensitivity_cost_coefficient=sensitivity_cost,
                sensitivity_attribute_coefficient=sensitivity_attribute,
                sensitivity_form=cast(Literal["conditional-logit"], sensitivity_form),
            )
        except (OSError, ValueError, ValidationError) as error:
            text = str(error)
            code = "OUTPUT_INVALID"
            if "cost coefficient" in text:
                code = "DCE_COST_SLOPE_INVALID"
            elif "attribute coefficient" in text or "welfare" in text:
                code = "DCE_TERM_UNIDENTIFIED"
            raise CausalOutputInvalid(
                text or "DCE output is invalid", code=code
            ) from error
        return result

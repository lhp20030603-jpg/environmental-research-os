"""Repository-owned Hedonic pricing recipe and strict parser."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import ValidationError

from envresearch.econometrics._causal_outputs import (
    CausalOutputInvalid,
    figure_digest,
    read_rows,
)
from envresearch.econometrics._r_owned_files import publish_owned_file
from envresearch.econometrics._valuation_outputs import (
    coefficients,
    configuration,
    covariance,
    sensitivities,
    support,
    welfare,
)
from envresearch.econometrics._valuation_script import (
    HEDONIC_TEMPLATE_ID,
    expected_hedonic_script,
)
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.econometrics.valuation_contracts import HedonicForm, HedonicSpec
from envresearch.econometrics.valuation_results import HedonicResult
from envresearch.models.artifact import ArtifactRef


class HedonicRecipe:
    method_id = "hedonic-pricing"
    expected_outputs = frozenset(
        {
            "coefficients.csv",
            "covariance.csv",
            "implicit_price.csv",
            "support.csv",
            "collinearity.csv",
            "sensitivity.csv",
            "package_configuration.csv",
            "hedonic_plot.svg",
        }
    )

    def __init__(self, workspace: Path) -> None:
        if not workspace.is_absolute():
            raise ValueError("recipe workspace must be absolute")
        self.workspace = workspace.resolve()

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None:
        if not isinstance(spec, HedonicSpec):
            raise CausalOutputInvalid("analysis spec selects a different method")
        if snapshot.row_count < 4 or any(
            name not in snapshot.columns for name in spec.required_columns()
        ):
            raise CausalOutputInvalid("snapshot does not satisfy the Hedonic design")

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript:
        self.validate(spec, snapshot)
        assert isinstance(spec, HedonicSpec)
        data, digest = expected_hedonic_script(spec)
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = publish_owned_file(
            self.workspace, "generated", f"hedonic-{digest}.R", data, 0o444
        )
        return GeneratedRScript(
            template_id=HEDONIC_TEMPLATE_ID, path=path, sha256=digest
        )

    def parse(
        self, output_root: Path, package_authorities: tuple[ArtifactRef, ...] = ()
    ) -> HedonicResult:
        try:
            estimates = coefficients(output_root / "coefficients.csv")
            sensitivity, max_change, sensitivity_beta, sensitivity_form = sensitivities(
                output_root / "sensitivity.csv"
            )
            rows = read_rows(
                output_root / "collinearity.csv",
                (
                    "condition_number",
                    "max_condition_number",
                    "max_vif",
                    "reference_price",
                    "reference_environment",
                ),
            )
            if len(rows) != 1:
                raise CausalOutputInvalid("Hedonic diagnostics must contain one row")
            diagnostic = rows[0]
            config = configuration(
                output_root / "package_configuration.csv", package_authorities
            )
            welfare_rows = welfare(output_root / "implicit_price.csv")
            result = HedonicResult(
                method_id="hedonic-pricing",
                coefficients=estimates,
                covariance=covariance(
                    output_root / "covariance.csv",
                    tuple(item.term for item in estimates),
                ),
                welfare=welfare_rows,
                support=support(output_root / "support.csv"),
                sensitivities=sensitivity,
                max_sensitivity_change=max_change,
                configuration=config,
                figure_sha256=figure_digest(output_root / "hedonic_plot.svg"),
                environmental_term=welfare_rows[0].numerator_term or "",
                price_term=welfare_rows[0].denominator_term,
                reference_price=float(diagnostic["reference_price"]),
                reference_environment=float(diagnostic["reference_environment"]),
                condition_number=float(diagnostic["condition_number"]),
                max_condition_number=float(diagnostic["max_condition_number"]),
                max_vif=float(diagnostic["max_vif"]),
                sensitivity_coefficient=sensitivity_beta,
                sensitivity_form=cast(HedonicForm, sensitivity_form),
            )
        except (OSError, ValueError, ValidationError) as error:
            text = str(error)
            code = "OUTPUT_INVALID"
            if "collinearity exceeds" in text:
                code = "HEDONIC_COLLINEARITY_EXCEEDED"
            elif "sensitivity exceeds" in text:
                code = "HEDONIC_SENSITIVITY_EXCEEDED"
            elif "environmental coefficient" in text:
                code = "HEDONIC_TERM_UNIDENTIFIED"
            raise CausalOutputInvalid(
                text or "Hedonic output is invalid", code=code
            ) from error
        return result

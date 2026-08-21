"""Repository-owned Travel-cost recipe and strict parser."""

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
    TRAVEL_COST_TEMPLATE_ID,
    expected_travel_cost_script,
)
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.econometrics.valuation_contracts import CountFamily, TravelCostSpec
from envresearch.econometrics.valuation_results import TravelCostResult
from envresearch.models.artifact import ArtifactRef


class TravelCostRecipe:
    method_id = "travel-cost"
    expected_outputs = frozenset(
        {
            "coefficients.csv",
            "covariance.csv",
            "consumer_surplus.csv",
            "support.csv",
            "dispersion.csv",
            "fit_evidence.csv",
            "sensitivity.csv",
            "package_configuration.csv",
            "travel_cost_plot.svg",
        }
    )

    def __init__(self, workspace: Path) -> None:
        if not workspace.is_absolute():
            raise ValueError("recipe workspace must be absolute")
        self.workspace = workspace.resolve()

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None:
        if not isinstance(spec, TravelCostSpec):
            raise CausalOutputInvalid("analysis spec selects a different method")
        if snapshot.row_count < 4 or any(
            name not in snapshot.columns for name in spec.required_columns()
        ):
            raise CausalOutputInvalid(
                "snapshot does not satisfy the Travel-cost design"
            )

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript:
        self.validate(spec, snapshot)
        assert isinstance(spec, TravelCostSpec)
        data, digest = expected_travel_cost_script(spec)
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = publish_owned_file(
            self.workspace, "generated", f"travel-cost-{digest}.R", data, 0o444
        )
        return GeneratedRScript(
            template_id=TRAVEL_COST_TEMPLATE_ID, path=path, sha256=digest
        )

    def parse(
        self, output_root: Path, package_authorities: tuple[ArtifactRef, ...] = ()
    ) -> TravelCostResult:
        try:
            estimates = coefficients(output_root / "coefficients.csv")
            sensitivity, max_change, sensitivity_beta, sensitivity_form = sensitivities(
                output_root / "sensitivity.csv"
            )
            rows = read_rows(
                output_root / "dispersion.csv",
                (
                    "dispersion",
                    "max_dispersion",
                    "log_likelihood",
                    "deviance",
                    "residual_df",
                    "theta",
                ),
            )
            if len(rows) != 1:
                raise CausalOutputInvalid("Travel-cost dispersion must contain one row")
            result = TravelCostResult(
                method_id="travel-cost",
                coefficients=estimates,
                covariance=covariance(
                    output_root / "covariance.csv",
                    tuple(item.term for item in estimates),
                ),
                welfare=welfare(output_root / "consumer_surplus.csv"),
                support=support(output_root / "support.csv"),
                sensitivities=sensitivity,
                max_sensitivity_change=max_change,
                configuration=configuration(
                    output_root / "package_configuration.csv", package_authorities
                ),
                figure_sha256=figure_digest(output_root / "travel_cost_plot.svg"),
                cost_term=estimates[0].term,
                dispersion=float(rows[0]["dispersion"]),
                max_dispersion=float(rows[0]["max_dispersion"]),
                log_likelihood=float(rows[0]["log_likelihood"]),
                deviance=float(rows[0]["deviance"]),
                residual_df=int(rows[0]["residual_df"]),
                theta=None if rows[0]["theta"] == "" else float(rows[0]["theta"]),
                sensitivity_cost_coefficient=sensitivity_beta,
                sensitivity_family=cast(CountFamily, sensitivity_form),
            )
        except (OSError, ValueError, ValidationError) as error:
            text = str(error)
            code = "OUTPUT_INVALID"
            if "cost coefficient" in text:
                code = "TRAVEL_COST_SLOPE_INVALID"
            elif "dispersion exceeds" in text:
                code = "TRAVEL_COST_DISPERSION_EXCEEDED"
            raise CausalOutputInvalid(
                text or "Travel-cost output is invalid", code=code
            ) from error
        return result

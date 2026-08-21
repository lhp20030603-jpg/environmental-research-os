"""Repository-owned Panel FE recipe and strict output parser."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from envresearch.econometrics._causal_outputs import (
    CausalOutputInvalid,
    figure_digest,
    parse_coefficients,
    parse_configuration,
    parse_support,
    read_rows,
)
from envresearch.econometrics._causal_script import (
    PANEL_TEMPLATE_ID,
    expected_panel_script,
)
from envresearch.econometrics._r_owned_files import publish_owned_file
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.causal_contracts import PanelFeSpec
from envresearch.econometrics.causal_models import FitDiagnostics, PanelFeResult
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.models.artifact import ArtifactRef


class PanelFeRecipe:
    """Render and parse one declared fixed-effects panel model."""

    method_id = "panel-fe"
    expected_outputs = frozenset(
        {
            "coefficients.csv",
            "support.csv",
            "fit.csv",
            "package_configuration.csv",
            "coefficient_plot.svg",
        }
    )

    def __init__(self, workspace: Path) -> None:
        if not workspace.is_absolute():
            raise ValueError("recipe workspace must be absolute")
        self.workspace = workspace.resolve()

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None:
        if not isinstance(spec, PanelFeSpec):
            raise CausalOutputInvalid("analysis spec selects a different method")
        missing = tuple(
            name for name in spec.required_columns() if name not in snapshot.columns
        )
        if missing or snapshot.row_count < 4:
            raise CausalOutputInvalid("snapshot does not satisfy the Panel FE design")

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript:
        self.validate(spec, snapshot)
        assert isinstance(spec, PanelFeSpec)
        data, digest = expected_panel_script(spec)
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = publish_owned_file(
            self.workspace, "generated", f"panel-fe-{digest}.R", data, 0o444
        )
        return GeneratedRScript(template_id=PANEL_TEMPLATE_ID, path=path, sha256=digest)

    def parse(
        self,
        output_root: Path,
        package_authorities: tuple[ArtifactRef, ...] = (),
    ) -> PanelFeResult:
        try:
            fit_rows = read_rows(
                output_root / "fit.csv", ("r_squared", "within_r_squared")
            )
            if len(fit_rows) != 1:
                raise CausalOutputInvalid("fit output must contain one row")
            result = PanelFeResult(
                method_id="panel-fe",
                coefficients=parse_coefficients(output_root / "coefficients.csv"),
                support=parse_support(output_root / "support.csv", panel=True),
                fit=FitDiagnostics(
                    r_squared=float(fit_rows[0]["r_squared"]),
                    within_r_squared=float(fit_rows[0]["within_r_squared"]),
                ),
                configuration=parse_configuration(
                    output_root / "package_configuration.csv"
                ),
                figure_sha256=figure_digest(output_root / "coefficient_plot.svg"),
            )
        except (OSError, ValueError, ValidationError) as error:
            raise CausalOutputInvalid(
                str(error) or "Panel FE output is invalid"
            ) from error
        return result

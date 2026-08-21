"""Repository-owned IV/2SLS recipe and strict diagnostic parser."""

from __future__ import annotations

from math import isfinite
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
from envresearch.econometrics._causal_script import IV_TEMPLATE_ID, expected_iv_script
from envresearch.econometrics._r_owned_files import publish_owned_file
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.causal_contracts import Iv2slsSpec
from envresearch.econometrics.causal_models import (
    FirstStageDiagnostic,
    Iv2slsResult,
    OveridentificationDiagnostic,
)
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.models.artifact import ArtifactRef


class Iv2slsRecipe:
    """Render and parse one declared IV/2SLS design."""

    method_id = "iv-2sls"
    expected_outputs = frozenset(
        {
            "structural.csv",
            "first_stage.csv",
            "overidentification.csv",
            "reduced_form.csv",
            "support.csv",
            "package_configuration.csv",
            "coefficient_plot.svg",
        }
    )

    def __init__(self, workspace: Path) -> None:
        if not workspace.is_absolute():
            raise ValueError("recipe workspace must be absolute")
        self.workspace = workspace.resolve()

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None:
        if not isinstance(spec, Iv2slsSpec):
            raise CausalOutputInvalid("analysis spec selects a different method")
        missing = tuple(
            name for name in spec.required_columns() if name not in snapshot.columns
        )
        if missing or snapshot.row_count < 4:
            raise CausalOutputInvalid("snapshot does not satisfy the IV/2SLS design")

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript:
        self.validate(spec, snapshot)
        assert isinstance(spec, Iv2slsSpec)
        data, digest = expected_iv_script(spec)
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = publish_owned_file(
            self.workspace, "generated", f"iv-2sls-{digest}.R", data, 0o444
        )
        return GeneratedRScript(template_id=IV_TEMPLATE_ID, path=path, sha256=digest)

    def parse(
        self,
        output_root: Path,
        package_authorities: tuple[ArtifactRef, ...] = (),
    ) -> Iv2slsResult:
        try:
            rows = read_rows(
                output_root / "first_stage.csv",
                ("endogenous", "instruments", "f_statistic", "threshold"),
            )
            first_stage: list[FirstStageDiagnostic] = []
            for row in rows:
                f_statistic = float(row["f_statistic"])
                threshold = float(row["threshold"])
                if (
                    not isfinite(f_statistic)
                    or not isfinite(threshold)
                    or f_statistic < 0.0
                    or threshold <= 0.0
                ):
                    raise CausalOutputInvalid(
                        "first-stage evidence has an invalid numeric domain"
                    )
                if f_statistic < threshold:
                    raise CausalOutputInvalid(
                        "weak instrument evidence blocks a green IV result",
                        code="IV_WEAK_INSTRUMENT",
                    )
                first_stage.append(
                    FirstStageDiagnostic(
                        endogenous=row["endogenous"],
                        instruments=tuple(
                            item for item in row["instruments"].split(";") if item
                        ),
                        f_statistic=f_statistic,
                        threshold=threshold,
                    )
                )
            overidentification = _parse_overidentification(
                output_root / "overidentification.csv"
            )
            result = Iv2slsResult(
                method_id="iv-2sls",
                structural=parse_coefficients(output_root / "structural.csv"),
                first_stage=tuple(first_stage),
                reduced_form=parse_coefficients(output_root / "reduced_form.csv"),
                overidentification=overidentification,
                support=parse_support(output_root / "support.csv"),
                configuration=parse_configuration(
                    output_root / "package_configuration.csv"
                ),
                figure_sha256=figure_digest(output_root / "coefficient_plot.svg"),
            )
        except CausalOutputInvalid:
            raise
        except (OSError, ValueError, ValidationError) as error:
            raise CausalOutputInvalid(
                str(error) or "IV/2SLS output is invalid"
            ) from error
        return result


def _parse_overidentification(path: Path) -> OveridentificationDiagnostic | None:
    rows = read_rows(
        path,
        ("test", "statistic", "p_value", "degrees_of_freedom"),
        allow_empty=True,
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise CausalOutputInvalid(
            "overidentification output must contain at most one row"
        )
    row = rows[0]
    return OveridentificationDiagnostic(
        test=row["test"],  # type: ignore[arg-type]
        statistic=float(row["statistic"]),
        p_value=float(row["p_value"]),
        degrees_of_freedom=int(row["degrees_of_freedom"]),
    )

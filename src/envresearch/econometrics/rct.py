"""Repository-owned individual-randomized RCT recipe."""

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
from envresearch.econometrics._wave1_evidence_models import RctBalance
from envresearch.econometrics._wave1_script import RCT_TEMPLATE_ID, expected_rct_script
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.causal_models import RegressionCoefficient
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.econometrics.wave1_contracts import RctSpec
from envresearch.econometrics.wave1_results import (
    RctResult,
    RctSupport,
    WavePackageConfiguration,
)
from envresearch.models.artifact import ArtifactRef


class RctRecipe:
    """Render and parse one bounded individual-level ITT analysis."""

    method_id = "rct-itt"
    expected_outputs = frozenset(
        {
            "unadjusted.csv",
            "ancova.csv",
            "allocation.csv",
            "attrition.csv",
            "balance.csv",
            "package_configuration.csv",
            "coefficient_plot.svg",
        }
    )

    def __init__(self, workspace: Path) -> None:
        if not workspace.is_absolute():
            raise ValueError("recipe workspace must be absolute")
        self.workspace = workspace.resolve()

    def validate(self, spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> None:
        if not isinstance(spec, RctSpec):
            raise CausalOutputInvalid("analysis spec selects a different method")
        missing = tuple(
            name for name in spec.required_columns() if name not in snapshot.columns
        )
        if missing or snapshot.row_count < 4:
            raise CausalOutputInvalid("snapshot does not satisfy the RCT design")

    def render(
        self, spec: AnalysisSpec, snapshot: LocalDataSnapshot
    ) -> GeneratedRScript:
        self.validate(spec, snapshot)
        assert isinstance(spec, RctSpec)
        data, digest = expected_rct_script(spec)
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = publish_owned_file(
            self.workspace, "generated", f"rct-{digest}.R", data, 0o444
        )
        return GeneratedRScript(template_id=RCT_TEMPLATE_ID, path=path, sha256=digest)

    def parse(
        self,
        output_root: Path,
        package_authorities: tuple[ArtifactRef, ...] = (),
    ) -> RctResult:
        try:
            allocation = _allocation(output_root / "allocation.csv")
            attrition = _one_row(
                output_root / "attrition.csv",
                ("attrition_rate", "max_attrition_rate"),
            )
            balance = read_rows(output_root / "balance.csv", ("term", "smd"))
            configuration = _one_row(
                output_root / "package_configuration.csv",
                (
                    "method_id",
                    "r_version",
                    "confidence_level",
                    "balance_smd_threshold",
                ),
            )
            if configuration["method_id"] != "rct-itt":
                raise CausalOutputInvalid("RCT configuration selects another method")
            result = RctResult(
                method_id="rct-itt",
                unadjusted=_one_coefficient(output_root / "unadjusted.csv"),
                ancova=_one_coefficient(output_root / "ancova.csv"),
                support=allocation,
                balance=tuple(
                    RctBalance(term=row["term"], smd=float(row["smd"]))
                    for row in balance
                ),
                attrition_rate=float(attrition["attrition_rate"]),
                max_attrition_rate=float(attrition["max_attrition_rate"]),
                max_abs_balance_smd=max(abs(float(row["smd"])) for row in balance),
                balance_smd_threshold=float(configuration["balance_smd_threshold"]),
                configuration=WavePackageConfiguration(
                    method_id="rct-itt",
                    r_version=configuration["r_version"],
                    confidence_level=float(configuration["confidence_level"]),
                    package_authorities=package_authorities,
                ),
                figure_sha256=figure_digest(output_root / "coefficient_plot.svg"),
            )
        except (OSError, ValueError, ValidationError) as error:
            raise CausalOutputInvalid(str(error) or "RCT output is invalid") from error
        return result


def _one_coefficient(path: Path) -> RegressionCoefficient:
    values = parse_coefficients(path)
    if len(values) != 1:
        raise CausalOutputInvalid("RCT coefficient output must contain one row")
    return values[0]


def _one_row(path: Path, header: tuple[str, ...]) -> dict[str, str]:
    rows = read_rows(path, header)
    if len(rows) != 1:
        raise CausalOutputInvalid("RCT output must contain one row")
    return rows[0]


def _allocation(path: Path) -> RctSupport:
    rows = read_rows(path, ("arm", "assigned", "outcomes_observed", "outcomes_missing"))
    if tuple(row["arm"] for row in rows) != ("control", "treated"):
        raise CausalOutputInvalid("RCT allocation must contain exact arms")
    assigned = tuple(int(row["assigned"]) for row in rows)
    observed = tuple(int(row["outcomes_observed"]) for row in rows)
    missing = tuple(int(row["outcomes_missing"]) for row in rows)
    return RctSupport(
        total=sum(assigned),
        assigned_control=assigned[0],
        assigned_treated=assigned[1],
        control_outcomes_observed=observed[0],
        treated_outcomes_observed=observed[1],
        outcomes_observed=sum(observed),
        outcomes_missing=sum(missing),
    )

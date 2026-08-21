"""Deterministic four-method fixtures for valuation verifier tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from econometrics_valuation_fixtures import (
    cv_spec,
    dce_spec,
    hedonic_spec,
    travel_spec,
    write_hedonic_outputs,
    write_travel_outputs,
)

from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.installed_package_authority import (
    InstalledPackageAuthority,
)
from envresearch.econometrics.r_evidence import RExecutionEvidence, RRuntimeIdentity
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.service import BackendResult

FIXTURES = Path(__file__).parents[1] / "fixtures" / "econometrics"


class ValuationVerifierBackend:
    """Emit deterministic estimator evidence without invoking external R."""

    def __init__(
        self,
        method_id: str,
        authorities: tuple[InstalledPackageAuthority, ...] | None = None,
    ) -> None:
        self.method_id = method_id
        self.package_authorities = (
            authorities_for(method_id) if authorities is None else authorities
        )

    def execute(
        self,
        spec: AnalysisSpec,
        snapshot: LocalDataSnapshot,
        snapshot_bytes: bytes,
        workspace: Path,
    ) -> BackendResult:
        assert spec.method_id == self.method_id
        assert hashlib.sha256(snapshot_bytes).hexdigest() == snapshot.sha256
        recipe = recipe_for(spec.method_id, workspace=workspace)
        script = recipe.render(spec, snapshot)
        output = workspace / "output"
        _write_outputs(spec.method_id, output)
        authorities = self.package_authorities
        result = recipe.parse(output, tuple(item.ref() for item in authorities))
        runtime = workspace / "runtime-fixture"
        runtime.write_bytes(b"R fixture 4.4.3")
        runtime.chmod(0o555)
        identity = RRuntimeIdentity(
            source_executable=runtime,
            executable=runtime,
            sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
            version="Rscript (R) version 4.4.3 (2025-02-28)",
            device=runtime.stat().st_dev,
            inode=runtime.stat().st_ino,
            size_bytes=runtime.stat().st_size,
        )
        execution = RExecutionEvidence(
            runtime=identity,
            script=script,
            argv=(str(runtime), "--vanilla", str(script.path)),
            environment=(),
            return_code=0,
            stdout_sha256=hashlib.sha256(b"").hexdigest(),
            stderr_sha256=hashlib.sha256(b"").hexdigest(),
            redacted_stdout="",
            redacted_stderr="",
            workspace_bytes=4096,
            package_authorities=authorities,
        )
        return BackendResult(
            script=script,
            execution=execution,
            result=result,
            output_root=output,
        )


def spec_for(method_id: str) -> AnalysisSpec:
    """Return the registered fixture spec for one valuation method."""
    factories = {
        "hedonic-pricing": (hedonic_spec, "hedonic_pricing.csv"),
        "travel-cost": (travel_spec, "travel_cost.csv"),
        "contingent-valuation": (cv_spec, "contingent_valuation.csv"),
        "dce-clogit": (dce_spec, "dce_clogit.csv"),
    }
    factory, filename = factories[method_id]
    return factory(FIXTURES / filename)


def authorities_for(method_id: str) -> tuple[InstalledPackageAuthority, ...]:
    """Return the minimum method-selected package authority fixture."""
    package = {
        "hedonic-pricing": "fixest",
        "travel-cost": "MASS",
        "dce-clogit": "survival",
    }.get(method_id)
    return () if package is None else (package_authority(package),)


def package_authority(package: str) -> InstalledPackageAuthority:
    """Build one syntactically valid immutable package authority record."""
    digest = hashlib.sha256(package.encode()).hexdigest()
    return InstalledPackageAuthority(
        schema_version="econometrics.frozen-r-package.v1",
        authority_kind="frozen-local-tree",
        package=package,
        version="1.0.0",
        observed_license="GPL-3",
        description_sha256=digest,
        installed_tree_sha256=digest,
        package_relative_path=Path("authorities/frozen-r-pack/library") / package,
        dependencies=(),
        r_version="4.4.3",
        pack_hash=digest,
        observed_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


def _write_outputs(method_id: str, root: Path) -> None:
    if method_id == "hedonic-pricing":
        write_hedonic_outputs(root)
    elif method_id == "travel-cost":
        write_travel_outputs(root)
    elif method_id == "contingent-valuation":
        _write_cv_outputs(root)
    else:
        _write_dce_outputs(root)


def _write_cv_outputs(root: Path) -> None:
    terms = ("(Intercept)", "bid", "income")
    _write(
        root,
        "coefficients.csv",
        "term,estimate,std_error,confidence_low,confidence_high\n"
        "(Intercept),2,0.2,1.6080072030919892,2.3919927969080108\n"
        "bid,-0.1,0.01,-0.11959963984540055,-0.08040036015459946\n"
        "income,0.01,0.002,0.006080072030919892,0.013919927969080109\n",
    )
    _write(
        root,
        "covariance.csv",
        "row_term,column_term,value\n"
        + "".join(
            f"{left},{right},{0.04 if left == right == '(Intercept)' else 0.0001 if left == right == 'bid' else 0.000004 if left == right else 0}\n"
            for left in terms
            for right in terms
        ),
    )
    _write(
        root,
        "wtp.csv",
        "name,estimate,std_error,confidence_low,confidence_high,currency,price_base,time_basis,population_basis,transformation,numerator_term,denominator_term\n"
        "median-wtp,20,2.8284271247461903,14.45638470260129,25.54361529739871,USD,2025,per-year,sample-household,negative-intercept-over-bid,(Intercept),bid\n",
    )
    _write(
        root,
        "bid_support.csv",
        "observations,primary_units,groups,zero_or_no_count\n40,40,4,20\n",
    )
    _write(
        root,
        "bid_yes_shares.csv",
        "bid,yes_count,observations,yes_share\n"
        "10,7,10,0.7\n20,6,10,0.6\n30,4,10,0.4\n40,3,10,0.3\n",
    )
    _write(
        root,
        "probabilities.csv",
        "minimum,maximum,extreme_share,max_extreme_share\n"
        "0.10475248044630801,0.7585048861053055,0,0.2\n",
    )
    _write(
        root,
        "sensitivity.csv",
        "label,estimate,baseline_estimate,absolute_change,max_sensitivity_change,numerator_coefficient,denominator_coefficient,model_form\n"
        "exclude-covariates,20.1,20,0.1,100,2.01,-0.1,logit\n",
    )
    _write(
        root,
        "package_configuration.csv",
        "method_id,r_version,confidence_level,cluster_column,fixed_effects,functional_form,family,link\n"
        "contingent-valuation,R version 4.4.3,0.95,,,,,logit\n",
    )
    _write(root, "cv_plot.svg", _figure())


def _write_dce_outputs(root: Path) -> None:
    terms = ("cost", "air_quality", "green_space")
    _write(
        root,
        "coefficients.csv",
        "term,estimate,std_error,confidence_low,confidence_high\n"
        "cost,-0.5,0.1,-0.6959963984540054,-0.3040036015459946\n"
        "air_quality,1,0.2,0.6080072030919892,1.3919927969080108\n"
        "green_space,0.5,0.1,0.3040036015459946,0.6959963984540054\n",
    )
    _write(
        root,
        "covariance.csv",
        "row_term,column_term,value\n"
        + "".join(
            f"{left},{right},{0.01 if left == right == 'cost' else 0.04 if left == right == 'air_quality' else 0.01 if left == right else 0}\n"
            for left in terms
            for right in terms
        ),
    )
    _write(
        root,
        "wtp.csv",
        "name,estimate,std_error,confidence_low,confidence_high,currency,price_base,time_basis,population_basis,transformation,numerator_term,denominator_term\n"
        "air_quality-wtp,2,0.565685424949238,0.8912769405202581,3.108723059479742,USD,2025,per-year,sample-household,negative-attribute-over-cost,air_quality,cost\n"
        "green_space-wtp,1,0.282842712474619,0.44563847026012904,1.554361529739871,USD,2025,per-year,sample-household,negative-attribute-over-cost,green_space,cost\n",
    )
    _write(
        root,
        "choice_support.csv",
        "observations,primary_units,groups,zero_or_no_count,min_abs_cost_coefficient\n"
        "60,10,20,40,0.0001\n",
    )
    _write(
        root,
        "sensitivity.csv",
        "label,estimate,baseline_estimate,absolute_change,max_sensitivity_change,numerator_coefficient,denominator_coefficient,model_form\n"
        "include-alternative-specific-constants,2.1,2,0.1,100,1.05,-0.5,conditional-logit\n",
    )
    _write(
        root,
        "package_configuration.csv",
        "method_id,r_version,confidence_level,cluster_column,fixed_effects,functional_form,family,link\n"
        "dce-clogit,R version 4.4.3,0.95,respondent_id,,,,\n",
    )
    _write(root, "dce_plot.svg", _figure())


def _figure() -> str:
    return '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"><text>0</text></g></svg>\n'


def _write(root: Path, name: str, data: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(data, encoding="utf-8")

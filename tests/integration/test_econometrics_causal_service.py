"""Method-neutral service coverage for the shared causal-policy bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER, AnalysisSpec
from envresearch.econometrics.r_evidence import RExecutionEvidence, RRuntimeIdentity
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.service import (
    BackendResult,
    EvidenceTampered,
    LocalAnalysisService,
)
from envresearch.storage.research_artifacts import ResearchArtifactStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "econometrics"


def _spec(method_id: str) -> AnalysisSpec:
    method_payloads: dict[str, dict[str, object]] = {
        "panel-fe": {
            "schema_version": "econometrics.panel-fe.v1",
            "columns": {
                "unit": "unit",
                "time": "year",
                "outcome": "emissions",
                "regressors": ["policy"],
                "fixed_effects": ["unit", "year"],
            },
        },
        "iv-2sls": {
            "schema_version": "econometrics.iv-2sls.v1",
            "columns": {
                "outcome": "emissions",
                "endogenous": ["price"],
                "instruments": ["wind"],
                "controls": ["income"],
                "fixed_effects": ["unit", "year"],
            },
            "weak_instrument_f_threshold": 10.0,
        },
        "rdd-local-linear": {
            "schema_version": "econometrics.rdd-local-linear.v1",
            "columns": {
                "outcome": "emissions",
                "running": "score",
                "covariates": ["income"],
            },
            "design": {
                "cutoff": 0.0,
                "bandwidth": 4.0,
                "donut_radius": 0.25,
                "kernel": "triangular",
            },
        },
    }
    fixture = {
        "panel-fe": "panel_fe.csv",
        "iv-2sls": "iv_2sls.csv",
        "rdd-local-linear": "rdd_local_linear.csv",
    }[method_id]
    return ANALYSIS_SPEC_ADAPTER.validate_json(
        json.dumps(
            {
                **method_payloads[method_id],
                "method_id": method_id,
                "data_path": str(FIXTURES / fixture),
                "inference": {
                    "confidence_level": 0.95,
                    "cluster_column": None
                    if method_id == "rdd-local-linear"
                    else "unit",
                },
                "budget": {
                    "inactivity_seconds": 60,
                    "max_output_bytes": 1_000_000,
                    "max_workspace_bytes": 10_000_000,
                },
            }
        )
    )


class _CausalFixtureBackend:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, spec, snapshot, snapshot_bytes, workspace) -> BackendResult:
        del snapshot_bytes
        self.calls += 1
        recipe = recipe_for(spec.method_id, workspace=workspace)
        script = recipe.render(spec, snapshot)
        output = workspace / "output"
        output.mkdir(parents=True)
        _write_outputs(output, spec.method_id)
        runtime = workspace / "runtime" / "Rscript"
        runtime.parent.mkdir()
        runtime.write_bytes(b"fixture-runtime")
        runtime.chmod(0o555)
        identity = RRuntimeIdentity(
            source_executable=runtime,
            executable=runtime,
            sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
            version="R fixture 4.4.3",
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
            stdout_sha256=hashlib.sha256(b"ok").hexdigest(),
            stderr_sha256=hashlib.sha256(b"").hexdigest(),
            redacted_stdout="ok",
            redacted_stderr="",
            workspace_bytes=1024,
        )
        return BackendResult(
            script=script,
            execution=execution,
            result=recipe.parse(output),
            output_root=output,
        )


def _write_outputs(output: Path, method_id: str) -> None:
    if method_id == "panel-fe":
        (output / "coefficients.csv").write_text(
            "term,estimate,std_error,conf_low,conf_high\npolicy,-1,0.2,-1.4,-0.6\n",
            encoding="utf-8",
        )
        (output / "support.csv").write_text(
            "observations,clusters,units,time_periods\n12,4,4,3\n",
            encoding="utf-8",
        )
        (output / "fit.csv").write_text(
            "r_squared,within_r_squared\n0.8,0.4\n", encoding="utf-8"
        )
        (output / "package_configuration.csv").write_text(
            "method_id,r_version,fixest_version,confidence_level,cluster_column,"
            "fixed_effects,estimator_label,cutoff,bandwidth,kernel,donut_radius\n"
            "panel-fe,R version 4.4.3,0.14.0,0.95,unit,unit;year,"
            "fixest::feols-panel-fe,,,,\n",
            encoding="utf-8",
        )
        (output / "coefficient_plot.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><text>policy</text></svg>\n',
            encoding="utf-8",
        )
        return
    if method_id == "iv-2sls":
        coefficient = "term,estimate,std_error,conf_low,conf_high\n"
        (output / "structural.csv").write_text(
            coefficient + "price,-1,0.2,-1.4,-0.6\n", encoding="utf-8"
        )
        (output / "reduced_form.csv").write_text(
            coefficient + "wind,-0.8,0.2,-1.2,-0.4\n", encoding="utf-8"
        )
        (output / "first_stage.csv").write_text(
            "endogenous,instruments,f_statistic,threshold\nprice,wind,20,10\n",
            encoding="utf-8",
        )
        (output / "overidentification.csv").write_text(
            "test,statistic,p_value,degrees_of_freedom\n", encoding="utf-8"
        )
        (output / "support.csv").write_text(
            "observations,clusters\n32,8\n", encoding="utf-8"
        )
        _write_configuration(output, method_id, "unit")
        _write_svg(output, "coefficient_plot.svg")
        return
    coefficient = "term,estimate,std_error,conf_low,conf_high\n"
    (output / "main.csv").write_text(
        coefficient + "cutoff,-1,0.2,-1.4,-0.6\n", encoding="utf-8"
    )
    (output / "donut.csv").write_text(
        coefficient + "cutoff,-0.9,0.2,-1.3,-0.5\n", encoding="utf-8"
    )
    (output / "covariate_continuity.csv").write_text(
        coefficient + "income,0.1,0.1,-0.1,0.3\n", encoding="utf-8"
    )
    (output / "bandwidth_sensitivity.csv").write_text(
        "multiplier,term,estimate,std_error,conf_low,conf_high\n"
        "0.5,cutoff,-1.1,0.3,-1.7,-0.5\n"
        "1.0,cutoff,-1,0.2,-1.4,-0.6\n"
        "1.5,cutoff,-0.9,0.2,-1.3,-0.5\n",
        encoding="utf-8",
    )
    (output / "support.csv").write_text(
        "observations,left_observations,right_observations,left_unique_running,"
        "right_unique_running,donut_left_observations,donut_right_observations\n"
        "19,9,10,9,10,9,9\n",
        encoding="utf-8",
    )
    _write_configuration(output, method_id, "")
    _write_svg(output, "rdd_plot.svg")


def _write_configuration(output: Path, method_id: str, cluster: str) -> None:
    fixed_effects = "" if method_id == "rdd-local-linear" else "unit;year"
    estimator = (
        "sharp-local-linear"
        if method_id == "rdd-local-linear"
        else "fixest::feols-2sls"
    )
    design = ",0,4,triangular,0.25" if method_id == "rdd-local-linear" else ",,,,"
    (output / "package_configuration.csv").write_text(
        "method_id,r_version,fixest_version,confidence_level,cluster_column,"
        "fixed_effects,estimator_label,cutoff,bandwidth,kernel,donut_radius\n"
        f"{method_id},R version 4.4.3,0.14.0,0.95,{cluster},{fixed_effects},"
        f"{estimator}{design}\n",
        encoding="utf-8",
    )


def _write_svg(output: Path, name: str) -> None:
    (output / name).write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>result</text></svg>\n',
        encoding="utf-8",
    )


@pytest.mark.parametrize("method_id", ["panel-fe", "iv-2sls", "rdd-local-linear"])
def test_service_persists_and_reverifies_each_registered_method(
    method_id: str, tmp_path: Path
) -> None:
    backend = _CausalFixtureBackend()
    service = LocalAnalysisService(ResearchArtifactStore(tmp_path / "store"), backend)

    reference = service.run(_spec(method_id))
    report = service.status(reference)

    assert report.status == "passed"
    assert report.spec.method_id == method_id
    assert report.result is not None
    assert report.result.method_id == method_id
    assert backend.calls == 1


def test_completed_iv_recovery_does_not_reexecute(tmp_path: Path) -> None:
    backend = _CausalFixtureBackend()
    service = LocalAnalysisService(ResearchArtifactStore(tmp_path / "store"), backend)
    spec = _spec("iv-2sls")

    first = service.run(spec)
    second = LocalAnalysisService(service.store, backend).run(spec)

    assert second == first
    assert backend.calls == 1


def test_verifier_rejects_cross_method_output_manifest(tmp_path: Path) -> None:
    backend = _CausalFixtureBackend()
    service = LocalAnalysisService(ResearchArtifactStore(tmp_path / "store"), backend)
    report = service.status(service.run(_spec("panel-fe")))
    forged = report.model_copy(
        update={
            "outputs": (
                report.outputs[0].model_copy(update={"name": "first_stage.csv"}),
                *report.outputs[1:],
            )
        }
    )

    assert "OUTPUT_SET_INVALID" in service.verifier.verify(forged)


@pytest.mark.parametrize("method_id", ["panel-fe", "iv-2sls", "rdd-local-linear"])
def test_cli_validates_each_registered_causal_spec(
    method_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _CausalFixtureBackend()
    service = LocalAnalysisService(ResearchArtifactStore(tmp_path / "store"), backend)
    spec = _spec(method_id)
    path = tmp_path / f"{method_id}.yaml"
    path.write_text(
        yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "envresearch.econometrics.cli._service_for", lambda *args, **kwargs: service
    )

    result = CliRunner().invoke(app, ["econometrics", "validate", str(path), "--json"])
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["method_id"] == method_id
    assert tuple(payload["required_columns"]) == spec.required_columns()
    assert backend.calls == 0


@pytest.mark.parametrize("method_id", ["panel-fe", "iv-2sls", "rdd-local-linear"])
def test_status_rejects_tampered_causal_output(method_id: str, tmp_path: Path) -> None:
    backend = _CausalFixtureBackend()
    service = LocalAnalysisService(ResearchArtifactStore(tmp_path / "store"), backend)
    reference = service.run(_spec(method_id))
    report = service.status(reference)
    target = service.store.root / report.outputs[0].relative_path
    target.chmod(0o644)
    target.write_bytes(b"tampered")

    with pytest.raises(EvidenceTampered, match="OUTPUT_TAMPERED"):
        service.status(reference)


@pytest.mark.parametrize(
    ("method_id", "forged_row"),
    [
        ("panel-fe", "12,4,5,3\n"),
        ("iv-2sls", "32,7\n"),
        ("rdd-local-linear", "19,8,11,8,11,8,10\n"),
    ],
)
def test_verifier_reconstructs_support_from_authenticated_snapshot(
    method_id: str, forged_row: str, tmp_path: Path
) -> None:
    backend = _CausalFixtureBackend()
    service = LocalAnalysisService(ResearchArtifactStore(tmp_path / "store"), backend)
    report = service.status(service.run(_spec(method_id)))
    support_item = next(item for item in report.outputs if item.name == "support.csv")
    target = service.store.root / support_item.relative_path
    header = target.read_text(encoding="utf-8").splitlines(keepends=True)[0]
    forged_bytes = (header + forged_row).encode("utf-8")
    target.chmod(0o644)
    target.write_bytes(forged_bytes)
    assert report.output_root is not None
    forged_result = recipe_for(method_id, workspace=tmp_path / "verify").parse(
        service.store.root / report.output_root
    )
    forged_outputs = tuple(
        item.model_copy(
            update={
                "sha256": hashlib.sha256(forged_bytes).hexdigest(),
                "size_bytes": len(forged_bytes),
            }
        )
        if item.name == "support.csv"
        else item
        for item in report.outputs
    )
    forged = report.model_copy(
        update={"outputs": forged_outputs, "result": forged_result}
    )

    assert "CONFIGURATION_MISMATCH" in service.verifier.verify(forged)

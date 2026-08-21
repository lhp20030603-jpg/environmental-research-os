"""Registry and opt-in local-R integration for RCT and measurement."""

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER
from envresearch.econometrics.data_snapshot import snapshot_csv
from envresearch.econometrics.measurement import MeasurementRecipe
from envresearch.econometrics.method_authority import (
    MethodAuthority,
    MethodAuthorityProposal,
    PackageRequirement,
)
from envresearch.econometrics.r_evidence import RExecutionEvidence, RRuntimeIdentity
from envresearch.econometrics.rct import RctRecipe
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.service import BackendResult, LocalAnalysisService
from envresearch.models.artifact import ArtifactRef
from envresearch.storage.research_artifacts import ResearchArtifactStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "econometrics"


def test_rct_and_measurement_are_available_through_shared_registry(
    tmp_path: Path,
) -> None:
    assert isinstance(recipe_for("rct-itt", workspace=tmp_path / "rct"), RctRecipe)
    assert isinstance(
        recipe_for("environmental-measurement", workspace=tmp_path / "measurement"),
        MeasurementRecipe,
    )


def _payload(method_id: str, path: Path) -> dict[str, object]:
    shared = {
        "data_path": str(path),
        "budget": {
            "inactivity_seconds": 60,
            "max_output_bytes": 1_000_000,
            "max_workspace_bytes": 10_000_000,
        },
    }
    if method_id == "rct-itt":
        return {
            **shared,
            "schema_version": "econometrics.rct-itt.v1",
            "method_id": method_id,
            "columns": {
                "unit": "unit",
                "assignment": "assigned",
                "outcome": "outcome",
                "baseline_covariates": ["baseline"],
            },
            "inference": {"confidence_level": 0.95, "cluster_column": None},
            "max_attrition_rate": 0.25,
            "balance_smd_threshold": 0.25,
        }
    return {
        **shared,
        "schema_version": "econometrics.environmental-measurement.v1",
        "method_id": method_id,
        "columns": {
            "monitor": "monitor",
            "timestamp": "date",
            "value": "pm25",
            "unit": "unit",
            "detection_flag": "flag",
        },
        "declared_unit": "ug/m3",
        "max_missing_rate": 0.25,
        "valid_min": 0.0,
        "valid_max": 500.0,
        "exceedance_threshold": 35.0,
    }


@pytest.mark.parametrize(
    ("method_id", "fixture"),
    (
        ("rct-itt", "rct_itt.csv"),
        ("environmental-measurement", "environmental_measurement.csv"),
    ),
)
def test_real_local_wave1_recipe_when_explicitly_enabled(
    method_id: str, fixture: str, tmp_path: Path
) -> None:
    if os.getenv("ENVRESEARCH_RUN_OPTIONAL_R_WAVE1") != "1":
        pytest.skip("set ENVRESEARCH_RUN_OPTIONAL_R_WAVE1=1 for local R smoke")
    executable = shutil.which("Rscript")
    if executable is None:
        pytest.skip("Rscript is not installed")
    source = FIXTURES / fixture
    spec = ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(_payload(method_id, source)))
    snapshot = snapshot_csv(spec, ResearchArtifactStore(tmp_path / "store"))
    workspace = tmp_path / method_id
    (workspace / "input").mkdir(parents=True)
    shutil.copyfile(source, workspace / "input" / "data.csv")
    recipe = recipe_for(method_id, workspace=workspace)
    script = recipe.render(spec, snapshot)
    completed = subprocess.run(
        (executable, "--vanilla", str(script.path)),
        cwd=workspace,
        check=False,
        capture_output=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    authorities = (
        (
            ArtifactRef(
                artifact_id="r-package-authority-fixest-0.14.0",
                artifact_version=1,
                content_hash="a" * 64,
            ),
        )
        if method_id == "rct-itt"
        else ()
    )
    result = recipe.parse(workspace / "output", authorities)
    assert result.method_id == method_id


def _authority() -> MethodAuthority:
    return MethodAuthority(
        proposal=MethodAuthorityProposal(
            package="fixest",
            version="0.14.0",
            source_url="https://cran.r-project.org/src/contrib/fixest_0.14.0.tar.gz",
            source_sha256="c" * 64,
            license="GPL-3.0-only",
            description_license="GPL-3",
            dependencies=(PackageRequirement(package="R", version="4.4.3", base=True),),
        ),
        installed_tree_sha256="d" * 64,
        source_relative_path=Path("authorities/sources/" + "c" * 64 + "/fixest.tar.gz"),
        package_relative_path=Path("authorities/r-library/fixest"),
        description_sha256="e" * 64,
        observed_license="GPL-3",
        observed_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


class _FixtureBackend:
    def execute(self, spec, snapshot, snapshot_bytes, workspace) -> BackendResult:
        del snapshot_bytes
        recipe = recipe_for(spec.method_id, workspace=workspace)
        script = recipe.render(spec, snapshot)
        output = workspace / "output"
        output.mkdir(parents=True)
        _write_fixture_outputs(output, spec.method_id)
        runtime = workspace / "runtime/Rscript"
        runtime.parent.mkdir()
        runtime.write_bytes(b"fixture-runtime")
        runtime.chmod(0o555)
        identity = RRuntimeIdentity(
            source_executable=runtime,
            executable=runtime,
            sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
            version="R version 4.4.3",
            device=runtime.stat().st_dev,
            inode=runtime.stat().st_ino,
            size_bytes=runtime.stat().st_size,
        )
        authorities = (_authority(),) if spec.method_id == "rct-itt" else ()
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
            package_authorities=authorities,
        )
        refs = tuple(item.ref() for item in authorities)
        return BackendResult(
            script=script,
            execution=execution,
            result=recipe.parse(output, refs),
            output_root=output,
        )


def _write_fixture_outputs(root: Path, method_id: str) -> None:
    if method_id == "rct-itt":
        coefficient = "term,estimate,std_error,conf_low,conf_high\nassigned,2,0.5,1,3\n"
        (root / "unadjusted.csv").write_text(coefficient, encoding="utf-8")
        (root / "ancova.csv").write_text(coefficient, encoding="utf-8")
        (root / "allocation.csv").write_text(
            "arm,assigned,outcomes_observed,outcomes_missing\ncontrol,6,6,0\ntreated,6,5,1\n",
            encoding="utf-8",
        )
        (root / "attrition.csv").write_text(
            "attrition_rate,max_attrition_rate\n0.0833333333333333,0.25\n",
            encoding="utf-8",
        )
        (root / "balance.csv").write_text("term,smd\nbaseline,0\n", encoding="utf-8")
        (root / "package_configuration.csv").write_text(
            "method_id,r_version,confidence_level,balance_smd_threshold\n"
            "rct-itt,R version 4.4.3,0.95,0.25\n",
            encoding="utf-8",
        )
        _svg(root / "coefficient_plot.svg")
        return
    (root / "summary.csv").write_text(
        "mean,minimum,q25,median,q75,maximum,exceedances\n22,12,15,18,27,36,1\n",
        encoding="utf-8",
    )
    (root / "completeness.csv").write_text(
        "total,valid,missing,monitors,missing_rate,max_missing_rate\n4,3,1,2,0.25,0.25\n",
        encoding="utf-8",
    )
    (root / "exceedances.csv").write_text("threshold,count\n35,1\n", encoding="utf-8")
    (root / "temporal.csv").write_text(
        "date,mean\n2020-01-01,24\n2020-01-02,18\n", encoding="utf-8"
    )
    (root / "monitor_coverage.csv").write_text(
        "monitor,total,valid,missing\nm1,2,2,0\nm2,2,1,1\n", encoding="utf-8"
    )
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,declared_unit\n"
        "environmental-measurement,R version 4.4.3,ug/m3\n",
        encoding="utf-8",
    )
    _svg(root / "measurement_plot.svg")


def _svg(path: Path) -> None:
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>\n', encoding="utf-8")


@pytest.mark.parametrize(
    ("method_id", "fixture"),
    (
        ("rct-itt", "rct_itt.csv"),
        ("environmental-measurement", "environmental_measurement.csv"),
    ),
)
def test_service_persists_and_independently_reverifies_wave1(
    method_id: str, fixture: str, tmp_path: Path
) -> None:
    source = FIXTURES / fixture
    spec = ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(_payload(method_id, source)))
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "service-store"), _FixtureBackend()
    )
    report = service.status(service.run(spec))
    assert report.status == "passed"
    assert report.result is not None
    assert report.result.method_id == method_id

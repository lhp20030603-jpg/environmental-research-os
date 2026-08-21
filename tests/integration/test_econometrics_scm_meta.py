"""Shared registry, service, and verifier integration for SCM and meta-analysis."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER
from envresearch.econometrics.meta_analysis import MetaAnalysisRecipe
from envresearch.econometrics.method_authority import (
    MethodAuthority,
    MethodAuthorityProposal,
    PackageRequirement,
)
from envresearch.econometrics.r_evidence import RExecutionEvidence, RRuntimeIdentity
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.service import BackendResult, LocalAnalysisService
from envresearch.econometrics.synthetic_control import SyntheticControlRecipe
from envresearch.storage.research_artifacts import ResearchArtifactStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "econometrics"


def _payload(method: str, path: Path) -> dict[str, object]:
    shared = {
        "data_path": str(path),
        "budget": {
            "inactivity_seconds": 60,
            "max_output_bytes": 1_000_000,
            "max_workspace_bytes": 10_000_000,
        },
    }
    if method == "synthetic-control":
        return {
            **shared,
            "schema_version": "econometrics.synthetic-control.v1",
            "method_id": method,
            "columns": {
                "unit": "unit",
                "time": "year",
                "outcome": "emissions",
                "predictors": [],
            },
            "treated_unit": "treated",
            "intervention_time": 2010,
            "max_pre_rmspe": 2,
            "max_leave_one_out_change": 2,
        }
    return {
        **shared,
        "schema_version": "econometrics.meta-analysis.v1",
        "method_id": method,
        "columns": {"study": "study", "effect": "effect", "variance": "var"},
        "confidence_level": 0.95,
        "max_leave_one_out_change": 0.5,
        "model": "fixed-and-dl-random",
    }


def _authority(package: str, version: str) -> MethodAuthority:
    digest = "c" * 64
    return MethodAuthority(
        proposal=MethodAuthorityProposal(
            package=package,
            version=version,
            source_url=f"https://cran.r-project.org/src/contrib/{package}_{version}.tar.gz",
            source_sha256=digest,
            license="GPL-2.0-only",
            description_license="GPL-2",
            dependencies=(PackageRequirement(package="R", version="4.4.3", base=True),),
        ),
        installed_tree_sha256="d" * 64,
        source_relative_path=Path(f"authorities/sources/{digest}/{package}.tar.gz"),
        package_relative_path=Path(f"authorities/r-library/{package}"),
        description_sha256="e" * 64,
        observed_license="GPL-2",
        observed_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


class _Backend:
    def execute(self, spec, snapshot, snapshot_bytes, workspace) -> BackendResult:
        del snapshot_bytes
        recipe = recipe_for(spec.method_id, workspace=workspace)
        script = recipe.render(spec, snapshot)
        output = workspace / "output"
        output.mkdir(parents=True)
        _write_scm(output) if spec.method_id == "synthetic-control" else _write_meta(
            output
        )
        package, version = (
            ("synthdid", "0.0.9")
            if spec.method_id == "synthetic-control"
            else ("metafor", "4.8.0")
        )
        authority = _authority(package, version)
        runtime = workspace / "runtime/Rscript"
        runtime.parent.mkdir()
        runtime.write_bytes(b"fixture-runtime")
        runtime.chmod(0o555)
        stat = runtime.stat()
        identity = RRuntimeIdentity(
            source_executable=runtime,
            executable=runtime,
            sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
            version="Rscript (R) version 4.4.3 (2025-02-28)",
            device=stat.st_dev,
            inode=stat.st_ino,
            size_bytes=stat.st_size,
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
            package_authorities=(authority,),
        )
        return BackendResult(
            script=script,
            execution=execution,
            result=recipe.parse(output, (authority.ref(),)),
            output_root=output,
        )


def _write_scm(root: Path) -> None:
    (root / "effect.csv").write_text(
        "term,estimate,std_error,conf_low,conf_high\nATT,-1.4,0.2,-1.8,-1.0\n",
        encoding="utf-8",
    )
    (root / "weights.csv").write_text(
        "donor,weight\ndonor-a,0.6\ndonor-b,0.4\n", encoding="utf-8"
    )
    (root / "gaps.csv").write_text(
        "time,treated,synthetic,gap,period\n2008,11,10.8,0.2,pre\n2009,10,9.8,0.2,pre\n2010,8,9.4,-1.4,post\n2011,7,8.4,-1.4,post\n",
        encoding="utf-8",
    )
    (root / "rmspe.csv").write_text(
        "pre_periods,post_periods,pre_rmspe,post_rmspe,max_pre_rmspe,post_pre_ratio\n2,2,0.2,1.4,2,7\n",
        encoding="utf-8",
    )
    (root / "placebo.csv").write_text(
        "unit,effect\ndonor-a,0.1\ndonor-b,-0.1\n", encoding="utf-8"
    )
    (root / "leave_one_out.csv").write_text(
        "omitted,effect,absolute_change\ndonor-a,-1.2,0.2\ndonor-b,-1.6,0.2\n",
        encoding="utf-8",
    )
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,package_version,intervention_time,leave_one_out_threshold\nsynthetic-control,R version 4.4.3,0.0.9,2010,2\n",
        encoding="utf-8",
    )
    _svg(root / "synthetic_control.svg")


def _write_meta(root: Path) -> None:
    coefficient = "term,estimate,std_error,conf_low,conf_high\n%s,0.14,0.05,0.04,0.24\n"
    (root / "fixed.csv").write_text(coefficient % "fixed", encoding="utf-8")
    (root / "random.csv").write_text(coefficient % "random", encoding="utf-8")
    (root / "heterogeneity.csv").write_text(
        "studies,q,i_squared,tau_squared,inverse_variance_support,prediction_low,prediction_high\n3,1,0,0,183.3333333333,-0.1,0.38\n",
        encoding="utf-8",
    )
    studies = "s1,0.1,0.1\ns2,0.2,0.1414213562\ns3,0.15,0.1732050808\n"
    (root / "study_weights.csv").write_text(
        "study,effect,std_error,weight\ns1,0.1,0.1,0.5454545455\ns2,0.2,0.1414213562,0.2727272727\ns3,0.15,0.1732050808,0.1818181818\n",
        encoding="utf-8",
    )
    (root / "leave_one_out.csv").write_text(
        "omitted,effect,absolute_change\ns1,0.16,0.02\ns2,0.12,0.02\ns3,0.14,0\n",
        encoding="utf-8",
    )
    (root / "funnel.csv").write_text(
        "study,effect,std_error\n" + studies, encoding="utf-8"
    )
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,package_version,confidence_level,model,leave_one_out_threshold\nmeta-analysis,R version 4.4.3,4.8.0,0.95,fixed-and-dl-random,0.5\n",
        encoding="utf-8",
    )
    _svg(root / "forest_funnel.svg")


def _svg(path: Path) -> None:
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"/></svg>\n',
        encoding="utf-8",
    )


def test_shared_registry_contains_scm_and_meta(tmp_path: Path) -> None:
    assert isinstance(
        recipe_for("synthetic-control", workspace=tmp_path / "scm"),
        SyntheticControlRecipe,
    )
    assert isinstance(
        recipe_for("meta-analysis", workspace=tmp_path / "meta"), MetaAnalysisRecipe
    )


@pytest.mark.parametrize(
    ("method", "fixture"),
    (
        ("synthetic-control", "synthetic_control.csv"),
        ("meta-analysis", "meta_analysis.csv"),
    ),
)
def test_service_persists_and_reverifies_synthesis(
    method: str, fixture: str, tmp_path: Path
) -> None:
    source = FIXTURES / fixture
    spec = ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(_payload(method, source)))
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "store"), _Backend()
    )
    report = service.status(service.run(spec))
    assert report.status == "passed" and report.result is not None, report
    assert report.result.method_id == method

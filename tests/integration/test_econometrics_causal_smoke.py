"""Explicitly optional real-local-R smoke for the causal-policy bundle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER, AnalysisSpec
from envresearch.econometrics.local_backend import TrustedLocalRBackend
from envresearch.econometrics.r_subprocess import BoundedRSubprocessExecutor
from envresearch.econometrics.service import LocalAnalysisService
from envresearch.storage.research_artifacts import ResearchArtifactStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "econometrics"


def _spec(method_id: str) -> AnalysisSpec:
    selected: dict[str, dict[str, object]] = {
        "panel-fe": {
            "schema_version": "econometrics.panel-fe.v1",
            "data_path": str(FIXTURES / "panel_fe.csv"),
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
            "data_path": str(FIXTURES / "iv_2sls.csv"),
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
            "data_path": str(FIXTURES / "rdd_local_linear.csv"),
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
    }[method_id]
    return ANALYSIS_SPEC_ADAPTER.validate_json(
        json.dumps(
            {
                **selected,
                "method_id": method_id,
                "inference": {
                    "confidence_level": 0.95,
                    "cluster_column": None,
                },
                "budget": {
                    "inactivity_seconds": 60,
                    "max_output_bytes": 1_048_576,
                    "max_workspace_bytes": 16_777_216,
                },
            }
        )
    )


@pytest.mark.parametrize("method_id", ["panel-fe", "iv-2sls", "rdd-local-linear"])
def test_real_local_causal_recipe_when_explicitly_enabled(
    method_id: str, tmp_path: Path
) -> None:
    """Run only checked local fixtures with an already-installed local fixest."""
    if os.getenv("ENVRESEARCH_RUN_OPTIONAL_R_CAUSAL") != "1":
        pytest.skip("set ENVRESEARCH_RUN_OPTIONAL_R_CAUSAL=1 for local R smoke")
    discovered = shutil.which("Rscript")
    if discovered is None:
        pytest.skip("Rscript is not installed; installation is never automatic")
    source = Path(discovered).resolve(strict=True)
    reviewed = tmp_path / "reviewed" / "Rscript"
    reviewed.parent.mkdir()
    reviewed.write_bytes(source.read_bytes())
    reviewed.chmod(0o555)
    backend = TrustedLocalRBackend(
        executable=reviewed,
        expected_sha256=hashlib.sha256(reviewed.read_bytes()).hexdigest(),
        executor=BoundedRSubprocessExecutor(),
    )
    service = LocalAnalysisService(ResearchArtifactStore(tmp_path / "store"), backend)

    report = service.status(service.run(_spec(method_id)))

    assert report.status == "passed", (report.code, report.verification_findings)
    assert report.result is not None
    assert report.result.method_id == method_id
    assert report.result.configuration.fixest_version == "0.14.0"
    if method_id == "rdd-local-linear":
        assert report.output_root is not None
        root = ET.parse(
            service.store.root / report.output_root / "rdd_plot.svg"
        ).getroot()
        assert len(root.findall(".//*[@class='x-tick']")) == 5
        assert len(root.findall(".//*[@class='y-tick']")) == 5
        assert root.findall(".//*[@class='binned-mean']")
        assert len(root.findall(".//*[@class='fitted-line left']")) == 1
        assert len(root.findall(".//*[@class='fitted-line right']")) == 1
    else:
        assert report.output_root is not None
        root = ET.parse(
            service.store.root / report.output_root / "coefficient_plot.svg"
        ).getroot()
        assert len(root.findall(".//*[@class='x-tick']")) == 5
        assert root.findall(".//*[@class='confidence-interval']")
        assert root.findall(".//*[@class='estimate']")

"""Explicitly optional real local-R DiD estimator smoke test."""

from __future__ import annotations

import csv
import hashlib
import math
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from envresearch.econometrics.contracts import LocalAnalysisSpec, ResourceBudget
from envresearch.econometrics.data_snapshot import snapshot_csv
from envresearch.econometrics.did import DidEventStudyRecipe
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.econometrics.r_runtime import TrustedLocalRRunner
from envresearch.econometrics.r_subprocess import BoundedRSubprocessExecutor
from envresearch.storage.research_artifacts import ResearchArtifactStore


def test_real_local_did_recipe_when_explicitly_enabled(tmp_path: Path) -> None:
    """Run the checked fixture only with local R and already-present packages."""
    if os.getenv("ENVRESEARCH_RUN_OPTIONAL_R_DID") != "1":
        pytest.skip("set ENVRESEARCH_RUN_OPTIONAL_R_DID=1 for the local package smoke")
    discovered = shutil.which("Rscript")
    if discovered is None:
        pytest.skip("Rscript is not installed; installation is never automatic")
    workspace = tmp_path / "workspace"
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "econometrics"
        / "staggered_panel_smoke.csv"
    ).resolve()
    spec = _spec(fixture)
    snapshot = snapshot_csv(spec, ResearchArtifactStore(tmp_path / "store"))
    recipe = DidEventStudyRecipe(workspace)
    analysis = recipe.render(spec, snapshot)
    analysis_text = analysis.path.read_text(encoding="utf-8")
    assert "idname = did_id_column" in analysis_text
    assert "idname = unit_column" not in analysis_text
    assert "grDevices::svg" not in analysis_text
    assert 'xmlns="http://www.w3.org/2000/svg"' in analysis_text
    input_path = workspace / "input" / "data.csv"
    input_path.parent.mkdir()
    input_path.write_bytes(fixture.read_bytes())
    probe_path = workspace / "generated" / "package-probe.R"
    probe_path.write_text(
        'cat(requireNamespace("fixest", quietly=TRUE), '
        'requireNamespace("did", quietly=TRUE), "\\n")\n',
        encoding="utf-8",
    )
    probe_path.chmod(0o444)
    probe = GeneratedRScript(
        template_id="local-did-package-probe-v1",
        path=probe_path,
        sha256=hashlib.sha256(probe_path.read_bytes()).hexdigest(),
    )
    executable = tmp_path / "reviewed" / "Rscript"
    executable.parent.mkdir()
    executable.write_bytes(Path(discovered).resolve(strict=True).read_bytes())
    executable.chmod(0o555)
    runner = TrustedLocalRRunner.review(
        executable=executable,
        expected_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        workspace=workspace,
        executor=BoundedRSubprocessExecutor(),
        budget=spec.budget,
        approved_scripts={
            probe.template_id: probe.sha256,
            analysis.template_id: analysis.sha256,
        },
    )
    if runner.run(probe).redacted_stdout.strip() != "TRUE TRUE":
        pytest.skip("fixest and did are not already installed locally")

    runner.run(analysis)
    result = recipe.parse(workspace / "output")

    assert result.baseline.estimates
    assert result.group_time_att.estimates
    assert result.dynamic.estimates
    assert result.packages.did_version == "2.3.0"
    svg_root = ET.parse(workspace / "output" / "event_study.svg").getroot()
    assert svg_root.tag == "{http://www.w3.org/2000/svg}svg"
    assert len(svg_root.findall(".//*[@class='x-tick']")) >= 2
    assert len(svg_root.findall(".//*[@class='y-tick']")) >= 2


def test_real_local_did_preserves_small_cohort_warning_when_explicitly_enabled(
    tmp_path: Path,
) -> None:
    """A truthful small treated cohort warns but still returns estimates."""
    if os.getenv("ENVRESEARCH_RUN_OPTIONAL_R_DID") != "1":
        pytest.skip("set ENVRESEARCH_RUN_OPTIONAL_R_DID=1 for the local package smoke")
    discovered = shutil.which("Rscript")
    if discovered is None:
        pytest.skip("Rscript is not installed; installation is never automatic")

    fixture = tmp_path / "small-cohort-panel.csv"
    with fixture.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=("unit", "year", "outcome", "cohort", "x1")
        )
        writer.writeheader()
        for unit in range(1, 31):
            cohort = 2013 if unit <= 4 else 2014 if unit <= 6 else ""
            for year in range(2008, 2019):
                treated = cohort != "" and year >= cohort
                event_time = year - cohort if treated else -1
                writer.writerow(
                    {
                        "unit": unit,
                        "year": year,
                        "outcome": unit / 10
                        + (year - 2008) / 50
                        + 0.02 * math.sin(unit * year)
                        + 0.01 * math.cos(unit + year)
                        - (event_time + 1) / 20,
                        "cohort": cohort,
                        "x1": unit / 30 + (year - 2008) / 100,
                    }
                )

    workspace = tmp_path / "workspace"
    spec = _spec(fixture)
    snapshot = snapshot_csv(spec, ResearchArtifactStore(tmp_path / "store"))
    recipe = DidEventStudyRecipe(workspace)
    analysis = recipe.render(spec, snapshot)
    input_path = workspace / "input" / "data.csv"
    input_path.parent.mkdir()
    input_path.write_bytes(fixture.read_bytes())

    executable = tmp_path / "reviewed" / "Rscript"
    executable.parent.mkdir()
    executable.write_bytes(Path(discovered).resolve(strict=True).read_bytes())
    executable.chmod(0o555)
    runner = TrustedLocalRRunner.review(
        executable=executable,
        expected_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        workspace=workspace,
        executor=BoundedRSubprocessExecutor(),
        budget=spec.budget,
        approved_scripts={analysis.template_id: analysis.sha256},
    )

    run = runner.run(analysis)
    result = recipe.parse(workspace / "output")

    assert "small groups" in run.redacted_stderr
    assert result.group_time_att.estimates
    assert result.dynamic.estimates
    assert [(item.cohort, item.units) for item in result.cohort_timing] == [
        (2013, 4),
        (2014, 2),
    ]


def _spec(data_path: Path) -> LocalAnalysisSpec:
    """Build the exact checked-fixture authority."""
    return LocalAnalysisSpec.model_validate(
        {
            "schema_version": "econometrics.local-analysis.v1",
            "method_id": "did-event-study",
            "data_path": data_path,
            "columns": {
                "unit": "unit",
                "time": "year",
                "outcome": "outcome",
                "treatment_cohort": "cohort",
                "covariates": ("x1",),
            },
            "comparison_group": "never-treated",
            "reference_period": -1,
            "inference": {
                "confidence_level": 0.95,
                "cluster_column": "unit",
                "interval_mode": "pointwise",
                "bootstrap_seed": 20260811,
            },
            "budget": ResourceBudget(
                inactivity_seconds=60,
                max_output_bytes=1_048_576,
                max_workspace_bytes=16_777_216,
            ),
        }
    )

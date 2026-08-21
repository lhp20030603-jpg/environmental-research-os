"""Deterministic no-R fixtures for the local econometrics service."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from envresearch.econometrics.contracts import LocalAnalysisSpec
from envresearch.econometrics.did import DidEventStudyRecipe
from envresearch.econometrics.r_evidence import (
    RExecutionEvidence,
    RRuntimeIdentity,
)
from envresearch.econometrics.service import (
    BackendResult,
    LocalAnalysisService,
    LocalExecutionError,
)
from envresearch.storage.research_artifacts import ResearchArtifactStore

PANEL = """unit,year,outcome,cohort,x1
A,2018,10,,1
A,2019,11,,2
B,2018,9,2020,1
B,2019,12,2020,2
"""


class FakeBackend:
    """Produce exact deterministic evidence without invoking local R."""

    def __init__(self) -> None:
        self.calls = 0
        self.failure_code: str | None = None

    def execute(self, spec, snapshot, snapshot_path, workspace) -> BackendResult:
        """Write the complete parser protocol or raise one typed failure."""
        del snapshot_path
        self.calls += 1
        if self.failure_code:
            raise LocalExecutionError(self.failure_code, "injected backend failure")
        recipe = DidEventStudyRecipe(workspace)
        script = recipe.render(spec, snapshot)
        output = workspace / "output"
        _outputs(output)
        result = recipe.parse(output)
        runtime_path = workspace / "runtime" / "Rscript-fixture"
        runtime_path.parent.mkdir(exist_ok=True)
        runtime_path.write_bytes(b"fixture-runtime")
        runtime_path.chmod(0o555)
        runtime_hash = hashlib.sha256(runtime_path.read_bytes()).hexdigest()
        execution = RExecutionEvidence(
            runtime=RRuntimeIdentity(
                source_executable=runtime_path,
                executable=runtime_path,
                sha256=runtime_hash,
                version="R fixture 4.4.3",
                device=runtime_path.stat().st_dev,
                inode=runtime_path.stat().st_ino,
                size_bytes=runtime_path.stat().st_size,
            ),
            script=script,
            argv=(str(runtime_path), "--vanilla", str(script.path)),
            environment=(),
            return_code=0,
            stdout_sha256=hashlib.sha256(b"ok").hexdigest(),
            stderr_sha256=hashlib.sha256(b"").hexdigest(),
            redacted_stdout="ok",
            redacted_stderr="",
            workspace_bytes=1024,
        )
        return BackendResult(
            script=script, execution=execution, result=result, output_root=output
        )


@dataclass
class LocalServiceCase:
    """One complete service fixture and its expected source identity."""

    service: LocalAnalysisService
    spec: LocalAnalysisSpec
    backend: FakeBackend
    store: ResearchArtifactStore
    source_sha256: str


@pytest.fixture
def local_service(tmp_path: Path) -> LocalServiceCase:
    """Provide one isolated local analysis service with deterministic outputs."""
    source = tmp_path / "panel.csv"
    source.write_text(PANEL, encoding="utf-8")
    spec = LocalAnalysisSpec.model_validate(
        {
            "schema_version": "econometrics.local-analysis.v1",
            "method_id": "did-event-study",
            "data_path": source,
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
            "budget": {
                "inactivity_seconds": 30,
                "max_output_bytes": 100_000,
                "max_workspace_bytes": 1_000_000,
            },
        }
    )
    store = ResearchArtifactStore(tmp_path / "store")
    backend = FakeBackend()
    return LocalServiceCase(
        service=LocalAnalysisService(store, backend),
        spec=spec,
        backend=backend,
        store=store,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    """Write one deterministic CSV table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _outputs(root: Path) -> None:
    """Write one complete valid DiD parser protocol."""
    estimate = {
        "term": "event_time_0",
        "event_time": 0,
        "group": "",
        "time": "",
        "estimate": 1.0,
        "std_error": 0.1,
        "conf_low": 0.8,
        "conf_high": 1.2,
    }
    _write(root / "baseline.csv", [estimate])
    _write(
        root / "group_time_att.csv",
        [{**estimate, "term": "att_2020_2020", "group": 2020, "time": 2020}],
    )
    _write(root / "dynamic.csv", [estimate])
    _write(
        root / "support.csv",
        [
            {
                "observations": 4,
                "units": 2,
                "treated_units": 1,
                "comparison_units": 1,
                "cohorts": 1,
                "dropped_observations": 0,
                "duplicate_panel_keys": 0,
                "removal_rule": "complete-declared-columns",
            }
        ],
    )
    _write(
        root / "support_by_group_time.csv",
        [
            {
                "group": 2020,
                "time": 2020,
                "event_time": 0,
                "treated_observations": 1,
                "comparison_observations": 1,
                "treated_units": 1,
                "comparison_units": 1,
            }
        ],
    )
    _write(
        root / "cohort_timing.csv",
        [{"cohort": 2020, "units": 1, "first_period": 2018, "last_period": 2019}],
    )
    _write(
        root / "covariate_balance.csv",
        [
            {
                "covariate": "x1",
                "treated_mean": 1.5,
                "comparison_mean": 1.5,
                "standardized_difference": 0.0,
                "treated_n": 2,
                "comparison_n": 2,
            }
        ],
    )
    _write(
        root / "package_configuration.csv",
        [
            {
                "r_version": "4.4.3",
                "fixest_version": "0.13.2",
                "did_version": "2.1.2",
                "bootstrap_seed": 20260811,
                "comparison_group": "never-treated",
                "reference_period": -1,
                "base_period": "varying",
                "anticipation": 0,
                "confidence_level": 0.95,
                "interval_mode": "pointwise",
                "baseline_interval_method": "pointwise-normal",
                "did_interval_method": "pointwise-normal",
                "cluster_column": "unit",
            }
        ],
    )
    (root / "event_study.svg").write_text("<svg></svg>\n", encoding="utf-8")

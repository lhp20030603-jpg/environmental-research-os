"""Optional smoke test for an already-installed reviewed local R."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from envresearch.econometrics.contracts import ResourceBudget
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.econometrics.r_runtime import RRuntimeInvalid, TrustedLocalRRunner
from envresearch.econometrics.r_subprocess import BoundedRSubprocessExecutor


def test_reviewed_local_r_runs_generated_offline_script(tmp_path: Path) -> None:
    """Run no-package generated R only when a locally reviewable R exists."""
    discovered = shutil.which("Rscript")
    if discovered is None:
        pytest.skip("Rscript is not installed; installation is never automatic")
    installed = Path(discovered).resolve(strict=True)
    executable = tmp_path / "reviewed" / "Rscript"
    executable.parent.mkdir()
    executable.write_bytes(installed.read_bytes())
    executable.chmod(0o555)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    workspace = tmp_path / "workspace"
    script_path = workspace / "generated" / "smoke.R"
    script_path.parent.mkdir(parents=True)
    script_path.write_text('cat("LOCAL_R_OK\\n")\n', encoding="utf-8")
    script_path.chmod(0o444)
    script = GeneratedRScript(
        template_id="local-r-smoke-v1",
        path=script_path,
        sha256=hashlib.sha256(script_path.read_bytes()).hexdigest(),
    )
    try:
        runner = TrustedLocalRRunner.review(
            executable=executable,
            expected_sha256=digest,
            workspace=workspace,
            executor=BoundedRSubprocessExecutor(),
            budget=ResourceBudget(
                inactivity_seconds=10,
                max_output_bytes=16_384,
                max_workspace_bytes=1_048_576,
            ),
            approved_scripts={script.template_id: script.sha256},
        )
    except RRuntimeInvalid as error:
        pytest.skip(f"installed R is not reviewable: {error}")

    evidence = runner.run(script)

    assert evidence.return_code == 0
    assert "LOCAL_R_OK" in evidence.redacted_stdout

"""Orchestration for one deterministic benchmark replay."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from envresearch.benchmarks.finalization import (
    FINALIZER_TASK_ID,
    BenchmarkComparisonFailure,
    canonical_report,
    make_finding,
)
from envresearch.benchmarks.preparation import (
    PreparationIssue,
    copy_raw_inputs,
    initialize_workspace,
    inspect_raw_root,
    validate_roots,
    verify_source,
)
from envresearch.benchmarks.registry import BenchmarkRegistry
from envresearch.benchmarks.report import (
    publish_pending_finalization,
    read_finalization,
    stage_finalization,
)
from envresearch.kernel.engine import RunEngine, TaskCommandError, TaskDefinition
from envresearch.models.benchmark import BenchmarkManifest
from envresearch.models.enums import FindingSeverity, WorkflowStatus
from envresearch.models.run import RunManifest, RunReport
from envresearch.runner.command import CommandRunner


def _invoke_comparison_finalizer(
    manifest_json: str, workspace_text: str, expected_root_text: str
) -> None:
    """Keep the durable callback identity limited to canonical string inputs."""
    from pathlib import Path as LocalPath

    from envresearch.benchmarks.finalization import run_comparison_finalizer

    run_comparison_finalizer(
        manifest_json,
        LocalPath(workspace_text),
        LocalPath(expected_root_text),
    )


class BenchmarkRunner:
    """Replay a validated benchmark in a fresh, derived workspace."""

    def __init__(self, command_runner: CommandRunner) -> None:
        self.command_runner = command_runner

    @classmethod
    def default(cls) -> BenchmarkRunner:
        """Build the v0.1 trusted-package runner with pinned Python only."""
        return cls(CommandRunner({"python": Path(sys.executable)}))

    def run_manifest(
        self, manifest_path: Path, case_root: Path, run_root: Path
    ) -> RunReport:
        """Load one manifest and delegate its replay to :meth:`run`."""
        resolved_manifest = manifest_path.resolve()
        manifest = BenchmarkRegistry._read_manifest(resolved_manifest)
        return self.run(
            manifest,
            case_root,
            run_root,
            _manifest_root=resolved_manifest.parent,
        )

    def run(
        self,
        manifest: BenchmarkManifest,
        case_root: Path,
        run_root: Path,
        *,
        _manifest_root: Path | None = None,
    ) -> RunReport:
        """Verify provenance, execute commands, compare outputs, and report."""
        validated = BenchmarkManifest.model_validate(manifest.model_dump())
        case = case_root.resolve()
        workspace = run_root.resolve()
        expected_root = (_manifest_root or case).resolve()
        validate_roots(case, workspace)
        recovered = self._reconcile_existing(validated, workspace)
        if recovered is not None:
            return recovered
        initialize_workspace(workspace, validated)
        started_at = datetime.now(UTC)

        raw_issue = inspect_raw_root(case)
        if raw_issue is not None:
            return self._publish_preparation_failure(
                validated, workspace, started_at, raw_issue
            )
        source_issue = verify_source(validated, case)
        if source_issue is not None:
            return self._publish_preparation_failure(
                validated, workspace, started_at, source_issue
            )
        copy_issue = copy_raw_inputs(case, workspace)
        if copy_issue is not None:
            return self._publish_preparation_failure(
                validated, workspace, started_at, copy_issue
            )
        return self._execute_plan(validated, workspace, expected_root)

    @staticmethod
    def _reconcile_existing(
        manifest: BenchmarkManifest, workspace: Path
    ) -> RunReport | None:
        if not workspace.exists() or not any(workspace.iterdir()):
            return None
        journal = read_finalization(workspace)
        if journal is not None and journal.state == "pending":
            recovered = publish_pending_finalization(workspace, manifest.id)
            assert recovered is not None
            return recovered
        raise ValueError(f"run root is not empty: {workspace}")

    def _execute_plan(
        self,
        manifest: BenchmarkManifest,
        workspace: Path,
        expected_root: Path,
    ) -> RunReport:
        engine = RunEngine.for_workspace(workspace, runner=self.command_runner)
        engine.initialize(
            RunManifest(
                run_id=workspace.name or manifest.id,
                benchmark_id=manifest.id,
            )
        )
        tasks = [
            TaskDefinition(f"command-{index:04d}", command=command)
            for index, command in enumerate(manifest.commands, start=1)
        ]
        tasks.append(
            TaskDefinition(
                FINALIZER_TASK_ID,
                action=partial(
                    _invoke_comparison_finalizer,
                    manifest.model_dump_json(),
                    str(workspace),
                    str(expected_root),
                ),
                version="benchmark-finalization-v1",
            )
        )
        try:
            engine.execute(tasks)
        except BenchmarkComparisonFailure:
            self._require_engine_status(engine, WorkflowStatus.FAILED)
        except TaskCommandError as error:
            return self._publish_command_failure(engine, manifest, error)
        except (OSError, PermissionError, RuntimeError, ValueError) as error:
            return self._publish_command_failure(engine, manifest, error)
        else:
            self._require_engine_status(engine, WorkflowStatus.PASSED)
        finalized = publish_pending_finalization(workspace, manifest.id)
        if finalized is None:
            raise RuntimeError("benchmark finalization journal is missing")
        return finalized

    @staticmethod
    def _publish_command_failure(
        engine: RunEngine, manifest: BenchmarkManifest, error: Exception
    ) -> RunReport:
        base = BenchmarkRunner._read_engine_report(engine)
        timed_out = isinstance(error, TaskCommandError) and "timed out" in str(error)
        if isinstance(error, TaskCommandError):
            code = "COMMAND_TIMEOUT" if timed_out else "COMMAND_FAILED"
            severity = FindingSeverity.ERROR
            message = str(error)
        else:
            code = "COMMAND_ERROR"
            severity = FindingSeverity.CRITICAL
            message = f"command execution raised {type(error).__name__}: {error}"
        finding = make_finding(
            manifest.id,
            1,
            code,
            severity,
            message,
            (f"completed_commands={len(base.completed_tasks)}",),
        )
        canonical = canonical_report(
            base, [finding], [], finalizer_completed=False
        )
        stage_finalization(canonical, engine.workspace)
        finalized = publish_pending_finalization(engine.workspace, manifest.id)
        assert finalized is not None
        return finalized

    @staticmethod
    def _publish_preparation_failure(
        manifest: BenchmarkManifest,
        workspace: Path,
        started_at: datetime,
        issue: PreparationIssue,
    ) -> RunReport:
        base = RunReport(
            run_id=workspace.name or manifest.id,
            benchmark_id=manifest.id,
            status=WorkflowStatus.PENDING,
            started_at=started_at,
        )
        finding = make_finding(
            manifest.id,
            1,
            issue.code,
            issue.severity,
            issue.message,
            issue.evidence,
        )
        canonical = canonical_report(
            base, [finding], [], finalizer_completed=False
        )
        stage_finalization(canonical, workspace)
        finalized = publish_pending_finalization(workspace, manifest.id)
        assert finalized is not None
        return finalized

    @staticmethod
    def _read_engine_report(engine: RunEngine) -> RunReport:
        return RunReport.model_validate(
            engine.artifacts.read_json(Path("run-report.json"))
        )

    @staticmethod
    def _require_engine_status(
        engine: RunEngine, expected: WorkflowStatus
    ) -> None:
        actual = BenchmarkRunner._read_engine_report(engine).status
        if actual is not expected:
            raise RuntimeError(
                f"RunEngine finalized {actual.value}, expected {expected.value}"
            )

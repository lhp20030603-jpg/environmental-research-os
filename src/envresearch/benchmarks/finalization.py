"""RunEngine comparison task and canonical benchmark report construction."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from envresearch.benchmarks.compare import (
    ComparisonResult,
    ComparisonStatus,
    compare_output,
)
from envresearch.benchmarks.report import stage_finalization
from envresearch.models.benchmark import BenchmarkManifest, ExpectedOutput
from envresearch.models.enums import FindingSeverity, WorkflowStatus
from envresearch.models.finding import Finding
from envresearch.models.run import RunReport

_FAILURE_SEVERITIES = frozenset(
    {FindingSeverity.ERROR, FindingSeverity.CRITICAL}
)
_COMPARISON_CODES = {
    ComparisonStatus.MISMATCHED: "OUTPUT_MISMATCH",
    ComparisonStatus.MISSING: "OUTPUT_MISSING",
    ComparisonStatus.UNEXPECTED: "OUTPUT_UNEXPECTED",
    ComparisonStatus.ERROR: "COMPARATOR_ERROR",
}
FINALIZER_TASK_ID = "benchmark-finalize"


class BenchmarkComparisonFailure(RuntimeError):
    """Signal comparison failure after its canonical report is staged."""


def run_comparison_finalizer(
    manifest_json: str, workspace: Path, expected_root: Path
) -> None:
    """Compare outputs and stage a report from inside the RunEngine task plan."""
    manifest = BenchmarkManifest.model_validate_json(manifest_json)
    base_report = RunReport.model_validate_json(
        (workspace / "run-report.json").read_text(encoding="utf-8")
    )
    comparisons, findings = compare_outputs(manifest, workspace, expected_root)
    if not manifest.expected_outputs:
        findings.append(
            make_finding(
                manifest.id,
                1,
                "NO_EXPECTED_OUTPUTS",
                FindingSeverity.WARNING,
                "benchmark declares no expected outputs",
                ("expected_outputs=0",),
            )
        )
    failed = any(finding.severity in _FAILURE_SEVERITIES for finding in findings)
    canonical = canonical_report(
        base_report,
        findings,
        comparisons,
        finalizer_completed=not failed,
    )
    stage_finalization(canonical, workspace)
    if failed:
        raise BenchmarkComparisonFailure("benchmark output comparison failed")


def canonical_report(
    base: RunReport,
    findings: list[Finding],
    comparisons: list[dict[str, object]],
    *,
    finalizer_completed: bool,
) -> RunReport:
    """Return the complete terminal report staged for final publication."""
    numbered = number_findings(base.benchmark_id, findings)
    status = (
        WorkflowStatus.FAILED
        if any(item.severity in _FAILURE_SEVERITIES for item in numbered)
        else WorkflowStatus.PASSED
    )
    completed = list(base.completed_tasks)
    if finalizer_completed:
        completed.append(FINALIZER_TASK_ID)
    return base.model_copy(
        update={
            "status": status,
            "finished_at": datetime.now(UTC),
            "findings": numbered,
            "completed_tasks": completed,
            "output_comparisons": comparisons,
        }
    )


def compare_outputs(
    manifest: BenchmarkManifest, workspace: Path, expected_root: Path
) -> tuple[list[dict[str, object]], list[Finding]]:
    """Collect all declared comparisons and stable findings in manifest order."""
    comparisons: list[dict[str, object]] = []
    findings: list[Finding] = []
    for output in manifest.expected_outputs:
        result = compare_output(workspace, expected_root, output)
        comparisons.append(_comparison_record(output, result))
        if result.status is ComparisonStatus.MATCHED:
            continue
        findings.append(
            make_finding(
                manifest.id,
                len(findings) + 1,
                _COMPARISON_CODES[result.status],
                FindingSeverity.ERROR,
                f"{output.path.as_posix()} comparison {result.status.value}",
                _comparison_evidence(output, result),
            )
        )
    return comparisons, findings


def make_finding(
    benchmark_id: str,
    index: int,
    code: str,
    severity: FindingSeverity,
    message: str,
    evidence: tuple[str, ...],
) -> Finding:
    """Create one deterministic benchmark-runner Finding."""
    return Finding(
        id=f"{benchmark_id}.finding.{index:04d}",
        code=code,
        severity=severity,
        message=message,
        producer="benchmark-runner",
        evidence=evidence,
    )


def number_findings(benchmark_id: str, findings: list[Finding]) -> list[Finding]:
    """Renumber findings once after ordered collection."""
    return [
        finding.model_copy(update={"id": f"{benchmark_id}.finding.{index:04d}"})
        for index, finding in enumerate(findings, start=1)
    ]


def _comparison_record(
    output: ExpectedOutput, result: ComparisonResult
) -> dict[str, object]:
    return {
        "path": output.path.as_posix(),
        "expected_path": output.expected_path.as_posix(),
        "comparator": output.comparator,
        "status": result.status.value,
        "total_differences": result.total_differences,
        "differences": [asdict(difference) for difference in result.differences],
        "actual_sha256": result.actual_sha256,
        "expected_sha256": result.expected_sha256,
    }


def _comparison_evidence(
    output: ExpectedOutput, result: ComparisonResult
) -> tuple[str, ...]:
    evidence = [
        f"path={output.path.as_posix()}",
        f"expected_path={output.expected_path.as_posix()}",
        f"total_differences={result.total_differences}",
    ]
    evidence.extend(
        f"{difference.location}: {difference.message or 'values differ'}"
        for difference in result.differences
    )
    return tuple(evidence)

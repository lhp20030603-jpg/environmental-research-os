"""Typer command-line adapter for benchmark and workflow operations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, NoReturn, cast

import typer
import yaml  # type: ignore[import-untyped]
from pydantic import JsonValue, ValidationError
from rich.console import Console
from rich.table import Table
from yaml import YAMLError

from envresearch.benchmarks.blind_report import (
    BlindCaseInvalid,
    BlindLineageInvalid,
    BlindReviewRequired,
    CitationIntegrityError,
    blind_report_table,
    blind_status_table,
    blind_validation_table,
    load_and_evaluate_blind_run,
    load_blind_status,
    validate_blind_case,
)
from envresearch.benchmarks.registry import BenchmarkRegistry
from envresearch.benchmarks.runner import BenchmarkRunner
from envresearch.econometrics.cli import econometrics_app
from envresearch.factory.cli import factory_app
from envresearch.kernel.events import EventLog
from envresearch.kernel.gates import GateDecision, GateStore
from envresearch.models.benchmark import BenchmarkManifest
from envresearch.models.enums import FindingSeverity, GateStatus, WorkflowStatus
from envresearch.models.finding import Finding
from envresearch.models.run import RunReport
from envresearch.paper.cli import paper_app
from envresearch.replication.cli import replication_app
from envresearch.research.cli_adapter import research_app
from envresearch.storage.artifacts import ArtifactStore

app = typer.Typer(help="Environmental research workflow tools.")
benchmark_app = typer.Typer(help="Validate, list, and replay benchmarks.")
run_app = typer.Typer(help="Inspect durable workflow runs.")
gate_app = typer.Typer(help="Record independent human gate decisions.")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(run_app, name="run")
app.add_typer(gate_app, name="gate")
app.add_typer(research_app, name="research")
app.add_typer(replication_app, name="replication")
app.add_typer(econometrics_app, name="econometrics")
app.add_typer(paper_app, name="paper")
app.add_typer(factory_app, name="factory")

JsonOption = Annotated[bool, typer.Option("--json", help="Emit JSON output.")]


def _emit_json(payload: object) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _error(
    code: str, message: str, *, json_output: bool, exit_code: int = 2
) -> NoReturn:
    if json_output:
        _emit_json({"error": {"code": code, "message": message}})
    else:
        table = Table(title="Command error")
        table.add_column("Code")
        table.add_column("Message")
        table.add_row(code, message)
        Console().print(table)
    raise typer.Exit(code=exit_code)


def _validation_code(field: str, value: object) -> str:
    missing = value is None or (isinstance(value, str) and not value.strip())
    if missing and field in {
        "doi",
        "source_version",
        "source_archive",
        "source_sha256",
    }:
        return "PUBLIC_METADATA_MISSING"
    if missing and field in {"license_name", "license_url"}:
        return "LICENSE_METADATA_MISSING"
    return "SCHEMA_INVALID"


def _pydantic_findings(error: ValidationError) -> list[Finding]:
    findings: list[Finding] = []
    for index, detail in enumerate(error.errors(include_url=False), start=1):
        location = detail["loc"]
        field = str(location[0]) if location else "manifest"
        code = _validation_code(field, detail.get("input"))
        findings.append(
            Finding(
                id=f"manifest-validation-{index:04d}",
                code=code,
                severity=FindingSeverity.ERROR,
                message=f"{'.'.join(map(str, location))}: {detail['msg']}",
                producer="envresearch.cli",
                evidence=(f"field={field}",),
            )
        )
    return findings


def _schema_finding(message: str) -> Finding:
    return Finding(
        id="manifest-validation-0001",
        code="SCHEMA_INVALID",
        severity=FindingSeverity.ERROR,
        message=message,
        producer="envresearch.cli",
        evidence=("field=manifest",),
    )


def _load_manifest(path: Path) -> tuple[BenchmarkManifest | None, list[Finding]]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, YAMLError) as error:
        return None, [_schema_finding(str(error))]
    if not isinstance(payload, dict):
        return None, [_schema_finding("manifest must contain a YAML mapping")]
    try:
        return BenchmarkManifest.model_validate(payload), []
    except ValidationError as error:
        return None, _pydantic_findings(error)


def _validation_payload(
    path: Path,
    manifest: BenchmarkManifest | None,
    findings: list[Finding],
) -> dict[str, object]:
    return {
        "benchmark_id": manifest.id if manifest is not None else None,
        "findings": [item.model_dump(mode="json") for item in findings],
        "manifest": str(path),
        "valid": manifest is not None,
    }


def _emit_validation(
    path: Path,
    manifest: BenchmarkManifest | None,
    findings: list[Finding],
    *,
    json_output: bool,
) -> None:
    if json_output:
        _emit_json(_validation_payload(path, manifest, findings))
        return
    table = Table(title="Manifest validation")
    table.add_column("Status")
    table.add_column("Code")
    table.add_column("Details")
    if manifest is not None:
        table.add_row("VALID", "-", f"{manifest.id}: {path}")
    else:
        for finding in findings:
            table.add_row("INVALID", finding.code, finding.message)
    Console().print(table)


@benchmark_app.command("validate")
def benchmark_validate(
    manifest_path: Annotated[Path, typer.Argument(help="Benchmark manifest.")],
    json_output: JsonOption = False,
) -> None:
    """Validate one benchmark manifest without executing it."""
    manifest, findings = _load_manifest(manifest_path)
    _emit_validation(manifest_path, manifest, findings, json_output=json_output)
    if manifest is None:
        raise typer.Exit(code=2)


@benchmark_app.command("list")
def benchmark_list(
    catalog: Annotated[Path, typer.Option("--catalog", help="Catalog root.")],
    json_output: JsonOption = False,
) -> None:
    """List benchmark manifests discovered beneath a catalog root."""
    if not catalog.is_dir():
        _error("CATALOG_NOT_FOUND", str(catalog), json_output=json_output)
    try:
        manifests = BenchmarkRegistry.discover(catalog)
    except (OSError, TypeError, ValueError) as error:
        _error("SCHEMA_INVALID", str(error), json_output=json_output)
    items = [manifest.model_dump(mode="json") for manifest in manifests.values()]
    if json_output:
        _emit_json({"benchmarks": items})
        return
    table = Table(title="Benchmark catalog")
    table.add_column("ID")
    table.add_column("Method family")
    table.add_column("Topic")
    table.add_column("Public")
    for manifest in manifests.values():
        table.add_row(
            manifest.id,
            manifest.method_family,
            manifest.topic,
            "yes" if manifest.public else "no",
        )
    Console().print(table)


def _emit_report(report: RunReport, *, json_output: bool) -> None:
    if json_output:
        _emit_json(report.model_dump(mode="json"))
        return
    table = Table(title="Benchmark run")
    table.add_column("Run")
    table.add_column("Benchmark")
    table.add_column("Status")
    table.add_column("Findings")
    table.add_row(
        report.run_id,
        report.benchmark_id,
        report.status.value.upper(),
        ", ".join(item.code for item in report.findings) or "none",
    )
    Console().print(table)


@benchmark_app.command("run")
def benchmark_run(
    manifest_path: Annotated[Path, typer.Argument(help="Benchmark manifest.")],
    case_root: Annotated[Path, typer.Option("--case-root", help="Local case root.")],
    run_root: Annotated[Path, typer.Option("--run-root", help="Fresh run root.")],
    json_output: JsonOption = False,
) -> None:
    """Execute a validated benchmark replay in a derived workspace."""
    manifest, findings = _load_manifest(manifest_path)
    if manifest is None:
        _emit_validation(manifest_path, manifest, findings, json_output=json_output)
        raise typer.Exit(code=2)
    try:
        report = BenchmarkRunner.default().run_manifest(
            manifest_path, case_root, run_root
        )
    except (TypeError, ValueError) as error:
        _error("BENCHMARK_RUN_INVALID", str(error), json_output=json_output)
    except (OSError, RuntimeError) as error:
        _error(
            "BENCHMARK_EXECUTION_ERROR",
            str(error),
            json_output=json_output,
            exit_code=1,
        )
    _emit_report(report, json_output=json_output)
    if report.status is not WorkflowStatus.PASSED:
        raise typer.Exit(code=1)


@benchmark_app.command("blind-validate")
def benchmark_blind_validate(
    case_root: Annotated[Path, typer.Argument(help="Blind benchmark case root.")],
    json_output: JsonOption = False,
) -> None:
    """Validate one pinned blind case without executing it."""
    try:
        result = validate_blind_case(case_root)
    except BlindCaseInvalid as error:
        _error("BLIND_CASE_INVALID", str(error), json_output=json_output)
    if json_output:
        _emit_json(result.model_dump(mode="json"))
    else:
        Console().print(blind_validation_table(result))


@benchmark_app.command("blind-status")
def benchmark_blind_status(
    run_root: Annotated[Path, typer.Argument(help="Blind evaluation run root.")],
    json_output: JsonOption = False,
) -> None:
    """Inspect current durable blind workflow state."""
    try:
        result = load_blind_status(run_root)
    except BlindLineageInvalid as error:
        _error("BLIND_LINEAGE_INVALID", str(error), json_output=json_output)
    if json_output:
        _emit_json(result.model_dump(mode="json"))
    else:
        Console().print(blind_status_table(result))


@benchmark_app.command("blind-evaluate")
def benchmark_blind_evaluate(
    run_root: Annotated[Path, typer.Argument(help="Blind evaluation run root.")],
    json_output: JsonOption = False,
) -> None:
    """Evaluate only current authenticated blind artifacts and queues."""
    try:
        report = load_and_evaluate_blind_run(run_root)
    except BlindReviewRequired as error:
        _error("BLIND_REVIEW_REQUIRED", str(error), json_output=json_output)
    except CitationIntegrityError as error:
        _error("CITATION_INTEGRITY_FAILED", str(error), json_output=json_output)
    except (BlindCaseInvalid, BlindLineageInvalid) as error:
        _error("BLIND_LINEAGE_INVALID", str(error), json_output=json_output)
    if json_output:
        _emit_json(report.model_dump(mode="json"))
    else:
        Console().print(blind_report_table(report))


def _read_report(run_root: Path, *, json_output: bool) -> RunReport:
    report_path = run_root / "run-report.json"
    if not report_path.is_file():
        _error("RUN_NOT_FOUND", str(run_root), json_output=json_output)
    try:
        return RunReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        _error("RUN_REPORT_INVALID", str(error), json_output=json_output)


@run_app.command("status")
def run_status(
    run_root: Annotated[Path, typer.Argument(help="Workflow run root.")],
    json_output: JsonOption = False,
) -> None:
    """Display the canonical report for a durable workflow run."""
    _emit_report(
        _read_report(run_root, json_output=json_output), json_output=json_output
    )


@gate_app.command("decide")
def gate_decide(
    run_root: Annotated[Path, typer.Argument(help="Workflow run root.")],
    gate_id: Annotated[str, typer.Argument(help="Pending gate ID.")],
    approve: Annotated[bool, typer.Option("--approve")] = False,
    reject: Annotated[bool, typer.Option("--reject")] = False,
    actor: Annotated[str, typer.Option("--actor", help="Independent reviewer.")] = "",
    rationale: Annotated[
        str, typer.Option("--rationale", help="Decision reason.")
    ] = "",
    conditions_json: Annotated[
        Path | None,
        typer.Option("--conditions-json", help="UTF-8 JSON object with conditions."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Approve or reject one existing gate as an independent actor."""
    if approve == reject:
        _error(
            "GATE_ACTION_INVALID",
            "exactly one of --approve or --reject is required",
            json_output=json_output,
        )
    if not actor.strip() or not rationale.strip():
        _error(
            "GATE_DECISION_INVALID",
            "--actor and --rationale must not be blank",
            json_output=json_output,
        )
    if not run_root.is_dir():
        _error("RUN_NOT_FOUND", str(run_root), json_output=json_output)
    conditions: dict[str, JsonValue] = {}
    if conditions_json is not None:
        try:
            loaded_conditions = json.loads(conditions_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            _error("GATE_DECISION_INVALID", str(error), json_output=json_output)
        if not isinstance(loaded_conditions, dict):
            _error(
                "GATE_DECISION_INVALID",
                "--conditions-json must contain one JSON object",
                json_output=json_output,
            )
        conditions = cast(dict[str, JsonValue], loaded_conditions)
    status = GateStatus.APPROVED if approve else GateStatus.REJECTED
    store = GateStore(ArtifactStore(run_root), EventLog(run_root / "events.jsonl"))
    try:
        decided = store.decide(
            gate_id,
            GateDecision(
                status=status,
                decided_by=actor,
                rationale=rationale,
                conditions=conditions,
            ),
        )
    except (OSError, TypeError, UnicodeError, ValidationError, ValueError) as error:
        _error("GATE_DECISION_INVALID", str(error), json_output=json_output)
    payload = decided.model_dump(mode="json")
    if json_output:
        _emit_json(payload)
        return
    table = Table(title="Gate decision")
    table.add_column("Gate")
    table.add_column("Status")
    table.add_column("Actor")
    table.add_row(gate_id, decided.status.value.upper(), actor.strip().casefold())
    Console().print(table)

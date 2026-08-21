# Thin Typer adapter for durable, local-only research orchestration.
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, NoReturn

import typer
import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from yaml import YAMLError

from envresearch.benchmarks.design_files import PinnedFixtureRoot
from envresearch.kernel.gates import GateDecision, GateRequest
from envresearch.models.enums import GateStatus
from envresearch.models.intake import ResearchBriefPayload, ResearchIntakeMode
from envresearch.models.principal import PrincipalKind
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.run_config import (
    load_explicit_config,
    verify_bound_config_data,
)
from envresearch.research.workflow import (
    ResearchRunConfig,
    ResearchRunPhase,
    ResearchRunSummary,
)
from envresearch.storage.paths import require_safe_workspace_root
from envresearch.workers.contracts import require_safe_order_id

research_app = typer.Typer(help="Initialize and advance Discover/Design research runs.")
JsonOption = Annotated[bool, typer.Option("--json", help="Emit JSON output.")]
_APPLICATION_ERRORS = (
    OSError,
    UnicodeError,
    TypeError,
    ValueError,
    ValidationError,
    RuntimeError,
    YAMLError,
)


def _emit(payload: object, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    table = Table(title="Research run")
    table.add_column("Run")
    table.add_column("Phase")
    if isinstance(payload, dict):
        table.add_row(str(payload.get("run_id", "-")), str(payload.get("phase", "-")))
    Console().print(table)


def _emit_gate(gate: GateRequest, *, json_output: bool) -> None:
    if json_output:
        _emit(gate.model_dump(mode="json"), json_output=True)
        return
    table = Table(title="Research gate decision")
    table.add_column("Gate")
    table.add_column("Status")
    table.add_column("Actor")
    actor = gate.decision.decided_by if gate.decision is not None else "-"
    table.add_row(gate.id, gate.status.value.upper(), actor)
    Console().print(table)


def _error(code: str, message: str, *, json_output: bool) -> NoReturn:
    payload = {"error": {"code": code, "message": message}}
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        table = Table(title="Command error")
        table.add_column("Code")
        table.add_column("Message")
        table.add_row(code, message)
        Console().print(table)
    raise typer.Exit(code=2)


def _load_brief(path: Path) -> ResearchBriefPayload:
    payload = yaml.safe_load(path.read_bytes())
    if not isinstance(payload, dict):
        raise TypeError("research brief must contain one YAML mapping")
    return ResearchBriefPayload.model_validate(payload)


def _run_id(run_root: Path) -> str:
    digest = hashlib.sha256(str(run_root).encode()).hexdigest()[:16]
    return f"research-{digest}"


def _open_run(
    run_root: Path,
) -> tuple[ResearchOrchestrator, ResearchRunSummary]:
    target = require_safe_workspace_root(run_root)
    with PinnedFixtureRoot(target) as pinned:
        internal = pinned.read(
            Path("research-run-config.json"), description="internal run config"
        )
        copied = pinned.read(
            Path("research-run-config.yaml"), description="research config copy"
        )
    config = ResearchRunConfig.model_validate_json(internal)
    if config.workspace != target:
        raise ValueError("research run workspace identity does not match")
    verify_bound_config_data(copied, config)
    lifecycle = ResearchArtifactLifecycle(target, config.run_id)
    brief_path = (
        Path("artifacts/research-brief.yaml")
        if config.input_mode is ResearchIntakeMode.BROAD_TOPIC
        else Path("artifacts/intake-brief.yaml")
    )
    brief = lifecycle.read_payload(brief_path, ResearchBriefPayload)
    orchestrator = ResearchOrchestrator()
    try:
        summary = orchestrator.initialize(config, brief, explicit_config=copied)
    except BaseException:
        orchestrator.close()
        raise
    return orchestrator, summary


def _emit_advance(summary: ResearchRunSummary, *, json_output: bool) -> None:
    if summary.phase is ResearchRunPhase.WAITING_FOR_GATE:
        _error(
            "GATE_REQUIRED",
            f"human decision required: {', '.join(summary.pending_gate_ids)}",
            json_output=json_output,
        )
    if summary.phase is ResearchRunPhase.BLOCKED:
        _error(
            "RESEARCH_RUN_BLOCKED",
            "research run has an unresolved blocking decision or finding",
            json_output=json_output,
        )
    _emit(summary.model_dump(mode="json"), json_output=json_output)


@research_app.command("init")
def research_init(
    brief: Annotated[Path, typer.Argument(help="Research brief YAML.")],
    config: Annotated[Path, typer.Option("--config", help="Explicit run config.")],
    run_root: Annotated[Path, typer.Option("--run-root", help="Durable run root.")],
    json_output: JsonOption = False,
) -> None:
    """Initialize or idempotently reopen one local research run."""
    try:
        durable_brief = _load_brief(brief)
    except _APPLICATION_ERRORS as error:
        _error("RESEARCH_BRIEF_INVALID", str(error), json_output=json_output)
    orchestrator: ResearchOrchestrator | None = None
    try:
        explicit = load_explicit_config(config)
        target = require_safe_workspace_root(run_root)
        durable_config = ResearchRunConfig(
            workspace=target,
            run_id=_run_id(target),
            input_mode=durable_brief.intake_mode,
            ranking_policy=explicit.ranking_policy,
            acquisition_budget=explicit.acquisition_budget,
            require_claim_verified_citations=explicit.require_claim_verified_citations,
            citation_catalog_roots=explicit.citation_catalog_roots,
            config_sha256=explicit.sha256,
        )
        orchestrator = ResearchOrchestrator()
        summary = orchestrator.initialize(
            durable_config, durable_brief, explicit_config=explicit.data
        )
    except _APPLICATION_ERRORS as error:
        _error("RESEARCH_RUN_INVALID", str(error), json_output=json_output)
    finally:
        if orchestrator is not None:
            orchestrator.close()
    _emit(summary.model_dump(mode="json"), json_output=json_output)


@research_app.command("submit")
def research_submit(
    run_root: Annotated[Path, typer.Argument(help="Durable research run root.")],
    order_id: Annotated[str, typer.Argument(help="Issued work order ID.")],
    candidate: Annotated[Path, typer.Argument(help="Candidate inside the run root.")],
    order_hash: Annotated[
        str | None,
        typer.Option("--order-hash", help="SHA-256 from the observed work order."),
    ] = None,
    producer_context: Annotated[
        str | None,
        typer.Option(
            "--producer-context",
            help="Ignored caller label; never used as trusted identity.",
        ),
    ] = None,
    producer_component: Annotated[
        str, typer.Option("--producer-component", help="Ignored caller label.")
    ] = "filesystem-worker",
    producer_version: Annotated[
        str, typer.Option("--producer-version", help="Ignored caller label.")
    ] = "1.0",
    producer_model: Annotated[
        str | None, typer.Option("--producer-model", help="Ignored caller label.")
    ] = None,
    producer_runtime: Annotated[
        str | None, typer.Option("--producer-runtime", help="Ignored caller label.")
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Authenticate and validate one filesystem worker candidate."""
    try:
        require_safe_order_id(order_id)
    except (TypeError, ValueError) as error:
        _error("WORK_ORDER_INVALID", str(error), json_output=json_output)
    if order_hash is None:
        _error(
            "WORK_ORDER_INVALID",
            "--order-hash is required for generation-bound submission",
            json_output=json_output,
        )
    try:
        orchestrator, _ = _open_run(run_root)
    except _APPLICATION_ERRORS as error:
        _error("RESEARCH_RUN_INVALID", str(error), json_output=json_output)
    try:
        if not orchestrator.queue.exchange.exists(
            Path("work-orders") / f"{order_id}.json"
        ):
            _error(
                "WORK_ORDER_INVALID",
                f"unknown work order: {order_id}",
                json_output=json_output,
            )
        orchestrator.queue.submit(
            order_id,
            candidate,
            expected_order_hash=order_hash,
        )
        summary = orchestrator.accept_submission(order_id)
    except typer.Exit:
        raise
    except _APPLICATION_ERRORS as error:
        _error("SUBMISSION_INVALID", str(error), json_output=json_output)
    finally:
        orchestrator.close()
    _emit(summary.model_dump(mode="json"), json_output=json_output)


@research_app.command("advance")
def research_advance(
    run_root: Annotated[Path, typer.Argument(help="Durable research run root.")],
    json_output: JsonOption = False,
) -> None:
    """Reconcile durable submissions and gates, then issue ready work."""
    orchestrator: ResearchOrchestrator | None = None
    try:
        orchestrator, _ = _open_run(run_root)
        summary = orchestrator.advance()
    except _APPLICATION_ERRORS as error:
        _error("RESEARCH_RUN_INVALID", str(error), json_output=json_output)
    finally:
        if orchestrator is not None:
            orchestrator.close()
    _emit_advance(summary, json_output=json_output)


@research_app.command("gate-decide")
def research_gate_decide(
    run_root: Annotated[Path, typer.Argument(help="Durable research run root.")],
    gate_id: Annotated[str, typer.Argument(help="Exact pending gate ID.")],
    approve: Annotated[bool, typer.Option("--approve")] = False,
    reject: Annotated[bool, typer.Option("--reject")] = False,
    rationale: Annotated[str, typer.Option("--rationale")] = "",
    conditions_json: Annotated[
        Path | None, typer.Option("--conditions-json", help="Gate conditions JSON.")
    ] = None,
    principal_capability_file: Annotated[
        Path | None,
        typer.Option("--principal-capability-file", help="Owner capability file."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Record an owner-authenticated decision for one active research gate."""
    if approve == reject:
        _error(
            "GATE_DECISION_INVALID",
            "exactly one of --approve or --reject is required",
            json_output=json_output,
        )
    if principal_capability_file is None:
        _error(
            "GATE_DECISION_INVALID",
            "--principal-capability-file is required for owner-controlled gate decision",
            json_output=json_output,
        )
    if not rationale.strip() or conditions_json is None:
        _error(
            "GATE_DECISION_INVALID",
            "--rationale and --conditions-json are required",
            json_output=json_output,
        )
    orchestrator: ResearchOrchestrator | None = None
    try:
        conditions = json.loads(conditions_json.read_text(encoding="utf-8"))
        if not isinstance(conditions, dict):
            raise TypeError("--conditions-json must contain one JSON object")
        orchestrator, _ = _open_run(run_root)
        active = {
            context.gate_id: base
            for base in ("gate-1", "data-gate", "final-gate")
            if (context := orchestrator.bound_gates.active_context(base)) is not None
        }
        if gate_id not in active:
            raise ValueError("gate ID is not the exact active research gate")
        principal_capability = orchestrator.principals.capability_from_file(
            PrincipalKind.GATE, principal_capability_file
        )
        decided = orchestrator.decide_gate(
            active[gate_id],
            GateDecision(
                status=GateStatus.APPROVED if approve else GateStatus.REJECTED,
                decided_by="human-reviewer",
                rationale=rationale,
                conditions=conditions,
            ),
            principal_capability,
        )
    except _APPLICATION_ERRORS as error:
        _error("GATE_DECISION_INVALID", str(error), json_output=json_output)
    finally:
        if orchestrator is not None:
            orchestrator.close()
    _emit_gate(decided, json_output=json_output)


@research_app.command("revise")
def research_revise(
    run_root: Annotated[Path, typer.Argument(help="Durable research run root.")],
    node_id: Annotated[str, typer.Argument(help="Completed worker node to revise.")],
    actor: Annotated[str, typer.Option("--actor", help="Revision requester.")],
    reason: Annotated[str, typer.Option("--reason", help="Revision rationale.")],
    principal_capability_file: Annotated[
        Path | None,
        typer.Option("--principal-capability-file", help="Owner capability file."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Request durable target-plus-descendant local recomputation."""
    orchestrator: ResearchOrchestrator | None = None
    if principal_capability_file is None:
        _error(
            "REVISION_INVALID",
            "--principal-capability-file is required for owner-controlled revision",
            json_output=json_output,
        )
    try:
        orchestrator, _ = _open_run(run_root)
        principal_capability = orchestrator.principals.capability_from_file(
            PrincipalKind.REVISION, principal_capability_file
        )
        revision = orchestrator.request_revision(
            node_id,
            reason=reason,
            actor=actor,
            principal_capability=principal_capability,
        )
    except _APPLICATION_ERRORS as error:
        _error("REVISION_INVALID", str(error), json_output=json_output)
    finally:
        if orchestrator is not None:
            orchestrator.close()
    payload = revision.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    table = Table(title="Research revision")
    table.add_column("Revision")
    table.add_column("Node")
    table.add_row(revision.revision_id, revision.node_id)
    Console().print(table)


@research_app.command("status")
def research_status(
    run_root: Annotated[Path, typer.Argument(help="Durable research run root.")],
    json_output: JsonOption = False,
) -> None:
    """Recover and display the current durable research phase."""
    orchestrator: ResearchOrchestrator | None = None
    try:
        orchestrator, summary = _open_run(run_root)
    except _APPLICATION_ERRORS as error:
        _error("RESEARCH_RUN_INVALID", str(error), json_output=json_output)
    finally:
        if orchestrator is not None:
            orchestrator.close()
    _emit(summary.model_dump(mode="json"), json_output=json_output)

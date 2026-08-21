"""Narrow operator CLI for exact-reference Tier-2 replication actions."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import HttpUrl, TypeAdapter, ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.replication._cli_runtime import (
    _UnavailableEngine,
    configured_max_growth_bytes,
)
from envresearch.replication._runtime_subprocess import (
    select_container_engine,
)
from envresearch.replication._service_models import ReplicationReport
from envresearch.replication._service_support import read_admission
from envresearch.replication.container import (
    CommandExecutor,
    ContainerControl,
)
from envresearch.replication.contracts import (
    ExternalAdmission,
    ReplicationRunState,
    Tier2IntakeProposal,
)
from envresearch.replication.intake import Tier2IntakeService
from envresearch.replication.proposals import (
    Tier2DryProposal,
    load_replication_proposal,
)
from envresearch.replication.service import DidReplayConfiguration, ReplicationService
from envresearch.storage.research_artifacts import ResearchArtifactStore

replication_app = typer.Typer(
    help="Validate and operate approved container-only Tier-2 replications."
)
JsonOption = Annotated[bool, typer.Option("--json", help="Emit JSON output.")]
URL = TypeAdapter(HttpUrl)


def _service_for_root(
    run_root: Path,
    *,
    engine_configurations: Sequence[object] | None = None,
    executor: CommandExecutor | None = None,
    container_control: ContainerControl | None = None,
    max_growth_bytes: int | None = None,
) -> ReplicationService:
    """Open durable state; production external execution remains unavailable."""
    store = ResearchArtifactStore(run_root)
    engine = (
        None
        if engine_configurations is None
        else select_container_engine(
            tuple(engine_configurations),
            executor=executor,
            container_control=container_control,
        )
    )
    return ReplicationService(
        store,
        Tier2IntakeService(store),
        engine or _UnavailableEngine(),
        DidReplayConfiguration(
            author_script=Path("code/run.R"),
            data_path=Path("data/analysis.csv"),
            unit_column="unit",
            time_column="time",
            treatment_column="treated",
            cohort_column="cohort",
            outcome_column="outcome",
            reference_period=-1,
        ),
        max_growth_bytes=(
            configured_max_growth_bytes()
            if max_growth_bytes is None
            else max_growth_bytes
        ),
    )


def _emit(payload: object, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _fail(
    code: str, message: str, *, json_output: bool, exit_code: int = 2
) -> NoReturn:
    _emit({"error": {"code": code, "message": message}}, json_output=json_output)
    raise typer.Exit(code=exit_code)


def _load(path: Path, *, json_output: bool) -> Tier2DryProposal | Tier2IntakeProposal:
    try:
        return load_replication_proposal(path)
    except (TypeError, ValueError) as error:
        _fail("PROPOSAL_INVALID", str(error), json_output=json_output)


def _read_ref(
    path: Path, expected_ids: frozenset[str], *, code: str, json_output: bool
) -> ArtifactRef:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        candidates = [payload]
        if isinstance(payload, dict):
            candidates = [
                payload[key]
                for key in ("status_ref", "approved_ref", "run_ref")
                if key in payload
            ] or candidates
        references = tuple(
            ArtifactRef.model_validate(candidate) for candidate in candidates
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as error:
        _fail(code, str(error), json_output=json_output)
    matching = tuple(
        item
        for index, item in enumerate(references)
        if item.artifact_id in expected_ids and item not in references[:index]
    )
    if len(matching) != 1:
        _fail(
            code, "artifact reference has the wrong identity", json_output=json_output
        )
    return matching[0]


def _validated_run_root(path: Path, *, json_output: bool) -> Path:
    if path.is_symlink():
        _fail(
            "REPLICATION_ROOT_INVALID",
            "replication root must not be a symlink",
            json_output=json_output,
        )
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or (
        resolved.exists() and not resolved.is_dir()
    ):
        _fail(
            "REPLICATION_ROOT_INVALID",
            "replication root must be a dedicated directory",
            json_output=json_output,
        )
    return resolved


def _proposal_payload(
    path: Path, proposal: Tier2DryProposal | Tier2IntakeProposal
) -> dict[str, object]:
    admission_status: str
    if isinstance(proposal, Tier2DryProposal):
        blockers = [item.code for item in proposal.unresolved_blockers]
        admission_status = proposal.admission_status
        executable = False
    else:
        blockers = []
        admission_status = "ready_for_external_admission"
        executable = True
    return {
        "admission_status": admission_status,
        "executable": executable,
        "package_id": proposal.package_id,
        "proposal": str(path),
        "schema_version": proposal.schema_version,
        "unresolved_blockers": blockers,
        "valid": True,
    }


def _report_payload(
    report: ReplicationReport, *, status_ref: ArtifactRef
) -> dict[str, object]:
    """Serialize the frozen dataclass without relying on implicit encoders."""
    return {
        "author_outputs": [
            item.model_dump(mode="json") for item in report.author_outputs
        ],
        "derived_ref": report.derived_ref.model_dump(mode="json")
        if report.derived_ref
        else None,
        "exception": report.exception.model_dump(mode="json")
        if report.exception
        else None,
        "run_ref": report.run_ref.model_dump(mode="json"),
        "state": report.state.value,
        "status_ref": status_ref.model_dump(mode="json"),
        "verification_ref": report.verification_ref.model_dump(mode="json")
        if report.verification_ref
        else None,
    }


def _current_status_ref(
    report: ReplicationReport, subject_ref: ArtifactRef
) -> ArtifactRef:
    if report.run_ref.artifact_id == "replication-ledger":
        return report.run_ref
    return subject_ref


def _require_approval_authority(
    run_root: Path, reference: ArtifactRef, *, json_output: bool
) -> None:
    try:
        read_admission(ResearchArtifactStore(run_root), reference)
    except (OSError, TypeError, ValueError) as error:
        _fail("EXTERNAL_ADMISSION_REQUIRED", str(error), json_output=json_output)


@replication_app.command("validate")
def replication_validate(
    proposal_path: Annotated[
        Path, typer.Argument(help="Dry or executable proposal YAML.")
    ],
    json_output: JsonOption = False,
) -> None:
    """Validate one proposal without persisting artifacts or contacting URLs."""
    proposal = _load(proposal_path, json_output=json_output)
    _emit(_proposal_payload(proposal_path, proposal), json_output=json_output)


@replication_app.command("approve-external")
def replication_approve_external(
    proposal_path: Annotated[
        Path, typer.Argument(help="Executable intake proposal YAML.")
    ],
    run_root: Annotated[
        Path, typer.Option("--run-root", help="Durable replication root.")
    ],
    approver_id: Annotated[
        str, typer.Option("--approver-id", help="Human decision identity.")
    ],
    rationale: Annotated[str, typer.Option("--rationale", help="Admission rationale.")],
    approved_locator: Annotated[
        str, typer.Option("--approved-locator", help="Exact acquisition URL.")
    ],
    json_output: JsonOption = False,
) -> None:
    """Record the only human decision allowed before exact acquisition."""
    proposal = _load(proposal_path, json_output=json_output)
    if isinstance(proposal, Tier2DryProposal):
        _fail(
            "DRY_PROPOSAL_NOT_EXECUTABLE",
            "the dry proposal retains unresolved admission blockers",
            json_output=json_output,
        )
    try:
        locator = URL.validate_python(approved_locator)
        admission = ExternalAdmission(
            approver_id=approver_id,
            rationale=rationale,
            approved_locator=locator,
        )
    except ValidationError as error:
        _fail("EXTERNAL_ADMISSION_INVALID", str(error), json_output=json_output)
    if str(locator) != str(proposal.canonical_url):
        _fail(
            "APPROVED_LOCATOR_MISMATCH",
            "approved locator must equal the proposal canonical URL",
            json_output=json_output,
        )
    validated_root = _validated_run_root(run_root, json_output=json_output)
    service = _service_for_root(validated_root)
    try:
        proposal_ref = service.intake.record_proposal(proposal)
        approved_ref = service.approve_external_admission(proposal_ref, admission)
    except (OSError, PermissionError, TypeError, ValueError) as error:
        _fail("EXTERNAL_ADMISSION_INVALID", str(error), json_output=json_output)
    _emit(
        {
            "approved_ref": approved_ref.model_dump(mode="json"),
            "proposal_ref": proposal_ref.model_dump(mode="json"),
        },
        json_output=json_output,
    )


@replication_app.command("run")
def replication_run(
    run_root: Annotated[
        Path, typer.Option("--run-root", help="Durable replication root.")
    ],
    approved_ref_path: Annotated[
        Path, typer.Option("--approved-ref", help="Exact approved ArtifactRef JSON.")
    ],
    json_output: JsonOption = False,
) -> None:
    """Run an exact approved reference without prompting or creating approval."""
    approved_ref = _read_ref(
        approved_ref_path,
        frozenset({"approved-tier2-intake"}),
        code="EXTERNAL_ADMISSION_REQUIRED",
        json_output=json_output,
    )
    validated_root = _validated_run_root(run_root, json_output=json_output)
    _require_approval_authority(validated_root, approved_ref, json_output=json_output)
    service = _service_for_root(validated_root)
    try:
        report = service.run(approved_ref)
    except (OSError, TypeError, ValueError) as error:
        _fail("REPLICATION_RUN_INVALID", str(error), json_output=json_output)
    status_ref = _current_status_ref(report, approved_ref)
    _emit(_report_payload(report, status_ref=status_ref), json_output=json_output)
    if report.state is not ReplicationRunState.PASSED:
        raise typer.Exit(code=1)


@replication_app.command("resume")
def replication_resume(
    run_root: Annotated[
        Path, typer.Option("--run-root", help="Durable replication root.")
    ],
    run_ref_path: Annotated[
        Path, typer.Option("--run-ref", help="Exact ledger ArtifactRef JSON.")
    ],
    json_output: JsonOption = False,
) -> None:
    """Resume only the exact authenticated paused ledger generation."""
    run_ref = _read_ref(
        run_ref_path,
        frozenset({"replication-ledger"}),
        code="RUN_REFERENCE_INVALID",
        json_output=json_output,
    )
    try:
        service = _service_for_root(
            _validated_run_root(run_root, json_output=json_output)
        )
        report = service.resume(run_ref)
    except (OSError, TypeError, ValueError) as error:
        _fail("RUN_REFERENCE_INVALID", str(error), json_output=json_output)
    _emit(_report_payload(report, status_ref=report.run_ref), json_output=json_output)
    if report.state is not ReplicationRunState.PASSED:
        raise typer.Exit(code=1)


@replication_app.command("status")
def replication_status(
    run_root: Annotated[
        Path, typer.Option("--run-root", help="Durable replication root.")
    ],
    reference_path: Annotated[
        Path, typer.Option("--ref", help="Exact status authority ArtifactRef JSON.")
    ],
    json_output: JsonOption = False,
) -> None:
    """Read current authenticated status without changing durable state."""
    reference = _read_ref(
        reference_path,
        frozenset({"approved-tier2-intake", "replication-ledger"}),
        code="RUN_REFERENCE_INVALID",
        json_output=json_output,
    )
    try:
        service = _service_for_root(
            _validated_run_root(run_root, json_output=json_output)
        )
        report = service.status(reference)
    except (OSError, TypeError, ValueError) as error:
        _fail("RUN_REFERENCE_INVALID", str(error), json_output=json_output)
    status_ref = _current_status_ref(report, reference)
    _emit(_report_payload(report, status_ref=status_ref), json_output=json_output)

"""Exact-reference deterministic JSON CLI for governed factory runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import BaseModel, ConfigDict, ValidationError
from typer import _click as click
from typer._click import exceptions as click_exceptions
from typer.core import TyperGroup

from envresearch.factory._cli_composition import compose_service
from envresearch.factory._cli_roots import validated_roots
from envresearch.factory.errors import FactoryAuthorityInvalid, FactoryError
from envresearch.factory.promotion_contracts import (
    FactoryPromotionRejected,
    FactoryPromotionRequired,
)
from envresearch.factory.service import FactoryRunService
from envresearch.kernel.gates import GateDecision
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import GateStatus


def _emit(payload: object) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _parser_error(message: str) -> NoReturn:
    _emit(
        {
            "error": {
                "code": "FACTORY_AUTHORITY_INVALID",
                "finding_kind": "cli-input-invalid",
                "message": message,
            }
        }
    )
    raise click_exceptions.Exit(2)


class _JSONFactoryGroup(TyperGroup):
    """Keep nested Click/Typer usage failures on the JSON contract."""

    def make_context(self, *args: object, **kwargs: object) -> click.Context:
        try:
            return super().make_context(*args, **kwargs)  # type: ignore[arg-type]
        except click_exceptions.UsageError as error:
            _parser_error(error.format_message())

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except click_exceptions.UsageError as error:
            _parser_error(error.format_message())


factory_app = typer.Typer(
    cls=_JSONFactoryGroup,
    help="Assemble and inspect exact governed factory runs.",
    no_args_is_help=False,
)


class _StrictGateDecisionInput(GateDecision):
    """Reject fields outside the public decision-file contract."""

    model_config = ConfigDict(extra="forbid", strict=True)


def _fail(
    error: FactoryError,
    *,
    exit_code: int = 2,
    handoff: dict[str, object] | None = None,
) -> NoReturn:
    payload = {} if handoff is None else dict(handoff)
    payload["error"] = {
        "code": error.code,
        "finding_kind": error.finding_kind,
        "message": str(error),
    }
    _emit(payload)
    raise typer.Exit(code=exit_code)


def _load_ref(path: Path | None) -> ArtifactRef:
    if path is None:
        raise FactoryAuthorityInvalid(
            "explicit ArtifactRef JSON is required",
            finding_kind="reference-input-invalid",
        )
    try:
        return ArtifactRef.model_validate_json(path.read_bytes())
    except (OSError, ValueError, ValidationError) as exc:
        raise FactoryAuthorityInvalid(
            "explicit ArtifactRef JSON is invalid",
            finding_kind="reference-input-invalid",
        ) from exc


def _load_decision(path: Path | None) -> GateDecision:
    if path is None:
        raise FactoryAuthorityInvalid(
            "explicit GateDecision JSON is required",
            finding_kind="decision-input-invalid",
        )
    try:
        return _StrictGateDecisionInput.model_validate_json(path.read_bytes())
    except (OSError, ValueError, ValidationError) as exc:
        raise FactoryAuthorityInvalid(
            "explicit GateDecision JSON is invalid",
            finding_kind="decision-input-invalid",
        ) from exc


def _load_capability(path: Path | None) -> str:
    if path is None:
        raise FactoryAuthorityInvalid(
            "explicit principal capability file is required",
            finding_kind="principal-capability-input-invalid",
        )
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise FactoryAuthorityInvalid(
            "principal capability file is invalid",
            finding_kind="principal-capability-input-invalid",
        ) from exc
    if not value:
        raise FactoryAuthorityInvalid(
            "principal capability file is invalid",
            finding_kind="principal-capability-input-invalid",
        )
    return value


def _validated_roots(
    research_root: Path | None,
    v031_root: Path | None,
    paper_root: Path | None,
    factory_root: Path | None,
) -> tuple[Path, Path, Path, Path]:
    return validated_roots(research_root, v031_root, paper_root, factory_root)


def service_for_roots(
    research_root: Path,
    v031_root: Path,
    paper_root: Path,
    factory_root: Path,
    *,
    create: bool,
) -> FactoryRunService:
    """Compose the public facade from four already validated exact roots."""
    if not isinstance(create, bool):
        raise FactoryAuthorityInvalid(
            "factory creation policy is invalid",
            finding_kind="factory-root-invalid",
        )
    research, v031, paper, factory = _validated_roots(
        research_root, v031_root, paper_root, factory_root
    )
    return compose_service(research, v031, paper, factory)


def _close(service: FactoryRunService) -> None:
    close = getattr(service, "close", None)
    if callable(close):
        close()


def _success(
    reference: ArtifactRef, payload: BaseModel, status: BaseModel
) -> dict[str, object]:
    return {
        "reference": reference.model_dump(mode="json"),
        "payload": payload.model_dump(mode="json"),
        "status": status.model_dump(mode="json"),
    }


RootOption = Annotated[Path | None, typer.Option()]


def _service(
    research: Path | None,
    v031: Path | None,
    paper: Path | None,
    factory: Path | None,
    *,
    create: bool,
) -> FactoryRunService:
    roots = _validated_roots(research, v031, paper, factory)
    return service_for_roots(*roots, create=create)


@factory_app.command("assemble")
def factory_assemble(
    design_reference: Annotated[Path | None, typer.Argument()] = None,
    release_reference: Annotated[Path | None, typer.Argument()] = None,
    research_root: RootOption = None,
    v031_root: RootOption = None,
    paper_root: RootOption = None,
    factory_root: RootOption = None,
) -> None:
    """Assemble one exact approved-design and paper-release pair."""
    service: FactoryRunService | None = None
    try:
        design_ref, release_ref = (
            _load_ref(design_reference),
            _load_ref(release_reference),
        )
        service = _service(
            research_root, v031_root, paper_root, factory_root, create=True
        )
        reference = service.assemble(design_ref, release_ref)
        status = service.status(reference)
        _emit(_success(reference, status.run, status))
    except FactoryError as error:
        _fail(error)
    finally:
        if service is not None:
            _close(service)


@factory_app.command("status")
def factory_status(
    run_reference: Annotated[Path | None, typer.Argument()] = None,
    research_root: RootOption = None,
    v031_root: RootOption = None,
    paper_root: RootOption = None,
    factory_root: RootOption = None,
) -> None:
    """Reopen one exact current run without recovery or publication."""
    service: FactoryRunService | None = None
    try:
        reference = _load_ref(run_reference)
        service = _service(
            research_root, v031_root, paper_root, factory_root, create=False
        )
        status = service.status(reference)
        if status.state == "promotion-required":
            raise FactoryPromotionRequired(
                "this exact run requires independent human promotion",
                finding_kind="promotion-required",
            )
        _emit(_success(reference, status.run, status))
    except FactoryPromotionRequired as error:
        _fail(error, exit_code=1)
    except FactoryError as error:
        _fail(error)
    finally:
        if service is not None:
            _close(service)


@factory_app.command("request-promotion")
def factory_request_promotion(
    run_reference: Annotated[Path | None, typer.Argument()] = None,
    requested_by: Annotated[str | None, typer.Option("--requested-by")] = None,
    research_root: RootOption = None,
    v031_root: RootOption = None,
    paper_root: RootOption = None,
    factory_root: RootOption = None,
) -> None:
    """Request independent human review for one exact current run."""
    service: FactoryRunService | None = None
    try:
        run_ref = _load_ref(run_reference)
        if requested_by is None:
            raise FactoryAuthorityInvalid(
                "explicit promotion requester is required",
                finding_kind="promotion-requester-invalid",
            )
        service = _service(
            research_root, v031_root, paper_root, factory_root, create=True
        )
        reference = service.request_promotion(run_ref, requested_by)
        status = service.status(run_ref)
        context = service._promotions.store.load_context(reference)
        _emit(_success(reference, context, status))
    except FactoryError as error:
        _fail(error)
    finally:
        if service is not None:
            _close(service)


@factory_app.command("record-promotion")
def factory_record_promotion(
    context_reference: Annotated[Path | None, typer.Argument()] = None,
    run_reference: Annotated[Path | None, typer.Argument()] = None,
    decision: Annotated[Path | None, typer.Argument()] = None,
    principal_capability_file: Annotated[
        Path | None, typer.Option("--principal-capability-file")
    ] = None,
    research_root: RootOption = None,
    v031_root: RootOption = None,
    paper_root: RootOption = None,
    factory_root: RootOption = None,
) -> None:
    """Record one exact independent terminal promotion decision."""
    service: FactoryRunService | None = None
    try:
        context_ref, run_ref = _load_ref(context_reference), _load_ref(run_reference)
        durable_decision = _load_decision(decision)
        capability = _load_capability(principal_capability_file)
        service = _service(
            research_root, v031_root, paper_root, factory_root, create=True
        )
        reference = service.record_promotion(context_ref, durable_decision, capability)
        status = service.promotion_status(reference, run_ref)
        promotion = service._promotions.store.load_promotion(reference)
        handoff = _success(reference, promotion, status)
        if durable_decision.status is GateStatus.REJECTED:
            _fail(
                FactoryPromotionRejected(
                    "the independent human decision rejected this exact run",
                    finding_kind="promotion-rejected",
                ),
                exit_code=1,
                handoff=handoff,
            )
        _emit(handoff)
    except FactoryPromotionRejected as error:
        _fail(error, exit_code=1)
    except FactoryError as error:
        _fail(error)
    finally:
        if service is not None:
            _close(service)


@factory_app.command("promotion-status")
def factory_promotion_status(
    promotion_reference: Annotated[Path | None, typer.Argument()] = None,
    run_reference: Annotated[Path | None, typer.Argument()] = None,
    research_root: RootOption = None,
    v031_root: RootOption = None,
    paper_root: RootOption = None,
    factory_root: RootOption = None,
) -> None:
    """Reopen one exact terminal promotion without healing state."""
    service: FactoryRunService | None = None
    try:
        promotion_ref, run_ref = (
            _load_ref(promotion_reference),
            _load_ref(run_reference),
        )
        service = _service(
            research_root, v031_root, paper_root, factory_root, create=False
        )
        status = service.promotion_status(promotion_ref, run_ref)
        promotion = service._promotions.store.load_promotion(promotion_ref)
        handoff = _success(promotion_ref, promotion, status)
        if status.state == "promotion-rejected":
            _fail(
                FactoryPromotionRejected(
                    "the independent human decision rejected this exact run",
                    finding_kind="promotion-rejected",
                ),
                exit_code=1,
                handoff=handoff,
            )
        _emit(handoff)
    except FactoryPromotionRejected as error:
        _fail(error, exit_code=1)
    except FactoryError as error:
        _fail(error)
    finally:
        if service is not None:
            _close(service)


__all__ = ["factory_app", "service_for_roots"]

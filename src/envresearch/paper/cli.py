"""Reference-only deterministic JSON CLI for V0.4 Paper Builder releases."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from envresearch.benchmarks.design_files import PinnedFixtureRoot
from envresearch.econometrics.exit_registry import ExitRegistry, validate_separate_roots
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.argument_map import ArgumentMapService
from envresearch.paper.auditor import PaperAuditService
from envresearch.paper.citation_authority import (
    CitationAuthority,
    LifecycleCitationAuthority,
)
from envresearch.paper.draft_builder import DraftService
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperBuilderError,
)
from envresearch.paper.ledger import (
    AcceptedEvidenceResolver,
    ClaimLedgerService,
    V031AcceptedEvidenceResolver,
)
from envresearch.paper.release import PaperReleaseCandidate, PaperReleaseService
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.citation_attestations import ProtectedCitationAttestations
from envresearch.research.run_config import verify_bound_config_data
from envresearch.research.workflow import ResearchRunConfig
from envresearch.workers.queue import FilesystemWorkerQueue

paper_app = typer.Typer(help="Build and inspect exact audited paper releases.")


def _emit(payload: object) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _fail(error: PaperBuilderError, *, exit_code: int = 2) -> NoReturn:
    _emit(
        {
            "error": {
                "code": error.code,
                "finding_kind": error.finding_kind,
                "message": str(error),
            }
        }
    )
    raise typer.Exit(code=exit_code)


def _load_ref(path: Path | None) -> ArtifactRef:
    if path is None:
        raise PaperAuthorityInvalid(
            "explicit Paper Builder inputs are required",
            finding_kind="reference-input-invalid",
        )
    try:
        return ArtifactRef.model_validate_json(path.read_bytes())
    except (OSError, ValueError, ValidationError) as exc:
        raise PaperAuthorityInvalid(
            "explicit ArtifactRef JSON is invalid",
            finding_kind="reference-input-invalid",
        ) from exc


def _validated_roots(
    v031_root: Path | None,
    paper_root: Path | None,
    research_root: Path | None,
) -> tuple[Path, Path, Path]:
    try:
        if v031_root is None or paper_root is None or research_root is None:
            raise ValueError("explicit roots are required")
        lexical_v031 = v031_root.expanduser().absolute()
        lexical_paper = paper_root.expanduser().absolute()
        lexical_research = research_root.expanduser().absolute()
        v031, paper = validate_separate_roots(lexical_v031, lexical_paper)
        _, research = validate_separate_roots(lexical_v031, lexical_research)
        validate_separate_roots(lexical_paper, lexical_research)
        control = research.parent / f".{research.name}.worker-queue-control"
        for root in (v031, paper, research):
            validate_separate_roots(root, control)
        v031.resolve(strict=True)
        research.resolve(strict=True)
        return v031, paper, research
    except (OSError, ValueError) as exc:
        raise PaperAuthorityInvalid(
            "Paper Builder roots are invalid or overlap",
            finding_kind="root-authority-overlap",
        ) from exc


def _citation_authority(
    research_root: Path,
) -> tuple[LifecycleCitationAuthority, FilesystemWorkerQueue]:
    queue: FilesystemWorkerQueue | None = None
    try:
        with PinnedFixtureRoot(research_root) as pinned:
            internal = pinned.read(
                Path("research-run-config.json"), description="internal run config"
            )
            copied = pinned.read(
                Path("research-run-config.yaml"), description="research config copy"
            )
        config = ResearchRunConfig.model_validate_json(internal)
        if config.workspace != research_root:
            raise ValueError("research workspace identity does not match root")
        verify_bound_config_data(copied, config)
        queue = FilesystemWorkerQueue.open_existing(
            research_root, require_producer_context=True
        )
        attestations = ProtectedCitationAttestations.open_existing(queue)
        authority = LifecycleCitationAuthority(
            lifecycle=ResearchArtifactLifecycle(research_root, config.run_id),
            attestations=attestations,
        )
        return authority, queue
    except (OSError, TypeError, ValueError, ValidationError) as exc:
        if queue is not None:
            queue.close()
        raise PaperAuthorityInvalid(
            "sealed research citation authority is invalid",
            finding_kind="citation-authority-invalid",
        ) from exc


class _PaperCLIService(PaperReleaseService):
    def __init__(
        self,
        *,
        audit_service: PaperAuditService,
        queue: FilesystemWorkerQueue | None,
    ) -> None:
        super().__init__(audit_service=audit_service)
        self.cli_queue = queue


def service_for_roots(
    v031_root: Path,
    paper_root: Path,
    research_root: Path,
    *,
    create: bool,
    resolver_factory: Callable[[Path], AcceptedEvidenceResolver] = (
        V031AcceptedEvidenceResolver
    ),
    citation_factory: Callable[
        [Path], tuple[CitationAuthority, FilesystemWorkerQueue | None]
    ] = _citation_authority,
) -> PaperReleaseService:
    """Compose release services from explicit physically separate roots."""
    v031, paper, research = _validated_roots(v031_root, paper_root, research_root)
    authority, queue = citation_factory(research)
    try:
        ledger = ClaimLedgerService(
            registry=ExitRegistry(paper, create=create),
            resolver=resolver_factory(v031),
        )
        maps = ArgumentMapService(ledger_service=ledger)
        drafts = DraftService(map_service=maps, citation_authority=authority)
        return _PaperCLIService(
            audit_service=PaperAuditService(draft_service=drafts), queue=queue
        )
    except (OSError, TypeError, ValueError, ValidationError) as exc:
        if queue is not None:
            queue.close()
        raise PaperAuthorityInvalid(
            "Paper Builder authority could not be opened",
            finding_kind="paper-root-invalid",
        ) from exc


def _close(service: PaperReleaseService) -> None:
    queue = service.cli_queue if isinstance(service, _PaperCLIService) else None
    if isinstance(queue, FilesystemWorkerQueue):
        queue.close()


def _payload(
    reference: ArtifactRef, release: PaperReleaseCandidate
) -> dict[str, object]:
    return {
        "release": release.model_dump(mode="json"),
        "release_reference": reference.model_dump(mode="json"),
    }


RootOption = Annotated[Path, typer.Option()]


@paper_app.command("build")
def paper_build(
    audit_reference: Annotated[
        Path | None, typer.Argument(help="Exact audit ArtifactRef JSON.")
    ] = None,
    draft_reference: Annotated[
        Path | None, typer.Argument(help="Exact draft ArtifactRef JSON.")
    ] = None,
    v031_root: Annotated[Path | None, typer.Option("--v031-root")] = None,
    paper_root: Annotated[Path | None, typer.Option("--paper-root")] = None,
    research_root: Annotated[Path | None, typer.Option("--research-root")] = None,
    revision_reference: Annotated[
        Path | None, typer.Option("--revision-reference")
    ] = None,
) -> None:
    """Build from one explicit current audit and draft reference."""
    service: PaperReleaseService | None = None
    try:
        audit_ref = _load_ref(audit_reference)
        draft_ref = _load_ref(draft_reference)
        revision_ref = (
            _load_ref(revision_reference) if revision_reference is not None else None
        )
        roots = _validated_roots(v031_root, paper_root, research_root)
        service = service_for_roots(*roots, create=True)
        release_ref = service.build(audit_ref, draft_ref, revision_ref=revision_ref)
        release = service.status(release_ref)
        _emit(_payload(release_ref, release))
    except PaperBuilderError as error:
        exit_code = 1 if error.finding_kind == "audit-findings-open" else 2
        _fail(error, exit_code=exit_code)
    finally:
        if service is not None:
            _close(service)


@paper_app.command("status")
def paper_status(
    release_reference: Annotated[
        Path | None, typer.Argument(help="Exact release ArtifactRef JSON.")
    ] = None,
    v031_root: Annotated[Path | None, typer.Option("--v031-root")] = None,
    paper_root: Annotated[Path | None, typer.Option("--paper-root")] = None,
    research_root: Annotated[Path | None, typer.Option("--research-root")] = None,
) -> None:
    """Read one explicit current release without recovery or publication."""
    service: PaperReleaseService | None = None
    try:
        release_ref = _load_ref(release_reference)
        roots = _validated_roots(v031_root, paper_root, research_root)
        service = service_for_roots(*roots, create=False)
        _emit(_payload(release_ref, service.status(release_ref)))
    except PaperBuilderError as error:
        _fail(error)
    finally:
        if service is not None:
            _close(service)


__all__ = ["paper_app", "service_for_roots"]

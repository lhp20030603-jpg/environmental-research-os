"""Descriptor-owning read-only composition for the factory CLI."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from envresearch.factory.authority import open_existing_research_authority
from envresearch.factory.design_resolver import V02ApprovedDesignResolver
from envresearch.factory.errors import FactoryAuthorityInvalid, FactoryError
from envresearch.factory.service import FactoryRunService
from envresearch.paper.cli import _close as close_paper_service
from envresearch.paper.cli import service_for_roots as paper_service_for_roots
from envresearch.paper.errors import PaperBuilderError
from envresearch.paper.release import PaperReleaseService
from envresearch.research.orchestrator import ResearchOrchestrator


class FactoryCLIService(FactoryRunService):
    """Facade owning only the descriptors opened by CLI composition."""

    def __init__(
        self,
        *,
        design_resolver: V02ApprovedDesignResolver,
        release_service: PaperReleaseService,
        orchestrator: ResearchOrchestrator,
    ) -> None:
        super().__init__(
            design_resolver=design_resolver, release_service=release_service
        )
        self._cli_orchestrator = orchestrator

    def close(self) -> None:
        close_paper_service(self.release_service)
        self._cli_orchestrator.close()


def compose_service(
    research: Path, v031: Path, paper: Path, factory: Path
) -> FactoryRunService:
    """Open all authorities read-only and close every partial composition."""
    orchestrator: ResearchOrchestrator | None = None
    release_service: PaperReleaseService | None = None
    owned = False
    try:
        design_root = research / "design"
        citation_root = research / "citation/research"
        orchestrator = open_existing_research_authority(design_root)
        release_service = paper_service_for_roots(
            v031, paper, citation_root, create=False
        )
        resolver = V02ApprovedDesignResolver(orchestrator, factory)
        service = FactoryCLIService(
            design_resolver=resolver,
            release_service=release_service,
            orchestrator=orchestrator,
        )
        owned = True
        return service
    except FactoryError:
        raise
    except (PaperBuilderError, OSError, TypeError, ValueError, ValidationError) as exc:
        raise FactoryAuthorityInvalid(
            "factory authorities could not be opened",
            finding_kind="factory-root-invalid",
        ) from exc
    finally:
        if not owned:
            if release_service is not None:
                close_paper_service(release_service)
            if orchestrator is not None:
                orchestrator.close()

"""Spawn-safe reconstruction and crash injection for factory writers."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.factory.authority import open_existing_research_authority
from envresearch.factory.design_resolver import V02ApprovedDesignResolver
from envresearch.factory.errors import FactoryError
from envresearch.factory.service import FactoryRunService
from envresearch.kernel.gates import GateDecision
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.argument_map import ArgumentMapService
from envresearch.paper.auditor import PaperAuditService
from envresearch.paper.citation_authority import LifecycleCitationAuthority
from envresearch.paper.draft_builder import DraftService
from envresearch.paper.ledger import ClaimLedgerService
from envresearch.paper.release import PaperReleaseService
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.citation_attestations import ProtectedCitationAttestations
from envresearch.research.workflow import ResearchRunConfig
from envresearch.workers.filesystem import PinnedRoot
from envresearch.workers.queue import FilesystemWorkerQueue


class ProcessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    research_root: Path
    citation_root: Path
    accepted_root: Path
    paper_root: Path
    factory_root: Path
    transition_ref: ArtifactRef
    analysis_ref: LocalAnalysisReference
    report: LocalAnalysisReport


@dataclass
class _Resolver:
    transition_ref: ArtifactRef
    analysis_ref: LocalAnalysisReference
    report: LocalAnalysisReport
    authority_root: Path

    def resolve(
        self, transition_ref: ArtifactRef
    ) -> tuple[tuple[LocalAnalysisReference, LocalAnalysisReport], ...]:
        if transition_ref != self.transition_ref:
            raise ValueError("accepted evidence authority changed")
        return ((self.analysis_ref, self.report),)

    def require_current(self, transition_ref: ArtifactRef) -> None:
        if transition_ref != self.transition_ref:
            raise ValueError("accepted evidence authority changed")

    @contextmanager
    def authority_lease(self) -> Iterator[None]:
        yield


@dataclass
class OpenedService:
    service: FactoryRunService
    design: Any
    citation_queue: FilesystemWorkerQueue

    def close(self) -> None:
        self.citation_queue.close()
        self.design.close()


def write_config(service: FactoryRunService, path: Path) -> Path:
    roots = dict(service.authority.root_manifest.roots)
    resolver = service.release_service.audit_service.ledger_service.resolver
    analysis_ref, report = resolver.reports[0]
    config = ProcessConfig(
        research_root=roots["research"],
        citation_root=roots["citation"],
        accepted_root=roots["accepted-evidence"],
        paper_root=roots["paper"],
        factory_root=roots["factory"],
        transition_ref=resolver.transition_ref,
        analysis_ref=analysis_ref,
        report=report,
    )
    path.write_text(config.model_dump_json(), encoding="utf-8")
    return path


def _citation_authority(
    root: Path,
) -> tuple[LifecycleCitationAuthority, FilesystemWorkerQueue]:
    pinned = PinnedRoot(root, create=False)
    try:
        config = ResearchRunConfig.model_validate_json(
            pinned.read_file(
                Path("research-run-config.json"),
                description="citation run config",
            )
        )
    finally:
        pinned.close()
    queue = FilesystemWorkerQueue.open_existing(root, require_producer_context=True)
    try:
        authority = LifecycleCitationAuthority(
            lifecycle=ResearchArtifactLifecycle(root, config.run_id),
            attestations=ProtectedCitationAttestations.open_existing(queue),
        )
        return authority, queue
    except BaseException:
        queue.close()
        raise


def open_service(config_path: str) -> OpenedService:
    config = ProcessConfig.model_validate_json(Path(config_path).read_bytes())
    design = open_existing_research_authority(config.research_root)
    citation_queue = None
    try:
        citations, citation_queue = _citation_authority(config.citation_root)
        resolver = _Resolver(
            config.transition_ref,
            config.analysis_ref,
            config.report,
            config.accepted_root,
        )
        ledger = ClaimLedgerService(
            registry=ExitRegistry(config.paper_root, create=False),
            resolver=resolver,
        )
        maps = ArgumentMapService(ledger_service=ledger)
        drafts = DraftService(map_service=maps, citation_authority=citations)
        releases = PaperReleaseService(
            audit_service=PaperAuditService(draft_service=drafts)
        )
        service = FactoryRunService(
            design_resolver=V02ApprovedDesignResolver(design, config.factory_root),
            release_service=releases,
        )
        return OpenedService(service, design, citation_queue)
    except BaseException:
        if citation_queue is not None:
            citation_queue.close()
        design.close()
        raise


def _exit_after(call: Any, event: Any, code: int) -> Any:
    call()
    event.set()
    os._exit(code)


def crash_design(
    config_path: str,
    plan_json: str,
    context_json: str,
    phase: str,
    attempting: Any,
    acquired: Any,
) -> None:
    opened = open_service(config_path)
    resolver = opened.service.design_resolver
    original_registry = resolver._registry
    original_flow = resolver._publish
    plan_ref = ArtifactRef.model_validate_json(plan_json)
    context_ref = ArtifactRef.model_validate_json(context_json)
    design_id = resolver._design_id(plan_ref, context_ref)

    def registry(*, create: bool) -> ExitRegistry:
        value = original_registry(create=create)
        original_publish = value.publish
        original_set = value.set_current

        def publish(*args: Any, **kwargs: Any) -> ArtifactRef:
            return (
                _exit_after(lambda: original_publish(*args, **kwargs), acquired, 91)
                if phase == "object"
                else original_publish(*args, **kwargs)
            )

        def set_current(subject: str, reference: ArtifactRef) -> None:
            original_set(subject, reference)
            if (phase == "prepared" and subject.endswith("-prepared")) or (
                phase == "commit" and subject == resolver._subject(design_id)
            ):
                acquired.set()
                os._exit(92 if phase == "prepared" else 93)

        value.publish = publish  # type: ignore[method-assign]
        value.set_current = set_current  # type: ignore[method-assign]
        return value

    def publish_flow(*args: Any, **kwargs: Any) -> ArtifactRef:
        result = original_flow(*args, **kwargs)
        if phase == "post-commit-final-check":
            acquired.set()
            os._exit(94)
        return result

    resolver._registry = registry  # type: ignore[method-assign]
    resolver._publish = publish_flow  # type: ignore[method-assign]
    attempting.set()
    resolver.build(plan_ref, context_ref)


def crash_operation(
    config_path: str,
    operation: str,
    phase: str,
    refs_json: str,
    decision_json: str | None,
    capability: str | None,
    attempting: Any,
    acquired: Any,
) -> None:
    opened = open_service(config_path)
    service = opened.service
    refs = tuple(ArtifactRef.model_validate(item) for item in json.loads(refs_json))
    if operation == "run":
        store = service.store
        publish_name, prepared, committed = (
            None,
            store.prepared_subject,
            store.committed_subject,
        )
        invoke = lambda: service.assemble(refs[0], refs[1])
    else:
        store = service._promotions.store
        publish_name = (
            "publish_context" if operation == "context" else "publish_promotion"
        )
        prepared = (
            store.context_prepared_subject
            if operation == "context"
            else store.promotion_prepared_subject
        )
        committed = (
            store.context_committed_subject
            if operation == "context"
            else store.promotion_committed_subject
        )
        invoke = (
            (lambda: service.request_promotion(refs[0], "factory-agent"))
            if operation == "context"
            else lambda: service.record_promotion(
                refs[0], GateDecision.model_validate_json(decision_json), capability
            )
        )
    original_publish = (
        store.registry.publish if publish_name is None else getattr(store, publish_name)
    )
    original_set = store.registry.set_current if operation == "run" else store.install

    def publish(*args: Any, **kwargs: Any) -> ArtifactRef:
        result = original_publish(*args, **kwargs)
        if phase == "object":
            acquired.set()
            os._exit(91)
        return result

    def install(
        subject: str, reference: ArtifactRef, *args: Any, **kwargs: Any
    ) -> None:
        original_set(subject, reference, *args, **kwargs)
        if subject == prepared and phase == "prepared":
            acquired.set()
            os._exit(92)
        if subject == committed and phase == "commit":
            acquired.set()
            os._exit(93)

    if publish_name is None:
        store.registry.publish = publish  # type: ignore[method-assign]
    else:
        setattr(store, publish_name, publish)
    if operation == "run":
        store.registry.set_current = install  # type: ignore[method-assign]
    else:
        store.install = install  # type: ignore[method-assign]
    attempting.set()
    invoke()
    if phase == "post-commit-final-check":
        acquired.set()
        os._exit(94)


def request_once(
    config_path: str,
    run_json: str,
    requester: str,
    attempting: Any,
    release: Any,
    results: Any,
) -> None:
    opened = open_service(config_path)
    try:
        attempting.set()
        release.wait(timeout=20)
        reference = opened.service.request_promotion(
            ArtifactRef.model_validate_json(run_json), requester
        )
        results.put(("ok", reference.model_dump_json()))
    except FactoryError as error:
        results.put(("error", type(error).__name__))
    finally:
        opened.close()


def decision_once(
    config_path: str,
    context_json: str,
    decision_json: str,
    capability: str,
    attempting: Any,
    release: Any,
    results: Any,
) -> None:
    opened = open_service(config_path)
    try:
        attempting.set()
        release.wait(timeout=20)
        reference = opened.service.record_promotion(
            ArtifactRef.model_validate_json(context_json),
            GateDecision.model_validate_json(decision_json),
            capability,
        )
        results.put(("ok", reference.model_dump_json()))
    except FactoryError as error:
        results.put(("error", type(error).__name__))
    finally:
        opened.close()

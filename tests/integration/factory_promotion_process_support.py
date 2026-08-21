"""Fork-process workers for governed factory-promotion contention tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from envresearch.econometrics.valuation_transition import publish_v031_transition
from envresearch.factory.service import FactoryRunService
from envresearch.kernel.gates import GateDecision
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.errors import PaperBuilderError
from envresearch.research.orchestrator import ResearchOrchestrator

_SERVICE: FactoryRunService | None = None
_DESIGN: ResearchOrchestrator | None = None
_CITATION: ResearchOrchestrator | None = None


def configure(
    service: FactoryRunService,
    design: ResearchOrchestrator,
    citation: ResearchOrchestrator,
) -> None:
    """Install fork-inherited authorities before creating child processes."""
    global _SERVICE, _DESIGN, _CITATION
    _SERVICE = service
    _DESIGN = design
    _CITATION = citation


def request_worker(
    run_json: str, requester: str, start: Any, results: Any
) -> None:
    start.wait()
    try:
        reference = _service().request_promotion(
            ArtifactRef.model_validate_json(run_json), requested_by=requester
        )
        results.put(("ok", reference.model_dump_json()))
    except Exception as error:  # noqa: BLE001 - child reports typed contention
        results.put((getattr(error, "code", type(error).__name__), str(error)))


def decision_worker(
    context_json: str,
    decision_json: str,
    capability: str,
    start: Any,
    results: Any,
) -> None:
    start.wait()
    try:
        reference = _service().record_promotion(
            ArtifactRef.model_validate_json(context_json),
            GateDecision.model_validate_json(decision_json),
            capability,
        )
        results.put(("ok", reference.model_dump_json()))
    except Exception as error:  # noqa: BLE001 - child reports typed contention
        results.put((getattr(error, "code", type(error).__name__), str(error)))


def crash_after_request_event(run_json: str) -> None:
    service = _service()
    promotions = _promotion_service(service)
    original = promotions.store.ensure_request_event

    def die(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        os._exit(91)

    promotions.store.ensure_request_event = die  # type: ignore[method-assign]
    service.request_promotion(
        ArtifactRef.model_validate_json(run_json), requested_by="factory-agent"
    )


def crash_after_decision_event(
    context_json: str, decision_json: str, capability: str
) -> None:
    service = _service()
    promotions = _promotion_service(service)
    original = promotions.store.ensure_decision_event

    def die(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        os._exit(92)

    promotions.store.ensure_decision_event = die  # type: ignore[method-assign]
    service.record_promotion(
        ArtifactRef.model_validate_json(context_json),
        GateDecision.model_validate_json(decision_json),
        capability,
    )


def held_promotion(
    context_json: str,
    decision_json: str,
    capability: str,
    entered: Any,
    release: Any,
    results: Any,
) -> None:
    service = _service()
    promotions = _promotion_service(service)
    original = promotions._record_locked

    def hold(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        if not release.wait(timeout=20):
            raise TimeoutError("promotion test release timed out")
        return original(*args, **kwargs)

    promotions._record_locked = hold  # type: ignore[method-assign]
    decision_worker(context_json, decision_json, capability, _AlreadySet(), results)


def design_revision_writer(capability: str, attempting: Any, results: Any) -> None:
    attempting.set()
    try:
        intent = _design().request_revision(
            "compose-plan", "concurrent authority probe", "human-reviewer", capability
        )
        results.put(("design", "ok", intent.revision_id))
    except Exception as error:  # noqa: BLE001 - genuine public mutation outcome
        results.put(("design", type(error).__name__, str(error)))


def citation_revision_writer(capability: str, attempting: Any, results: Any) -> None:
    attempting.set()
    try:
        intent = _citation().request_revision(
            "compose-plan", "concurrent authority probe", "human-reviewer", capability
        )
        results.put(("citation", "ok", intent.revision_id))
    except Exception as error:  # noqa: BLE001 - genuine public mutation outcome
        results.put(("citation", type(error).__name__, str(error)))


def paper_release_writer(
    release_json: str, attempting: Any, results: Any
) -> None:
    attempting.set()
    try:
        release = _service().release_service.store.load(
            ArtifactRef.model_validate_json(release_json)
        )
        reference = _service().release_service.build(
            release.audit_ref, release.draft_ref
        )
        results.put(("paper", "ok", reference.model_dump_json()))
    except PaperBuilderError as error:
        results.put(("paper", error.code, str(error)))
    except Exception as error:  # noqa: BLE001 - genuine public mutation outcome
        results.put(("paper", type(error).__name__, str(error)))


def v031_transition_writer(
    root: str,
    refs: tuple[str, ...],
    pack: str,
    attempting: Any,
    results: Any,
) -> None:
    attempting.set()
    parsed = tuple(ArtifactRef.model_validate_json(item) for item in refs)
    try:
        reference = publish_v031_transition(
            Path(root), manifest_ref=parsed[0], run_ref=parsed[1],
            catalog_binding_ref=parsed[2], catalog_ref=parsed[3],
            report_ref=parsed[4], runtime_relative_path=Path("reviewed/Rscript"),
            runtime_sha256="b" * 64, frozen_pack_root=Path(pack),
            frozen_pack_hash="c" * 64,
        )
        results.put(("v031", "ok", reference.model_dump_json()))
    except Exception as error:  # noqa: BLE001 - genuine public mutation outcome
        results.put(("v031", type(error).__name__, str(error)))


class _AlreadySet:
    def wait(self) -> bool:
        return True


def _service() -> FactoryRunService:
    assert _SERVICE is not None
    return _SERVICE


def _promotion_service(service: FactoryRunService) -> Any:
    return service._promotions


def _design() -> ResearchOrchestrator:
    assert _DESIGN is not None
    return _DESIGN


def _citation() -> ResearchOrchestrator:
    assert _CITATION is not None
    return _CITATION


__all__ = [
    "citation_revision_writer", "configure", "crash_after_decision_event",
    "crash_after_request_event", "decision_worker", "design_revision_writer",
    "held_promotion", "paper_release_writer", "request_worker",
    "v031_transition_writer",
]

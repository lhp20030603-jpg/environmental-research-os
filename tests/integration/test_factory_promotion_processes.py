"""Real-process promotion recovery, contention, and upstream-writer coverage."""

from __future__ import annotations

from contextlib import suppress
from multiprocessing import get_context
from pathlib import Path
from queue import Empty
from typing import Any

import pytest
from factory_promotion_process_support import (
    citation_revision_writer,
    configure,
    crash_after_decision_event,
    crash_after_request_event,
    decision_worker,
    design_revision_writer,
    held_promotion,
    paper_release_writer,
    request_worker,
    v031_transition_writer,
)
from test_factory_promotion import _capability, _decision
from test_factory_run import connected_factory

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.valuation_authority import valuation_authority_lease
from envresearch.factory.errors import FactoryError
from envresearch.factory.service import FactoryRunService
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import GateStatus


def _fork() -> Any:
    return get_context("fork")


def _join(process: Any, *, expected: int = 0) -> None:
    process.join(timeout=20)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        pytest.fail(f"child process {process.name} timed out")
    assert process.exitcode == expected


def _stop(processes: list[Any]) -> None:
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        with suppress(Exception):
            process.join(timeout=5)
        with suppress(Exception):
            process.close()


def _close_queue(queue: Any) -> None:
    if queue is not None:
        queue.cancel_join_thread()
        queue.close()


def test_separate_process_request_and_decision_writers_serialize(tmp_path: Path) -> None:
    fixture = connected_factory(tmp_path)
    processes: list[Any] = []
    results = None
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        configure(service, *fixture.orchestrators)
        context = _fork()
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(
                target=request_worker,
                args=(run_ref.model_dump_json(), "factory-agent", start, results),
            )
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        start.set()
        outcomes = [results.get(timeout=20) for _ in processes]
        for process in processes:
            _join(process)
        assert {item[0] for item in outcomes} == {"ok"}
        contexts = {ArtifactRef.model_validate_json(item[1]) for item in outcomes}
        assert len(contexts) == 1
        context_ref = contexts.pop()
        _close_queue(results)
        results = None

        start = context.Event()
        results = context.Queue()
        decision = _decision()
        processes = [
            context.Process(
                target=decision_worker,
                args=(
                    context_ref.model_dump_json(), decision.model_dump_json(),
                    _capability(fixture), start, results,
                ),
            )
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        start.set()
        outcomes = [results.get(timeout=20) for _ in processes]
        for process in processes:
            _join(process)
        assert {item[0] for item in outcomes} == {"ok"}
        promotions = {ArtifactRef.model_validate_json(item[1]) for item in outcomes}
        assert len(promotions) == 1
    finally:
        _stop(processes)
        _close_queue(results)
        fixture.close()


def test_real_process_crashes_recover_exact_intent_and_reject_opposite(
    tmp_path: Path,
) -> None:
    fixture = connected_factory(tmp_path)
    processes: list[Any] = []
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        configure(service, *fixture.orchestrators)
        context = _fork()
        request = context.Process(
            target=crash_after_request_event, args=(run_ref.model_dump_json(),)
        )
        processes = [request]
        request.start()
        _join(request, expected=91)

        recovered = FactoryRunService(
            design_resolver=service.design_resolver,
            release_service=service.release_service,
        )
        context_ref = recovered.request_promotion(
            run_ref, requested_by="factory-agent"
        )
        configure(recovered, *fixture.orchestrators)
        approved = _decision()
        decision = context.Process(
            target=crash_after_decision_event,
            args=(
                context_ref.model_dump_json(), approved.model_dump_json(),
                _capability(fixture),
            ),
        )
        processes = [decision]
        decision.start()
        _join(decision, expected=92)

        fresh = FactoryRunService(
            design_resolver=service.design_resolver,
            release_service=service.release_service,
        )
        rejected = approved.model_copy(update={"status": GateStatus.REJECTED})
        with pytest.raises(FactoryError) as caught:
            fresh.record_promotion(
                context_ref, rejected, principal_capability=_capability(fixture)
            )
        assert caught.value.finding_kind == "promotion-terminal"
        promotion_ref = fresh.record_promotion(
            context_ref, approved, principal_capability=_capability(fixture)
        )
        assert fresh.promotion_status(promotion_ref, run_ref).state == "promoted"
    finally:
        _stop(processes)
        fixture.close()


def test_real_public_upstream_writers_wait_for_promotion(tmp_path: Path) -> None:
    fixture = connected_factory(tmp_path)
    processes: list[Any] = []
    release = None
    results = None
    try:
        base = fixture.service
        run_root = (tmp_path / "v031-authority").resolve()
        runner = ExitRegistry(run_root / "runner")
        ExitRegistry(run_root / "evaluator")
        pack = run_root / "pack"
        pack.mkdir()
        resolver = base.release_service.audit_service.ledger_service.resolver
        resolver.authority_root = run_root
        resolver.authority_lease = lambda: valuation_authority_lease(runner)  # type: ignore[method-assign]
        service = FactoryRunService(
            design_resolver=base.design_resolver,
            release_service=base.release_service,
        )
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        configure(service, *fixture.orchestrators)

        context = _fork()
        entered = context.Event()
        release = context.Event()
        results = context.Queue()
        promotion = context.Process(
            target=held_promotion,
            args=(
                context_ref.model_dump_json(), _decision().model_dump_json(),
                _capability(fixture), entered, release, results,
            ),
        )
        processes = [promotion]
        promotion.start()
        assert entered.wait(timeout=20)

        attempts = [context.Event() for _ in range(4)]
        design_capability = (
            fixture.orchestrators[0].queue.control.path
            / "principals/revision.capability"
        ).read_text().strip()
        citation_capability = (
            fixture.orchestrators[1].queue.control.path
            / "principals/revision.capability"
        ).read_text().strip()
        dummy = tuple(
            ArtifactRef(
                artifact_id=name, artifact_version=1, content_hash="a" * 64
            ).model_dump_json()
            for name in ("manifest", "run", "binding", "catalog", "report")
        )
        writers = [
            context.Process(
                target=design_revision_writer,
                args=(design_capability, attempts[0], results),
            ),
            context.Process(
                target=v031_transition_writer,
                args=(str(run_root), dummy, str(pack), attempts[1], results),
            ),
            context.Process(
                target=citation_revision_writer,
                args=(citation_capability, attempts[2], results),
            ),
            context.Process(
                target=paper_release_writer,
                args=(fixture.release_ref.model_dump_json(), attempts[3], results),
            ),
        ]
        processes.extend(writers)
        for process in writers:
            process.start()
        assert all(event.wait(timeout=20) for event in attempts)
        with pytest.raises(Empty):
            results.get(timeout=0.5)

        release.set()
        outcomes = [results.get(timeout=20) for _ in range(5)]
        for process in processes:
            _join(process)
        assert {item[0] for item in outcomes} == {
            "ok", "design", "v031", "citation", "paper"
        }
    finally:
        if release is not None:
            release.set()
        _stop(processes)
        _close_queue(results)
        fixture.close()

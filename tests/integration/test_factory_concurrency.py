"""Spawn-only factory crash recovery and writer convergence."""

from __future__ import annotations

import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import pytest
from factory_process_fixtures import (
    crash_design,
    crash_operation,
    decision_once,
    request_once,
    write_config,
)
from test_factory_promotion import _capability
from test_factory_run import connected_factory

from envresearch.kernel.gates import GateDecision
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import GateStatus

PHASES = ("object", "prepared", "commit", "post-commit-final-check")


def _stop(processes: list[Any]) -> None:
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        with suppress(Exception):
            process.join(timeout=5)
        with suppress(Exception):
            process.close()


def _crash(target: Any, args: tuple[object, ...], expected: int) -> None:
    context = get_context("spawn")
    attempting = context.Event()
    acquired = context.Event()
    process = context.Process(target=target, args=(*args, attempting, acquired))
    processes = [process]
    try:
        process.start()
        assert attempting.wait(timeout=20)
        assert acquired.wait(timeout=20)
        process.join(timeout=20)
        assert not process.is_alive()
        assert process.exitcode == expected
    finally:
        _stop(processes)


def _all_pointers(fixture: Any) -> tuple[ArtifactRef | None, ...]:
    resolver = fixture.service.design_resolver
    handoff = resolver.resolve(fixture.design_ref)
    registry = resolver._registry(create=False)
    return (
        registry.current(resolver._prepared_subject(handoff.design_id)),
        registry.current(resolver._subject(handoff.design_id)),
        fixture.service.store.prepared(),
        fixture.service.store.committed(),
        fixture.service._promotions.store.context_prepared(),
        fixture.service._promotions.store.context_committed(),
        fixture.service._promotions.store.promotion_prepared(),
        fixture.service._promotions.store.promotion_committed(),
    )


@pytest.mark.parametrize("phase", PHASES)
def test_design_writer_process_death_recovers_exact_handoff(
    tmp_path: Path, phase: str
) -> None:
    """Catch any design publication window returning before a final exact reopen."""
    fixture = connected_factory(tmp_path)
    try:
        config = write_config(fixture.service, tmp_path / "process.json")
        handoff = fixture.service.design_resolver.resolve(fixture.design_ref)
        resolver = fixture.service.design_resolver
        registry = resolver._registry(create=False)
        for subject in (
            resolver._prepared_subject(handoff.design_id),
            resolver._subject(handoff.design_id),
        ):
            (registry.root / "exit/current" / f"{subject}.json").unlink()
        _crash(
            crash_design,
            (
                str(config),
                handoff.plan_ref.model_dump_json(),
                handoff.final_context_ref.model_dump_json(),
                phase,
            ),
            PHASES.index(phase) + 91,
        )

        registry = fixture.service.design_resolver._registry(create=False)
        prepared = registry.current(
            fixture.service.design_resolver._prepared_subject(handoff.design_id)
        )
        committed = registry.current(
            fixture.service.design_resolver._subject(handoff.design_id)
        )
        if phase == "object":
            assert prepared is committed is None
        elif phase == "prepared":
            assert prepared is not None and committed is None
        else:
            assert prepared == committed == fixture.design_ref

        assert (
            fixture.service.design_resolver.build(
                handoff.plan_ref, handoff.final_context_ref
            )
            == fixture.design_ref
        )
    finally:
        fixture.close()


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("operation", ("run", "context", "promotion"))
def test_factory_writer_process_death_recovers_same_exact_reference(
    tmp_path: Path, operation: str, phase: str
) -> None:
    """Catch torn object/pointer/final-check windows changing exact identities."""
    fixture = connected_factory(tmp_path)
    try:
        config = write_config(fixture.service, tmp_path / "process.json")
        decision = GateDecision(
            status=GateStatus.APPROVED,
            decided_by="human-reviewer",
            rationale="Reviewed the exact governed run.",
            decided_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        if operation == "run":
            refs = (fixture.design_ref, fixture.release_ref)
        else:
            run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
            refs = (run_ref,)
            if operation == "promotion":
                refs = (
                    fixture.service.request_promotion(
                        run_ref, requested_by="factory-agent"
                    ),
                )
        _crash(
            crash_operation,
            (
                str(config),
                operation,
                phase,
                json.dumps([item.model_dump(mode="json") for item in refs]),
                decision.model_dump_json() if operation == "promotion" else None,
                _capability(fixture) if operation == "promotion" else None,
            ),
            PHASES.index(phase) + 91,
        )

        if operation == "run":
            prepared = fixture.service.store.prepared()
            committed = fixture.service.store.committed()
        elif operation == "context":
            prepared = fixture.service._promotions.store.context_prepared()
            committed = fixture.service._promotions.store.context_committed()
        else:
            prepared = fixture.service._promotions.store.promotion_prepared()
            committed = fixture.service._promotions.store.promotion_committed()
        if phase == "object":
            assert prepared is committed is None
        elif phase == "prepared":
            assert prepared is not None and committed is None
        else:
            assert prepared is not None and prepared == committed

        if operation == "run":
            recovered = fixture.service.assemble(*refs)
            assert fixture.service.status(recovered).run_ref == recovered
        elif operation == "context":
            recovered = fixture.service.request_promotion(
                refs[0], requested_by="factory-agent"
            )
            assert fixture.service._promotions.store.context_current() == recovered
        else:
            recovered = fixture.service.record_promotion(
                refs[0], decision, principal_capability=_capability(fixture)
            )
            assert fixture.service._promotions.store.promotion_current() == recovered
    finally:
        fixture.close()


def test_spawned_identical_writers_converge_and_conflict_preserves_pointer(
    tmp_path: Path,
) -> None:
    """Catch process serialization accepting a conflicting replacement context."""
    fixture = connected_factory(tmp_path)
    processes: list[Any] = []
    results = None
    try:
        config = write_config(fixture.service, tmp_path / "process.json")
        run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        context = get_context("spawn")
        release = context.Event()
        attempts = [context.Event(), context.Event()]
        results = context.Queue()
        processes = [
            context.Process(
                target=request_once,
                args=(
                    str(config),
                    run_ref.model_dump_json(),
                    "factory-agent",
                    attempts[index],
                    release,
                    results,
                ),
            )
            for index in range(2)
        ]
        for process in processes:
            process.start()
        assert all(event.wait(timeout=20) for event in attempts)
        release.set()
        outcomes = [results.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
        assert [status for status, _ in outcomes] == ["ok", "ok"]
        references = {
            ArtifactRef.model_validate_json(value)
            for status, value in outcomes
            if status == "ok"
        }
        assert len(references) == 1
        original = references.pop()
        pointers_before_request_conflict = _all_pointers(fixture)

        attempt = context.Event()
        release = context.Event()
        conflict = context.Process(
            target=request_once,
            args=(
                str(config),
                run_ref.model_dump_json(),
                "other-agent",
                attempt,
                release,
                results,
            ),
        )
        processes.append(conflict)
        conflict.start()
        assert attempt.wait(timeout=20)
        release.set()
        assert results.get(timeout=20)[0] == "error"
        conflict.join(timeout=20)
        assert conflict.exitcode == 0
        assert fixture.service._promotions.store.context_current() == original
        assert _all_pointers(fixture) == pointers_before_request_conflict

        decision = GateDecision(
            status=GateStatus.APPROVED,
            decided_by="human-reviewer",
            rationale="Reviewed the exact governed run.",
            decided_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        promotion = fixture.service.record_promotion(
            original, decision, principal_capability=_capability(fixture)
        )
        pointers_before = _all_pointers(fixture)
        assert pointers_before[-2:] == (promotion, promotion)
        rejected = decision.model_copy(update={"status": GateStatus.REJECTED})
        attempt = context.Event()
        release = context.Event()
        promotion_conflict = context.Process(
            target=decision_once,
            args=(
                str(config),
                original.model_dump_json(),
                rejected.model_dump_json(),
                _capability(fixture),
                attempt,
                release,
                results,
            ),
        )
        processes.append(promotion_conflict)
        promotion_conflict.start()
        assert attempt.wait(timeout=20)
        release.set()
        assert results.get(timeout=20)[0] == "error"
        promotion_conflict.join(timeout=20)
        assert promotion_conflict.exitcode == 0
        assert _all_pointers(fixture) == pointers_before
    finally:
        if results is not None:
            results.cancel_join_thread()
            results.close()
        _stop(processes)
        fixture.close()

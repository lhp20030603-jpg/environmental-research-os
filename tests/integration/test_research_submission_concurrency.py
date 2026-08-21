"""Acceptance transaction recovery and contention regressions."""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest
from orchestrator_fixtures import (
    broad_brief,
    candidate_payload,
    config,
    revision_capability,
    submit,
)

from envresearch.models.intake import ResearchIntakeMode
from envresearch.research.orchestrator import ResearchOrchestrator


def _accept_in_process(root: str, barrier: object, results: object) -> None:
    orchestrator = ResearchOrchestrator()
    try:
        orchestrator.initialize(
            config(Path(root), ResearchIntakeMode.BROAD_TOPIC), broad_brief()
        )
        barrier.wait()  # type: ignore[attr-defined]
        summary = orchestrator.accept_submission("frame-charters")
        results.put(("ok", tuple(summary.completed_nodes)))  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001 - child reports failure to parent
        results.put(("error", type(error).__name__, str(error)))  # type: ignore[attr-defined]
    finally:
        orchestrator.close()


def _advance_in_process(root: str, barrier: object, results: object) -> None:
    orchestrator = ResearchOrchestrator()
    try:
        orchestrator.initialize(
            config(Path(root), ResearchIntakeMode.BROAD_TOPIC), broad_brief()
        )
        barrier.wait()  # type: ignore[attr-defined]
        summary = orchestrator.advance()
        results.put(("ok", tuple(summary.pending_gate_ids)))  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001 - child reports failure to parent
        results.put(("error", type(error).__name__, str(error)))  # type: ignore[attr-defined]
    finally:
        orchestrator.close()


def _submit_in_process(
    root: str, source: str, context_id: str, barrier: object, results: object
) -> None:
    orchestrator = ResearchOrchestrator()
    try:
        orchestrator.initialize(
            config(Path(root), ResearchIntakeMode.BROAD_TOPIC), broad_brief()
        )
        barrier.wait()  # type: ignore[attr-defined]
        order = orchestrator.queue.read_order("frame-charters")
        orchestrator.queue.submit(
            "frame-charters",
            Path(source),
            expected_order_hash=order.order_hash,
        )
        results.put(("ok", context_id))  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001 - child reports failure to parent
        results.put(("error", type(error).__name__, str(error)))  # type: ignore[attr-defined]
    finally:
        orchestrator.close()


def _die_at_acceptance_boundary(root: str, boundary: int) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(Path(root), ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    if boundary < 4:
        real_write = orchestrator.lifecycle.store.write_structured
        writes = 0

        def die_after_write(path: Path, artifact: object) -> object:
            nonlocal writes
            result = real_write(path, artifact)  # type: ignore[arg-type]
            writes += 1
            if writes == boundary:
                os._exit(70 + boundary)
            return result

        orchestrator.lifecycle.store.write_structured = die_after_write  # type: ignore[method-assign]
    else:
        orchestrator.checkpoints.publish = lambda *args, **kwargs: os._exit(74)  # type: ignore[method-assign]
    orchestrator.accept_submission("frame-charters")


def _revise_with_pause(
    root: str, entered: object, release: object, results: object
) -> None:
    orchestrator = ResearchOrchestrator()
    try:
        orchestrator.initialize(
            config(Path(root), ResearchIntakeMode.BROAD_TOPIC), broad_brief()
        )
        real = orchestrator.lifecycle.supersede

        def paused(*args: object, **kwargs: object) -> object:
            entered.set()  # type: ignore[attr-defined]
            if not release.wait(10):  # type: ignore[attr-defined]
                raise TimeoutError("revision pause was not released")
            return real(*args, **kwargs)  # type: ignore[misc]

        orchestrator.lifecycle.supersede = paused  # type: ignore[method-assign]
        revision = orchestrator.request_revision(
            "frame-charters",
            reason="Concurrent mutation probe",
            actor="researcher",
            principal_capability=revision_capability(orchestrator),
        )
        results.put(("revision", revision.revision_id))  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001 - child reports failure to parent
        results.put(("error", type(error).__name__, str(error)))  # type: ignore[attr-defined]
    finally:
        orchestrator.close()


def _advance_without_barrier(root: str, results: object) -> None:
    orchestrator = ResearchOrchestrator()
    try:
        orchestrator.initialize(
            config(Path(root), ResearchIntakeMode.BROAD_TOPIC), broad_brief()
        )
        summary = orchestrator.advance()
        results.put(("advance", tuple(summary.pending_work_order_nodes)))  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001 - child reports failure to parent
        results.put(("error", type(error).__name__, str(error)))  # type: ignore[attr-defined]
    finally:
        orchestrator.close()


def test_identical_completed_submission_retry_recovers_as_success(
    tmp_path: Path,
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())

    summary = orchestrator.accept_submission("frame-charters")

    assert "frame-charters" in summary.completed_nodes
    assert orchestrator.checkpoints.verify(
        orchestrator._nodes["frame-charters"],
        orchestrator.lifecycle.input_refs(orchestrator._nodes["frame-charters"]),
    )


def test_checkpoint_retry_repairs_artifact_published_before_checkpoint(
    tmp_path: Path, monkeypatch
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    node = orchestrator._nodes["frame-charters"]
    real_publish = orchestrator.checkpoints.publish

    def die_before_checkpoint(*args: object, **kwargs: object) -> object:
        raise RuntimeError("process died before checkpoint")

    monkeypatch.setattr(orchestrator.checkpoints, "publish", die_before_checkpoint)
    try:
        submit(orchestrator, "frame-charters", candidate_payload())
    except RuntimeError as error:
        assert "before checkpoint" in str(error)
    else:  # pragma: no cover
        raise AssertionError("simulated process death did not occur")
    monkeypatch.setattr(orchestrator.checkpoints, "publish", real_publish)

    summary = orchestrator.accept_submission("frame-charters")

    assert "frame-charters" in summary.completed_nodes
    assert orchestrator.checkpoints.verify(
        node, orchestrator.lifecycle.input_refs(node)
    )


def test_identical_multiprocess_acceptance_is_deterministic(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    filename = "candidate-charters.json"
    source = tmp_path / "incoming" / filename
    source.parent.mkdir()
    source.write_text(
        json.dumps(candidate_payload().model_dump(mode="json"), sort_keys=True)
    )
    order = orchestrator.queue.read_order("frame-charters")
    orchestrator.queue.submit(
        "frame-charters", source, expected_order_hash=order.order_hash
    )
    orchestrator.close()
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_accept_in_process, args=(str(tmp_path), barrier, results)
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)

    outcomes = [results.get(timeout=2) for _ in processes]
    assert all(process.exitcode == 0 for process in processes)
    assert outcomes == [
        ("ok", ("frame-charters",)),
        ("ok", ("frame-charters",)),
    ]


def test_simultaneous_advance_reuses_one_gate_context(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.close()
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_advance_in_process, args=(str(tmp_path), barrier, results)
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)

    outcomes = [results.get(timeout=2) for _ in processes]
    assert all(process.exitcode == 0 for process in processes)
    assert outcomes == [("ok", ("gate-1",)), ("ok", ("gate-1",))]
    assert len(list((tmp_path / "gate-contexts/gate-1").glob("*.json"))) == 1


def test_conflicting_multiprocess_submissions_fail_without_residue(
    tmp_path: Path,
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    orchestrator.close()
    sources: list[Path] = []
    for index in range(2):
        source = tmp_path / f"incoming-{index}/candidate-charters.json"
        source.parent.mkdir()
        payload = candidate_payload().model_dump(mode="json")
        payload["brief"]["broad_topic"] = f"Conflicting topic {index}"
        source.write_text(json.dumps(payload, sort_keys=True))
        sources.append(source)
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_submit_in_process,
            args=(str(tmp_path), str(source), f"context-{index}", barrier, results),
        )
        for index, source in enumerate(sources)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)

    outcomes = [results.get(timeout=2) for _ in processes]
    assert sum(item[0] == "ok" for item in outcomes) == 1
    assert sum(item[0] == "error" for item in outcomes) == 1
    recovered = ResearchOrchestrator()
    recovered.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submissions = recovered.queue.collect("frame-charters")
    assert len(submissions) == 1


def test_revision_and_advance_share_one_run_mutation_lock(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.close()
    context = multiprocessing.get_context("fork")
    entered = context.Event()
    release = context.Event()
    results = context.Queue()
    revision_process = context.Process(
        target=_revise_with_pause,
        args=(str(tmp_path), entered, release, results),
    )
    revision_process.start()
    assert entered.wait(10)
    advance_process = context.Process(
        target=_advance_without_barrier, args=(str(tmp_path), results)
    )
    advance_process.start()
    advance_process.join(0.25)
    assert advance_process.is_alive()
    release.set()
    revision_process.join(20)
    advance_process.join(20)

    outcomes = [results.get(timeout=2), results.get(timeout=2)]
    assert all(item[0] != "error" for item in outcomes)
    revision_id = next(item[1] for item in outcomes if item[0] == "revision")
    order = json.loads((tmp_path / "work-orders/frame-charters.json").read_text())
    assert revision_id in order["node_version"]


@pytest.mark.parametrize("boundary", (1, 2, 3, 4))
def test_real_process_death_recovers_each_acceptance_boundary(
    tmp_path: Path, boundary: int
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    source = tmp_path / "incoming/candidate-charters.json"
    source.parent.mkdir()
    source.write_text(
        json.dumps(candidate_payload().model_dump(mode="json"), sort_keys=True)
    )
    order = orchestrator.queue.read_order("frame-charters")
    orchestrator.queue.submit(
        "frame-charters", source, expected_order_hash=order.order_hash
    )
    orchestrator.close()
    context = multiprocessing.get_context("fork")
    process = context.Process(
        target=_die_at_acceptance_boundary, args=(str(tmp_path), boundary)
    )
    process.start()
    process.join(20)
    assert process.exitcode == 70 + boundary

    recovered = ResearchOrchestrator()
    recovered.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    assert (
        "frame-charters"
        in recovered.accept_submission("frame-charters").completed_nodes
    )

"""Cross-process serialization for optional literature coverage binding."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

from orchestrator_fixtures import (
    approve,
    broad_brief,
    candidate_payload,
    config,
    submit,
)

from envresearch.connectors.contracts import ConnectorCoverage
from envresearch.models.intake import ResearchIntakeMode
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.workers.contracts import WorkOrder


def _coverage() -> ConnectorCoverage:
    return ConnectorCoverage(
        connector_id="repository-local-literature",
        connector_version="1.0",
        status="degraded",
        records=(),
        reason_code="CONNECTOR_UNAVAILABLE",
        connector_reason_code="EXPORT_MISSING",
        diagnostic="local literature export is unavailable",
    )


def _bind_with_pause(
    root: str, entered: object, release: object, results: object
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator._issue_ready = lambda: None  # type: ignore[method-assign]
    try:
        orchestrator.initialize(
            config(Path(root), ResearchIntakeMode.BROAD_TOPIC), broad_brief()
        )
        real_persist = orchestrator.lifecycle.persist_structured

        def paused(*args: object, **kwargs: object) -> object:
            entered.set()  # type: ignore[attr-defined]
            if not release.wait(10):  # type: ignore[attr-defined]
                raise TimeoutError("coverage pause was not released")
            return real_persist(*args, **kwargs)  # type: ignore[misc]

        orchestrator.lifecycle.persist_structured = paused  # type: ignore[method-assign]
        orchestrator.bind_literature_coverage(_coverage())
        results.put(("bind", "ok"))  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001 - child reports failure to parent
        results.put(("error", type(error).__name__, str(error)))  # type: ignore[attr-defined]
    finally:
        orchestrator.close()


def _advance(root: str, results: object) -> None:
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


def test_coverage_binding_serializes_with_ready_order_issuance(
    tmp_path: Path,
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    approve(orchestrator, "gate-1", selected_candidate_id="charter-air")
    with orchestrator.queue.control.transaction_lock("mutation"):
        orchestrator._apply_gate_one()
    assert not (tmp_path / "work-orders/map-literature.json").exists()
    orchestrator.close()

    context = multiprocessing.get_context("fork")
    entered = context.Event()
    release = context.Event()
    results = context.Queue()
    binder = context.Process(
        target=_bind_with_pause,
        args=(str(tmp_path), entered, release, results),
    )
    binder.start()
    assert entered.wait(10)
    advancer = context.Process(target=_advance, args=(str(tmp_path), results))
    advancer.start()
    advancer.join(0.25)
    assert advancer.is_alive()
    release.set()
    binder.join(20)
    advancer.join(20)

    outcomes = [results.get(timeout=2), results.get(timeout=2)]
    assert all(item[0] != "error" for item in outcomes)
    assert binder.exitcode == advancer.exitcode == 0
    recovered = ResearchOrchestrator()
    recovered.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    node = recovered._nodes["map-literature"]
    order = WorkOrder.model_validate_json(
        (tmp_path / "work-orders/map-literature.json").read_bytes()
    )
    assert len(order.input_artifacts) == 2
    assert order.input_artifacts == recovered.lifecycle.input_refs(node)

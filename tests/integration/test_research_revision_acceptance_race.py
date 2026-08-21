"""Ancestor revision versus descendant acceptance serialization regression."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

from orchestrator_fixtures import (
    approve,
    broad_brief,
    candidate_payload,
    config,
    estimand,
    literature,
    revision_capability,
    safe_feasibility,
    submit,
)

from envresearch.models.intake import ResearchIntakeMode
from envresearch.research.orchestrator import ResearchOrchestrator


def _accept_define_with_pause(
    root: str, entered: object, release: object, results: object
) -> None:
    orchestrator = ResearchOrchestrator()
    try:
        orchestrator.initialize(
            config(Path(root), ResearchIntakeMode.BROAD_TOPIC), broad_brief()
        )
        real_publish = orchestrator.checkpoints.publish

        def paused(node: object, *args: object, **kwargs: object) -> object:
            if getattr(node, "node_id", None) == "define-estimand":
                entered.set()  # type: ignore[attr-defined]
                if not release.wait(10):  # type: ignore[attr-defined]
                    raise TimeoutError("acceptance pause was not released")
            return real_publish(node, *args, **kwargs)  # type: ignore[arg-type]

        orchestrator.checkpoints.publish = paused  # type: ignore[method-assign]
        summary = orchestrator.accept_submission("define-estimand")
        results.put(("accept", tuple(summary.completed_nodes)))  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001 - child reports failure to parent
        results.put(("error", type(error).__name__, str(error)))  # type: ignore[attr-defined]
    finally:
        orchestrator.close()


def _revise_map(root: str, results: object) -> None:
    orchestrator = ResearchOrchestrator()
    try:
        orchestrator.initialize(
            config(Path(root), ResearchIntakeMode.BROAD_TOPIC), broad_brief()
        )
        revision = orchestrator.request_revision(
            "map-literature",
            reason="Refresh evidence",
            actor="researcher",
            principal_capability=revision_capability(orchestrator),
        )
        results.put(("revision", revision.revision_id, revision.affected_nodes))  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001 - child reports failure to parent
        results.put(("error", type(error).__name__, str(error)))  # type: ignore[attr-defined]
    finally:
        orchestrator.close()


def test_ancestor_revision_waits_for_descendant_acceptance(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    approve(orchestrator, "gate-1", selected_candidate_id="charter-air")
    orchestrator.advance()
    submit(orchestrator, "map-literature", literature())
    submit(orchestrator, "inspect-data", safe_feasibility())
    orchestrator.advance()
    candidate = tmp_path / "incoming/estimand-spec.yaml"
    candidate.parent.mkdir(exist_ok=True)
    candidate.write_text(json.dumps(estimand().model_dump(mode="json"), sort_keys=True))
    order = orchestrator.queue.read_order("define-estimand")
    orchestrator.queue.submit(
        "define-estimand", candidate, expected_order_hash=order.order_hash
    )
    orchestrator.close()

    context = multiprocessing.get_context("fork")
    entered = context.Event()
    release = context.Event()
    results = context.Queue()
    acceptance = context.Process(
        target=_accept_define_with_pause,
        args=(str(tmp_path), entered, release, results),
    )
    acceptance.start()
    assert entered.wait(10)
    revision = context.Process(target=_revise_map, args=(str(tmp_path), results))
    revision.start()
    revision.join(0.25)
    assert revision.is_alive()
    release.set()
    acceptance.join(20)
    revision.join(20)

    outcomes = [results.get(timeout=2), results.get(timeout=2)]
    assert all(item[0] != "error" for item in outcomes)
    revised = next(item for item in outcomes if item[0] == "revision")
    assert "define-estimand" in revised[2]
    reopened = ResearchOrchestrator()
    summary = reopened.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    assert "define-estimand" not in summary.completed_nodes
    order = json.loads((tmp_path / "work-orders/map-literature.json").read_text())
    assert revised[1] in order["node_version"]

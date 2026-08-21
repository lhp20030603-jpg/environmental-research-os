"""Enrollment-authorized isolated order publication."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from envresearch.models.principal import PrincipalKind
from envresearch.research.order_policy import (
    canonical_blind_json,
    extend_isolated_workspace,
    publish_isolated_workspace,
)
from envresearch.workers.contracts import WorkerRole, WorkOrder
from envresearch.workers.control import serialize_model
from envresearch.workers.queue import FilesystemWorkerQueue

if TYPE_CHECKING:
    from envresearch.research.principal_registry import PrincipalRegistry


def issue_isolated_order(
    queue: FilesystemWorkerQueue,
    workspace: Path,
    order: WorkOrder,
    inputs: tuple[tuple[str, object], ...],
    *,
    enrollment_registry: PrincipalRegistry,
    case_id: str,
) -> tuple[str, ...]:
    _authorize_queue(queue, workspace, order, enrollment_registry, case_id)
    if len({name for name, _ in inputs}) != len(inputs):
        raise ValueError("isolated input filenames must be unique")
    documents = tuple(
        (name, canonical_blind_json(payload)) for name, payload in inputs
    ) + (("work-order.json", serialize_model(order)),)
    publish_isolated_workspace(workspace, documents)
    queue.issue(order)
    return tuple(sorted(name for name, _ in documents))


def issue_isolated_extension(
    queue: FilesystemWorkerQueue,
    workspace: Path,
    order: WorkOrder,
    filename: str,
    existing_inputs: tuple[tuple[str, object], ...],
    *,
    enrollment_registry: PrincipalRegistry,
    case_id: str,
) -> None:
    _authorize_queue(queue, workspace, order, enrollment_registry, case_id)
    existing = tuple(
        (
            name,
            canonical_blind_json(
                payload.model_dump(mode="json")
                if isinstance(payload, BaseModel)
                else payload
            ),
        )
        for name, payload in existing_inputs
    )
    extend_isolated_workspace(
        workspace,
        ((filename, serialize_model(order)),),
        existing,
        lambda: queue.issue(order),
        lambda: _rollback_isolated_order(queue, order),
    )


def _authorize_queue(
    queue: FilesystemWorkerQueue,
    workspace: Path,
    order: WorkOrder,
    registry: PrincipalRegistry,
    case_id: str,
) -> None:
    from envresearch.benchmarks.blind_enrollment_marker import (
        require_frozen_enrollment,
    )

    control = registry.control.path
    queue_base = control.parents[1]
    if control != queue_base / "recommender" / case_id:
        raise ValueError("enrollment registry does not match the blind run case")
    try:
        target = queue.control.path.relative_to(queue_base)
    except ValueError as error:
        raise ValueError("order queue does not match the enrolled blind run") from error
    run_root = queue_base.parents[1]
    routes = {
        Path("recommender") / case_id: (
            WorkerRole.BENCHMARK_RECOMMENDER,
            PrincipalKind.RECOMMENDER,
            None,
            run_root / "isolated/recommender" / case_id,
        ),
        Path("expert") / case_id / "1": (
            WorkerRole.BENCHMARK_EXPERT,
            PrincipalKind.EXPERT,
            1,
            run_root / "isolated/expert" / case_id / "1",
        ),
        Path("expert") / case_id / "2": (
            WorkerRole.BENCHMARK_EXPERT,
            PrincipalKind.EXPERT,
            2,
            run_root / "isolated/expert" / case_id / "2",
        ),
        Path("adjudicator") / case_id: (
            WorkerRole.BENCHMARK_ADJUDICATOR,
            PrincipalKind.ADJUDICATOR,
            1,
            run_root / "isolated/adjudicator" / case_id,
        ),
    }
    route = routes.get(target)
    if route is None:
        raise ValueError("order queue does not match the enrolled blind run")
    role, kind, slot, expected_workspace = route
    if workspace != expected_workspace or order.role is not role:
        raise ValueError("order workspace or role does not match the enrolled blind run")
    if order.principal_assignment is None:
        raise ValueError("authenticated blind order assignment is required")
    from envresearch.benchmarks.blind_principal_auth import PrincipalAuthenticator

    PrincipalAuthenticator(registry).require_assignment(
        case_id, order.principal_assignment, kind, slot
    )
    require_frozen_enrollment(registry, case_id)


def _rollback_isolated_order(queue: FilesystemWorkerQueue, order: WorkOrder) -> None:
    data = serialize_model(order)
    public = Path("work-orders") / f"{order.order_id}.json"
    protected = Path("orders") / f"{order.order_id}.json"
    if not (queue.exchange.exists(public) or queue.control.storage.exists(protected)):
        return
    with queue.control.order_lock(order.order_id):
        queue.control.ensure_order(order, data)
        if not queue.exchange.exists(public):
            queue.exchange.write_file_noreplace(public, data, mode=0o600)
    base = f"failed-{cast(str, order.order_hash)[:12]}"
    attempt = 1
    while True:
        revision = base if attempt == 1 else f"{base}-{attempt}"
        try:
            queue.archive_generation(order.order_id, revision, allow_cancellation=True)
        except RuntimeError as error:
            if "revision archive namespace collision" not in str(error):
                raise
            attempt += 1
        else:
            return

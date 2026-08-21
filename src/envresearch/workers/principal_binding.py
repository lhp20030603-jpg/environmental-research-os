"""Worker-order principal and generation binding helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from envresearch.models.artifact import ProducerIdentity
from envresearch.workers.contracts import (
    WorkOrder,
    require_safe_order_id,
    revalidate_producer_identity,
)

if TYPE_CHECKING:
    from envresearch.workers.queue import FilesystemWorkerQueue


def assigned_producer(
    order: WorkOrder,
    claimed: ProducerIdentity | None,
    expected_order_hash: str | None,
    *,
    default: ProducerIdentity,
    require_context: bool,
) -> ProducerIdentity:
    """Resolve only a scheduler assignment or a legacy queue identity."""
    assignment = order.principal_assignment
    if assignment is not None:
        if expected_order_hash != order.order_hash:
            raise ValueError("submission uses a superseded work order")
        assigned = revalidate_producer_identity(assignment.producer)
        if claimed is not None and revalidate_producer_identity(claimed) != assigned:
            raise ValueError("submission producer does not match assigned principal")
        return assigned
    durable = revalidate_producer_identity(claimed or default)
    if require_context and durable.context_id is None:
        raise ValueError("queue producer requires an authenticated context_id")
    return durable


def has_generation(queue: FilesystemWorkerQueue, order_id: str) -> bool:
    """Authenticate matching public/protected state for a current generation."""
    require_safe_order_id(order_id)
    public = queue.exchange.exists(Path("work-orders") / f"{order_id}.json")
    protected = queue.control.storage.exists(Path("orders") / f"{order_id}.json")
    if public != protected:
        raise ValueError("worker generation order state is incomplete")
    if public:
        queue.read_order(order_id)
    return public

"""Descriptor-relative archival of superseded worker queue generations."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from envresearch.workers.contracts import (
    WorkerSubmission,
    WorkOrder,
    require_bound_order_hash,
    require_safe_order_id,
)
from envresearch.workers.control import OrderAnchor, ReceiptAnchor, serialize_model
from envresearch.workers.filesystem import PinnedRoot, entry_exists_at, list_names_at
from envresearch.workers.native import rename_noreplace_at

if TYPE_CHECKING:
    from envresearch.workers.queue import FilesystemWorkerQueue


class _CancellationAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    order_id: str
    revision_id: str
    order_hash: str
    receipt_sha256: tuple[str, ...]
    mac: str


def archive_generation(
    queue: FilesystemWorkerQueue,
    order_id: str,
    revision_id: str,
    *,
    allow_cancellation: bool = False,
) -> None:
    """Move one complete public/protected order generation into an archive."""
    require_safe_order_id(order_id)
    require_safe_order_id(revision_id)
    control = queue.control
    with control.transaction_lock("accept", order_id), control.order_lock(order_id):
        public = Path("revisions") / revision_id / "worker"
        protected = Path("revisions") / revision_id
        if queue.exchange.exists(Path("work-orders") / f"{order_id}.json"):
            order = queue._read_order_unlocked(order_id)
            anchors, complete = _inspect_current_generation(queue, order)
            if anchors and not complete:
                if not allow_cancellation:
                    raise ValueError("completed worker submission evidence is missing")
                _ensure_cancellation(queue, order, revision_id, anchors)
        _move(
            queue.exchange,
            Path("work-orders"),
            f"{order_id}.json",
            public / "work-orders",
        )
        _move(
            queue.exchange,
            Path("worker-submissions"),
            order_id,
            public / "worker-submissions",
            required=False,
        )
        _move(control.storage, Path("orders"), f"{order_id}.json", protected / "orders")
        _move(
            control.storage,
            Path("receipts"),
            order_id,
            protected / "receipts",
            required=False,
        )
        validate_archive(
            queue, order_id, revision_id, allow_cancellation=allow_cancellation
        )


def _inspect_current_generation(
    queue: FilesystemWorkerQueue, order: WorkOrder
) -> tuple[tuple[ReceiptAnchor, ...], bool]:
    """Authenticate current receipts and distinguish unpublished crash residue."""
    anchors = queue.control.list_receipts(order.order_id)
    if len(anchors) > 1:
        raise ValueError("worker generation has multiple receipt intents")
    base = Path("worker-submissions") / order.order_id
    try:
        with queue.exchange.directory(base) as base_fd:
            names = set(list_names_at(base_fd))
            if names - {".staging", "transactions"}:
                raise ValueError("submission path mismatch")
            if "transactions" not in names:
                return anchors, not anchors
            transactions_fd = queue.exchange.open_directory(base / "transactions")
            try:
                transaction_names = set(list_names_at(transactions_fd))
                expected = {anchor.transaction_name for anchor in anchors}
                if transaction_names - expected:
                    raise ValueError("submission anchor authentication missing")
                if transaction_names != expected:
                    return anchors, False
                submissions = queue._collect_transactions(
                    order,
                    transactions_fd,
                    {anchor.transaction_name: anchor for anchor in anchors},
                )
                return anchors, len(submissions) == 1
            finally:
                os.close(transactions_fd)
    except FileNotFoundError:
        return anchors, not anchors


def _ensure_cancellation(
    queue: FilesystemWorkerQueue,
    order: WorkOrder,
    revision_id: str,
    anchors: tuple[ReceiptAnchor, ...],
) -> None:
    receipt_sha256 = tuple(
        hashlib.sha256(serialize_model(anchor)).hexdigest() for anchor in anchors
    )
    order_hash = require_bound_order_hash(order)
    identity = {
        "order_id": order.order_id,
        "revision_id": revision_id,
        "order_hash": order_hash,
        "receipt_sha256": receipt_sha256,
    }
    record = _CancellationAnchor(
        order_id=order.order_id,
        revision_id=revision_id,
        order_hash=order_hash,
        receipt_sha256=receipt_sha256,
        mac=hmac.new(
            queue.control.key, _canonical(identity), hashlib.sha256
        ).hexdigest(),
    )
    path = _cancellation_path(revision_id, order.order_id)
    data = _canonical(record.model_dump(mode="json"))
    if not queue.control.storage.exists(path):
        try:
            queue.control.storage.write_file_noreplace(path, data, mode=0o600)
        except FileExistsError:
            pass
    _require_cancellation(queue, order, revision_id, anchors)


def _move(
    root: PinnedRoot,
    source_parent: Path,
    name: str,
    destination_parent: Path,
    *,
    required: bool = True,
) -> None:
    try:
        source_fd = root.open_directory(source_parent)
    except FileNotFoundError:
        source_fd = -1
    destination_fd = root.open_directory(destination_parent, create=True)
    try:
        source_exists = source_fd >= 0 and entry_exists_at(source_fd, name)
        destination_exists = entry_exists_at(destination_fd, name)
        if source_exists and destination_exists:
            raise RuntimeError("revision archive namespace collision")
        if source_exists:
            rename_noreplace_at(source_fd, name, destination_fd, name)
            os.fsync(source_fd)
            os.fsync(destination_fd)
        elif required and not destination_exists:
            raise FileNotFoundError("required worker generation entry is missing")
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        os.close(destination_fd)


def validate_archive(
    queue: FilesystemWorkerQueue,
    order_id: str,
    revision_id: str,
    *,
    allow_cancellation: bool = False,
) -> None:
    public = Path("revisions") / revision_id / "worker"
    protected = Path("revisions") / revision_id
    order_data = queue.exchange.read_file(
        public / "work-orders" / f"{order_id}.json",
        description="archived work order",
    )
    order = WorkOrder.model_validate_json(order_data)
    if order_data != serialize_model(order) or order.order_id != order_id:
        raise ValueError("archived work order is invalid")
    order_anchor = queue.control._read_anchor(
        protected / "orders" / f"{order_id}.json", OrderAnchor
    )
    if (
        order_anchor.order_hash != order.order_hash
        or order_anchor.record_sha256 != hashlib.sha256(order_data).hexdigest()
    ):
        raise ValueError("archived work order anchor mismatch")
    receipt_root = protected / "receipts" / order_id
    transaction_root = public / "worker-submissions" / order_id
    has_receipts = queue.control.storage.exists(receipt_root)
    published_root = transaction_root / "transactions"
    has_transactions = queue.exchange.exists(published_root)
    has_cancellation = queue.control.storage.exists(
        _cancellation_path(revision_id, order_id)
    )
    if not has_receipts and not has_transactions:
        if has_cancellation:
            raise ValueError("revision cancellation has no receipt intent")
        return
    if has_transactions and not has_receipts:
        raise ValueError("archived incomplete generation state is inconsistent")
    with queue.control.storage.directory(receipt_root) as receipts_fd:
        receipt_names = tuple(
            name for name in os.listdir(receipts_fd) if name.endswith(".json")
        )
    if len(receipt_names) != 1:
        raise ValueError("archived receipt generation is incomplete")
    anchor = queue.control._read_anchor(receipt_root / receipt_names[0], ReceiptAnchor)
    if has_cancellation:
        if not allow_cancellation:
            raise ValueError("completed worker generation cannot be cancelled")
        _require_cancellation(queue, order, revision_id, (anchor,))
        if has_transactions and queue.exchange.list_directory(published_root):
            raise ValueError("cancelled generation contains a public transaction")
        return
    if not has_transactions:
        raise ValueError("archived incomplete generation state is inconsistent")
    submission = anchor.submission
    filename = submission.candidate_relative_paths[0].name
    transaction = published_root / anchor.transaction_name
    with queue.exchange.directory(transaction) as transaction_fd:
        if set(list_names_at(transaction_fd)) != {filename, "receipt.json"}:
            raise ValueError("archived submission transaction is incomplete")
    candidate = queue.exchange.read_file(
        transaction / filename, description="archived candidate"
    )
    receipt = queue.exchange.read_file(
        transaction / "receipt.json", description="archived receipt"
    )
    durable = WorkerSubmission.model_validate_json(receipt)
    if receipt != serialize_model(durable) or durable != submission:
        raise ValueError("archived submission receipt mismatch")
    if (
        hashlib.sha256(candidate).hexdigest() != submission.candidate_sha256[0]
        or len(candidate) != anchor.candidate_size
        or hashlib.sha256(receipt).hexdigest() != anchor.receipt_sha256
        or submission.order_hash != order.order_hash
        or submission.claimed_schema != order.expected_output_schema
        or anchor.transaction_name != f"{filename}.submission"
    ):
        raise ValueError("archived candidate hash mismatch")


def _require_cancellation(
    queue: FilesystemWorkerQueue,
    order: WorkOrder,
    revision_id: str,
    anchors: tuple[ReceiptAnchor, ...],
) -> None:
    data = queue.control.storage.read_file(
        _cancellation_path(revision_id, order.order_id),
        description="revision cancellation anchor",
        required_mode=0o600,
    )
    record = _CancellationAnchor.model_validate_json(data)
    receipt_sha256 = tuple(
        hashlib.sha256(serialize_model(anchor)).hexdigest() for anchor in anchors
    )
    identity = {
        "order_id": order.order_id,
        "revision_id": revision_id,
        "order_hash": order.order_hash,
        "receipt_sha256": receipt_sha256,
    }
    expected = hmac.new(
        queue.control.key, _canonical(identity), hashlib.sha256
    ).hexdigest()
    if (
        data != _canonical(record.model_dump(mode="json"))
        or record.order_id != order.order_id
        or record.revision_id != revision_id
        or record.order_hash != order.order_hash
        or record.receipt_sha256 != receipt_sha256
        or not hmac.compare_digest(record.mac, expected)
    ):
        raise ValueError("revision cancellation authentication failed")


def _cancellation_path(revision_id: str, order_id: str) -> Path:
    return Path("revisions") / revision_id / "cancelled" / f"{order_id}.json"


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()

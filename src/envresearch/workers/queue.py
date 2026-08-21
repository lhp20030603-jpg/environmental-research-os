"""Authenticated, descriptor-pinned filesystem exchange for worker candidates."""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from envresearch.models.artifact import ProducerIdentity
from envresearch.storage.artifacts import ArtifactRecord
from envresearch.workers import filesystem
from envresearch.workers.contracts import (
    WorkerSubmission,
    WorkOrder,
    require_candidate_filename,
    require_safe_order_id,
    require_schema_identifier,
    revalidate_work_order_instance,
)
from envresearch.workers.control import QueueControl, ReceiptAnchor, serialize_model
from envresearch.workers.filesystem import (
    PinnedRoot,
    create_directory_at,
    directories_overlap,
    entry_exists_at,
    list_names_at,
    open_directory_at,
    read_regular_at,
    remove_tree_at,
    write_file_noreplace_at,
)
from envresearch.workers.principal_binding import assigned_producer, has_generation
from envresearch.workers.queue_records import MANAGED_SOURCE_NAMESPACES
from envresearch.workers.queue_records import artifact_record as _record
from envresearch.workers.queue_records import candidate_path as _candidate_path
from envresearch.workers.queue_records import order_path as _order_path

_HASH = hashlib.sha256
_DEFAULT_PRODUCER = ProducerIdentity(component="filesystem-worker", version="1.0")


class FilesystemWorkerQueue:
    """Exchange immutable orders and isolated candidates with protected anchors."""

    def __init__(
        self,
        root: Path,
        *,
        producer: ProducerIdentity | None = None,
        control_root: Path | None = None,
        require_producer_context: bool = False,
        create: bool = True,
    ) -> None:
        self.exchange = PinnedRoot(root, create=create)
        selected_control = control_root or (
            self.exchange.path.parent
            / f".{self.exchange.path.name}.worker-queue-control"
        )
        _ = selected_control.resolve(strict=False)
        control_storage = PinnedRoot(selected_control, private=True, create=create)
        if directories_overlap(self.exchange.fd, control_storage.fd):
            control_storage.close()
            raise ValueError("control root must be separate from the exchange root")
        self.control = QueueControl(control_storage, create=create)
        self.root = self.exchange.path
        self.control_root = self.control.path
        self.producer = producer or _DEFAULT_PRODUCER
        self.require_producer_context = require_producer_context

    @classmethod
    def open_existing(
        cls,
        root: Path,
        *,
        producer: ProducerIdentity | None = None,
        control_root: Path | None = None,
        require_producer_context: bool = False,
    ) -> FilesystemWorkerQueue:
        """Authenticate an existing exchange without creating or chmodding state."""
        return cls(
            root,
            producer=producer,
            control_root=control_root,
            require_producer_context=require_producer_context,
            create=False,
        )

    def close(self) -> None:
        """Release public and protected pinned roots idempotently."""
        self.exchange.close()
        self.control.storage.close()

    def issue(self, order: WorkOrder) -> ArtifactRecord:
        """Anchor then no-replace publish one canonical immutable work order."""
        durable_order = revalidate_work_order_instance(order)
        self._validate_order_entry_names(durable_order)
        data = serialize_model(durable_order)
        with self.control.order_lock(durable_order.order_id):
            anchor = self.control.ensure_order(durable_order, data)
            relative = _order_path(durable_order.order_id)
            if self.exchange.exists(relative):
                existing = self._read_order_unlocked(durable_order.order_id)
                if existing != durable_order:
                    raise RuntimeError("work order identity collision")
            else:
                try:
                    self.exchange.write_file_noreplace(relative, data, mode=0o600)
                except FileExistsError:
                    existing = self._read_order_unlocked(durable_order.order_id)
                    if existing != durable_order:
                        raise RuntimeError("work order identity collision") from None
            return _record(relative, anchor.record_sha256, len(data))

    def submit(
        self,
        order_id: str,
        path: Path,
        *,
        producer: ProducerIdentity | None = None,
        claimed_schema: str | None = None,
        expected_order_hash: str | None = None,
    ) -> ArtifactRecord:
        """Prepare a protected receipt and atomically publish one transaction."""
        require_safe_order_id(order_id)
        with self.control.order_lock(order_id):
            order = self._read_order_unlocked(order_id)
            durable_producer = assigned_producer(
                order,
                producer,
                expected_order_hash,
                default=self.producer,
                require_context=self.require_producer_context,
            )
            source_relative = self._source_relative(path)
            filename = require_candidate_filename(source_relative.name)
            self._validate_candidate_entry_names(filename)
            if filename not in order.expected_output_filenames:
                raise ValueError("unexpected output filename")
            schema = (
                order.expected_output_schema
                if claimed_schema is None
                else claimed_schema
            )
            require_schema_identifier(schema)
            if schema != order.expected_output_schema:
                raise ValueError("claimed schema mismatch")
            candidate = self.exchange.read_file(
                source_relative, description="candidate source"
            )
            candidate_relative = _candidate_path(order.order_id, filename)
            transaction_name = f"{filename}.submission"
            anchor = self.control.ensure_receipt(
                order,
                candidate_relative,
                candidate,
                durable_producer,
                schema,
                transaction_name,
            )
            receipt_data = serialize_model(anchor.submission)
            return self._publish_transaction(anchor, candidate, receipt_data)

    def read_order(self, order_id: str) -> WorkOrder:
        """Return one control-authenticated public work order."""
        require_safe_order_id(order_id)
        with self.control.order_lock(order_id):
            return self._read_order_unlocked(order_id)

    def collect(self, order_id: str) -> tuple[WorkerSubmission, ...]:
        """Return authenticated transactions without promoting their candidates."""
        require_safe_order_id(order_id)
        with self.control.order_lock(order_id):
            order = self._read_order_unlocked(order_id)
            anchors = {
                anchor.transaction_name: anchor
                for anchor in self.control.list_receipts(order_id)
            }
            base = Path("worker-submissions") / order_id
            try:
                with self.exchange.directory(base) as base_fd:
                    names = set(list_names_at(base_fd))
                    if names - {".staging", "transactions"}:
                        raise ValueError("submission path mismatch")
                    if "transactions" not in names:
                        if anchors:
                            raise ValueError("submission transaction is incomplete")
                        return ()
                    transactions_fd = open_directory_at(base_fd, "transactions")
                    try:
                        submissions = self._collect_transactions(
                            order, transactions_fd, anchors
                        )
                    finally:
                        os.close(transactions_fd)
            except FileNotFoundError:
                if anchors:
                    raise ValueError("submission transaction is incomplete") from None
                return ()
            return tuple(submissions)

    def archive_generation(
        self, order_id: str, revision_id: str, *, allow_cancellation: bool = False
    ) -> None:
        """Move one complete public/protected order generation into an archive."""
        from envresearch.workers.revision_archive import archive_generation

        archive_generation(
            self, order_id, revision_id, allow_cancellation=allow_cancellation
        )

    def has_generation(self, order_id: str) -> bool:
        """Return whether a current authenticated work-order generation exists."""
        return has_generation(self, order_id)

    def _collect_transactions(
        self,
        order: WorkOrder,
        transactions_fd: int,
        anchors: dict[str, ReceiptAnchor],
    ) -> list[WorkerSubmission]:
        submissions: list[WorkerSubmission] = []
        transaction_names = set(list_names_at(transactions_fd))
        missing = set(anchors) - transaction_names
        if missing:
            raise ValueError("submission transaction is incomplete")
        if transaction_names - set(anchors):
            raise ValueError("submission anchor authentication missing")
        for transaction_name in sorted(transaction_names):
            if not transaction_name.endswith(".submission"):
                raise ValueError("submission path mismatch")
            anchor = anchors[transaction_name]
            self._read_transaction_at(transactions_fd, anchor, order)
            submissions.append(anchor.submission)
        return submissions

    def _read_order_unlocked(self, order_id: str) -> WorkOrder:
        try:
            anchor = self.control.read_order(order_id)
            data = self.exchange.read_file(
                _order_path(order_id), description="work order"
            )
        except FileNotFoundError as error:
            raise FileNotFoundError(f"unknown work order: {order_id}") from error
        try:
            order = WorkOrder.model_validate_json(data)
        except ValueError as error:
            if "work order hash mismatch" in str(error):
                raise ValueError("work order hash mismatch") from error
            raise ValueError(f"work order is invalid: {error}") from error
        if data != serialize_model(order):
            raise ValueError("work order is non-canonical")
        if order.order_id != order_id or order.order_hash != anchor.order_hash:
            raise ValueError("work order anchor mismatch")
        if _HASH(data).hexdigest() != anchor.record_sha256:
            raise ValueError("work order anchor mismatch")
        return order

    def _publish_transaction(
        self, anchor: ReceiptAnchor, candidate: bytes, receipt: bytes
    ) -> ArtifactRecord:
        submission = anchor.submission
        order_id = submission.order_id
        filename = submission.candidate_relative_paths[0].name
        base = Path("worker-submissions") / order_id
        with (
            self.exchange.directory(base / ".staging", create=True) as staging_fd,
            self.exchange.directory(base / "transactions", create=True) as tx_fd,
        ):
            if entry_exists_at(tx_fd, anchor.transaction_name):
                return self._existing_transaction(tx_fd, anchor, candidate)
            stage_name = f"txn-{uuid.uuid4().hex}"
            stage_fd = create_directory_at(staging_fd, stage_name)
            try:
                write_file_noreplace_at(stage_fd, filename, candidate, mode=0o600)
                write_file_noreplace_at(stage_fd, "receipt.json", receipt, mode=0o600)
                os.fsync(stage_fd)
            finally:
                os.close(stage_fd)
            try:
                filesystem._rename_directory_noreplace(
                    staging_fd, stage_name, tx_fd, anchor.transaction_name
                )
                os.fsync(staging_fd)
                os.fsync(tx_fd)
            except FileExistsError:
                remove_tree_at(staging_fd, stage_name)
                try:
                    return self._existing_transaction(tx_fd, anchor, candidate)
                except (OSError, ValueError) as error:
                    raise RuntimeError("submission conflict") from error
            except Exception:
                remove_tree_at(staging_fd, stage_name)
                raise
            return self._existing_transaction(tx_fd, anchor, candidate)

    def _existing_transaction(
        self, transactions_fd: int, anchor: ReceiptAnchor, candidate: bytes
    ) -> ArtifactRecord:
        self._read_transaction_at(
            transactions_fd,
            anchor,
            expected_order=None,
        )
        expected_hash = anchor.submission.candidate_sha256[0]
        if _HASH(candidate).hexdigest() != expected_hash:
            raise RuntimeError("submission conflict")
        relative = anchor.submission.candidate_relative_paths[0]
        return ArtifactRecord(
            relative_path=relative,
            sha256=expected_hash,
            size_bytes=anchor.candidate_size,
            written_at=anchor.submission.submitted_at,
        )

    def _read_transaction_at(
        self,
        transactions_fd: int,
        anchor: ReceiptAnchor,
        expected_order: WorkOrder | None,
    ) -> None:
        transaction_fd = open_directory_at(transactions_fd, anchor.transaction_name)
        try:
            filename = anchor.submission.candidate_relative_paths[0].name
            names = set(list_names_at(transaction_fd))
            expected_names = {filename, "receipt.json"}
            if not expected_names.issubset(names):
                raise ValueError("submission transaction is incomplete")
            if names != expected_names:
                raise ValueError("submission path mismatch")
            candidate = read_regular_at(
                transaction_fd, filename, description="candidate"
            )
            receipt = read_regular_at(
                transaction_fd, "receipt.json", description="submission receipt"
            )
        finally:
            os.close(transaction_fd)
        submission = WorkerSubmission.model_validate_json(receipt)
        if receipt != serialize_model(submission):
            raise ValueError("submission manifest is non-canonical")
        if expected_order is not None:
            if submission.order_hash != expected_order.order_hash:
                raise ValueError("submission order hash mismatch")
            if submission.claimed_schema != expected_order.expected_output_schema:
                raise ValueError("submission schema mismatch")
            if submission.principal_assignment != expected_order.principal_assignment:
                raise ValueError("submission principal assignment mismatch")
        candidate_hash = _HASH(candidate).hexdigest()
        if candidate_hash != submission.candidate_sha256[0]:
            raise ValueError("submission hash mismatch")
        if (
            len(candidate) != anchor.candidate_size
            or submission != anchor.submission
            or _HASH(receipt).hexdigest() != anchor.receipt_sha256
        ):
            raise ValueError("submission anchor authentication mismatch")

    def _source_relative(self, path: Path) -> Path:
        if ".." in path.parts:
            raise ValueError("source path must be safe and queue-relative")
        if path.is_absolute():
            lexical = Path(os.path.abspath(path))
            for root_alias in (self.exchange.lexical_path, self.root):
                try:
                    relative = lexical.relative_to(root_alias)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError("absolute source must be inside the queue root")
        else:
            relative = path
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("source path must be safe and queue-relative")
        if relative.parts[0].casefold() in MANAGED_SOURCE_NAMESPACES:
            raise PermissionError("candidate source is in an authoritative namespace")
        return relative

    def _validate_order_entry_names(self, order: WorkOrder) -> None:
        self.exchange.require_name(order.order_id)
        self.exchange.require_name(f"{order.order_id}.json")
        self.control.storage.require_name(order.order_id)
        self.control.storage.require_name(f"{order.order_id}.json")
        self.control.storage.require_name(f"{order.order_id}.filelock")
        for filename in order.expected_output_filenames:
            self._validate_candidate_entry_names(filename)

    def _validate_candidate_entry_names(self, filename: str) -> None:
        self.exchange.require_name(filename)
        self.exchange.require_name(f"{filename}.submission")
        self.control.storage.require_name(f"{filename}.json")

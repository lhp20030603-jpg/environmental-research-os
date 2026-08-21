"""Protected authenticated anchors and locks for the worker exchange."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, field_validator

from envresearch.models.artifact import ProducerIdentity
from envresearch.workers.contracts import (
    WorkerSubmission,
    WorkOrder,
    require_bound_order_hash,
)
from envresearch.workers.filesystem import (
    PinnedRoot,
    entry_exists_at,
    write_file_noreplace_at,
)
from envresearch.workers.native import locked_regular_at
from envresearch.workers.read_only import load_existing_key, prepare_directories
from envresearch.workers.recovery import recover_receipt_namespace_at

_HASH = hashlib.sha256
_SHA256_LENGTH = 64
_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, strict=True)


class OrderAnchor(BaseModel):
    """Authenticated digest of one originally issued public order."""

    model_config = _MODEL_CONFIG

    order_id: str
    order_hash: str
    record_sha256: str
    mac: str

    @field_validator("order_hash", "record_sha256", "mac")
    @classmethod
    def require_digest(cls, value: str) -> str:
        return _require_digest(value)


class ReceiptAnchor(BaseModel):
    """Authenticated queue intent for one complete public transaction."""

    model_config = _MODEL_CONFIG

    submission: WorkerSubmission
    receipt_sha256: str
    candidate_size: int
    transaction_name: str
    mac: str

    @field_validator("receipt_sha256", "mac")
    @classmethod
    def require_digest(cls, value: str) -> str:
        return _require_digest(value)

    @field_validator("candidate_size")
    @classmethod
    def require_size(cls, value: int) -> int:
        if value < 0:
            raise ValueError("candidate size must not be negative")
        return value


AnchorT = TypeVar("AnchorT", OrderAnchor, ReceiptAnchor)


class QueueControl:
    def __init__(self, storage: PinnedRoot, *, create: bool = True) -> None:
        self.storage = storage
        self.create = create
        prepare_directories(storage, ("locks", "orders", "receipts"), create=create)
        self.key = self._load_or_create_key() if create else load_existing_key(storage)

    path = property(lambda self: self.storage.path)

    def ensure_order(self, order: WorkOrder, data: bytes) -> OrderAnchor:
        """Create or verify the protected identity for one issued order."""
        relative = Path("orders") / f"{order.order_id}.json"
        record_hash = _HASH(data).hexdigest()
        if self.storage.exists(relative):
            anchor = self.read_order(order.order_id)
            if (
                anchor.order_hash != order.order_hash
                or anchor.record_sha256 != record_hash
            ):
                raise RuntimeError("work order identity collision")
            return anchor
        anchor = _seal_order(order, record_hash, self.key)
        try:
            self.storage.write_file_noreplace(
                relative, serialize_model(anchor), mode=0o600
            )
        except FileExistsError:
            return self.ensure_order(order, data)
        return anchor

    def read_order(self, order_id: str) -> OrderAnchor:
        """Load and authenticate one protected order identity."""
        anchor = self._read_anchor(Path("orders") / f"{order_id}.json", OrderAnchor)
        if anchor.order_id != order_id:
            raise ValueError("work order anchor mismatch")
        return anchor

    def ensure_receipt(
        self,
        order: WorkOrder,
        candidate_relative: Path,
        candidate: bytes,
        producer: ProducerIdentity,
        schema: str,
        transaction_name: str,
    ) -> ReceiptAnchor:
        """Create or verify protected intent before public transaction publish."""
        with self._receipt_publication_lock(order.order_id):
            self._recover_receipt_temporaries(order.order_id, create=True)
            return self._ensure_receipt_unlocked(
                order,
                candidate_relative,
                candidate,
                producer,
                schema,
                transaction_name,
            )

    def _ensure_receipt_unlocked(
        self,
        order: WorkOrder,
        candidate_relative: Path,
        candidate: bytes,
        producer: ProducerIdentity,
        schema: str,
        transaction_name: str,
    ) -> ReceiptAnchor:
        filename = candidate_relative.name
        relative = _receipt_path(order.order_id, filename)
        candidate_hash = _HASH(candidate).hexdigest()
        if self.storage.exists(relative):
            anchor = self.read_receipt(order.order_id, filename)
            _require_matching_receipt(
                anchor,
                order,
                producer,
                schema,
                candidate_hash,
                len(candidate),
                transaction_name,
            )
            return anchor
        submission = WorkerSubmission(
            order_id=order.order_id,
            order_hash=require_bound_order_hash(order),
            producer=producer,
            candidate_relative_paths=(candidate_relative,),
            candidate_sha256=(candidate_hash,),
            claimed_schema=schema,
            submitted_at=datetime.now(UTC),
            principal_assignment=order.principal_assignment,
        )
        anchor = _seal_receipt(submission, len(candidate), transaction_name, self.key)
        try:
            self.storage.write_file_noreplace(
                relative, serialize_model(anchor), mode=0o600
            )
        except FileExistsError:
            anchor = self.read_receipt(order.order_id, filename)
            _require_matching_receipt(
                anchor,
                order,
                producer,
                schema,
                candidate_hash,
                len(candidate),
                transaction_name,
            )
            return anchor
        return anchor

    def read_receipt(self, order_id: str, filename: str) -> ReceiptAnchor:
        """Load and authenticate one protected submission intent."""
        try:
            anchor = self._read_anchor(_receipt_path(order_id, filename), ReceiptAnchor)
        except FileNotFoundError as error:
            raise ValueError("submission anchor authentication missing") from error
        _require_receipt_binding(anchor, order_id, filename)
        return anchor

    def list_receipts(self, order_id: str) -> tuple[ReceiptAnchor, ...]:
        """List every authenticated submission intent for one order."""
        with self._receipt_publication_lock(order_id):
            names = self._recover_receipt_temporaries(order_id, create=False)
            if names is None:
                return ()
            anchors: list[ReceiptAnchor] = []
            for name in names:
                filename = name.removesuffix(".json")
                anchors.append(self.read_receipt(order_id, filename))
            return tuple(anchors)

    @contextmanager
    def order_lock(self, order_id: str, *, timeout: float = 30) -> Iterator[None]:
        """Lock one verified inode beneath the pinned protected lock directory."""
        with self._file_lock(f"{order_id}.filelock", timeout=timeout):
            yield

    @contextmanager
    def transaction_lock(
        self, kind: str, identity: str = "run", *, timeout: float = 30
    ) -> Iterator[None]:
        """Lock a complete workflow transaction beneath the protected namespace."""
        digest = _HASH(identity.encode("utf-8")).hexdigest()
        with self._file_lock(f"research-{kind}-{digest}.filelock", timeout=timeout):
            yield

    @contextmanager
    def _receipt_publication_lock(self, order_id: str) -> Iterator[None]:
        digest = _HASH(order_id.encode("utf-8")).hexdigest()
        with self._file_lock(f"receipt-{digest}.filelock", timeout=30):
            yield

    @contextmanager
    def _file_lock(self, name: str, *, timeout: float) -> Iterator[None]:
        self.storage.require_name(name)
        with self.storage.directory(Path("locks")) as locks_fd:
            if not entry_exists_at(locks_fd, name):
                if not self.create:
                    raise FileNotFoundError(name)
                try:
                    write_file_noreplace_at(locks_fd, name, b"", mode=0o600)
                except FileExistsError:
                    pass
            with locked_regular_at(locks_fd, name, timeout=timeout):
                yield

    def _recover_receipt_temporaries(
        self, order_id: str, *, create: bool
    ) -> tuple[str, ...] | None:
        relative = Path("receipts") / order_id
        try:
            receipts_fd = self.storage.open_directory(relative, create=create)
        except FileNotFoundError:
            if create:
                raise
            return None
        try:
            return recover_receipt_namespace_at(
                receipts_fd,
                owner=os.geteuid(),
                authenticate_target=lambda data: self._receipt_temp_target(
                    order_id, data
                ),
            )
        finally:
            os.close(receipts_fd)

    def _receipt_temp_target(self, order_id: str, data: bytes) -> str:
        anchor = self._validate_anchor_data(data, ReceiptAnchor)
        filename = anchor.submission.candidate_relative_paths[0].name
        _require_receipt_binding(anchor, order_id, filename)
        return f"{filename}.json"

    def _read_anchor(self, relative: Path, model: type[AnchorT]) -> AnchorT:
        data = self.storage.read_file(
            relative, description="control anchor", required_mode=0o600
        )
        return self._validate_anchor_data(data, model)

    def _validate_anchor_data(self, data: bytes, model: type[AnchorT]) -> AnchorT:
        try:
            anchor = model.model_validate_json(data)
        except ValueError as error:
            raise ValueError("control anchor authentication failed") from error
        if data != serialize_model(anchor) or not hmac.compare_digest(
            anchor.mac, _mac_for(anchor, self.key)
        ):
            raise ValueError("control anchor authentication failed")
        return anchor

    def _load_or_create_key(self) -> bytes:
        relative = Path("queue.key")
        try:
            key = self.storage.read_file(
                relative, description="queue key", required_mode=0o600
            )
        except FileNotFoundError:
            try:
                self.storage.write_file_noreplace(
                    relative, secrets.token_bytes(32), mode=0o600
                )
            except FileExistsError:
                pass
            key = self.storage.read_file(
                relative, description="queue key", required_mode=0o600
            )
        if len(key) != 32:
            raise ValueError("queue key is invalid")
        return key


def serialize_model(model: BaseModel) -> bytes:
    """Serialize a public contract or private anchor canonically."""
    return _serialize_value(model.model_dump(mode="json"))


def _seal_order(order: WorkOrder, record_hash: str, key: bytes) -> OrderAnchor:
    anchor = OrderAnchor(
        order_id=order.order_id,
        order_hash=require_bound_order_hash(order),
        record_sha256=record_hash,
        mac="0" * _SHA256_LENGTH,
    )
    return anchor.model_copy(update={"mac": _mac_for(anchor, key)})


def _seal_receipt(
    submission: WorkerSubmission,
    candidate_size: int,
    transaction_name: str,
    key: bytes,
) -> ReceiptAnchor:
    anchor = ReceiptAnchor(
        submission=submission,
        receipt_sha256=_HASH(serialize_model(submission)).hexdigest(),
        candidate_size=candidate_size,
        transaction_name=transaction_name,
        mac="0" * _SHA256_LENGTH,
    )
    return anchor.model_copy(update={"mac": _mac_for(anchor, key)})


def _mac_for(anchor: OrderAnchor | ReceiptAnchor, key: bytes) -> str:
    payload = anchor.model_dump(mode="json", exclude={"mac"})
    return hmac.new(key, _serialize_value(payload), _HASH).hexdigest()


def _receipt_path(order_id: str, filename: str) -> Path:
    return Path("receipts") / order_id / f"{filename}.json"


def _require_receipt_binding(
    anchor: ReceiptAnchor, order_id: str, filename: str
) -> None:
    if (
        anchor.submission.order_id != order_id
        or anchor.transaction_name != f"{filename}.submission"
    ):
        raise ValueError("submission anchor authentication mismatch")


def _require_matching_receipt(
    anchor: ReceiptAnchor,
    order: WorkOrder,
    producer: ProducerIdentity,
    schema: str,
    candidate_hash: str,
    candidate_size: int,
    transaction_name: str,
) -> None:
    submission = anchor.submission
    if (
        submission.order_hash != order.order_hash
        or submission.producer != producer
        or submission.claimed_schema != schema
        or submission.candidate_sha256 != (candidate_hash,)
        or anchor.candidate_size != candidate_size
        or anchor.transaction_name != transaction_name
    ):
        raise RuntimeError("submission conflict")


def _serialize_value(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _require_digest(value: str) -> str:
    if len(value) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError("digest must be a lowercase SHA-256")
    return value

"""Descriptor-pinned authenticated append-only journals for control state."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, Self

from envresearch.storage.secure_journal_files import (
    identity,
    open_regular,
    read_all,
    require_safe_file,
    write_all,
)
from envresearch.storage.secure_journal_lock import secured_journal_lock
from envresearch.storage.secure_journal_open import (
    create_pinned_journal,
    load_or_create_journal_key,
    open_existing_journal,
    open_recovery_journal,
)
from envresearch.storage.secure_journal_records import (
    HASH,
    ZERO_HASH,
    JournalHead,
    SecureRecord,
    json_object,
    parse_records,
    seal_record,
)
from envresearch.storage.secure_journal_verify import (
    build_journal_head,
    read_journal_head,
    repair_lagging_head,
    verify_journal_head_strict,
    write_journal_head,
)
from envresearch.workers.filesystem import PinnedRoot


class SecureJournal:
    """Bind one public JSONL file to a private authenticated head and lock."""

    def __init__(
        self,
        path: Path,
        *,
        storage_root: Path | None = None,
        control_root: Path | None = None,
    ) -> None:
        absolute = Path(os.path.abspath(path))
        root = Path(os.path.abspath(storage_root or absolute.parent))
        try:
            relative = absolute.relative_to(root)
        except ValueError as error:
            raise ValueError(
                "journal path must remain beneath its storage root"
            ) from error
        self.path = absolute
        self.storage = PinnedRoot(root)
        selected_control = control_root or root.parent / (
            f".{root.name}.worker-queue-control"
        )
        self.control = PinnedRoot(selected_control, private=True)
        self._closed = False
        self._owns_roots = True
        self._writable = True
        self._can_create_control = True
        self._can_reconcile_head = True
        self._relative = relative
        self._journal_id = HASH(
            f"{root.name}/{relative.as_posix()}".encode()
        ).hexdigest()
        self.control.ensure_directory(Path("journal-locks"))
        self.control.ensure_directory(Path("journal-lock-anchors"))
        self.control.ensure_directory(Path("journal-heads"))
        self._key = load_or_create_journal_key(self.control)

    @classmethod
    def create_from_pinned(
        cls,
        path: Path,
        *,
        storage_root: PinnedRoot,
        control_root: PinnedRoot,
    ) -> Self:
        return create_pinned_journal(
            cls,
            path=path,
            storage_root=storage_root,
            control_root=control_root,
        )

    @classmethod
    def open_existing(
        cls,
        path: Path,
        *,
        storage_root: PinnedRoot,
        control_root: PinnedRoot,
        reconcile: Literal[False] = False,
    ) -> Self:
        return open_existing_journal(
            cls,
            path=path,
            storage_root=storage_root,
            control_root=control_root,
            reconcile=reconcile,
        )

    @classmethod
    def open_for_recovery(
        cls,
        path: Path,
        *,
        storage_root: PinnedRoot,
        control_root: PinnedRoot,
    ) -> Self:
        return open_recovery_journal(
            cls,
            path=path,
            storage_root=storage_root,
            control_root=control_root,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Release journal-owned pinned roots idempotently."""
        if self._closed:
            return
        self._closed = True
        if self._owns_roots:
            try:
                self.control.close()
            finally:
                self.storage.close()

    def __del__(self) -> None:
        if not getattr(self, "_closed", True):
            try:
                self.close()
            except OSError:
                pass

    def ensure(self) -> None:
        """Create and authenticate an empty owner-only journal if absent."""
        self._require_writable()
        with self._locked():
            parent_fd, descriptor = self._open_journal(create=True)
            try:
                payloads, records, size, prefix_size = self._read_descriptor(descriptor)
                self._verify_or_reconcile_head(descriptor, records, size, prefix_size)
                if payloads:
                    return
            finally:
                os.close(descriptor)
                os.close(parent_fd)

    def verify_roots(self) -> None:
        """Fail closed unless both public and protected roots remain attached."""
        self._require_open()
        self._require_attached(self.storage)
        self._require_attached(self.control)

    def read_all(self) -> list[dict[str, Any]]:
        """Read one canonical authenticated history from retained descriptors."""
        with self._locked():
            try:
                parent_fd, descriptor = self._open_journal(create=False)
            except FileNotFoundError:
                if self._head_exists():
                    raise ValueError("journal is missing behind its authenticated head")
                return []
            try:
                payloads, records, size, prefix_size = self._read_descriptor(descriptor)
                self._verify_or_reconcile_head(descriptor, records, size, prefix_size)
                self._require_unchanged_entry(parent_fd, descriptor)
                return payloads
            finally:
                os.close(descriptor)
                os.close(parent_fd)

    def append(self, payload: Mapping[str, object]) -> None:
        """Append one canonical payload and durably advance its protected head."""
        self._require_writable()
        durable = json_object(payload)
        if "_journal" in durable:
            raise ValueError("journal payload uses a reserved field")
        with self._locked():
            self._append_locked(durable)

    def append_unique(
        self, payload: Mapping[str, object], *, identity_fields: tuple[str, ...]
    ) -> bool:
        """Atomically append one identity, returning false for an exact retry."""
        self._require_writable()
        durable = json_object(payload)
        if not identity_fields or "_journal" in durable:
            raise ValueError("journal unique append requires safe identity fields")
        try:
            identity = tuple(durable[field] for field in identity_fields)
        except KeyError as error:
            raise ValueError("journal identity field is missing") from error
        with self._locked():
            parent_fd, descriptor = self._open_journal(create=True)
            try:
                payloads, records, size, prefix_size = self._read_descriptor(descriptor)
                self._verify_or_reconcile_head(descriptor, records, size, prefix_size)
                for existing in payloads:
                    if (
                        tuple(existing.get(field) for field in identity_fields)
                        != identity
                    ):
                        continue
                    if existing != durable:
                        raise RuntimeError("journal identity collision")
                    return False
                self._append_to_descriptor(
                    parent_fd, descriptor, durable, records, size
                )
                return True
            finally:
                os.close(descriptor)
                os.close(parent_fd)

    def _append_locked(self, durable: dict[str, Any]) -> None:
        parent_fd, descriptor = self._open_journal(create=True)
        try:
            _, records, size, prefix_size = self._read_descriptor(descriptor)
            self._verify_or_reconcile_head(descriptor, records, size, prefix_size)
            self._append_to_descriptor(parent_fd, descriptor, durable, records, size)
        finally:
            os.close(descriptor)
            os.close(parent_fd)

    def _append_to_descriptor(
        self,
        parent_fd: int,
        descriptor: int,
        durable: dict[str, Any],
        records: list[SecureRecord],
        size: int,
    ) -> None:
        prior = records[-1].record_sha256 if records else ZERO_HASH
        _, record, encoded = seal_record(self._key, durable, len(records) + 1, prior)
        os.lseek(descriptor, 0, os.SEEK_END)
        write_all(descriptor, encoded + b"\n")
        os.fsync(descriptor)
        self._require_unchanged_entry(parent_fd, descriptor)
        self._write_head(self._head(descriptor, record, size + len(encoded) + 1))
        self._require_unchanged_entry(parent_fd, descriptor)

    @contextmanager
    def _locked(self, *, create_control: bool | None = None) -> Iterator[None]:
        selected = (
            self._can_create_control if create_control is None else create_control
        )
        with secured_journal_lock(
            self.control,
            self._journal_id,
            self._key,
            self.verify_roots,
            create_control=selected,
        ):
            yield

    def _open_journal(self, *, create: bool) -> tuple[int, int]:
        if create and not self._writable:
            raise RuntimeError("secure journal is read-only")
        parent_fd = self.storage.open_directory(self._relative.parent, create=create)
        descriptor = -1
        try:
            descriptor, created = open_regular(
                parent_fd,
                self._relative.name,
                create=create,
                writable=self._writable,
            )
            metadata = os.fstat(descriptor)
            if created:
                os.fchmod(descriptor, 0o600)
                metadata = os.fstat(descriptor)
                os.fsync(parent_fd)
            require_safe_file(metadata, "journal")
            self._require_same_parent(parent_fd)
            return parent_fd, descriptor
        except BaseException:
            try:
                if descriptor >= 0:
                    os.close(descriptor)
            finally:
                os.close(parent_fd)
            raise

    def _read_descriptor(
        self, descriptor: int
    ) -> tuple[list[dict[str, Any]], list[SecureRecord], int, int]:
        os.lseek(descriptor, 0, os.SEEK_SET)
        data = read_all(descriptor)
        payloads, records = parse_records(data, self._key, self.path)
        lines = data.splitlines(keepends=True)
        prefix_size = len(data) - (len(lines[-1]) if lines else 0)
        return payloads, records, len(data), prefix_size

    def _verify_or_reconcile_head(
        self,
        descriptor: int,
        records: list[SecureRecord],
        size: int,
        prefix_size: int,
    ) -> None:
        actual = self._head(
            descriptor,
            records[-1] if records else None,
            size,
            count=len(records),
        )
        try:
            expected = self._read_head()
        except FileNotFoundError:
            if not self._can_create_control:
                raise
            self._write_head(actual)
            return
        if self._can_reconcile_head:
            repair_lagging_head(
                expected=expected,
                actual=actual,
                records=records,
                prefix_size=prefix_size,
                write_head=self._write_head,
            )
            return
        verify_journal_head_strict(
            expected=expected,
            actual=actual,
            records=records,
            prefix_size=prefix_size,
        )

    def _head(
        self,
        descriptor: int,
        record: SecureRecord | None,
        size: int,
        *,
        count: int | None = None,
    ) -> JournalHead:
        return build_journal_head(
            key=self._key,
            journal_id=self._journal_id,
            descriptor=descriptor,
            record=record,
            size=size,
            count=count,
        )

    def _read_head(self) -> JournalHead:
        return read_journal_head(self.control, self._journal_id, self._key)

    def _write_head(self, head: JournalHead) -> None:
        write_journal_head(self.control, self._journal_id, head)

    def _head_exists(self) -> bool:
        return self.control.exists(Path("journal-heads") / f"{self._journal_id}.json")

    def _require_unchanged_entry(self, parent_fd: int, descriptor: int) -> None:
        current = os.stat(self._relative.name, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(descriptor)
        require_safe_file(current, "journal")
        if identity(current) != identity(opened):
            raise ValueError("journal file replacement detected")
        self._require_same_parent(parent_fd)

    def _require_same_parent(self, parent_fd: int) -> None:
        with self.storage.directory(self._relative.parent) as current_fd:
            if identity(os.fstat(current_fd)) != identity(os.fstat(parent_fd)):
                raise ValueError("journal parent directory changed")

    def _require_writable(self) -> None:
        self._require_open()
        if not self._writable:
            raise RuntimeError("secure journal is read-only")

    @staticmethod
    def _require_attached(root: PinnedRoot) -> None:
        root.require_attached()

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("secure journal is closed")

"""Canonical Personal Validation event identities and chain verification."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import (
    STRICT,
    Sha256,
    StrictArtifactRef,
    canonical_json,
    require_nonblank,
)
from envresearch.personal_validation.errors import (
    PersonalValidationAuthorityInvalid,
    PersonalValidationIntegrityInvalid,
)
from envresearch.storage.secure_journal import SecureJournal
from envresearch.storage.secure_journal_records import JournalHead
from envresearch.storage.secure_journal_verify import repair_lagging_head
from envresearch.workers.filesystem import PinnedRoot

ZERO_PREDECESSOR = "0" * 64
_OPERATION = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def event_id(
    session_id: str,
    operation: str,
    object_ref: ArtifactRef,
    predecessor_sha256: str,
    sequence: int,
) -> str:
    payload = canonical_json(
        {
            "session_id": session_id,
            "operation": operation,
            "object_ref": object_ref.model_dump(mode="json"),
            "predecessor_sha256": predecessor_sha256,
            "sequence": sequence,
        }
    )
    return f"personal-event-{hashlib.sha256(payload).hexdigest()}"


class PersonalValidationEvent(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.validation-event.v1"]
    event_id: str
    session_id: str
    operation: str
    object_ref: StrictArtifactRef
    predecessor_sha256: Sha256
    sequence: int = Field(ge=1)

    @field_validator("session_id")
    @classmethod
    def require_session(cls, value: str) -> str:
        return require_nonblank(value)

    @field_validator("operation")
    @classmethod
    def require_operation(cls, value: str) -> str:
        if not _OPERATION.fullmatch(value):
            raise ValueError("event operation must be a canonical identifier")
        return value

    @model_validator(mode="after")
    def require_identity(self) -> PersonalValidationEvent:
        expected = event_id(
            self.session_id,
            self.operation,
            self.object_ref,
            self.predecessor_sha256,
            self.sequence,
        )
        if self.event_id != expected:
            raise ValueError("personal event identity mismatch")
        return self

    def event_sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def make_event(
    *,
    session_id: str,
    operation: str,
    object_ref: ArtifactRef,
    predecessor_sha256: str,
    sequence: int,
) -> PersonalValidationEvent:
    return PersonalValidationEvent(
        schema_version="personal.validation-event.v1",
        event_id=event_id(
            session_id,
            operation,
            object_ref,
            predecessor_sha256,
            sequence,
        ),
        session_id=session_id,
        operation=operation,
        object_ref=object_ref,
        predecessor_sha256=predecessor_sha256,
        sequence=sequence,
    )


def validate_event_history(
    payloads: list[dict[str, object]],
) -> tuple[PersonalValidationEvent, ...]:
    """Validate every session chain in journal order without mutation."""
    try:
        events = tuple(
            PersonalValidationEvent.model_validate(item) for item in payloads
        )
        seen_ids: set[str] = set()
        by_session: dict[str, list[PersonalValidationEvent]] = defaultdict(list)
        for event in events:
            if event.event_id in seen_ids:
                raise ValueError("duplicate personal event identity")
            seen_ids.add(event.event_id)
            session = by_session[event.session_id]
            expected_sequence = len(session) + 1
            expected_predecessor = (
                session[-1].event_sha256() if session else ZERO_PREDECESSOR
            )
            if event.sequence != expected_sequence:
                raise ValueError("personal event sequence gap or reuse")
            if event.predecessor_sha256 != expected_predecessor:
                raise ValueError("personal event predecessor mismatch")
            session.append(event)
        return events
    except PersonalValidationIntegrityInvalid:
        raise
    except (TypeError, ValueError) as error:
        raise PersonalValidationIntegrityInvalid(
            "personal event history is invalid",
            finding_kind="event-history-invalid",
        ) from error


def session_events(
    events: tuple[PersonalValidationEvent, ...], session_id: str
) -> tuple[PersonalValidationEvent, ...]:
    return tuple(event for event in events if event.session_id == session_id)


@dataclass(frozen=True, slots=True)
class PersonalWriterHistory:
    events: tuple[PersonalValidationEvent, ...]
    recovery_event_id: str | None


class PersonalEventJournal:
    """Strict-reader/writer-capability adapter over one borrowed secure journal."""

    def __init__(
        self,
        *,
        journals: PinnedRoot,
        control: PinnedRoot,
        writer: SecureJournal,
        verify_authority: Callable[[], None],
    ) -> None:
        self.journals = journals
        self.control = control
        self.writer = writer
        self.verify_authority = verify_authority
        self.path = writer.path

    def read_strict(self) -> tuple[PersonalValidationEvent, ...]:
        """Read through a fresh never-reconcile capability."""
        self.verify_authority()
        try:
            with SecureJournal.open_existing(
                self.path,
                storage_root=self.journals,
                control_root=self.control,
                reconcile=False,
            ) as reader:
                events = validate_event_history(reader.read_all())
            self.verify_authority()
            return events
        except PersonalValidationIntegrityInvalid:
            raise
        except (OSError, TypeError, ValueError, ValidationError) as error:
            raise PersonalValidationIntegrityInvalid(
                "Personal event history cannot be reopened without recovery",
                finding_kind="event-history-unavailable",
            ) from error

    def require_control(self) -> None:
        """Authenticate the retained key, journal lock, anchor, and head."""
        try:
            key = self.control.read_file(
                Path("queue.key"),
                description="journal key",
                required_mode=0o600,
                required_owner=os.geteuid(),
            )
            if not hmac.compare_digest(key, self.writer._key):
                raise ValueError("journal key changed")
            with self.writer._locked(create_control=False):
                self.writer._read_head()
        except (OSError, TypeError, ValueError) as error:
            raise PersonalValidationAuthorityInvalid(
                "Personal journal control authority changed",
                finding_kind="journal-control-invalid",
            ) from error

    def inspect_writer(self) -> PersonalWriterHistory:
        """Inspect a writer and report, but never repair, an exact one-record lag."""
        return self._read_writer(recover_event_id=None)

    def recover_expected(self, event_identity: str) -> None:
        """Repair a one-record lag only when its event is the caller's exact retry."""
        self._read_writer(recover_event_id=event_identity)

    def _read_writer(self, *, recover_event_id: str | None) -> PersonalWriterHistory:
        try:
            pending: list[JournalHead] = []
            with self.writer._locked(create_control=False):
                parent_fd, descriptor = self.writer._open_journal(create=False)
                try:
                    payloads, records, size, prefix_size = self.writer._read_descriptor(
                        descriptor
                    )
                    events = validate_event_history(payloads)
                    actual = self.writer._head(
                        descriptor,
                        records[-1] if records else None,
                        size,
                        count=len(records),
                    )

                    def recover_or_capture(head: JournalHead) -> None:
                        pending.append(head)
                        if recover_event_id is None:
                            return
                        if not events or events[-1].event_id != recover_event_id:
                            raise event_divergence()
                        self.writer._write_head(head)

                    repair_lagging_head(
                        expected=self.writer._read_head(),
                        actual=actual,
                        records=records,
                        prefix_size=prefix_size,
                        write_head=recover_or_capture,
                    )
                    self.writer._require_unchanged_entry(parent_fd, descriptor)
                finally:
                    os.close(descriptor)
                    os.close(parent_fd)
            self.verify_authority()
            return PersonalWriterHistory(
                events=events,
                recovery_event_id=(events[-1].event_id if pending and events else None),
            )
        except PersonalValidationIntegrityInvalid:
            raise
        except (OSError, TypeError, ValueError, ValidationError) as error:
            raise PersonalValidationIntegrityInvalid(
                "Personal writer event history is invalid",
                finding_kind="event-history-invalid",
            ) from error

    def require(self, identity: str) -> PersonalValidationEvent:
        matches = tuple(
            event for event in self.read_strict() if event.event_id == identity
        )
        if len(matches) != 1:
            raise PersonalValidationIntegrityInvalid(
                "required Personal event is missing or duplicated",
                finding_kind="event-missing",
            )
        return matches[0]

    def append_expected(
        self,
        *,
        session_id: str,
        operation: str,
        object_ref: ArtifactRef,
        expected_sequence: int,
    ) -> PersonalValidationEvent:
        """Validate, recover exactly, or append beneath one journal lock."""
        self.verify_authority()
        try:
            with self.writer._locked(create_control=False):
                parent_fd, descriptor = self.writer._open_journal(create=False)
                try:
                    payloads, records, size, prefix_size = self.writer._read_descriptor(
                        descriptor
                    )
                    events = validate_event_history(payloads)
                    actual = self.writer._head(
                        descriptor,
                        records[-1] if records else None,
                        size,
                        count=len(records),
                    )
                    pending: list[JournalHead] = []
                    repair_lagging_head(
                        expected=self.writer._read_head(),
                        actual=actual,
                        records=records,
                        prefix_size=prefix_size,
                        write_head=pending.append,
                    )
                    current = session_events(events, session_id)
                    if expected_sequence > len(current) + 1:
                        raise event_divergence()
                    predecessor = (
                        current[expected_sequence - 2].event_sha256()
                        if expected_sequence > 1
                        else ZERO_PREDECESSOR
                    )
                    candidate = make_event(
                        session_id=session_id,
                        operation=operation,
                        object_ref=object_ref,
                        predecessor_sha256=predecessor,
                        sequence=expected_sequence,
                    )
                    if expected_sequence <= len(current):
                        if current[expected_sequence - 1] != candidate:
                            raise event_divergence()
                        if pending and events and events[-1] == candidate:
                            self.writer._write_head(actual)
                    else:
                        if pending:
                            raise PersonalValidationIntegrityInvalid(
                                "Personal event recovery differs from requested append",
                                finding_kind="event-recovery-not-exact",
                            )
                        self.writer._append_to_descriptor(
                            parent_fd,
                            descriptor,
                            candidate.model_dump(mode="json"),
                            records,
                            size,
                        )
                        reopened, _, _, _ = self.writer._read_descriptor(descriptor)
                        if validate_event_history(reopened) != (*events, candidate):
                            raise PersonalValidationIntegrityInvalid(
                                "Personal event did not reopen exactly",
                                finding_kind="event-publication-invalid",
                            )
                    self.writer._require_unchanged_entry(parent_fd, descriptor)
                finally:
                    os.close(descriptor)
                    os.close(parent_fd)
            self.verify_authority()
            return candidate
        except PersonalValidationIntegrityInvalid:
            raise
        except (OSError, TypeError, ValueError, ValidationError) as error:
            raise PersonalValidationIntegrityInvalid(
                "Personal writer event history is invalid",
                finding_kind="event-history-invalid",
            ) from error


def event_divergence() -> PersonalValidationIntegrityInvalid:
    return PersonalValidationIntegrityInvalid(
        "Personal event sequence was divergently reused",
        finding_kind="event-sequence-divergent",
    )


__all__ = [
    "ZERO_PREDECESSOR",
    "PersonalEventJournal",
    "PersonalValidationEvent",
    "event_id",
    "make_event",
    "session_events",
    "validate_event_history",
]

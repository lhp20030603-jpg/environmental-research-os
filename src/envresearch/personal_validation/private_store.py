"""Owner-private canonical objects and strict Personal event persistence."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from itertools import combinations
from pathlib import Path
from types import TracebackType
from typing import Self, TypeVar

from pydantic import BaseModel, ValidationError

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation.contracts import PersonalValidationAttempt
from envresearch.personal_validation.errors import (
    PersonalValidationAuthorityInvalid,
    PersonalValidationError,
)
from envresearch.personal_validation.events import (
    PersonalEventJournal,
    PersonalValidationEvent,
    PersonalWriterHistory,
)
from envresearch.personal_validation.roots import (
    PersonalPinnedRoot,
    PersonalRootAuthorityManifest,
    PersonalSessionLockLease,
    RootExclusionSet,
    ensure_personal_session_lock,
    personal_session_lock,
    publish_root_authority_manifest,
    require_exact_root_authority_manifest,
    require_private_validation_root,
)
from envresearch.personal_validation.targets import (
    PersonalObjectRegistry,
    require_exact_object_layout,
)
from envresearch.storage.secure_journal import SecureJournal
from envresearch.workers.filesystem import directories_overlap

Payload = TypeVar("Payload", bound=BaseModel)
_JOURNAL_PATH = Path("personal-validation.jsonl")


class PersonalValidationStore:
    """Sole owner of one exact top root and all borrowed storage pins."""

    def __init__(
        self,
        *,
        root: PersonalPinnedRoot,
        objects: PersonalPinnedRoot,
        journals: PersonalPinnedRoot,
        control: PersonalPinnedRoot,
        registry: ExitRegistry,
        journal: SecureJournal,
        manifest: PersonalRootAuthorityManifest,
        exclusions: RootExclusionSet,
        writable: bool,
    ) -> None:
        self.root = root
        self.objects = objects
        self.journals = journals
        self.control = control
        self.registry = registry
        self.journal = journal
        self.manifest = manifest
        self.exclusions = exclusions
        self.writable = writable
        self._session_lease: PersonalSessionLockLease | None = None
        self._owned = ExitStack()
        self._closed = False
        self.events = PersonalEventJournal(
            journals=journals,
            control=control,
            writer=journal,
            verify_authority=self._require_authority,
        )
        self.object_store = PersonalObjectRegistry(
            registry=registry,
            objects=objects,
            verify_authority=self._require_authority,
            require_writable=self._require_writable,
        )

    @classmethod
    def create(
        cls, private_root: Path, exclusions: RootExclusionSet
    ) -> PersonalValidationStore:
        try:
            return cls._create(private_root, exclusions)
        except PersonalValidationError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise PersonalValidationAuthorityInvalid(
                "Personal store composition authority is invalid",
                finding_kind="private-store-composition-invalid",
            ) from error

    @classmethod
    def _create(
        cls, private_root: Path, exclusions: RootExclusionSet
    ) -> PersonalValidationStore:
        with ExitStack() as owned:
            root = owned.enter_context(
                require_private_validation_root(private_root, exclusions, create=True)
            )
            existing_manifest = root.exists(Path("root-authority-manifest.json"))
            if existing_manifest:
                require_exact_root_authority_manifest(root, exclusions)
            objects = owned.enter_context(
                _open_child(root, Path("objects"), create=not existing_manifest)
            )
            root.require_attached()
            journals = owned.enter_context(
                _open_child(root, Path("journals"), create=not existing_manifest)
            )
            root.require_attached()
            control = owned.enter_context(
                _open_child(root, Path("control/journal"), create=not existing_manifest)
            )
            _require_child_composition(root, objects, journals, control)
            if existing_manifest:
                require_exact_object_layout(objects)
            registry = ExitRegistry.from_pinned(objects, create=not existing_manifest)
            root.require_attached()
            if existing_manifest:
                journal = owned.enter_context(
                    SecureJournal.open_for_recovery(
                        journals.lexical_path / _JOURNAL_PATH,
                        storage_root=journals,
                        control_root=control,
                    )
                )
                journal._writable = True
            else:
                journal = owned.enter_context(
                    SecureJournal.create_from_pinned(
                        journals.lexical_path / _JOURNAL_PATH,
                        storage_root=journals,
                        control_root=control,
                    )
                )
                journal.ensure()
            ensure_personal_session_lock(
                objects, journal._key, create=not existing_manifest
            )
            journal._can_create_control = False
            journal._can_reconcile_head = False
            registry.create = False
            manifest = publish_root_authority_manifest(root, exclusions)
            _require_child_composition(root, objects, journals, control)
            store = cls(
                root=root,
                objects=objects,
                journals=journals,
                control=control,
                registry=registry,
                journal=journal,
                manifest=manifest,
                exclusions=exclusions,
                writable=True,
            )
            store._owned = owned.pop_all()
            return store

    @classmethod
    def open_existing(
        cls, private_root: Path, exclusions: RootExclusionSet
    ) -> PersonalValidationStore:
        try:
            return cls._open_existing(private_root, exclusions)
        except PersonalValidationError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise PersonalValidationAuthorityInvalid(
                "Personal store reopen authority is invalid",
                finding_kind="private-store-composition-invalid",
            ) from error

    @classmethod
    def _open_existing(
        cls, private_root: Path, exclusions: RootExclusionSet
    ) -> PersonalValidationStore:
        with ExitStack() as owned:
            root = owned.enter_context(
                require_private_validation_root(private_root, exclusions, create=False)
            )
            manifest = require_exact_root_authority_manifest(root, exclusions)
            objects = owned.enter_context(
                _open_child(root, Path("objects"), create=False)
            )
            journals = owned.enter_context(
                _open_child(root, Path("journals"), create=False)
            )
            control = owned.enter_context(
                _open_child(root, Path("control/journal"), create=False)
            )
            _require_child_composition(root, objects, journals, control)
            registry = ExitRegistry.from_pinned(objects, create=False)
            journal = owned.enter_context(
                SecureJournal.open_existing(
                    journals.lexical_path / _JOURNAL_PATH,
                    storage_root=journals,
                    control_root=control,
                    reconcile=False,
                )
            )
            ensure_personal_session_lock(objects, journal._key, create=False)
            require_exact_root_authority_manifest(root, exclusions)
            store = cls(
                root=root,
                objects=objects,
                journals=journals,
                control=control,
                registry=registry,
                journal=journal,
                manifest=manifest,
                exclusions=exclusions,
                writable=False,
            )
            store._owned = owned.pop_all()
            return store

    def __enter__(self) -> Self:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Validate the end of the lease and release every owned pin once."""
        if self._closed:
            return
        self._closed = True
        authority_error: BaseException | None = None
        try:
            self._require_authority(allow_closed=True)
        except (PersonalValidationError, OSError, TypeError, ValueError) as error:
            authority_error = error
        close_error: BaseException | None = None
        try:
            self._owned.close()
        except (OSError, TypeError, ValueError) as error:
            close_error = error
        if authority_error is not None:
            raise authority_error
        if close_error is not None:
            raise close_error

    def publish(self, artifact_id: str, payload: Payload) -> ArtifactRef:
        """Publish one exact canonical object beneath the retained object pin."""
        return self.object_store.publish(artifact_id, payload)

    def load(self, reference: ArtifactRef, model: type[Payload]) -> Payload:
        """Strictly reopen one explicit canonical object without mutation."""
        return self.object_store.load(reference, model)

    def read_events(self) -> tuple[PersonalValidationEvent, ...]:
        """Use a fresh never-reconcile reader even when this store is writable."""
        return self.events.read_strict()

    def require_event(self, event_id: str) -> PersonalValidationEvent:
        return self.events.require(event_id)

    @contextmanager
    def session_lock(self, session_id: str) -> Iterator[None]:
        """Hold one descriptor-relative session lock across a complete writer."""
        self._require_writable()
        self._require_authority()
        try:
            with personal_session_lock(self.objects, self.journal._key) as lease:
                self._session_lease = lease
                try:
                    self._require_authority()
                    yield
                    self._require_authority()
                finally:
                    self._session_lease = None
        except PersonalValidationError:
            raise
        except (OSError, TypeError, ValueError, ValidationError) as error:
            raise PersonalValidationAuthorityInvalid(
                "Personal session lock authority is invalid",
                finding_kind="session-lock-invalid",
            ) from error

    def _writer_events(self) -> PersonalWriterHistory:
        self._require_writable()
        return self.events.inspect_writer()

    def _recover_event_head_locked(self, event_id: str) -> None:
        self._require_writable()
        self.events.recover_expected(event_id)

    def _append_event_locked(
        self,
        *,
        session_id: str,
        operation: str,
        object_ref: ArtifactRef,
        expected_sequence: int,
    ) -> PersonalValidationEvent:
        self._require_writable()
        return self.events.append_expected(
            session_id=session_id,
            operation=operation,
            object_ref=object_ref,
            expected_sequence=expected_sequence,
        )

    def attempt_objects(
        self,
    ) -> tuple[tuple[ArtifactRef, PersonalValidationAttempt], ...]:
        """Enumerate only attempt objects to detect an explicit orphan boundary."""
        return self.object_store.attempts()

    def _require_open(self) -> None:
        if self._closed:
            raise PersonalValidationAuthorityInvalid(
                "Personal store is closed", finding_kind="private-store-closed"
            )

    def _require_writable(self) -> None:
        self._require_open()
        if not self.writable:
            raise PersonalValidationAuthorityInvalid(
                "Personal store is read-only", finding_kind="private-store-read-only"
            )

    def _require_authority(self, *, allow_closed: bool = False) -> None:
        if not allow_closed:
            self._require_open()
        actual = require_exact_root_authority_manifest(self.root, self.exclusions)
        if actual != self.manifest:
            raise PersonalValidationAuthorityInvalid(
                "Personal root manifest changed during its lease",
                finding_kind="private-root-authority-changed",
            )
        _require_child_composition(self.root, self.objects, self.journals, self.control)
        require_exact_object_layout(self.objects)
        if self._session_lease is not None:
            try:
                self._session_lease.require_valid()
            except (OSError, TypeError, ValueError) as error:
                raise PersonalValidationAuthorityInvalid(
                    "Personal session lock authority changed",
                    finding_kind="session-lock-invalid",
                ) from error
        self.events.require_control()


def _open_child(
    root: PersonalPinnedRoot, relative: Path, *, create: bool
) -> PersonalPinnedRoot:
    exists = True
    try:
        descriptor = root.open_directory(relative, create=False)
        os.close(descriptor)
    except FileNotFoundError:
        exists = False
    return root.open_child_root(relative, private=True, create=create and not exists)


def _require_child_composition(
    root: PersonalPinnedRoot,
    objects: PersonalPinnedRoot,
    journals: PersonalPinnedRoot,
    control: PersonalPinnedRoot,
) -> None:
    root.require_attached()
    if not objects.is_exact_descendant_of(root, Path("objects")):
        raise PersonalValidationAuthorityInvalid(
            "Personal object root changed", finding_kind="private-child-root-invalid"
        )
    if not journals.is_exact_descendant_of(root, Path("journals")):
        raise PersonalValidationAuthorityInvalid(
            "Personal journal root changed", finding_kind="private-child-root-invalid"
        )
    if not control.is_exact_descendant_of(root, Path("control/journal")):
        raise PersonalValidationAuthorityInvalid(
            "Personal control root changed", finding_kind="private-child-root-invalid"
        )
    for left, right in combinations((objects, journals, control), 2):
        if directories_overlap(left.fd, right.fd):
            raise PersonalValidationAuthorityInvalid(
                "Personal child roots overlap",
                finding_kind="private-child-root-overlap",
            )
    root.require_attached()

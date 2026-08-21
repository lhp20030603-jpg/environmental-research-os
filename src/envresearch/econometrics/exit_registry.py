"""Small exact-reference registry for blinded V0.3 exit artifacts."""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from envresearch.econometrics._store_files import StoreFiles
from envresearch.models.artifact import ArtifactRef
from envresearch.workers.filesystem import PinnedRoot

Payload = TypeVar("Payload", bound=BaseModel)


class ExitRegistry:
    """Persist canonical JSON objects and exact current pointers."""

    def __init__(self, root: Path, *, create: bool = True) -> None:
        if not root.is_absolute() or root.is_symlink():
            raise ValueError("exit registry root must be absolute and non-symlink")
        self.root = root.resolve()
        self.create = create
        self.files = StoreFiles(self.root)
        self._pinned: PinnedRoot | None = None
        if not create:
            if not self.root.is_dir():
                raise FileNotFoundError(self.root)
            return
        self.files.ensure_directory(Path("exit/objects"))
        self.files.ensure_directory(Path("exit/current"))
        self.files.ensure_directory(Path("exit/locks"))
        os.chmod(self.root, 0o700)

    @classmethod
    def from_pinned(cls, root: PinnedRoot, *, create: bool) -> ExitRegistry:
        """Borrow a retained root without reopening its lexical path."""
        instance = cls.__new__(cls)
        instance.root = root.path
        instance.create = create
        instance.files = StoreFiles.from_pinned(root)
        instance._pinned = root
        if create:
            for path in (
                Path("exit/objects"),
                Path("exit/current"),
                Path("exit/locks"),
            ):
                instance.files.ensure_directory(path)
        return instance

    def publish(
        self, artifact_id: str, payload: BaseModel, *, version: int = 1
    ) -> ArtifactRef:
        """Publish immutable canonical bytes and return their exact reference."""
        _identifier(artifact_id)
        data = payload.model_dump_json(indent=None).encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        relative = _object_path(artifact_id, version, digest)
        self.files.persist_exact(relative, data)
        return ArtifactRef(
            artifact_id=artifact_id, artifact_version=version, content_hash=digest
        )

    def publish_bytes(
        self, artifact_id: str, data: bytes, *, version: int = 1
    ) -> ArtifactRef:
        """Publish immutable raw input bytes under one content-addressed ref."""
        _identifier(artifact_id)
        digest = hashlib.sha256(data).hexdigest()
        self.files.persist_exact(_data_path(artifact_id, version, digest), data)
        return ArtifactRef(
            artifact_id=artifact_id, artifact_version=version, content_hash=digest
        )

    def load_bytes(self, reference: ArtifactRef) -> bytes:
        """Reopen and authenticate one exact raw input object."""
        data = self.files.read(
            _data_path(
                reference.artifact_id,
                reference.artifact_version,
                reference.content_hash,
            )
        )
        if hashlib.sha256(data).hexdigest() != reference.content_hash:
            raise ValueError("exit data content hash mismatch")
        return data

    def materialize_data(self, reference: ArtifactRef, *, suffix: str = ".bin") -> Path:
        """Authenticate exact bytes and provide a read-only typed local view."""
        if suffix not in {".bin", ".csv"}:
            raise ValueError("exit data materialization suffix is not registered")
        source = _data_path(
            reference.artifact_id,
            reference.artifact_version,
            reference.content_hash,
        )
        data = self.load_bytes(reference)
        if suffix == ".bin":
            return self.root / source
        target = source.with_suffix(suffix)
        self.files.persist_exact(target, data)
        return self.root / target

    def load(self, reference: ArtifactRef, model: type[Payload]) -> Payload:
        """Reopen exact bytes and validate the requested payload model."""
        data = self.files.read(
            _object_path(
                reference.artifact_id,
                reference.artifact_version,
                reference.content_hash,
            )
        )
        if hashlib.sha256(data).hexdigest() != reference.content_hash:
            raise ValueError("exit object content hash mismatch")
        return model.model_validate_json(data)

    def set_current(self, subject: str, reference: ArtifactRef) -> None:
        """Atomically update one authenticated exact-reference pointer."""
        _identifier(subject)
        data = reference.model_dump_json().encode("utf-8")
        relative = Path("exit/current") / f"{subject}.json"
        self.files.write(relative, data)

    def current(self, subject: str) -> ArtifactRef | None:
        """Read one current pointer, returning None only when absent."""
        _identifier(subject)
        try:
            data = self.files.read(Path("exit/current") / f"{subject}.json")
        except FileNotFoundError:
            return None
        reference = ArtifactRef.model_validate_json(data)
        object_bytes = self.files.read(
            _object_path(
                reference.artifact_id,
                reference.artifact_version,
                reference.content_hash,
            )
        )
        if hashlib.sha256(object_bytes).hexdigest() != reference.content_hash:
            raise ValueError("exit object content hash mismatch")
        return reference

    def restore_current_if_unchanged(
        self,
        subject: str,
        *,
        installed: ArtifactRef,
        previous: ArtifactRef | None,
    ) -> bool:
        """Restore a pointer only if this transaction still owns its update."""
        _identifier(subject)
        relative = Path("exit/current") / f"{subject}.json"
        try:
            data = self.files.read(relative)
        except FileNotFoundError:
            return previous is None
        current = ArtifactRef.model_validate_json(data)
        if current == previous:
            return True
        if current != installed:
            return False
        if previous is None:
            self.files.unlink(relative)
        else:
            self.set_current(subject, previous)
        return True

    @contextmanager
    def lock(self, subject: str) -> Iterator[None]:
        """Serialize one manifest run across processes."""
        _identifier(subject)
        descriptor = self.files.open_lock(
            Path("exit/locks") / f"{subject}.lock", create=self.create
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError("exit lock must be a regular file")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def validate_separate_roots(runner: Path, evaluator: Path) -> tuple[Path, Path]:
    """Require physically separate, non-aliased runner/evaluator roots."""
    if runner.is_symlink() or evaluator.is_symlink():
        raise ValueError("exit roots must not be symlinks")
    left, right = runner.resolve(), evaluator.resolve()
    if left == right or left.is_relative_to(right) or right.is_relative_to(left):
        raise ValueError("runner and evaluator roots must not overlap")
    return left, right


def _identifier(value: str) -> str:
    if not value or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-._"
        for character in value
    ):
        raise ValueError("exit registry identifier is not canonical")
    return value


def _object_path(artifact_id: str, version: int, digest: str) -> Path:
    _identifier(artifact_id)
    return Path("exit/objects") / artifact_id / f"v{version}-{digest}.json"


def _data_path(artifact_id: str, version: int, digest: str) -> Path:
    _identifier(artifact_id)
    return Path("exit/data") / artifact_id / f"v{version}-{digest}.bin"

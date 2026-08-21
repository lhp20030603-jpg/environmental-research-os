"""Descriptor-pinned owner-private root authority for Personal Validation."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from envresearch.personal_validation._strict import (
    STRICT,
    canonical_json,
    materialize_id,
    require_nonblank,
)
from envresearch.personal_validation.errors import PersonalValidationAuthorityInvalid
from envresearch.storage.secure_journal_lock import (
    _require_anchor,
    _require_named_lock,
    secured_journal_lock,
)
from envresearch.workers.filesystem import PinnedRoot, directories_overlap
from envresearch.workers.native import locked_directory_at, locked_regular_at

_MANIFEST_PATH = Path("root-authority-manifest.json")
_SESSION_LOCK_ID = "personal-validation-session"
_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
)


@dataclass(frozen=True, slots=True)
class RootExclusionSet:
    """Every physical authority tree forbidden to Personal writes."""

    repository: Path
    git_common_dir: Path
    worktrees: tuple[Path, ...]
    obsidian_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", Path(self.repository))
        object.__setattr__(self, "git_common_dir", Path(self.git_common_dir))
        object.__setattr__(
            self, "worktrees", tuple(Path(path) for path in self.worktrees)
        )
        object.__setattr__(
            self,
            "obsidian_roots",
            tuple(Path(path) for path in self.obsidian_roots),
        )
        if not self.worktrees:
            raise ValueError("at least one explicit worktree root is required")

    @property
    def repository_root(self) -> Path:
        return self.repository

    @property
    def worktree_roots(self) -> tuple[Path, ...]:
        return self.worktrees


class PinnedDirectoryIdentity(BaseModel):
    model_config = STRICT
    logical_name: str
    canonical_path: str
    device: int = Field(ge=0)
    inode: int = Field(ge=0)

    @field_validator("logical_name")
    @classmethod
    def require_name(cls, value: str) -> str:
        return require_nonblank(value)

    @field_validator("canonical_path")
    @classmethod
    def require_absolute_canonical_path(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute() or str(path) != value:
            raise ValueError("directory identity path must be absolute")
        return value


class PersonalRootAuthorityManifest(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.root-authority-manifest.v1"]
    manifest_id: str
    private_root: PinnedDirectoryIdentity
    exclusion_roots: tuple[PinnedDirectoryIdentity, ...] = Field(min_length=1)

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"manifest_id"})

    @model_validator(mode="after")
    def require_canonical_identity(self) -> PersonalRootAuthorityManifest:
        names = tuple(item.logical_name for item in self.exclusion_roots)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("exclusion identities must use canonical logical names")
        if self.private_root.logical_name != "private-validation":
            raise ValueError("private root identity has the wrong logical name")
        expected = materialize_id("personal-root-manifest-", self.identity_payload())
        if self.manifest_id != expected:
            raise ValueError("personal root manifest identity mismatch")
        return self


class PersonalPinnedRoot(PinnedRoot):
    """PinnedRoot with exact relative-identity predicates for composition."""

    def is_exact_child_of(self, parent: PinnedRoot) -> bool:
        return self.is_exact_descendant_of(parent, Path(self.lexical_path.name))

    def is_exact_descendant_of(self, parent: PinnedRoot, relative: Path) -> bool:
        binding = self._parent_binding
        if binding is None or binding != (parent, relative):
            return False
        try:
            parent.require_attached()
            self.require_attached()
            self.require_parent(parent.fd, relative)
        except (OSError, ValueError):
            return False
        return True


@dataclass(frozen=True, slots=True)
class PersonalSessionLockLease:
    control: PinnedRoot
    parent_fd: int
    descriptor: int
    key: bytes

    def require_valid(self) -> None:
        _require_anchor(
            self.control,
            _SESSION_LOCK_ID,
            self.key,
            self.descriptor,
            create_control=False,
        )
        _require_named_lock(
            self.control,
            self.parent_fd,
            f"{_SESSION_LOCK_ID}.filelock",
            self.descriptor,
        )


def ensure_personal_session_lock(
    control: PinnedRoot, key: bytes, *, create: bool
) -> None:
    if create:
        control.ensure_directory(Path("journal-locks"))
        control.ensure_directory(Path("journal-lock-anchors"))
    with secured_journal_lock(
        control,
        _SESSION_LOCK_ID,
        key,
        control.require_attached,
        create_control=create,
    ):
        pass


@contextmanager
def personal_session_lock(
    control: PinnedRoot, key: bytes
) -> Iterator[PersonalSessionLockLease]:
    name = f"{_SESSION_LOCK_ID}.filelock"
    with locked_directory_at(control.fd):
        control.require_attached()
        with (
            control.directory(Path("journal-locks")) as parent_fd,
            locked_regular_at(parent_fd, name) as descriptor,
        ):
            lease = PersonalSessionLockLease(control, parent_fd, descriptor, key)
            lease.require_valid()
            try:
                yield lease
            finally:
                lease.require_valid()
                control.require_attached()


def require_private_validation_root(
    private_root: Path, exclusions: RootExclusionSet, *, create: bool
) -> PersonalPinnedRoot:
    """Pin one exact owner/0700 root after proving physical separation."""
    path = Path(private_root)
    try:
        identities = capture_exclusion_identities(exclusions)
        _require_absolute_nonsymlink(path, description="private validation root")
        candidate = _candidate_path(path)
        _require_path_separation(candidate, identities)
        if not path.exists() and create:
            _mkdir_private(path)
        root = PersonalPinnedRoot(path, private=True, create=False)
        try:
            _require_private_metadata(root)
            _require_physical_separation(root, exclusions)
            root.require_attached()
            return root
        except BaseException:
            root.close()
            raise
    except PersonalValidationAuthorityInvalid:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise PersonalValidationAuthorityInvalid(
            "private validation root authority is invalid",
            finding_kind="private-root-invalid",
        ) from error


def publish_root_authority_manifest(
    root: PersonalPinnedRoot, exclusions: RootExclusionSet
) -> PersonalRootAuthorityManifest:
    """Publish or exactly reuse the authenticated root binding."""
    manifest = expected_root_authority_manifest(root, exclusions)
    encoded = canonical_json(manifest.model_dump(mode="json"))
    try:
        try:
            root.write_file_noreplace(_MANIFEST_PATH, encoded, mode=0o600)
        except FileExistsError:
            pass
        actual = _read_manifest(root)
        if actual != manifest:
            raise ValueError("root authority manifest differs from current authority")
        root.require_attached()
        return actual
    except PersonalValidationAuthorityInvalid:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise PersonalValidationAuthorityInvalid(
            "private root manifest publication failed",
            finding_kind="private-root-manifest-invalid",
        ) from error


def require_exact_root_authority_manifest(
    root: PersonalPinnedRoot, exclusions: RootExclusionSet
) -> PersonalRootAuthorityManifest:
    """Strictly revalidate current paths, inodes, and immutable manifest bytes."""
    try:
        _require_private_metadata(root)
        _require_physical_separation(root, exclusions)
        expected = expected_root_authority_manifest(root, exclusions)
        actual = _read_manifest(root)
        if actual != expected:
            raise ValueError("root authority manifest differs from current authority")
        root.require_attached()
        return actual
    except PersonalValidationAuthorityInvalid:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise PersonalValidationAuthorityInvalid(
            "private root authority changed",
            finding_kind="private-root-authority-changed",
        ) from error


def expected_root_authority_manifest(
    root: PersonalPinnedRoot, exclusions: RootExclusionSet
) -> PersonalRootAuthorityManifest:
    payload: dict[str, object] = {
        "schema_version": "personal.root-authority-manifest.v1",
        "private_root": _identity("private-validation", root),
        "exclusion_roots": capture_exclusion_identities(exclusions),
    }
    payload["manifest_id"] = materialize_id("personal-root-manifest-", payload)
    return PersonalRootAuthorityManifest.model_validate(payload)


def capture_exclusion_identities(
    exclusions: RootExclusionSet,
) -> tuple[PinnedDirectoryIdentity, ...]:
    """Capture a canonical complete set without retaining borrowed descriptors."""
    entries = _exclusion_entries(exclusions)
    try:
        with ExitStack() as stack:
            captured = []
            for logical_name, path in entries:
                _require_absolute_nonsymlink(path, description=logical_name)
                pinned = stack.enter_context(PinnedRoot(path, create=False))
                pinned.require_attached()
                captured.append(_identity(logical_name, pinned))
            return tuple(sorted(captured, key=lambda item: item.logical_name))
    except (OSError, TypeError, ValueError) as error:
        raise PersonalValidationAuthorityInvalid(
            "an exclusion root is invalid",
            finding_kind="exclusion-root-invalid",
        ) from error


def _read_manifest(root: PersonalPinnedRoot) -> PersonalRootAuthorityManifest:
    data = root.read_file(
        _MANIFEST_PATH,
        description="personal root authority manifest",
        required_mode=0o600,
        required_owner=os.geteuid(),
    )
    manifest = PersonalRootAuthorityManifest.model_validate_json(data)
    if data != canonical_json(manifest.model_dump(mode="json")):
        raise ValueError("personal root authority manifest is noncanonical")
    return manifest


def _identity(logical_name: str, root: PinnedRoot) -> PinnedDirectoryIdentity:
    metadata = os.fstat(root.fd)
    return PinnedDirectoryIdentity(
        logical_name=logical_name,
        canonical_path=str(root.path),
        device=metadata.st_dev,
        inode=metadata.st_ino,
    )


def _exclusion_entries(
    exclusions: RootExclusionSet,
) -> tuple[tuple[str, Path], ...]:
    worktrees = tuple(sorted(exclusions.worktrees, key=lambda path: str(path)))
    obsidian = tuple(sorted(exclusions.obsidian_roots, key=lambda path: str(path)))
    if len(worktrees) != len(set(worktrees)) or len(obsidian) != len(set(obsidian)):
        raise PersonalValidationAuthorityInvalid(
            "exclusion root lists contain duplicates",
            finding_kind="exclusion-root-invalid",
        )
    return (
        ("git-common", exclusions.git_common_dir),
        ("repository", exclusions.repository),
        *((f"worktree-{index:04d}", path) for index, path in enumerate(worktrees)),
        *((f"obsidian-{index:04d}", path) for index, path in enumerate(obsidian)),
    )


def _require_private_metadata(root: PinnedRoot) -> None:
    metadata = os.fstat(root.fd)
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ValueError("private validation root must be owner-only")


def _require_physical_separation(
    root: PinnedRoot, exclusions: RootExclusionSet
) -> None:
    with ExitStack() as stack:
        for _, path in _exclusion_entries(exclusions):
            pinned = stack.enter_context(PinnedRoot(path, create=False))
            if directories_overlap(root.fd, pinned.fd):
                raise PersonalValidationAuthorityInvalid(
                    "private validation root overlaps an excluded authority",
                    finding_kind="private-root-overlap",
                )


def _require_path_separation(
    candidate: Path, exclusions: tuple[PinnedDirectoryIdentity, ...]
) -> None:
    for identity in exclusions:
        excluded = Path(identity.canonical_path)
        if (
            candidate == excluded
            or candidate.is_relative_to(excluded)
            or excluded.is_relative_to(candidate)
        ):
            raise PersonalValidationAuthorityInvalid(
                "private validation root overlaps an excluded authority",
                finding_kind="private-root-overlap",
            )


def _require_absolute_nonsymlink(path: Path, *, description: str) -> None:
    if not path.is_absolute() or not path.name or path.is_symlink():
        raise ValueError(f"{description} must be absolute and non-symlink")


def _candidate_path(path: Path) -> Path:
    if path.exists():
        return path.resolve(strict=True)
    return path.parent.resolve(strict=True) / path.name


def _mkdir_private(path: Path) -> None:
    parent = path.parent.resolve(strict=True)
    descriptor = os.open(parent, _DIRECTORY_FLAGS)
    try:
        try:
            os.mkdir(path.name, 0o700, dir_fd=descriptor)
            os.fsync(descriptor)
        except FileExistsError:
            pass
    finally:
        os.close(descriptor)

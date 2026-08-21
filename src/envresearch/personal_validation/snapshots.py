from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path, PurePosixPath
from typing import Literal, TypeAlias

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import canonical_json, materialize_id
from envresearch.personal_validation.contracts import (
    PERSONAL_ATTEMPT_ROOTS_V1,
    AttemptRootInventory,
    InputEntry,
    InputSnapshot,
    RootIdentity,
    RootInventoryEntry,
    SystemSnapshot,
)
from envresearch.personal_validation.errors import PersonalValidationIntegrityInvalid
from envresearch.workers.filesystem import PinnedRoot, directories_overlap

_READ_FLAGS = (
    os.O_RDONLY
    | os.O_CLOEXEC
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
)


@dataclass(frozen=True, slots=True)
class _ObservedEntry:
    relative_path: str
    kind: Literal["file", "directory", "symlink"]
    sha256: str | None
    size_bytes: int
    owner: int
    mode: int
    link_count: int
    symlink_target: str | None = None


def snapshot_inputs(root: Path) -> InputSnapshot:
    observed, _ = _snapshot_path(root)
    entries = tuple(
        InputEntry(
            logical_name=item.relative_path,
            kind=item.kind,
            sha256=item.sha256,
            size_bytes=item.size_bytes,
            mode=item.mode,
            symlink_target=item.symlink_target,
        )
        for item in observed
    )
    payload: dict[str, object] = {
        "schema_version": "personal.input-snapshot.v1",
        "entries": entries,
    }
    payload["snapshot_id"] = materialize_id("personal-input-snapshot-", payload)
    return InputSnapshot.model_validate(payload)


def snapshot_system(
    repository: Path,
    protocol_ref: ArtifactRef,
    *,
    capability_manifest: Path,
    method_profile: Path,
    runtime_versions: tuple[tuple[str, str], ...],
) -> SystemSnapshot:
    root = repository.resolve(strict=True)
    commit = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    execution, _ = _snapshot_path(root, excluded_top_level=frozenset({".git"}))
    payload: dict[str, object] = {
        "schema_version": "personal.system-snapshot.v1",
        "git_commit": commit,
        "execution_tree_sha256": _observed_digest(execution),
        "uv_lock_sha256": _member_digest(root, Path("uv.lock")),
        "capability_manifest_sha256": _member_digest(root, capability_manifest),
        "method_profile_sha256": _member_digest(root, method_profile),
        "protocol_ref": protocol_ref,
        "runtime_versions": tuple(sorted(runtime_versions)),
        "clean_worktree": not status,
    }
    payload["snapshot_id"] = materialize_id("personal-system-snapshot-", payload)
    return SystemSnapshot.model_validate(payload)


def snapshot_roots(roots: Mapping[str, Path]) -> AttemptRootInventory:
    if not isinstance(roots, Mapping):
        raise PersonalValidationIntegrityInvalid(
            "attempt inventory requires the exact logical root set",
            finding_kind="attempt-root-inventory-incomplete",
        )
    root_paths = {str(name): Path(path) for name, path in roots.items()}
    if set(root_paths) != set(PERSONAL_ATTEMPT_ROOTS_V1):
        raise PersonalValidationIntegrityInvalid(
            "attempt inventory requires the exact logical root set",
            finding_kind="attempt-root-inventory-incomplete",
        )
    try:
        with ExitStack() as stack:
            pinned = {
                name: stack.enter_context(PinnedRoot(root_paths[name], create=False))
                for name in sorted(root_paths)
            }
            _require_separate_roots(pinned)
            identities: list[RootIdentity] = []
            inventory_entries: list[RootInventoryEntry] = []
            observed_roots: dict[str, tuple[_ObservedEntry, ...]] = {}
            for logical_root, root in pinned.items():
                observed, identity = _snapshot_pinned(root)
                observed_roots[logical_root] = observed
                entries = tuple(
                    RootInventoryEntry.model_validate(
                        {"logical_root": logical_root, **asdict(item)}
                    )
                    for item in observed
                )
                inventory_entries.extend(entries)
                identities.append(
                    RootIdentity(
                        logical_root=logical_root,
                        device=identity[0],
                        inode=identity[1],
                        tree_sha256=_inventory_tree_digest(entries),
                        entry_count=len(entries),
                    )
                )
            for logical_root, root in pinned.items():
                if _snapshot_pinned(root)[0] != observed_roots[logical_root]:
                    raise ValueError("attempt root changed during inventory")
    except PersonalValidationIntegrityInvalid:
        raise
    except (OSError, ValueError) as error:
        raise PersonalValidationIntegrityInvalid(
            "attempt logical root is unavailable",
            finding_kind="attempt-root-inventory-incomplete",
        ) from error
    payload: dict[str, object] = {
        "schema_version": "personal.attempt-root-inventory.v1",
        "root_identities": tuple(identities),
        "entries": tuple(inventory_entries),
    }
    payload["inventory_id"] = materialize_id(
        "personal-attempt-root-inventory-", payload
    )
    return AttemptRootInventory.model_validate(payload)


def require_correct_stop_inventory(inventory: AttemptRootInventory) -> None:
    validated = AttemptRootInventory.model_validate(
        inventory.model_dump(mode="python"), strict=True
    )
    identities = {item.logical_root: item for item in validated.root_identities}
    if set(identities) != set(PERSONAL_ATTEMPT_ROOTS_V1):
        raise PersonalValidationIntegrityInvalid(
            "correct-stop inventory has an incomplete logical root set",
            finding_kind="attempt-root-inventory-incomplete",
        )
    for logical_root, identity in identities.items():
        entries = tuple(
            item for item in validated.entries if item.logical_root == logical_root
        )
        if identity.entry_count != len(
            entries
        ) or identity.tree_sha256 != _inventory_tree_digest(entries):
            raise PersonalValidationIntegrityInvalid(
                "correct-stop inventory tree evidence is inconsistent",
                finding_kind="attempt-root-inventory-invalid",
            )
    for entry in validated.entries:
        path = PurePosixPath(entry.relative_path)
        for (
            name,
            logical_roots,
            finding_kind,
            predicate,
        ) in CORRECT_STOP_FORBIDDEN_NAMESPACES_V1:
            if entry.logical_root in logical_roots and predicate(path):
                raise PersonalValidationIntegrityInvalid(
                    "correct-stop inventory contains forbidden "
                    f"{name} result artifact at "
                    f"{entry.logical_root}:{entry.relative_path}",
                    finding_kind=finding_kind,
                )


def _require_separate_roots(roots: Mapping[str, PinnedRoot]) -> None:
    for (_, left), (_, right) in combinations(roots.items(), 2):
        if directories_overlap(left.fd, right.fd):
            raise PersonalValidationIntegrityInvalid(
                "attempt logical roots overlap",
                finding_kind="attempt-root-authority-overlap",
            )


def _snapshot_path(
    path: Path, *, excluded_top_level: frozenset[str] = frozenset()
) -> tuple[tuple[_ObservedEntry, ...], tuple[int, int]]:
    with PinnedRoot(path, create=False) as root:
        return _snapshot_pinned(root, excluded_top_level=excluded_top_level)


def _snapshot_pinned(
    root: PinnedRoot, *, excluded_top_level: frozenset[str] = frozenset()
) -> tuple[tuple[_ObservedEntry, ...], tuple[int, int]]:
    root.require_attached()
    metadata = os.fstat(root.fd)
    entries = [_observed(".", "directory", metadata)]
    entries.extend(_walk(root.fd, Path(), excluded=excluded_top_level, top_level=True))
    root.require_attached()
    return tuple(sorted(entries, key=lambda item: item.relative_path)), (
        metadata.st_dev,
        metadata.st_ino,
    )


def _walk(
    parent_fd: int,
    parent: Path,
    *,
    excluded: frozenset[str],
    top_level: bool,
) -> list[_ObservedEntry]:
    entries: list[_ObservedEntry] = []
    directory_before = os.fstat(parent_fd)
    names = tuple(
        name
        for name in sorted(os.listdir(parent_fd))
        if not (top_level and name in excluded)
    )
    for name in names:
        relative = parent / name
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISREG(before.st_mode):
            data = _read_file(parent_fd, name, before)
            entries.append(
                _observed(
                    relative.as_posix(),
                    "file",
                    before,
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            )
        elif stat.S_ISDIR(before.st_mode):
            entries.append(_observed(relative.as_posix(), "directory", before))
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise ValueError("snapshot directory changed during traversal")
                entries.extend(
                    _walk(descriptor, relative, excluded=excluded, top_level=False)
                )
                _require_unchanged(parent_fd, name, before)
            finally:
                os.close(descriptor)
        elif stat.S_ISLNK(before.st_mode):
            target = os.readlink(name, dir_fd=parent_fd)
            _require_unchanged(parent_fd, name, before)
            entries.append(
                _observed(relative.as_posix(), "symlink", before, symlink_target=target)
            )
        else:
            raise ValueError("snapshot root contains an unsupported file kind")
    current_names = tuple(
        name
        for name in sorted(os.listdir(parent_fd))
        if not (top_level and name in excluded)
    )
    if current_names != names or _stable(os.fstat(parent_fd)) != _stable(
        directory_before
    ):
        raise ValueError("snapshot directory changed during traversal")
    return entries


def _read_file(parent_fd: int, name: str, before: os.stat_result) -> bytes:
    descriptor = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (before.st_dev, before.st_ino):
            raise ValueError("snapshot file changed during traversal")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if _stable(after) != _stable(opened):
            raise ValueError("snapshot file changed during traversal")
        _require_unchanged(parent_fd, name, after)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _observed(
    relative_path: str,
    kind: Literal["file", "directory", "symlink"],
    metadata: os.stat_result,
    *,
    sha256: str | None = None,
    symlink_target: str | None = None,
) -> _ObservedEntry:
    return _ObservedEntry(
        relative_path=relative_path,
        kind=kind,
        sha256=sha256,
        size_bytes=metadata.st_size,
        owner=metadata.st_uid,
        mode=stat.S_IMODE(metadata.st_mode),
        link_count=metadata.st_nlink,
        symlink_target=symlink_target,
    )


def _require_unchanged(parent_fd: int, name: str, expected: os.stat_result) -> None:
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if _stable(current) != _stable(expected):
        raise ValueError("snapshot entry changed during traversal")


# Compact pure digest/predicate helpers keep this authority module within 400 lines.
# fmt: off
def _stable(metadata: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(metadata, field) for field in ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_size")) + (metadata.st_mtime_ns, metadata.st_ctime_ns)

def _observed_digest(entries: tuple[_ObservedEntry, ...]) -> str:
    return hashlib.sha256(canonical_json(entries)).hexdigest()

def _inventory_tree_digest(entries: tuple[RootInventoryEntry, ...]) -> str:
    return hashlib.sha256(canonical_json(tuple(item.model_dump(mode="json") for item in entries))).hexdigest()

def _member_digest(repository: Path, relative: Path) -> str:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("system snapshot member must be repository-relative")
    member = repository / relative
    if member.is_symlink():
        raise ValueError("system snapshot member must not be a symlink")
    resolved = member.resolve(strict=True)
    if not resolved.is_relative_to(repository):
        raise ValueError("system snapshot member escapes the repository")
    if resolved.is_file():
        return hashlib.sha256(resolved.read_bytes()).hexdigest()
    observed, _ = _snapshot_path(resolved)
    return _observed_digest(observed)

def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments),
        cwd=repository,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

def _has_pair(path: PurePosixPath, left: str, right: str) -> bool:
    return any(path.parts[index : index + 2] == (left, right) for index in range(max(0, len(path.parts) - 1)))


# Compact versioned policy table: each predicate is independently exercised below.
def _factory_result(path: PurePosixPath) -> bool:
    return path.as_posix() in {"exit/current/research-factory-run-prepared.json", "exit/current/research-factory-run.json"} or len(path.parts) >= 3 and path.parts[:2] == ("exit", "objects") and path.parts[2].startswith("factory-run-")

def _paper_result(path: PurePosixPath) -> bool:
    return len(path.parts) >= 3 and path.parts[:2] in {("exit", "current"), ("exit", "objects")}

def _local_analysis_result(path: PurePosixPath) -> bool:
    return bool(path.parts and path.parts[0] == "analyses") and (path.name in {"current.json", "pending.json"} or "history" in path.parts or "outputs" in path.parts)

def _transition_or_output(path: PurePosixPath) -> bool:
    return _has_pair(path, "exit", "current") or _has_pair(path, "exit", "objects") or "outputs" in path.parts or "transition" in path.name.casefold()

def _empirical_result(path: PurePosixPath) -> bool:
    return any(token in path.as_posix().casefold() for token in ("result", "table", "figure", "plot", "estimate", "coefficient")) and path.suffix.casefold() in {".csv", ".tsv", ".json", ".parquet", ".feather", ".svg", ".png", ".pdf"}

ForbiddenNamespace: TypeAlias = tuple[str, frozenset[str], str, Callable[[PurePosixPath], bool]]
CORRECT_STOP_FORBIDDEN_NAMESPACES_V1: tuple[ForbiddenNamespace, ...] = (
    ("Factory", frozenset({"factory"}), "factory-result-present", _factory_result),
    ("Paper", frozenset({"paper"}), "paper-result-present", _paper_result),
    ("LocalAnalysis", frozenset({"local-analysis"}), "local-analysis-result-present", _local_analysis_result),
    ("V0.3", frozenset({"v03"}), "v03-result-present", _transition_or_output),
    ("V0.3.1", frozenset({"v031"}), "v031-result-present", _transition_or_output),
    ("empirical", frozenset(PERSONAL_ATTEMPT_ROOTS_V1), "empirical-result-present", _empirical_result),
)
# fmt: on

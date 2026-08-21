"""Validation helpers for content-addressed managed R package trees."""

from __future__ import annotations

import hashlib
import os
import stat
import tarfile
import threading
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from envresearch.econometrics.method_authority import (
    MethodAuthority,
    MethodAuthorityProposal,
)

MAX_ARCHIVE_MEMBERS = 20_000
MAX_UNPACKED_BYTES = 512 * 1024 * 1024
_THREAD_LOCKS: dict[tuple[Path, str], threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
BASE_R_PACKAGES = {
    "R",
    "base",
    "compiler",
    "datasets",
    "graphics",
    "grDevices",
    "grid",
    "methods",
    "parallel",
    "splines",
    "stats",
    "stats4",
    "tcltk",
    "tools",
    "utils",
}


class AuthorityValidationError(ValueError):
    """One package source, tree, license, or graph failed validation."""


def require_source_identity(
    proposal: MethodAuthorityProposal, data: bytes, final_url: str
) -> None:
    expected = urlsplit(proposal.source_url)
    observed = urlsplit(final_url)
    if observed.scheme != "https" or observed.hostname != expected.hostname:
        raise AuthorityValidationError(
            "package source redirected outside official host"
        )
    if sha256(data) != proposal.source_sha256:
        raise AuthorityValidationError("package source digest does not match proposal")


def inspect_archive(data: bytes) -> None:
    total = 0
    with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise AuthorityValidationError("unsafe archive member count")
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or any(part in {"", ".", ".."} for part in path.parts)
                or not (member.isfile() or member.isdir())
            ):
                raise AuthorityValidationError("unsafe archive member")
            total += member.size
            if total > MAX_UNPACKED_BYTES:
                raise AuthorityValidationError("unsafe archive expansion")


def description(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise AuthorityValidationError("installed DESCRIPTION is missing") from error
    fields: dict[str, str] = {}
    current: str | None = None
    for line in lines:
        if line.startswith((" ", "\t")):
            if current is not None:
                fields[current] += " " + line.strip()
            continue
        if ":" in line:
            current, value = line.split(":", 1)
            fields[current] = value.strip()
    return fields


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    files, _ = tree_entries(root)
    if not files:
        raise AuthorityValidationError("installed package tree is empty")
    for path in files:
        relative = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def freeze_tree(root: Path) -> None:
    """Make the installed package tree immutable without following links."""
    files, directories = tree_entries(root)
    for path in files:
        os.chmod(path, 0o444, follow_symlinks=False)
    for path in reversed(directories):
        os.chmod(path, 0o555, follow_symlinks=False)


def tree_entries(root: Path) -> tuple[list[Path], list[Path]]:
    try:
        root_mode = root.lstat().st_mode
    except OSError as error:
        raise AuthorityValidationError(
            "installed package tree is unavailable"
        ) from error
    if not stat.S_ISDIR(root_mode) or stat.S_ISLNK(root_mode):
        raise AuthorityValidationError("installed package tree is unavailable")
    files: list[Path] = []
    directories = [root]
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise AuthorityValidationError("installed package tree contains a symlink")
        if stat.S_ISREG(mode):
            files.append(path)
        elif stat.S_ISDIR(mode):
            directories.append(path)
        else:
            raise AuthorityValidationError("installed package tree is nonregular")
    return files, directories


def thread_lock(root: Path, package: str) -> threading.Lock:
    key = (root, package)
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


def reject_alternate_version(
    library_root: Path, proposal: MethodAuthorityProposal
) -> None:
    package = library_root / proposal.package
    if not package.exists() and not package.is_symlink():
        return
    fields = description(package / "DESCRIPTION")
    if fields.get("Version") != proposal.version:
        raise AuthorityValidationError("another package version is already admitted")


def require_description_dependencies(
    fields: Mapping[str, str], proposal: MethodAuthorityProposal
) -> None:
    declared = {item.package for item in proposal.dependencies}
    observed: set[str] = set()
    for field in ("Depends", "Imports", "LinkingTo"):
        for item in fields.get(field, "").split(","):
            package = item.strip().split(" ", 1)[0]
            if package:
                observed.add(package)
    if observed != declared:
        raise AuthorityValidationError("DESCRIPTION dependency graph is not declared")


def require_closed_graph(authorities: tuple[MethodAuthority, ...]) -> None:
    available = {item.proposal.package: item.proposal.version for item in authorities}
    for authority in authorities:
        for dependency in authority.proposal.dependencies:
            if dependency.base and dependency.package not in BASE_R_PACKAGES:
                raise AuthorityValidationError("non-base dependency is marked as base")
            if (
                not dependency.base
                and available.get(dependency.package) != dependency.version
            ):
                raise AuthorityValidationError(
                    "package dependency authority is missing"
                )


def license_matches(spdx: str, observed: str) -> bool:
    aliases = {
        "GPL-2": "GPL-2.0-only",
        "GPL-3": "GPL-3.0-only",
        "GPL (>= 2)": "GPL-2.0-or-later",
        "GPL (>= 3)": "GPL-3.0-or-later",
        "BSD_3_clause + file LICENSE": "BSD-3-Clause",
    }
    expected = tuple(spdx.split(" OR "))
    actual = tuple(
        aliases.get(item.strip(), item.strip()) for item in observed.split("|")
    )
    if len(expected) != len(set(expected)) or len(actual) != len(set(actual)):
        return False
    return set(expected) == set(actual)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

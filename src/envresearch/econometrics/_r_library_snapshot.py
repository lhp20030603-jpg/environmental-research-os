"""Execution-owned snapshots for authenticated R package projections."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from envresearch.econometrics._managed_r_validation import (
    freeze_tree,
    tree_digest,
    tree_entries,
)
from envresearch.econometrics.method_authority import MethodAuthority
from envresearch.econometrics.r_evidence import PackageAuthority


@contextmanager
def execution_library_snapshot(
    source: Path,
    authorities: Sequence[PackageAuthority],
    workspace: Path,
) -> Iterator[Path]:
    """Clone, authenticate, freeze, use, and remove one package projection."""
    parent = _staging_parent(workspace)
    destination = parent / uuid.uuid4().hex
    try:
        _copy_projection(source, destination)
        _verify_projection(destination, authorities)
        freeze_tree(destination)
        yield destination
        _verify_projection(destination, authorities)
    finally:
        _remove_projection(destination)


def _copy_projection(source: Path, destination: Path) -> None:
    tree_entries(source)
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ("/bin/cp", "-cR", str(source), str(destination)),
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                timeout=300,
            )
            return
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError(
                "R package execution snapshot could not be cloned"
            ) from error
    shutil.copytree(source, destination, symlinks=False)


def _staging_parent(workspace: Path) -> Path:
    owner = workspace.parent
    parent = owner / ".r-library-snapshots"
    if owner.is_symlink() or not owner.is_dir():
        raise ValueError("R package snapshot staging directory is invalid")
    if parent.exists() or parent.is_symlink():
        try:
            metadata = parent.lstat()
        except OSError as error:
            raise ValueError(
                "R package snapshot staging directory is invalid"
            ) from error
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("R package snapshot staging directory is invalid")
    else:
        parent.mkdir(mode=0o700)
    if parent.resolve(strict=True).parent != owner.resolve(strict=True):
        raise ValueError("R package snapshot staging directory is invalid")
    return parent


def _verify_projection(root: Path, authorities: Sequence[PackageAuthority]) -> None:
    entries = tuple(root.iterdir())
    expected = {_package_name(item): item.installed_tree_sha256 for item in authorities}
    if (
        root.is_symlink()
        or any(path.is_symlink() or not path.is_dir() for path in entries)
        or {path.name for path in entries} != set(expected)
    ):
        raise ValueError("R package execution snapshot is not closed")
    for package, digest in expected.items():
        if tree_digest(root / package) != digest:
            raise ValueError("R package execution snapshot identity changed")


def _package_name(authority: PackageAuthority) -> str:
    if isinstance(authority, MethodAuthority):
        return authority.proposal.package
    return authority.package


def _remove_projection(root: Path) -> None:
    if not root.exists():
        return
    for directory, directories, files in os.walk(root, topdown=False):
        for name in files:
            Path(directory, name).chmod(0o600)
        for name in directories:
            Path(directory, name).chmod(0o700)
        Path(directory).chmod(0o700)
    shutil.rmtree(root)

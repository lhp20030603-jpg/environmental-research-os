"""Lexical raw-input validation and safe benchmark workspace preparation."""

from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from envresearch.models.benchmark import BenchmarkManifest
from envresearch.models.enums import FindingSeverity
from envresearch.storage.atomic import atomic_write_bytes
from envresearch.storage.hashing import sha256_file


@dataclass(frozen=True, slots=True)
class PreparationIssue:
    """Stable finding input for a provenance or raw preparation failure."""

    code: str
    severity: FindingSeverity
    message: str
    evidence: tuple[str, ...]


def validate_roots(case_root: Path, workspace: Path) -> None:
    """Reject a derived workspace nested beneath the immutable raw tree."""
    immutable_raw = (case_root / "raw").resolve()
    if workspace.is_relative_to(immutable_raw):
        raise ValueError("run root must be outside the immutable raw directory")


def initialize_workspace(workspace: Path, manifest: BenchmarkManifest) -> None:
    """Create a fresh workspace and persist its normalized benchmark manifest."""
    if workspace.exists():
        if not workspace.is_dir() or any(workspace.iterdir()):
            raise ValueError(f"run root is not empty: {workspace}")
    else:
        workspace.mkdir(parents=True)
    payload = yaml.safe_dump(
        manifest.model_dump(mode="json"), sort_keys=True
    ).encode("utf-8")
    atomic_write_bytes(workspace / "run-manifest.yaml", payload)


def inspect_raw_root(case_root: Path) -> PreparationIssue | None:
    """Inspect the lexical raw root without following a symbolic link."""
    raw_root = case_root / "raw"
    try:
        metadata = raw_root.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        return _raw_issue("raw input root cannot be inspected", error)
    if stat.S_ISLNK(metadata.st_mode):
        return _raw_issue("raw input root must not be a symbolic link")
    if not stat.S_ISDIR(metadata.st_mode):
        return _raw_issue("raw input root must be a regular directory")
    return None


def verify_source(
    manifest: BenchmarkManifest, case_root: Path
) -> PreparationIssue | None:
    """Verify a lexical regular source archive and its declared SHA-256."""
    if manifest.source_archive is None or manifest.source_sha256 is None:
        return None
    archive = case_root / manifest.source_archive
    issue = _inspect_source_components(case_root, manifest.source_archive)
    if issue is not None:
        return issue
    try:
        actual_hash = sha256_file(archive)
    except (OSError, ValueError) as error:
        return PreparationIssue(
            "SOURCE_ARCHIVE_INVALID",
            FindingSeverity.CRITICAL,
            "source archive cannot be read",
            (f"path={manifest.source_archive.as_posix()}", str(error)),
        )
    if actual_hash == manifest.source_sha256:
        return None
    return PreparationIssue(
        "SOURCE_HASH_MISMATCH",
        FindingSeverity.CRITICAL,
        "source archive hash does not match the manifest",
        (
            f"path={manifest.source_archive.as_posix()}",
            f"expected_sha256={manifest.source_sha256}",
            f"actual_sha256={actual_hash}",
        ),
    )


def copy_raw_inputs(case_root: Path, workspace: Path) -> PreparationIssue | None:
    """Copy an inspected raw tree without following lexical symbolic links."""
    raw_root = case_root / "raw"
    root_issue = inspect_raw_root(case_root)
    if root_issue is not None:
        return root_issue
    if not raw_root.exists():
        return None
    try:
        for path in raw_root.rglob("*"):
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return _raw_issue(
                    "raw input tree must not contain symbolic links",
                    evidence=(f"path={path.relative_to(case_root).as_posix()}",),
                )
            if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                return _raw_issue(
                    "raw input tree contains a non-regular entry",
                    evidence=(f"path={path.relative_to(case_root).as_posix()}",),
                )
        shutil.copytree(raw_root, workspace / "raw")
    except (OSError, ValueError) as error:
        return _raw_issue("raw input tree cannot be copied", error)
    return None


def _inspect_source_components(
    case_root: Path, relative: Path
) -> PreparationIssue | None:
    cursor = case_root
    for index, part in enumerate(relative.parts):
        cursor /= part
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            return PreparationIssue(
                "SOURCE_ARCHIVE_MISSING",
                FindingSeverity.ERROR,
                "declared source archive is missing",
                (f"path={relative.as_posix()}",),
            )
        except OSError as error:
            return PreparationIssue(
                "SOURCE_ARCHIVE_INVALID",
                FindingSeverity.CRITICAL,
                "source archive cannot be inspected",
                (f"path={relative.as_posix()}", str(error)),
            )
        if stat.S_ISLNK(metadata.st_mode):
            return PreparationIssue(
                "SOURCE_ARCHIVE_INVALID",
                FindingSeverity.CRITICAL,
                "source archive path must not contain symbolic links",
                (f"path={relative.as_posix()}",),
            )
        is_last = index == len(relative.parts) - 1
        if (not is_last and not stat.S_ISDIR(metadata.st_mode)) or (
            is_last and not stat.S_ISREG(metadata.st_mode)
        ):
            return PreparationIssue(
                "SOURCE_ARCHIVE_INVALID",
                FindingSeverity.CRITICAL,
                "source archive path contains a non-regular component",
                (f"path={relative.as_posix()}",),
            )
    return None


def _raw_issue(
    message: str,
    error: Exception | None = None,
    *,
    evidence: tuple[str, ...] = (),
) -> PreparationIssue:
    details = evidence + ((str(error),) if error is not None else ())
    return PreparationIssue(
        "RAW_INPUT_INVALID",
        FindingSeverity.CRITICAL,
        message,
        details,
    )

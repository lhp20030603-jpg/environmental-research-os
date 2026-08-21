"""Authenticated source inputs for the trusted local R boundary."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from envresearch.econometrics._r_owned_files import RRuntimeInvalid
from envresearch.econometrics.contracts import ResourceBudget
from envresearch.econometrics.r_evidence import GeneratedRScript


def inspect_executable(executable: Path) -> tuple[os.stat_result, str, bytes]:
    """Open and hash one immutable regular executable without following links."""
    if not executable.is_absolute():
        raise RRuntimeInvalid("Rscript must be a reviewed regular executable")
    try:
        lexical = executable.lstat()
        descriptor = os.open(
            executable,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as error:
        raise RRuntimeInvalid(
            "Rscript must be a reviewed regular executable"
        ) from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lexical.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (lexical.st_dev, lexical.st_ino) != (opened.st_dev, opened.st_ino)
            or _is_effectively_writable(opened)
            or opened.st_size <= 0
        ):
            raise RRuntimeInvalid("Rscript must be a reviewed regular executable")
        data = _read_bounded(descriptor, 128 * 1024 * 1024, "R executable")
        return opened, hashlib.sha256(data).hexdigest(), data
    finally:
        os.close(descriptor)


def _is_effectively_writable(metadata: os.stat_result) -> bool:
    """Reject executable bytes writable by this user or by everyone."""
    if metadata.st_mode & stat.S_IWOTH:
        return True
    if metadata.st_uid == os.geteuid() and metadata.st_mode & stat.S_IWUSR:
        return True
    return metadata.st_gid in os.getgroups() and bool(metadata.st_mode & stat.S_IWGRP)


def inspect_script(
    script: GeneratedRScript, workspace: Path, budget: ResourceBudget
) -> bytes:
    """Authenticate one confined immutable UTF-8 script against its manifest."""
    generated_root = workspace / "generated"
    try:
        resolved = script.path.resolve(strict=True)
    except OSError as error:
        raise RRuntimeInvalid(
            "generated script is outside the owned workspace"
        ) from error
    if (
        not script.path.is_absolute()
        or resolved != script.path
        or not resolved.is_relative_to(generated_root)
    ):
        raise RRuntimeInvalid("generated script is outside the owned workspace")
    try:
        lexical = script.path.lstat()
        descriptor = os.open(
            script.path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as error:
        raise RRuntimeInvalid("generated script must be a regular file") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lexical.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (lexical.st_dev, lexical.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > budget.max_workspace_bytes
        ):
            raise RRuntimeInvalid("generated script must be a regular file")
        data = _read_descriptor(descriptor, budget.max_workspace_bytes)
    finally:
        os.close(descriptor)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RRuntimeInvalid("generated script must be UTF-8") from error
    if hashlib.sha256(data).hexdigest() != script.sha256:
        raise RRuntimeInvalid("generated script identity changed after sealing")
    return data


def _read_descriptor(descriptor: int, max_bytes: int) -> bytes:
    """Read no more than one declared byte ceiling."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    data = bytearray()
    while chunk := os.read(descriptor, min(1024 * 1024, max_bytes - len(data) + 1)):
        data.extend(chunk)
        if len(data) > max_bytes:
            raise RRuntimeInvalid("generated script exceeds the workspace budget")
    return bytes(data)


def _read_bounded(descriptor: int, max_bytes: int, label: str) -> bytes:
    """Read authenticated bytes under one explicit ceiling."""
    try:
        return _read_descriptor(descriptor, max_bytes)
    except RRuntimeInvalid as error:
        raise RRuntimeInvalid(f"{label} exceeds its size limit") from error

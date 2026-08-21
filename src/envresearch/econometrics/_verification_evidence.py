"""Exact byte reopening used by independent econometrics verification."""

from __future__ import annotations

import hashlib
from pathlib import Path

from envresearch.econometrics._store_files import StoreFiles


def check_hash(
    files: StoreFiles,
    path: Path,
    expected: str,
    code: str,
    findings: list[str],
) -> bytes | None:
    """Append one stable finding when exact evidence bytes cannot be reopened."""
    try:
        data = files.read(path)
    except OSError:
        findings.append(code)
        return None
    if hashlib.sha256(data).hexdigest() != expected:
        findings.append(code)
        return None
    return data

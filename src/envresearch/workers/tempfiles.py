"""Strict target-bound names for crash-recoverable temporary files."""

from __future__ import annotations

import hashlib
import os
import re
import uuid

_TEMPORARY_NAME = re.compile(r"^\.tmp-([0-9a-f]{64})-([0-9a-f]{32})$")


def temporary_name_for(target: str) -> str:
    """Return a unique temporary name bound to one intended final basename."""
    return f".tmp-{target_name_digest(target)}-{uuid.uuid4().hex}"


def temporary_target_digest(name: str) -> str:
    """Parse one exact internal temporary name or reject it as corruption."""
    match = _TEMPORARY_NAME.fullmatch(name)
    if match is None:
        raise ValueError("protected temporary filename has invalid grammar")
    return match.group(1)


def target_name_digest(target: str) -> str:
    """Bind temporary identity to the exact encoded target basename."""
    return hashlib.sha256(os.fsencode(target)).hexdigest()

"""No-follow helpers for immutable local econometrics evidence leaves."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def read_regular(path: Path) -> bytes:
    """Read one exact regular leaf without following a replacement link."""
    lexical = path.lstat()
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lexical.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (lexical.st_dev, lexical.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise OSError("evidence is not a regular file")
        chunks: list[bytes] = []
        remaining = opened.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) != opened.st_size:
            raise OSError("evidence size changed while reading")
        return data
    finally:
        os.close(descriptor)

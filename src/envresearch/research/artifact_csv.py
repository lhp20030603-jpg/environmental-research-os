"""Deterministic CSV serialization for research artifact publication."""

from __future__ import annotations

import csv
import io
from typing import Any

_EVIDENCE_FIELDS = (
    "evidence_id",
    "source_id",
    "finding",
    "relevance",
    "evidence_reason",
)


def _csv_bytes(rows: tuple[dict[str, Any], ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=_EVIDENCE_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()

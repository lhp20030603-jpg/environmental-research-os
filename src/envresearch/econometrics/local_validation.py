"""Read-only validation for one explicitly selected local CSV."""

from __future__ import annotations

import hashlib
import os

from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.data_snapshot import (
    LocalDataValidation,
    _inspect_csv,
    _open_source,
    _read_all,
    _require_source_unchanged,
)


def validate_csv(spec: AnalysisSpec) -> LocalDataValidation:
    """Inspect exact local bytes without writing to the artifact store."""
    limit = spec.budget.max_workspace_bytes
    descriptor, identity = _open_source(spec.data_path, limit)
    try:
        data = _read_all(descriptor, limit)
    finally:
        os.close(descriptor)
    digest = hashlib.sha256(data).hexdigest()
    inspection = _inspect_csv(data, spec)
    _require_source_unchanged(spec.data_path, identity, digest, limit)
    return LocalDataValidation(
        sha256=digest,
        size_bytes=len(data),
        row_count=inspection.row_count,
        columns=inspection.columns,
        missing_values=inspection.missing_values,
    )

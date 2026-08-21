"""Caller-supplied exact V0.3.1 transition opening for V0.4."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from envresearch.econometrics.valuation_transition import V031ExitHarness
from envresearch.models.artifact import ArtifactRef


def test_open_exact_never_substitutes_an_implicit_current_transition() -> None:
    value = os.environ.get("ENVRESEARCH_V031_ACCEPTANCE_ROOT")
    if value is None:
        pytest.skip("sealed V0.3.1 acceptance root is not configured")
    root = Path(value).resolve(strict=True)
    current = V031ExitHarness(root).marker_ref

    assert V031ExitHarness.open_exact(root, current).marker_ref == current
    forged = ArtifactRef(
        artifact_id=current.artifact_id,
        artifact_version=current.artifact_version,
        content_hash="0" * 64,
    )
    with pytest.raises((FileNotFoundError, ValueError)):
        V031ExitHarness.open_exact(root, forged)

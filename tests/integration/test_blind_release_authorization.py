"""Canonical release authorization over durable descriptor-pinned snapshots."""

from pathlib import Path

import pytest
from blind_release_catalog_helpers import build_releasable_catalog

from envresearch.benchmarks.blind_report import evaluate_blind_catalog


def test_descriptor_pinned_catalog_with_external_authority_can_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, run, authority = build_releasable_catalog(tmp_path)
    monkeypatch.setenv("ENVRESEARCH_BLIND_RELEASE_AUTHORITY", str(authority))

    report = evaluate_blind_catalog(catalog, run)

    assert report.released is True
    assert report.blockers == ()
    assert report.held_out_cases == 16
    assert report.passed_cases == 16


def test_release_authority_inside_caller_catalog_cannot_authorize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, run, authority = build_releasable_catalog(tmp_path)
    unsafe = catalog / "caller-authority.json"
    unsafe.write_bytes(authority.read_bytes())
    unsafe.chmod(0o600)
    monkeypatch.setenv("ENVRESEARCH_BLIND_RELEASE_AUTHORITY", str(unsafe))

    with pytest.raises(ValueError, match="outside catalog and run"):
        evaluate_blind_catalog(catalog, run)

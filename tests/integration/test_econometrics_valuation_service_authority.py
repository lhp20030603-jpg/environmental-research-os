"""Consumer-visible exact-input and evidence-collision service boundaries."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from econometrics_valuation_verifier_fixtures import (
    ValuationVerifierBackend,
    spec_for,
)

from envresearch.econometrics.service import EvidenceTampered, LocalAnalysisService
from envresearch.storage.research_artifacts import ResearchArtifactStore


def _service(tmp_path: Path) -> tuple[LocalAnalysisService, object]:
    spec = spec_for("contingent-valuation")
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "store"),
        ValuationVerifierBackend("contingent-valuation"),
    )
    return service, spec


def test_exact_run_rejects_changed_caller_authenticated_bytes(tmp_path: Path) -> None:
    service, spec = _service(tmp_path)

    with pytest.raises(EvidenceTampered, match="do not match their authority"):
        service.run_exact(spec, b"changed", "0" * 64)  # type: ignore[arg-type]


def test_exact_run_executes_and_recovers_by_the_same_data_authority(
    tmp_path: Path,
) -> None:
    service, spec = _service(tmp_path)
    data = spec.data_path.read_bytes()  # type: ignore[union-attr]
    digest = hashlib.sha256(data).hexdigest()

    first = service.run_exact(spec, data, digest)  # type: ignore[arg-type]
    second = service.run_exact(spec, data, digest)  # type: ignore[arg-type]
    report = service.status(first)

    assert second == first
    assert report.status == "passed"
    assert report.snapshot is not None and report.snapshot.sha256 == digest


def test_invalid_exact_run_is_hash_bound_and_idempotent(tmp_path: Path) -> None:
    service, spec = _service(tmp_path)
    data = b"unexpected\nvalue\n"
    digest = hashlib.sha256(data).hexdigest()

    first = service.run_exact(spec, data, digest)  # type: ignore[arg-type]
    second = service.run_exact(spec, data, digest)  # type: ignore[arg-type]
    report = service.status(first)

    assert second == first
    assert report.status == "exception"
    assert report.code == "LOCAL_DATA_INVALID"


def test_invalid_exact_run_rejects_unauthentic_control_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, spec = _service(tmp_path)
    data = b"unexpected\nvalue\n"
    digest = hashlib.sha256(data).hexdigest()

    def reject(_analysis_id: str) -> None:
        raise ValueError("forged pending state")

    monkeypatch.setattr(service.publisher, "recover", reject)
    with pytest.raises(EvidenceTampered, match="control state is not authentic"):
        service.run_exact(spec, data, digest)  # type: ignore[arg-type]


def test_evidence_collision_is_published_as_typed_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, spec = _service(tmp_path)
    data = spec.data_path.read_bytes()  # type: ignore[union-attr]
    digest = hashlib.sha256(data).hexdigest()
    persist_exact = service.files.persist_exact

    def reject_logs(relative: Path, evidence: bytes) -> None:
        if "logs" in relative.parts:
            raise ValueError("content-addressed collision")
        persist_exact(relative, evidence)

    monkeypatch.setattr(service.files, "persist_exact", reject_logs)
    report = service.status(
        service.run_exact(spec, data, digest)  # type: ignore[arg-type]
    )

    assert report.status == "exception"
    assert report.code == "EVIDENCE_TAMPERED"
    assert report.verification_findings == ("content-addressed collision",)

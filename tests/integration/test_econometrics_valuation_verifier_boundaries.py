"""Missing, malformed, and contradictory verifier-evidence coverage."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from econometrics_valuation_verifier_fixtures import (
    ValuationVerifierBackend,
    spec_for,
)

from envresearch.econometrics.service import LocalAnalysisService
from envresearch.econometrics.verify import LocalAnalysisVerifier
from envresearch.storage.research_artifacts import ResearchArtifactStore


def _report(tmp_path: Path, method_id: str = "contingent-valuation"):
    store = ResearchArtifactStore(tmp_path / "store")
    service = LocalAnalysisService(store, ValuationVerifierBackend(method_id))
    report = service.status(service.run(spec_for(method_id)))
    return LocalAnalysisVerifier(store.root), report


def test_verifier_requires_snapshot_script_runtime_result_and_execution(
    tmp_path: Path,
) -> None:
    verifier, report = _report(tmp_path)
    assert verifier.verify(report.model_copy(update={"snapshot": None})) == (
        "SNAPSHOT_MISSING",
    )

    findings = verifier.verify(
        report.model_copy(update={"script_path": None, "script_sha256": None})
    )
    assert "SCRIPT_MISSING" in findings

    findings = verifier.verify(report.model_copy(update={"runtime_path": None}))
    assert "RUNTIME_EVIDENCE_MISSING" in findings

    findings = verifier.verify(
        report.model_copy(update={"output_root": None, "result": None})
    )
    assert "RESULT_MISSING" in findings

    findings = verifier.verify(report.model_copy(update={"execution": None}))
    assert {"RUNTIME_EVIDENCE_MISSING", "EXECUTION_EVIDENCE_INVALID"}.issubset(findings)


def test_verifier_rejects_metadata_size_and_result_mismatch(tmp_path: Path) -> None:
    verifier, report = _report(tmp_path)
    assert report.snapshot is not None
    snapshot = report.snapshot.model_copy(
        update={"row_count": report.snapshot.row_count + 1}
    )
    assert "SNAPSHOT_METADATA_MISMATCH" in verifier.verify(
        report.model_copy(update={"snapshot": snapshot})
    )

    outputs = list(report.outputs)
    outputs[0] = outputs[0].model_copy(update={"size_bytes": outputs[0].size_bytes + 1})
    assert f"OUTPUT_TAMPERED:{outputs[0].name}" in verifier.verify(
        report.model_copy(update={"outputs": tuple(outputs)})
    )

    logs = list(report.logs)
    logs[0] = logs[0].model_copy(update={"size_bytes": logs[0].size_bytes + 1})
    assert f"LOG_TAMPERED:{logs[0].name}" in verifier.verify(
        report.model_copy(update={"logs": tuple(logs)})
    )

    assert report.execution is not None
    runtime = report.execution.runtime.model_copy(
        update={"size_bytes": report.execution.runtime.size_bytes + 1}
    )
    execution = report.execution.model_copy(update={"runtime": runtime})
    assert "RUNTIME_TAMPERED" in verifier.verify(
        report.model_copy(update={"execution": execution})
    )

    assert report.result is not None
    result = report.result.model_copy(
        update={
            "support": report.result.support.model_copy(
                update={"observations": report.result.support.observations + 1}
            )
        }
    )
    assert "RESULT_MISMATCH" in verifier.verify(
        report.model_copy(update={"result": result})
    )


def test_verifier_rejects_unregistered_method_script_and_execution(
    tmp_path: Path,
) -> None:
    verifier, report = _report(tmp_path)
    forged_spec = report.spec.model_copy(update={"method_id": "not-registered"})
    findings = verifier.verify(report.model_copy(update={"spec": forged_spec}))
    assert {"METHOD_NOT_REGISTERED", "RESULT_MISSING"}.issubset(findings)

    assert report.execution is not None
    script = report.execution.script.model_copy(
        update={"template_id": "forged-template"}
    )
    execution = report.execution.model_copy(update={"script": script})
    assert "SCRIPT_NOT_REGISTERED" in verifier.verify(
        report.model_copy(update={"execution": execution})
    )

    bad_execution = report.execution.model_copy(update={"return_code": 1})
    assert "EXECUTION_EVIDENCE_INVALID" in verifier.verify(
        report.model_copy(update={"execution": bad_execution})
    )


def test_verifier_maps_malformed_authenticated_snapshot_to_metadata_mismatch(
    tmp_path: Path,
) -> None:
    verifier, report = _report(tmp_path)
    assert report.snapshot is not None
    data = b"\xff"
    verifier.files.write(report.snapshot.relative_path, data)
    snapshot = report.snapshot.model_copy(
        update={"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
    )

    assert "SNAPSHOT_METADATA_MISMATCH" in verifier.verify(
        report.model_copy(update={"snapshot": snapshot})
    )


def test_verifier_maps_missing_script_registry_to_typed_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier, report = _report(tmp_path)

    def missing(_spec: object) -> None:
        raise KeyError("registry removed")

    monkeypatch.setattr("envresearch.econometrics.verify.expected_script_for", missing)
    assert "SCRIPT_NOT_REGISTERED" in verifier.verify(report)


def test_verifier_rejects_cross_method_typed_result_substitution(
    tmp_path: Path,
) -> None:
    verifier, report = _report(tmp_path / "cv")
    _, hedonic_report = _report(tmp_path / "hedonic", "hedonic-pricing")
    assert hedonic_report.result is not None

    findings = verifier.verify(
        report.model_copy(update={"result": hedonic_report.result})
    )

    assert {"RESULT_MISMATCH", "CONFIGURATION_MISMATCH"}.issubset(findings)

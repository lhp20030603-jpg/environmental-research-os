"""Local DiD service publication and independent verification tests."""

import hashlib
import json
from pathlib import Path

import pytest

from envresearch.econometrics.data_snapshot import snapshot_csv
from envresearch.econometrics.r_evidence import GeneratedRScript
from envresearch.econometrics.report import LocalAnalysisReference
from envresearch.econometrics.service import EvidenceTampered, _analysis_id


def test_green_report_requires_independent_recomputation(local_service) -> None:
    """PASSED is returned only after reopening every persisted input and output."""
    reference = local_service.service.run(local_service.spec)

    report = local_service.service.status(reference)

    assert report.status == "passed"
    assert report.verification_findings == ()
    assert report.snapshot.sha256 == local_service.source_sha256
    assert local_service.backend.calls == 1


def test_validate_is_read_only(local_service) -> None:
    """Validation inspects exact local bytes without creating store artifacts."""
    validation = local_service.service.validate(local_service.spec)

    assert validation.row_count == 4
    assert validation.sha256 == local_service.source_sha256
    assert not local_service.store.root.exists()


def test_invalid_local_input_publishes_typed_exception(local_service) -> None:
    """Invalid input is durable and never reaches the estimator backend."""
    local_service.spec.data_path.write_text("wrong,columns\n1,2\n", encoding="utf-8")

    reference = local_service.service.run(local_service.spec)
    report = local_service.service.status(reference)

    assert report.status == "exception"
    assert report.code == "LOCAL_DATA_INVALID"
    assert report.snapshot is None
    assert local_service.backend.calls == 0


def test_legacy_invalid_input_id_remains_spec_only(local_service) -> None:
    """Ordinary run(spec) retains its V0.2/V0.3 durable invalid identity."""
    local_service.spec.data_path.write_text("wrong,columns\n1,2\n", encoding="utf-8")
    payload = json.dumps(
        local_service.spec.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    expected = f"local-did-invalid-{hashlib.sha256(payload).hexdigest()[:24]}"

    assert local_service.service.run(local_service.spec).analysis_id == expected


def test_repeated_run_is_content_idempotent(local_service) -> None:
    """The same spec and bytes resolve the same independently verified report."""
    first = local_service.service.run(local_service.spec)
    second = local_service.service.run(local_service.spec)

    assert second == first
    assert local_service.backend.calls == 1


@pytest.mark.parametrize("target", ["snapshot", "script", "runtime", "log", "output"])
def test_status_rejects_tampered_evidence(local_service, target: str) -> None:
    """Stored success never overrides recomputation from current evidence bytes."""
    reference = local_service.service.run(local_service.spec)
    report = local_service.service.status(reference)
    path = {
        "snapshot": local_service.store.root / report.snapshot.relative_path,
        "script": local_service.store.root / report.script_path,
        "runtime": local_service.store.root / report.runtime_path,
        "log": local_service.store.root / report.logs[0].relative_path,
        "output": local_service.store.root / report.outputs[0].relative_path,
    }[target]
    path.chmod(0o644)
    path.write_bytes(b"tampered")

    with pytest.raises(EvidenceTampered):
        local_service.service.status(reference)


def test_status_rejects_same_bytes_through_output_symlink(
    local_service, tmp_path
) -> None:
    """A matching external file cannot replace owned estimator evidence."""
    reference = local_service.service.run(local_service.spec)
    report = local_service.service.status(reference)
    path = local_service.store.root / report.outputs[0].relative_path
    outside = tmp_path / "outside.csv"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)

    with pytest.raises(EvidenceTampered):
        local_service.service.status(reference)


def test_failure_is_typed_and_does_not_publish_passed(local_service) -> None:
    """Execution failure produces a durable non-green report, never PASSED."""
    local_service.backend.failure_code = "R_PACKAGE_UNAVAILABLE"

    reference = local_service.service.run(local_service.spec)
    report = local_service.service.status(reference)

    assert report.status == "exception"
    assert report.code == "R_PACKAGE_UNAVAILABLE"
    assert not tuple((local_service.store.root / "analyses").rglob("passed.marker"))


def test_status_rejects_noncanonical_report_reference(local_service) -> None:
    """A caller cannot redirect report loading outside canonical history."""
    reference = local_service.service.run(local_service.spec)

    with pytest.raises(ValueError, match="canonical relative"):
        LocalAnalysisReference(
            analysis_id=reference.analysis_id,
            generation=reference.generation,
            relative_path=Path("../../forged.json"),
            sha256=reference.sha256,
        )


def test_nonzero_execution_cannot_publish_passed(local_service) -> None:
    """A self-consistent result cannot override a failed R process outcome."""
    original = local_service.backend.execute

    def failed(*args, **kwargs):
        result = original(*args, **kwargs)
        return result.model_copy(
            update={"execution": result.execution.model_copy(update={"return_code": 9})}
        )

    local_service.backend.execute = failed
    reference = local_service.service.run(local_service.spec)

    assert local_service.service.status(reference).status == "exception"


def test_nonrepository_script_cannot_publish_passed(local_service) -> None:
    """Verification regenerates the registered script instead of trusting a hash."""
    original = local_service.backend.execute

    def forged(*args, **kwargs):
        result = original(*args, **kwargs)
        path = result.script.path.parent / "forged.R"
        path.write_text('stop("not repository template")\n', encoding="utf-8")
        import hashlib

        script = GeneratedRScript(
            template_id="forged-v1",
            path=path,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        return result.model_copy(
            update={
                "script": script,
                "execution": result.execution.model_copy(update={"script": script}),
            }
        )

    local_service.backend.execute = forged
    reference = local_service.service.run(local_service.spec)

    assert local_service.service.status(reference).status == "exception"


def test_analysis_ancestor_symlink_cannot_redirect_publication(
    local_service, tmp_path
) -> None:
    """Every owned path component is authenticated before evidence publication."""
    snapshot = snapshot_csv(local_service.spec, local_service.store)
    analysis_id = _analysis_id(local_service.spec, snapshot)
    outside = tmp_path / "outside"
    outside.mkdir()
    analyses = local_service.store.root / "analyses"
    analyses.mkdir(exist_ok=True)
    (analyses / analysis_id).symlink_to(outside, target_is_directory=True)

    with pytest.raises((EvidenceTampered, OSError, ValueError)):
        local_service.service.run(local_service.spec)

    assert not tuple(outside.rglob("*"))


def test_contradictory_passed_report_cannot_be_published(local_service) -> None:
    """Unchecked model copies are revalidated at the publication boundary."""
    reference = local_service.service.run(local_service.spec)
    report = local_service.service.status(reference)
    contradictory = report.model_copy(
        update={
            "code": "VERIFICATION_FAILED",
            "verification_findings": ("SCRIPT_TAMPERED",),
        }
    )

    with pytest.raises(ValueError, match="coherent green"):
        local_service.service.publisher.publish(contradictory)

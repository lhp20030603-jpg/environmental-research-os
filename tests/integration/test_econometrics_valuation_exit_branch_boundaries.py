"""Adversarial branch coverage for the shared Valuation Core exit seam."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from test_econometrics_valuation_exit import _OfflineService, offline_refs

from envresearch.econometrics.exit_evaluator import ValuationExitEvaluator, _matches
from envresearch.econometrics.exit_models import ExpectedComparison
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.report import LocalAnalysisReference, OutputEvidence
from envresearch.econometrics.service import EvidenceTampered
from envresearch.econometrics.valuation_exit_corpus import freeze_valuation_exit_corpus
from envresearch.econometrics.valuation_exit_models import (
    ValuationExitAnalysisBinding,
    ValuationExitCaseInput,
    ValuationExitExpectationCatalog,
    ValuationExitManifest,
)
from envresearch.econometrics.valuation_exit_runner import (
    ValuationExitRunner,
    ValuationRegistryAnalysisExecutor,
)


class _Files:
    def __init__(self) -> None:
        self.bytes: dict[Path, bytes] = {}

    def read(self, path: Path) -> bytes:
        return self.bytes[path]

    def write(self, path: Path, data: bytes) -> None:
        self.bytes[path] = data


class _Service:
    def __init__(self, status: object) -> None:
        self.report = status
        self.files = _Files()
        self.reference = LocalAnalysisReference(
            analysis_id="branch-analysis",
            generation=1,
            relative_path=Path(
                "analyses/branch-analysis/history/generation-1-" + "1" * 64 + ".json"
            ),
            sha256="1" * 64,
        )

    def run_exact(self, *args: object) -> LocalAnalysisReference:
        return self.reference

    def status(self, reference: LocalAnalysisReference):
        del reference
        if isinstance(self.report, Exception):
            raise self.report
        return self.report


def _frozen(tmp_path: Path):
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    manifest = runner.load(refs.manifest_ref, ValuationExitManifest)
    catalog = evaluator.load(refs.catalog_ref, ValuationExitExpectationCatalog)
    return runner, evaluator, refs, manifest, catalog


def _status(data_hash: str, *, passed: bool = True, outputs: tuple = ()):
    return SimpleNamespace(
        status="passed" if passed else "exception",
        code=None if passed else "WRONG_CODE",
        snapshot=SimpleNamespace(sha256=data_hash),
        outputs=outputs,
    )


def _comparison(kind: str, selector: str, expected: object) -> ExpectedComparison:
    return ExpectedComparison(comparison_type=kind, output_name="result.csv", selector=selector, expected=expected)  # type: ignore[arg-type]  # fmt: skip


def _bind(runner: ExitRegistry, case, reference: LocalAnalysisReference) -> None:
    binding = ValuationExitAnalysisBinding(
        schema_version="econometrics.valuation-exit-analysis-binding.v1",
        case_ref=case.case_ref,
        analysis_ref=reference,
        data_sha256=case.data_ref.content_hash,
    )
    binding_ref = runner.publish(
        f"valuation-analysis-ref-{case.case_id}", binding, version=1
    )
    runner.set_current(f"valuation-analysis-{case.case_id}", binding_ref)


def test_executor_rejects_descriptor_and_spec_data_path_mismatch(
    tmp_path: Path,
) -> None:
    runner, _, _, manifest, _ = _frozen(tmp_path)
    case = next(item for item in manifest.cases if item.case_id == "green-hedonic")
    service = _Service(_status(case.data_ref.content_hash))
    executor = ValuationRegistryAnalysisExecutor(runner, service)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="does not match its blinded descriptor"):
        executor.execute(case.model_copy(update={"case_id": "changed-case"}))

    payload = runner.load(case.case_ref, ValuationExitCaseInput)
    changed = payload.model_copy(
        update={
            "spec": payload.spec.model_copy(
                update={"data_path": Path("/tmp/wrong.csv")}
            )
        }
    )
    changed_ref = runner.publish("valuation-case-green-hedonic-changed", changed)
    with pytest.raises(ValueError, match="not bound to its exact data"):
        executor.execute(case.model_copy(update={"case_ref": changed_ref}))


def test_executor_verify_rejects_missing_binding_data_and_status(
    tmp_path: Path,
) -> None:
    runner, _, _, manifest, _ = _frozen(tmp_path)
    green = next(item for item in manifest.cases if item.case_id == "green-hedonic")
    service = _Service(_status(green.data_ref.content_hash, passed=False))
    executor = ValuationRegistryAnalysisExecutor(runner, service)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="binding is missing"):
        executor.verify(green, service.reference)

    _bind(runner, green, service.reference)
    with pytest.raises(ValueError, match="data authority is stale"):
        executor.verify(
            green.model_copy(update={"data_ref": manifest.cases[1].data_ref}),
            service.reference,
        )
    with pytest.raises(ValueError, match="green exit case did not pass"):
        executor.verify(green, service.reference)

    failure = next(item for item in manifest.cases if item.case_id == "fail-cv-wtp")
    service.report = _status(failure.data_ref.content_hash, passed=True)
    _bind(runner, failure, service.reference)
    with pytest.raises(ValueError, match="scientific-failure case did not reject"):
        executor.verify(failure, service.reference)


def test_executor_rejects_snapshot_and_unmutatable_integrity_case(
    tmp_path: Path,
) -> None:
    runner, _, _, manifest, _ = _frozen(tmp_path)
    green = next(item for item in manifest.cases if item.case_id == "green-hedonic")
    service = _Service(_status("0" * 64))
    executor = ValuationRegistryAnalysisExecutor(runner, service)  # type: ignore[arg-type]
    _bind(runner, green, service.reference)
    with pytest.raises(ValueError, match="snapshot is not bound"):
        executor.verify(green, service.reference)

    integrity = next(
        item for item in manifest.cases if item.case_id == "integrity-output-tamper"
    )
    service.report = _status(integrity.data_ref.content_hash, outputs=())
    with pytest.raises(ValueError, match="requires one green output"):
        executor.execute(integrity)


def test_evaluator_case_records_every_fail_closed_finding(tmp_path: Path) -> None:
    runner, evaluator, _, manifest, catalog = _frozen(tmp_path)
    case = next(item for item in manifest.cases if item.case_id == "green-hedonic")
    expected = next(item for item in catalog.cases if item.case_id == case.case_id)
    service = _Service(OSError("missing evidence"))
    protected = ValuationExitEvaluator(runner, evaluator, service)  # type: ignore[arg-type]

    outcome = protected._case(
        case.case_id, "scientific-failure", service.reference, expected, "0" * 64
    )
    assert set(outcome.findings) == {"ROLE_MISMATCH", "ANALYSIS_EVIDENCE_INVALID"}

    integrity = next(
        item for item in manifest.cases if item.case_id == "integrity-output-tamper"
    )
    integrity_expected = next(
        item for item in catalog.cases if item.case_id == integrity.case_id
    ).model_copy(update={"expected_code": "WRONG"})
    outcome = protected._case(
        integrity.case_id,
        integrity.role,
        service.reference,
        integrity_expected,
        "0" * 64,
    )
    assert set(outcome.findings) == {
        "EXPECTED_FAILURE_MISMATCH",
        "ANALYSIS_EVIDENCE_INVALID",
    }


def test_evaluator_green_finds_snapshot_output_and_comparison_mismatch(
    tmp_path: Path,
) -> None:
    runner, evaluator, _, manifest, catalog = _frozen(tmp_path)
    case = next(item for item in manifest.cases if item.case_id == "green-hedonic")
    expected = next(item for item in catalog.cases if item.case_id == case.case_id)
    service = _Service(_status("0" * 64, passed=False))
    protected = ValuationExitEvaluator(runner, evaluator, service)  # type: ignore[arg-type]
    outcome = protected._case(
        case.case_id, case.role, service.reference, expected, case.data_ref.content_hash
    )
    assert set(outcome.findings) == {"DATA_AUTHORITY_MISMATCH", "GREEN_CASE_NOT_PASSED"}

    service.report = _status(case.data_ref.content_hash, outputs=())
    outcome = protected._case(
        case.case_id, case.role, service.reference, expected, case.data_ref.content_hash
    )
    assert outcome.findings == ("OUTPUT_SET_MISMATCH",)

    evidence = tuple(
        OutputEvidence(
            name=item.output_name,
            relative_path=Path("fake") / item.output_name,
            sha256="0" * 64,
            size_bytes=1,
        )
        for item in expected.comparisons
    )
    for item in evidence:
        service.files.bytes[item.relative_path] = b"x"
    service.report = _status(case.data_ref.content_hash, outputs=evidence)
    outcome = protected._case(
        case.case_id, case.role, service.reference, expected, case.data_ref.content_hash
    )
    assert len(outcome.findings) == len(expected.comparisons)
    assert all(item.startswith("COMPARISON_MISMATCH") for item in outcome.findings)


@pytest.mark.parametrize(
    ("comparison", "data"),
    (
        (_comparison("json", "relative", 1), b'{"value":1}'),
        (_comparison("csv", "row=missing,column=value", 1), b"id,value\na,1\n"),
        (_comparison("csv", "row=a,column=value", "text"), b"id,value\na,text\n"),
    ),
)
def test_exit_comparison_selectors_fail_closed_or_match_text(
    comparison: ExpectedComparison, data: bytes
) -> None:
    assert _matches(data, comparison) is (comparison.expected == "text")


def test_evaluator_catalog_authority_requires_current_binding(tmp_path: Path) -> None:
    runner, evaluator, refs, manifest, _ = _frozen(tmp_path)
    service = _Service(EvidenceTampered("tampered"))
    protected = ValuationExitEvaluator(runner, evaluator, service)  # type: ignore[arg-type]
    pointer = (
        evaluator.root / "exit/current/valuation-catalog-valuation-core-local.json"
    )
    pointer.chmod(0o600)
    pointer.unlink()

    assert not protected._catalog_is_authorized(
        manifest, refs.manifest_ref, refs.catalog_ref
    )


def test_integrity_execution_and_verification_fail_closed(tmp_path: Path) -> None:
    runner, _, _, manifest, _ = _frozen(tmp_path)
    integrity = next(
        item for item in manifest.cases if item.role == "integrity-failure"
    )

    detected = _Service(_status(integrity.data_ref.content_hash))
    status_calls = 0

    def detect_on_reopen(reference: LocalAnalysisReference):
        nonlocal status_calls
        status_calls += 1
        if status_calls > 1:
            raise EvidenceTampered("already mutated")
        return detected.report

    detected.status = detect_on_reopen  # type: ignore[method-assign]
    executor = ValuationRegistryAnalysisExecutor(runner, detected)  # type: ignore[arg-type]
    assert executor.execute(integrity) == detected.reference

    runner2, _, _, manifest2, _ = _frozen(tmp_path / "undetected")
    integrity2 = next(
        item for item in manifest2.cases if item.role == "integrity-failure"
    )
    path = Path("analyses/branch-analysis/evidence/output.csv")
    evidence = OutputEvidence(
        name="output.csv", relative_path=path, sha256="0" * 64, size_bytes=1
    )
    undetected = _Service(
        _status(integrity2.data_ref.content_hash, outputs=(evidence,))
    )
    undetected.files.bytes[path] = b"x"
    executor2 = ValuationRegistryAnalysisExecutor(
        runner2,
        undetected,  # type: ignore[arg-type]
    )
    reference = executor2.execute(integrity2)
    with pytest.raises(ValueError, match="not independently rejected"):
        executor2.verify(integrity2, reference)


def test_evaluator_records_undetected_integrity_and_wrong_scientific_code(
    tmp_path: Path,
) -> None:
    runner, evaluator, _, manifest, catalog = _frozen(tmp_path)
    service = _Service(None)
    protected = ValuationExitEvaluator(runner, evaluator, service)  # type: ignore[arg-type]
    for role, finding in (
        ("integrity-failure", "INTEGRITY_FAILURE_NOT_DETECTED"),
        ("scientific-failure", "EXPECTED_FAILURE_MISMATCH"),
    ):
        case = next(item for item in manifest.cases if item.role == role)
        expected = next(item for item in catalog.cases if item.case_id == case.case_id)
        service.report = _status(case.data_ref.content_hash)
        outcome = protected._case(
            case.case_id,
            case.role,
            service.reference,
            expected,
            case.data_ref.content_hash,
        )
        assert outcome.findings == (finding,)


def test_evaluator_requires_every_current_binding_and_reproduced_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    refs = offline_refs(tmp_path, runner, evaluator)
    service = _OfflineService()
    run_ref = ValuationExitRunner(
        runner,
        ValuationRegistryAnalysisExecutor(runner, service),  # type: ignore[arg-type]
    ).run(refs.manifest_ref)
    protected = ValuationExitEvaluator(runner, evaluator, service)  # type: ignore[arg-type]

    pointer = runner.root / "exit/current/valuation-analysis-green-hedonic.json"
    pointer.chmod(0o600)
    saved = pointer.read_bytes()
    pointer.unlink()
    with pytest.raises(ValueError, match="binding is missing"):
        protected.evaluate(run_ref, refs.catalog_ref)
    pointer.write_bytes(saved)

    report_ref, report = protected.evaluate_reference(run_ref, refs.catalog_ref)
    monkeypatch.setattr(protected, "_build_report", lambda *args: object())
    with pytest.raises(ValueError, match="independent evaluation"):
        protected.status(report_ref)
    assert report.status == "passed"


def test_runner_rejects_forged_resume_state(tmp_path: Path) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    refs = offline_refs(tmp_path, runner, evaluator)
    service = _OfflineService()
    protected = ValuationExitRunner(
        runner,
        ValuationRegistryAnalysisExecutor(runner, service),  # type: ignore[arg-type]
    )
    run_ref = protected.run(refs.manifest_ref)
    run = runner.load(run_ref, protected._runner.run_model)
    subject = "valuation-run-valuation-core-local"

    wrong_manifest = run.model_copy(
        update={
            "manifest_ref": refs.manifest_ref.model_copy(
                update={"content_hash": "f" * 64}
            )
        }
    )
    for version, state, message in (
        (11, wrong_manifest, "another manifest"),
        (12, run.model_copy(update={"receipts": (run.receipts[0],) * 2}), "duplicated"),
        (
            13,
            run.model_copy(
                update={
                    "receipts": (
                        run.receipts[0].model_copy(update={"role": "green"}),
                        *run.receipts[1:],
                    )
                }
            ),
            "not authorized",
        ),
    ):
        forged = runner.publish(subject, state, version=version)
        runner.set_current(subject, forged)
        with pytest.raises(ValueError, match=message):
            protected.run(refs.manifest_ref)

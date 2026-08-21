"""Resumable blinded exit runner integration."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from envresearch.econometrics.contracts import LocalAnalysisSpec
from envresearch.econometrics.exit_models import (
    ExitAnalysisBinding,
    ExitCase,
    ExitCaseInput,
    V03ExitManifest,
    V03ExitRun,
)
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.exit_runner import RegistryAnalysisExecutor, V03ExitRunner
from envresearch.econometrics.report import LocalAnalysisReference
from envresearch.models.artifact import ArtifactRef

GREEN = (
    "rct-itt",
    "did-event-study",
    "rdd-local-linear",
    "iv-2sls",
    "synthetic-control",
    "environmental-measurement",
    "meta-analysis",
    "panel-fe",
)


def _artifact(index: int) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=f"case-{index:02d}",
        artifact_version=1,
        content_hash=f"{index + 1:064x}",
    )


def _analysis(case_id: str, index: int) -> LocalAnalysisReference:
    digest = f"{index + 40:064x}"
    return LocalAnalysisReference(
        analysis_id=case_id,
        generation=1,
        relative_path=Path("analyses")
        / case_id
        / "history"
        / f"generation-1-{digest}.json",
        sha256=digest,
    )


def _manifest(
    catalog: ArtifactRef, data_refs: tuple[ArtifactRef, ...] | None = None
) -> V03ExitManifest:
    refs = data_refs or tuple(_artifact(index + 100) for index in range(16))
    cases = [
        ExitCase(
            case_id=f"green-{family}",
            family=family,
            role="green",
            case_ref=_artifact(i),
            data_ref=refs[i],
        )
        for i, family in enumerate(GREEN)
    ]
    cases += [
        ExitCase(
            case_id=f"fail-{family}",
            family=family,
            role="assumption-failure",
            case_ref=_artifact(i + 8),
            data_ref=refs[i + 8],
        )
        for i, family in enumerate(GREEN[:-1])
    ]
    cases.append(
        ExitCase(
            case_id="integrity-rct",
            family="rct-itt",
            role="integrity-failure",
            case_ref=_artifact(15),
            data_ref=refs[15],
        )
    )
    return V03ExitManifest(
        schema_version="econometrics.v03-exit-manifest.v1",
        manifest_id="wave1",
        cases=tuple(cases),
        expectation_catalog_ref=catalog,
    )


class _Executor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.cache: dict[str, LocalAnalysisReference] = {}
        self.lock = threading.Lock()

    def execute(self, case: ExitCase) -> LocalAnalysisReference:
        with self.lock:
            if case.case_id not in self.cache:
                self.calls.append(case.case_id)
                self.cache[case.case_id] = _analysis(case.case_id, len(self.cache))
            return self.cache[case.case_id]

    def verify(self, case: ExitCase, reference: LocalAnalysisReference) -> None:
        assert case.case_id == reference.analysis_id
        assert self.cache.get(reference.analysis_id) == reference


class _AnalysisService:
    def __init__(self, reference: LocalAnalysisReference) -> None:
        self.reference = reference
        self.calls = 0

    def run_exact(
        self, spec, data: bytes, expected_sha256: str
    ) -> LocalAnalysisReference:
        assert spec.method_id == "did-event-study"
        assert data
        assert expected_sha256
        self.calls += 1
        return self.reference

    def status(self, reference: LocalAnalysisReference):
        assert reference == self.reference
        return SimpleNamespace(status="passed", outputs=(), snapshot=None)


def test_runner_resumes_without_duplicate_execution(tmp_path: Path) -> None:
    registry = ExitRegistry((tmp_path / "runner").resolve())
    catalog = ArtifactRef(
        artifact_id="protected-catalog", artifact_version=1, content_hash="f" * 64
    )
    manifest_ref = registry.publish("manifest-wave1", _manifest(catalog))
    executor = _Executor()
    runner = V03ExitRunner(registry, executor)
    first = runner.run(manifest_ref)
    second = runner.run(manifest_ref)
    assert first == second and len(executor.calls) == 16
    state = registry.load(second, V03ExitRun)
    assert tuple(item.case_id for item in state.receipts) == tuple(
        sorted(executor.calls)
    )


def test_v03_runner_retains_public_registry_and_executor_attributes(
    tmp_path: Path,
) -> None:
    """Delegation must not remove established caller-visible runner attributes."""
    registry = ExitRegistry((tmp_path / "runner").resolve())
    executor = _Executor()

    runner = V03ExitRunner(registry, executor)

    assert runner.registry is registry
    assert runner.executor is executor


def test_concurrent_runners_serialize_same_manifest(tmp_path: Path) -> None:
    registry = ExitRegistry((tmp_path / "runner").resolve())
    catalog = ArtifactRef(
        artifact_id="protected-catalog", artifact_version=1, content_hash="f" * 64
    )
    manifest_ref = registry.publish("manifest-wave1", _manifest(catalog))
    executor = _Executor()
    runner = V03ExitRunner(registry, executor)
    results: list[ArtifactRef] = []
    threads = [
        threading.Thread(target=lambda: results.append(runner.run(manifest_ref)))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(executor.calls) == 16 and results[0] == results[1]


def test_runner_recovers_after_current_pointer_publication_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry = ExitRegistry((tmp_path / "runner").resolve())
    catalog = ArtifactRef(
        artifact_id="protected-catalog", artifact_version=1, content_hash="f" * 64
    )
    manifest_ref = registry.publish("manifest-wave1", _manifest(catalog))
    executor = _Executor()
    runner = V03ExitRunner(registry, executor)
    original = registry.set_current
    failures = 0

    def fail_once(subject: str, reference: ArtifactRef) -> None:
        nonlocal failures
        if failures == 0:
            failures += 1
            raise OSError("injected current publication failure")
        original(subject, reference)

    monkeypatch.setattr(registry, "set_current", fail_once)
    with pytest.raises(OSError, match="injected"):
        runner.run(manifest_ref)
    result = runner.run(manifest_ref)
    assert len(executor.calls) == 16
    assert len(registry.load(result, V03ExitRun).receipts) == 16


def test_registry_executor_resolves_blinded_case_once(tmp_path: Path) -> None:
    registry = ExitRegistry((tmp_path / "runner").resolve())
    data_ref = registry.publish_bytes("data-green-did", b"unit,year\n1,2020\n")
    spec = LocalAnalysisSpec(
        schema_version="econometrics.local-analysis.v1",
        method_id="did-event-study",
        data_path=registry.materialize_data(data_ref),
        columns={
            "unit": "unit",
            "time": "year",
            "outcome": "emissions",
            "treatment_cohort": "first_treated",
            "covariates": (),
        },
        comparison_group="not-yet-treated",
        reference_period=-1,
        inference={
            "confidence_level": 0.95,
            "cluster_column": "unit",
            "interval_mode": "simultaneous",
            "bootstrap_seed": 20260812,
        },
        budget={
            "inactivity_seconds": 120,
            "max_output_bytes": 2_000_000,
            "max_workspace_bytes": 20_000_000,
        },
    )
    payload = ExitCaseInput(
        schema_version="econometrics.v03-exit-case.v1",
        case_id="green-did",
        family="did-event-study",
        data_ref=data_ref,
        spec=spec,
    )
    case_ref = registry.publish("case-green-did", payload)
    case = ExitCase(
        case_id="green-did",
        family="did-event-study",
        role="green",
        case_ref=case_ref,
        data_ref=data_ref,
    )
    service = _AnalysisService(_analysis("green-did", 1))
    executor = RegistryAnalysisExecutor(registry, service)  # type: ignore[arg-type]
    assert executor.execute(case) == executor.execute(case)
    assert service.calls == 1
    data_relative = spec.data_path.relative_to(registry.root)
    registry.files.write(data_relative, b"tampered")
    with pytest.raises(ValueError, match="content hash mismatch"):
        executor.verify(case, service.reference)
    registry.files.write(data_relative, b"unit,year\n1,2020\n")

    revised_data = registry.publish_bytes("data-green-did-revised", b"revised")
    revised = payload.model_copy(
        update={
            "data_ref": revised_data,
            "spec": spec.model_copy(
                update={"data_path": registry.materialize_data(revised_data)}
            ),
        }
    )
    revised_ref = registry.publish("case-green-did-revised", revised)
    revised_case = case.model_copy(
        update={"case_ref": revised_ref, "data_ref": revised_data}
    )
    with pytest.raises(ValueError, match="generation is stale"):
        executor.execute(revised_case)
    revised_binding = ExitAnalysisBinding(
        schema_version="econometrics.v03-exit-analysis-binding.v1",
        case_ref=revised_ref,
        analysis_ref=service.reference,
    )
    binding_ref = registry.publish(
        "analysis-ref-green-did-revised", revised_binding, version=2
    )
    registry.set_current("analysis-green-did", binding_ref)
    with pytest.raises(ValueError, match="stale or revised"):
        executor.verify(case, service.reference)


def test_registry_executor_rejects_tampered_case_data(tmp_path: Path) -> None:
    registry = ExitRegistry((tmp_path / "runner").resolve())
    data_ref = registry.publish_bytes("data-green-did", b"original")
    spec = LocalAnalysisSpec(
        schema_version="econometrics.local-analysis.v1",
        method_id="did-event-study",
        data_path=registry.materialize_data(data_ref),
        columns={
            "unit": "unit",
            "time": "year",
            "outcome": "emissions",
            "treatment_cohort": "first_treated",
            "covariates": (),
        },
        comparison_group="not-yet-treated",
        reference_period=-1,
        inference={
            "confidence_level": 0.95,
            "cluster_column": "unit",
            "interval_mode": "simultaneous",
            "bootstrap_seed": 20260812,
        },
        budget={
            "inactivity_seconds": 120,
            "max_output_bytes": 2_000_000,
            "max_workspace_bytes": 20_000_000,
        },
    )
    payload = ExitCaseInput(
        schema_version="econometrics.v03-exit-case.v1",
        case_id="green-did",
        family="did-event-study",
        data_ref=data_ref,
        spec=spec,
    )
    case = ExitCase(
        case_id="green-did",
        family="did-event-study",
        role="green",
        case_ref=registry.publish("case-green-did", payload),
        data_ref=data_ref,
    )
    relative = payload.spec.data_path.relative_to(registry.root)
    service = _AnalysisService(_analysis("green-did", 1))
    original_run = service.run_exact

    def replace_after_authentication(spec, data: bytes, expected_sha256: str):
        assert data == b"original"
        registry.files.write(relative, b"tampered")
        return original_run(spec, data, expected_sha256)

    service.run_exact = replace_after_authentication  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="content hash mismatch"):
        RegistryAnalysisExecutor(registry, service).execute(case)  # type: ignore[arg-type]
    assert service.calls == 1


def test_runner_recovers_after_receipt_history_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry = ExitRegistry((tmp_path / "runner").resolve())
    catalog = ArtifactRef(
        artifact_id="protected-catalog", artifact_version=1, content_hash="f" * 64
    )
    manifest_ref = registry.publish("manifest-wave1", _manifest(catalog))
    executor = _Executor()
    runner = V03ExitRunner(registry, executor)
    original = registry.publish
    failures = 0

    def fail_once(artifact_id, payload, *, version=1):
        nonlocal failures
        if artifact_id == "run-wave1" and failures == 0:
            failures += 1
            raise OSError("injected history failure")
        return original(artifact_id, payload, version=version)

    monkeypatch.setattr(registry, "publish", fail_once)
    with pytest.raises(OSError, match="history"):
        runner.run(manifest_ref)
    result = runner.run(manifest_ref)
    assert len(executor.calls) == 16
    assert len(registry.load(result, V03ExitRun).receipts) == 16

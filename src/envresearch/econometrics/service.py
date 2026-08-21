"""Concise local-analysis orchestration with independent verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from envresearch.econometrics._analysis_lock import AnalysisCoordinator
from envresearch.econometrics._file_evidence import read_regular
from envresearch.econometrics._store_files import StoreFiles
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.data_snapshot import (
    LocalDataInvalid,
    LocalDataSnapshot,
    LocalDataValidation,
    snapshot_csv,
    snapshot_csv_bytes,
)
from envresearch.econometrics.local_validation import validate_csv
from envresearch.econometrics.r_evidence import GeneratedRScript, RExecutionEvidence
from envresearch.econometrics.recipes import AnalysisResult, recipe_for
from envresearch.econometrics.report import (
    LocalAnalysisReference,
    LocalAnalysisReport,
    OutputEvidence,
)
from envresearch.econometrics.store import ReportPublisher
from envresearch.econometrics.verify import LocalAnalysisVerifier
from envresearch.storage.research_artifacts import ResearchArtifactStore

STRICT_FROZEN = ConfigDict(extra="forbid", frozen=True, strict=True)


class EvidenceTampered(ValueError):
    """Persisted analysis bytes no longer match their exact references."""


class LocalExecutionError(RuntimeError):
    """Typed non-green execution outcome before report publication."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BackendResult(BaseModel):
    """Complete in-workspace result returned by one trusted backend."""

    model_config = STRICT_FROZEN

    script: GeneratedRScript
    execution: RExecutionEvidence
    result: AnalysisResult
    output_root: Path


class LocalAnalysisBackend(Protocol):
    """Injected trusted estimator boundary used by the durable service."""

    def execute(
        self,
        spec: AnalysisSpec,
        snapshot: LocalDataSnapshot,
        snapshot_bytes: bytes,
        workspace: Path,
    ) -> BackendResult: ...


class LocalAnalysisService:
    """Snapshot, execute, verify, and publish one local analysis."""

    def __init__(
        self,
        store: ResearchArtifactStore,
        backend: LocalAnalysisBackend,
    ) -> None:
        self.store = store
        self.backend = backend
        self.publisher = ReportPublisher(store.root)
        configured_authorities = getattr(backend, "package_authorities", None)
        self.verifier = LocalAnalysisVerifier(
            store.root,
            None if configured_authorities is None else tuple(configured_authorities),
        )
        self.files = StoreFiles(store.root)

    def run(self, spec: AnalysisSpec) -> LocalAnalysisReference:
        """Run or recover one content-addressed local analysis."""
        try:
            snapshot = snapshot_csv(spec, self.store)
        except LocalDataInvalid as error:
            return self._publish_invalid_input(spec, error)
        analysis_id = _analysis_id(spec, snapshot)
        with AnalysisCoordinator(self.store.root, analysis_id).locked():
            return self._run_locked(spec, snapshot, analysis_id)

    def run_exact(
        self, spec: AnalysisSpec, data: bytes, expected_sha256: str
    ) -> LocalAnalysisReference:
        """Run from caller-authenticated bytes without reopening their pathname."""
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise EvidenceTampered(
                "exact local data bytes do not match their authority"
            )
        try:
            snapshot = snapshot_csv_bytes(spec, data, self.store)
        except LocalDataInvalid as error:
            return self._publish_invalid_input(spec, error, data_sha256=expected_sha256)
        analysis_id = _analysis_id(spec, snapshot)
        with AnalysisCoordinator(self.store.root, analysis_id).locked():
            return self._run_locked(spec, snapshot, analysis_id)

    def validate(self, spec: AnalysisSpec) -> LocalDataValidation:
        """Validate local CSV bytes without persisting or executing anything."""
        return validate_csv(spec)

    def _publish_invalid_input(
        self,
        spec: AnalysisSpec,
        error: LocalDataInvalid,
        *,
        data_sha256: str | None = None,
    ) -> LocalAnalysisReference:
        analysis_id = _invalid_analysis_id(spec, data_sha256)
        with AnalysisCoordinator(self.store.root, analysis_id).locked():
            try:
                existing = self.publisher.recover(
                    analysis_id
                ) or self.publisher.current(analysis_id)
            except (OSError, ValueError) as state_error:
                raise EvidenceTampered(
                    "invalid-input control state is not authentic"
                ) from state_error
            if existing is not None:
                return existing
            return self.publisher.publish(
                _exception_report(analysis_id, spec, None, error.code, str(error))
            )

    def _run_locked(
        self,
        spec: AnalysisSpec,
        snapshot: LocalDataSnapshot,
        analysis_id: str,
    ) -> LocalAnalysisReference:
        """Recover or execute after acquiring the exact analysis lock."""
        try:
            recovered = self.publisher.recover(analysis_id)
            current = self.publisher.current(analysis_id)
        except (OSError, ValueError) as error:
            raise EvidenceTampered(
                "local analysis control state is not authentic"
            ) from error
        if recovered is not None:
            self.status(recovered)
            return recovered
        if current is not None:
            self.status(current)
            return current
        workspace = self.store.root / "work" / analysis_id
        self.files.ensure_directory(Path("work") / analysis_id)
        try:
            backend_result = self.backend.execute(
                spec,
                snapshot,
                self.files.read(snapshot.relative_path),
                workspace,
            )
        except LocalExecutionError as error:
            return self.publisher.publish(
                _exception_report(analysis_id, spec, snapshot, error.code, str(error))
            )
        try:
            report = self._persist_and_verify(
                analysis_id, spec, snapshot, backend_result
            )
        except (EvidenceTampered, OSError, ValueError) as error:
            report = _exception_report(
                analysis_id, spec, snapshot, "EVIDENCE_TAMPERED", str(error)
            )
        return self.publisher.publish(report)

    def status(self, reference: LocalAnalysisReference) -> LocalAnalysisReport:
        """Reopen one exact report and independently recompute PASSED evidence."""
        try:
            report = self.publisher.load(reference)
        except (OSError, ValueError) as error:
            raise EvidenceTampered("local analysis report is not authentic") from error
        findings = self.verifier.verify(report)
        if report.status == "passed" and findings:
            raise EvidenceTampered(f"local analysis evidence failed: {findings}")
        return report

    def _persist_and_verify(
        self,
        analysis_id: str,
        spec: AnalysisSpec,
        snapshot: LocalDataSnapshot,
        backend: BackendResult,
    ) -> LocalAnalysisReport:
        """Persist exact script/outputs, then recompute before PASSED."""
        evidence_root = Path("analyses") / analysis_id / "evidence"
        script_relative = evidence_root / "script.R"
        self.files.persist_exact(script_relative, read_regular(backend.script.path))
        runtime_relative = evidence_root / "runtime.bin"
        self.files.persist_exact(
            runtime_relative,
            read_regular(backend.execution.runtime.executable),
        )
        logs = tuple(
            _persist_evidence(
                self.files,
                evidence_root / "logs" / name,
                data.encode("utf-8"),
                name,
            )
            for name, data in (
                ("stdout.log", backend.execution.redacted_stdout),
                ("stderr.log", backend.execution.redacted_stderr),
            )
        )
        output_root = evidence_root / "outputs"
        outputs: list[OutputEvidence] = []
        recipe = recipe_for(spec.method_id, workspace=self.store.root / "verification")
        for name in sorted(recipe.expected_outputs):
            source = backend.output_root / name
            data = read_regular(source)
            relative = output_root / name
            self.files.persist_exact(relative, data)
            outputs.append(
                OutputEvidence(
                    name=name,
                    relative_path=relative,
                    sha256=hashlib.sha256(data).hexdigest(),
                    size_bytes=len(data),
                )
            )
        draft = LocalAnalysisReport(
            schema_version="econometrics.local-report.v1",
            analysis_id=analysis_id,
            generation=1,
            status="passed",
            code=None,
            spec=spec,
            snapshot=snapshot,
            script_path=script_relative,
            script_sha256=backend.script.sha256,
            runtime_path=runtime_relative,
            output_root=output_root,
            outputs=tuple(outputs),
            logs=logs,
            execution=backend.execution,
            result=backend.result,
            verification_findings=(),
        )
        findings = self.verifier.verify(draft)
        if findings:
            return draft.model_copy(
                update={
                    "status": "exception",
                    "code": "VERIFICATION_FAILED",
                    "verification_findings": findings,
                }
            )
        return draft


def _analysis_id(spec: AnalysisSpec, snapshot: LocalDataSnapshot) -> str:
    """Bind analysis identity to canonical authority and exact input bytes."""
    payload = {
        "spec": spec.model_dump(mode="json"),
        "snapshot_sha256": snapshot.sha256,
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    prefix = (
        "local-did"
        if spec.method_id == "did-event-study"
        else f"local-{spec.method_id}"
    )
    return f"{prefix}-{hashlib.sha256(data).hexdigest()[:24]}"


def _invalid_analysis_id(spec: AnalysisSpec, data_sha256: str | None = None) -> str:
    payload: object
    if data_sha256 is None:
        payload = spec.model_dump(mode="json")
    else:
        payload = {
            "spec": spec.model_dump(mode="json"),
            "snapshot_sha256": data_sha256,
        }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    prefix = (
        "local-did-invalid"
        if spec.method_id == "did-event-study"
        else f"local-{spec.method_id}-invalid"
    )
    return f"{prefix}-{hashlib.sha256(data).hexdigest()[:24]}"


def _persist_evidence(
    files: StoreFiles, relative: Path, data: bytes, name: str
) -> OutputEvidence:
    """Persist and describe one exact supporting evidence file."""
    try:
        files.persist_exact(relative, data)
    except ValueError as error:
        raise EvidenceTampered(str(error)) from error
    return OutputEvidence(
        name=name,
        relative_path=relative,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def _exception_report(
    analysis_id: str,
    spec: AnalysisSpec,
    snapshot: LocalDataSnapshot | None,
    code: str,
    message: str,
) -> LocalAnalysisReport:
    """Build one durable typed non-green report."""
    return LocalAnalysisReport(
        schema_version="econometrics.local-report.v1",
        analysis_id=analysis_id,
        generation=1,
        status="exception",
        code=code,
        spec=spec,
        snapshot=snapshot,
        script_path=None,
        script_sha256=None,
        runtime_path=None,
        output_root=None,
        outputs=(),
        logs=(),
        execution=None,
        result=None,
        verification_findings=(message,),
    )

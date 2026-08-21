"""Resumable expectation-blind orchestration for the V0.3 exit matrix."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from envresearch.econometrics.exit_models import (
    ExitAnalysisBinding,
    ExitCase,
    ExitCaseInput,
    ExitCaseReceipt,
    V03ExitManifest,
    V03ExitRun,
)
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.report import LocalAnalysisReference
from envresearch.econometrics.service import EvidenceTampered, LocalAnalysisService
from envresearch.models.artifact import ArtifactRef


class ExitCaseExecutor(Protocol):
    """Execute and authenticate one blinded case reference."""

    def execute(self, case: ExitCase) -> LocalAnalysisReference: ...

    def verify(self, case: ExitCase, reference: LocalAnalysisReference) -> None: ...


class ResumableExitRunner:
    """Reusable exact-reference runner for an expectation-blind exit protocol."""

    def __init__(
        self,
        registry: ExitRegistry,
        executor: Any,
        *,
        manifest_model: type[BaseModel],
        run_model: type[BaseModel],
        receipt_model: type[BaseModel],
        schema_version: str,
        subject_prefix: str,
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.manifest_model = manifest_model
        self.run_model = run_model
        self.receipt_model = receipt_model
        self.schema_version = schema_version
        self.subject_prefix = subject_prefix

    def run(self, manifest_ref: ArtifactRef) -> ArtifactRef:
        """Resume a single exact manifest without inspecting evaluator bytes."""
        manifest: Any = self.registry.load(manifest_ref, self.manifest_model)
        subject = f"{self.subject_prefix}{manifest.manifest_id}"
        with self.registry.lock(subject):
            current = self.registry.current(subject)
            state: Any = self._state(current, manifest_ref, manifest)
            cases_by_id = {item.case_id: item for item in manifest.cases}
            completed = {item.case_id: item for item in state.receipts}
            for receipt in state.receipts:
                self.executor.verify(cases_by_id[receipt.case_id], receipt.analysis_ref)
            for case in sorted(manifest.cases, key=lambda item: item.case_id):
                if case.case_id in completed:
                    continue
                reference = self.executor.execute(case)
                self.executor.verify(case, reference)
                receipt = self.receipt_model.model_validate(
                    {
                        "case_id": case.case_id,
                        "role": case.role,
                        "analysis_ref": reference,
                    }
                )
                state = state.model_copy(
                    update={"receipts": (*state.receipts, receipt)}
                )
                current = self.registry.publish(
                    subject, state, version=len(state.receipts) + 1
                )
                self.registry.set_current(subject, current)
                completed[case.case_id] = receipt
            if current is None:
                current = self.registry.publish(subject, state, version=1)
                self.registry.set_current(subject, current)
            return current

    def _state(
        self,
        current: ArtifactRef | None,
        manifest_ref: ArtifactRef,
        manifest: Any,
    ) -> Any:
        if current is None:
            return self.run_model.model_validate(
                {
                    "schema_version": self.schema_version,
                    "manifest_ref": manifest_ref,
                    "receipts": (),
                }
            )
        state: Any = self.registry.load(current, self.run_model)
        if state.manifest_ref != manifest_ref:
            raise ValueError("current exit run belongs to another manifest generation")
        if len({item.case_id for item in state.receipts}) != len(state.receipts):
            raise ValueError("exit run receipts are duplicated")
        expected = {item.case_id: item.role for item in manifest.cases}
        if any(expected.get(item.case_id) != item.role for item in state.receipts):
            raise ValueError("exit run receipt is not authorized by the manifest")
        return state


class V03ExitRunner:
    """Run each blinded case once in stable order with durable receipts."""

    def __init__(self, registry: ExitRegistry, executor: ExitCaseExecutor) -> None:
        self.registry = registry
        self.executor = executor
        self._runner = ResumableExitRunner(
            registry,
            executor,
            manifest_model=V03ExitManifest,
            run_model=V03ExitRun,
            receipt_model=ExitCaseReceipt,
            schema_version="econometrics.v03-exit-run.v1",
            subject_prefix="run-",
        )

    def run(self, manifest_ref: ArtifactRef) -> ArtifactRef:
        """Resume or execute the exact manifest without loading expectations."""
        return self._runner.run(manifest_ref)


class RegistryAnalysisExecutor:
    """Resolve blinded specs and durably bind each case to one analysis."""

    def __init__(
        self,
        registry: ExitRegistry,
        service: LocalAnalysisService,
        *,
        case_input_model: type[BaseModel] = ExitCaseInput,
        binding_model: type[BaseModel] = ExitAnalysisBinding,
        binding_schema_version: str = "econometrics.v03-exit-analysis-binding.v1",
        analysis_subject_prefix: str = "analysis-",
        binding_artifact_prefix: str = "analysis-ref-",
        data_suffix: str = ".bin",
        require_snapshot: bool = False,
        binding_data_hash: bool = False,
    ) -> None:
        self.registry = registry
        self.service = service
        self.case_input_model = case_input_model
        self.binding_model = binding_model
        self.binding_schema_version = binding_schema_version
        self.analysis_subject_prefix = analysis_subject_prefix
        self.binding_artifact_prefix = binding_artifact_prefix
        self.data_suffix = data_suffix
        self.require_snapshot = require_snapshot
        self.binding_data_hash = binding_data_hash

    def execute(self, case: ExitCase) -> LocalAnalysisReference:
        """Execute once, persist the exact case binding, and apply test mutation."""
        subject = f"{self.analysis_subject_prefix}{case.case_id}"
        with self.registry.lock(subject):
            payload: Any = self.registry.load(case.case_ref, self.case_input_model)
            if (
                payload.case_id != case.case_id
                or payload.family != case.family
                or payload.data_ref != case.data_ref
            ):
                raise ValueError(
                    "exit case input does not match its blinded descriptor"
                )
            data = self.registry.load_bytes(payload.data_ref)
            data_path = self.registry.materialize_data(
                payload.data_ref, suffix=self.data_suffix
            )
            if payload.spec.data_path != data_path:
                raise ValueError("exit case input is not bound to its exact data")
            current = self.registry.current(subject)
            if current is None:
                reference = self.service.run_exact(
                    payload.spec, data, payload.data_ref.content_hash
                )
                self._verify_data_binding(payload, reference, case.role)
                binding_payload = {
                    "schema_version": self.binding_schema_version,
                    "case_ref": case.case_ref,
                    "analysis_ref": reference,
                }
                if self.binding_data_hash:
                    binding_payload["data_sha256"] = payload.data_ref.content_hash
                payload_binding: Any = self.binding_model.model_validate(
                    binding_payload
                )
                binding = self.registry.publish(
                    f"{self.binding_artifact_prefix}{case.case_id}",
                    payload_binding,
                    version=1,
                )
                self.registry.set_current(subject, binding)
            else:
                payload_binding = self.registry.load(current, self.binding_model)
                if payload_binding.case_ref != case.case_ref or (
                    self.binding_data_hash
                    and payload_binding.data_sha256 != payload.data_ref.content_hash
                ):
                    raise ValueError("exit case input generation is stale")
                reference = payload_binding.analysis_ref
                if payload.integrity_mutation == "none":
                    self._verify_data_binding(payload, reference, case.role)
            if payload.integrity_mutation == "output-byte":
                self._ensure_integrity_mutation(reference)
            return reference

    def _ensure_integrity_mutation(self, reference: LocalAnalysisReference) -> None:
        try:
            report = self.service.status(reference)
        except EvidenceTampered:
            return
        if report.status != "passed" or not report.outputs:
            raise ValueError("integrity case requires one green output to mutate")
        evidence = report.outputs[0]
        data = self.service.files.read(evidence.relative_path)
        mutated = (bytes([data[0] ^ 1]) + data[1:]) if data else b"tampered"
        self.service.files.write(evidence.relative_path, mutated)

    def verify(self, case: ExitCase, reference: LocalAnalysisReference) -> None:
        """Reopen normal evidence or require the planned integrity rejection."""
        payload: Any = self.registry.load(case.case_ref, self.case_input_model)
        if payload.data_ref != case.data_ref:
            raise ValueError("exit case data authority is stale or revised")
        self.registry.load_bytes(payload.data_ref)
        current = self.registry.current(f"{self.analysis_subject_prefix}{case.case_id}")
        if current is None:
            raise ValueError("exit analysis binding is missing")
        binding: Any = self.registry.load(current, self.binding_model)
        if (
            binding.case_ref != case.case_ref
            or binding.analysis_ref != reference
            or (
                self.binding_data_hash
                and binding.data_sha256 != payload.data_ref.content_hash
            )
        ):
            raise ValueError("exit analysis binding is stale or revised")
        if case.role == "integrity-failure":
            try:
                self.service.status(reference)
            except EvidenceTampered:
                return
            raise ValueError("integrity mutation was not independently rejected")
        report = self.service.status(reference)
        if self.require_snapshot and (
            (report.snapshot is None and case.role == "green")
            or (
                report.snapshot is not None
                and report.snapshot.sha256 != payload.data_ref.content_hash
            )
        ):
            raise ValueError("exit analysis snapshot is not bound to its exact data")
        if case.role == "green" and report.status != "passed":
            raise ValueError("green exit case did not pass")
        if (
            case.role not in {"green", "integrity-failure"}
            and report.status != "exception"
        ):
            raise ValueError("scientific-failure case did not reject")

    def _verify_data_binding(
        self, payload: Any, reference: LocalAnalysisReference, role: str
    ) -> None:
        self.registry.load_bytes(payload.data_ref)
        report = self.service.status(reference)
        if self.require_snapshot and (
            (report.snapshot is None and role == "green")
            or (
                report.snapshot is not None
                and report.snapshot.sha256 != payload.data_ref.content_hash
            )
        ):
            raise ValueError("exit analysis snapshot is not bound to its exact data")

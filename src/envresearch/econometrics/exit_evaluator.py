"""Independent protected evaluation for blinded V0.3 exit runs."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from typing import Any, Literal

from pydantic import BaseModel

from envresearch.econometrics.exit_models import (
    CaseRole,
    ExitAnalysisBinding,
    ExitCaseOutcome,
    ExitExpectationCatalog,
    ExpectedComparison,
    V03ExitManifest,
    V03ExitReport,
    V03ExitRun,
    numeric_matches,
)
from envresearch.econometrics.exit_registry import ExitRegistry, validate_separate_roots
from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.econometrics.service import EvidenceTampered, LocalAnalysisService
from envresearch.models.artifact import ArtifactRef


class V03ExitEvaluator:
    """Evaluate exact run receipts while keeping expectation bytes protected."""

    def __init__(
        self,
        runner: ExitRegistry,
        evaluator: ExitRegistry,
        service: LocalAnalysisService,
        *,
        manifest_model: type[BaseModel] = V03ExitManifest,
        run_model: type[BaseModel] = V03ExitRun,
        catalog_model: type[BaseModel] = ExitExpectationCatalog,
        report_model: type[BaseModel] = V03ExitReport,
        outcome_model: type[BaseModel] = ExitCaseOutcome,
        binding_model: type[BaseModel] = ExitAnalysisBinding,
        report_schema_version: str = "econometrics.v03-exit-report.v1",
        run_subject_prefix: str = "run-",
        report_subject_prefix: str = "report-",
        analysis_subject_prefix: str = "analysis-",
        report_artifact_prefix: str = "exit-report-",
        catalog_binding_model: type[BaseModel] | None = None,
        catalog_binding_subject_prefix: str = "",
        binding_data_hash: bool = False,
        require_snapshot: bool = False,
    ) -> None:
        validate_separate_roots(runner.root, evaluator.root)
        self.runner = runner
        self.evaluator = evaluator
        self.service = service
        self.manifest_model = manifest_model
        self.run_model = run_model
        self.catalog_model = catalog_model
        self.report_model = report_model
        self.outcome_model = outcome_model
        self.binding_model = binding_model
        self.report_schema_version = report_schema_version
        self.run_subject_prefix = run_subject_prefix
        self.report_subject_prefix = report_subject_prefix
        self.analysis_subject_prefix = analysis_subject_prefix
        self.report_artifact_prefix = report_artifact_prefix
        self.catalog_binding_model = catalog_binding_model
        self.catalog_binding_subject_prefix = catalog_binding_subject_prefix
        self.binding_data_hash = binding_data_hash
        self.require_snapshot = require_snapshot

    def evaluate(self, run_ref: ArtifactRef, catalog_ref: ArtifactRef) -> Any:
        """Publish and return an all-or-nothing independently verified report."""
        _, report = self.evaluate_reference(run_ref, catalog_ref)
        return report

    def evaluate_reference(
        self, run_ref: ArtifactRef, catalog_ref: ArtifactRef
    ) -> tuple[ArtifactRef, Any]:
        """Atomically publish and return one matching report reference and payload."""
        run: Any = self.runner.load(run_ref, self.run_model)
        manifest: Any = self.runner.load(run.manifest_ref, self.manifest_model)
        run_subject = f"{self.run_subject_prefix}{manifest.manifest_id}"
        report_subject = f"{self.report_subject_prefix}{manifest.manifest_id}"
        with self.runner.lock(run_subject), self.evaluator.lock(report_subject):
            if self.runner.current(run_subject) != run_ref:
                raise ValueError("exit run is not the authenticated current generation")
            report = self._build_report(run_ref, catalog_ref)
            reference = self.evaluator.publish(
                f"{self.report_artifact_prefix}{manifest.manifest_id}-{run_ref.content_hash[:12]}",
                report,
                version=1,
            )
            self.evaluator.set_current(report_subject, reference)
            return reference, report

    def _build_report(self, run_ref: ArtifactRef, catalog_ref: ArtifactRef) -> Any:
        run: Any = self.runner.load(run_ref, self.run_model)
        manifest: Any = self.runner.load(run.manifest_ref, self.manifest_model)
        self._verify_catalog_authority(manifest, run.manifest_ref, catalog_ref)
        catalog: Any = self.evaluator.load(catalog_ref, self.catalog_model)
        expectations = {item.case_id: item for item in catalog.cases}
        receipts = {item.case_id: item for item in run.receipts}
        if (
            catalog.manifest_id != manifest.manifest_id
            or set(expectations) != {item.case_id for item in manifest.cases}
            or set(receipts) != set(expectations)
        ):
            raise ValueError("exit run/catalog case matrix is incomplete")
        roles = {item.case_id: item.role for item in manifest.cases}
        case_refs = {item.case_id: item.case_ref for item in manifest.cases}
        case_data = {item.case_id: item.data_ref for item in manifest.cases}
        data_hashes: dict[str, str] = {}
        for case_id, receipt in receipts.items():
            self.runner.load_bytes(case_data[case_id])
            data_hashes[case_id] = case_data[case_id].content_hash
            current = self.runner.current(f"{self.analysis_subject_prefix}{case_id}")
            if current is None:
                raise ValueError("exit analysis binding is missing")
            binding: Any = self.runner.load(current, self.binding_model)
            if (
                binding.case_ref != case_refs[case_id]
                or binding.analysis_ref != receipt.analysis_ref
                or (
                    self.binding_data_hash
                    and binding.data_sha256 != case_data[case_id].content_hash
                )
            ):
                raise ValueError("exit analysis binding is stale or revised")
        outcomes = tuple(
            self._case(
                case_id,
                roles[case_id],
                receipts[case_id].analysis_ref,
                expectations[case_id],
                data_hashes[case_id],
            )
            for case_id in sorted(expectations)
        )
        status: Literal["passed", "failed"] = (
            "passed" if all(item.status == "matched" for item in outcomes) else "failed"
        )
        return self.report_model.model_validate(
            {
                "schema_version": self.report_schema_version,
                "status": status,
                "run_ref": run_ref,
                "catalog_ref": catalog_ref,
                "outcomes": outcomes,
            }
        )

    def status(self, reference: ArtifactRef) -> Any:
        """Reopen and independently reproduce one exact current report."""
        report: Any = self.evaluator.load(reference, self.report_model)
        run: Any = self.runner.load(report.run_ref, self.run_model)
        manifest: Any = self.runner.load(run.manifest_ref, self.manifest_model)
        report_subject = f"{self.report_subject_prefix}{manifest.manifest_id}"
        if (
            not self._catalog_is_authorized(
                manifest, run.manifest_ref, report.catalog_ref
            )
            or self.runner.current(f"{self.run_subject_prefix}{manifest.manifest_id}")
            != report.run_ref
            or self.evaluator.current(report_subject) != reference
        ):
            raise ValueError("exit report authority is not current and exact")
        reproduced = self._build_report(report.run_ref, report.catalog_ref)
        if reproduced != report or self.evaluator.current(report_subject) != reference:
            raise ValueError("exit report does not match independent evaluation")
        return report

    def _case(
        self,
        case_id: str,
        role: CaseRole,
        reference: LocalAnalysisReference,
        expected: Any,
        data_hash: str,
    ) -> Any:
        findings: list[str] = []
        if expected.role != role:
            findings.append("ROLE_MISMATCH")
        if role == "integrity-failure":
            if expected.expected_code != "EVIDENCE_TAMPERED":
                findings.append("EXPECTED_FAILURE_MISMATCH")
            try:
                self.service.status(reference)
            except EvidenceTampered:
                pass
            except (OSError, ValueError):
                findings.append("ANALYSIS_EVIDENCE_INVALID")
            else:
                findings.append("INTEGRITY_FAILURE_NOT_DETECTED")
        else:
            try:
                report = self.service.status(reference)
            except (EvidenceTampered, OSError, ValueError):
                findings.append("ANALYSIS_EVIDENCE_INVALID")
            else:
                if self.require_snapshot and (
                    (report.snapshot is None and role == "green")
                    or (
                        report.snapshot is not None
                        and report.snapshot.sha256 != data_hash
                    )
                ):
                    findings.append("DATA_AUTHORITY_MISMATCH")
                if role == "green":
                    self._green(report, expected, findings)
                elif (
                    report.status != "exception"
                    or report.code != expected.expected_code
                ):
                    findings.append("EXPECTED_FAILURE_MISMATCH")
        return self.outcome_model.model_validate(
            {
                "case_id": case_id,
                "role": role,
                "status": "matched" if not findings else "unresolved",
                "analysis_ref": reference,
                "findings": tuple(findings),
            }
        )

    def _green(
        self,
        report: LocalAnalysisReport,
        expected: Any,
        findings: list[str],
    ) -> None:
        if report.status != "passed":
            findings.append("GREEN_CASE_NOT_PASSED")
            return
        outputs = {item.name: item for item in report.outputs}
        expected_names = {item.output_name for item in expected.comparisons}
        if expected_names != set(outputs):
            findings.append("OUTPUT_SET_MISMATCH")
            return
        for comparison in expected.comparisons:
            evidence = outputs[comparison.output_name]
            data = self.service.files.read(evidence.relative_path)
            if hashlib.sha256(data).hexdigest() != evidence.sha256 or not _matches(
                data, comparison
            ):
                findings.append(
                    f"COMPARISON_MISMATCH:{comparison.output_name}:{comparison.selector or 'exact'}"
                )

    def _verify_catalog_authority(
        self, manifest: Any, manifest_ref: ArtifactRef, catalog_ref: ArtifactRef
    ) -> None:
        if not self._catalog_is_authorized(manifest, manifest_ref, catalog_ref):
            raise ValueError("expectation catalog does not match the exact manifest")

    def _catalog_is_authorized(
        self, manifest: Any, manifest_ref: ArtifactRef, catalog_ref: ArtifactRef
    ) -> bool:
        if self.catalog_binding_model is None:
            return bool(manifest.expectation_catalog_ref == catalog_ref)
        subject = f"{self.catalog_binding_subject_prefix}{manifest.manifest_id}"
        current = self.evaluator.current(subject)
        if current is None:
            return False
        binding: Any = self.evaluator.load(current, self.catalog_binding_model)
        return bool(
            binding.manifest_ref == manifest_ref and binding.catalog_ref == catalog_ref
        )


def _matches(data: bytes, comparison: ExpectedComparison) -> bool:
    if comparison.comparison_type == "exact":
        return (
            isinstance(comparison.expected, str)
            and hashlib.sha256(data).hexdigest() == comparison.expected
        )
    try:
        if comparison.comparison_type == "json":
            observed = _json_value(json.loads(data), comparison.selector or "")
        else:
            observed = _csv_value(data, comparison.selector or "")
    except (
        UnicodeError,
        ValueError,
        KeyError,
        IndexError,
        json.JSONDecodeError,
        csv.Error,
    ):
        return False
    if isinstance(observed, (int, float)) and not isinstance(observed, bool):
        return numeric_matches(float(observed), comparison)
    return (
        comparison.atol == 0
        and comparison.rtol == 0
        and observed == comparison.expected
    )


def _json_value(payload: object, selector: str) -> object:
    if not selector.startswith("/"):
        raise ValueError("JSON selector must be an absolute pointer")
    current = payload
    for token in selector[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]  # type: ignore[index]
    return current


def _csv_value(data: bytes, selector: str) -> object:
    parts = dict(item.split("=", 1) for item in selector.split(","))
    row_value, column = parts["row"], parts["column"]
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    if not rows or column not in rows[0]:
        raise KeyError(column)
    matches = [row for row in rows if next(iter(row.values())) == row_value]
    if len(matches) != 1:
        raise ValueError("CSV selector must match exactly one row")
    raw = matches[0][column]
    try:
        number = float(raw)
    except ValueError:
        return raw
    return number if math.isfinite(number) else float("nan")


class ValuationExitEvaluator(V03ExitEvaluator):
    """Protected exact-reference evaluator for the compact Valuation Core exit."""

    def __init__(
        self,
        runner: ExitRegistry,
        evaluator: ExitRegistry,
        service: LocalAnalysisService,
    ) -> None:
        from envresearch.econometrics.valuation_exit_models import (
            ValuationExitAnalysisBinding,
            ValuationExitCaseOutcome,
            ValuationExitCatalogBinding,
            ValuationExitExpectationCatalog,
            ValuationExitManifest,
            ValuationExitReport,
            ValuationExitRun,
        )

        super().__init__(
            runner,
            evaluator,
            service,
            manifest_model=ValuationExitManifest,
            run_model=ValuationExitRun,
            catalog_model=ValuationExitExpectationCatalog,
            report_model=ValuationExitReport,
            outcome_model=ValuationExitCaseOutcome,
            binding_model=ValuationExitAnalysisBinding,
            report_schema_version="econometrics.valuation-exit-report.v1",
            run_subject_prefix="valuation-run-",
            report_subject_prefix="valuation-report-",
            analysis_subject_prefix="valuation-analysis-",
            report_artifact_prefix="valuation-report-",
            catalog_binding_model=ValuationExitCatalogBinding,
            catalog_binding_subject_prefix="valuation-catalog-",
            binding_data_hash=True,
            require_snapshot=True,
        )

    def evaluate_reference(
        self, run_ref: ArtifactRef, catalog_ref: ArtifactRef
    ) -> tuple[ArtifactRef, Any]:
        """Evaluate beneath the V0.3.1 complete-chain writer lease."""
        from envresearch.econometrics.valuation_authority import (
            valuation_authority_lease,
        )

        with valuation_authority_lease(self.runner):
            return super().evaluate_reference(run_ref, catalog_ref)

    def status(self, reference: ArtifactRef) -> Any:
        """Reconstruct beneath the same complete-chain reader lease."""
        from envresearch.econometrics.valuation_authority import (
            valuation_authority_lease,
        )

        with valuation_authority_lease(self.runner):
            return super().status(reference)

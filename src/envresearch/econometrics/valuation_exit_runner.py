"""Compact Valuation Core runner built on the shared exact-reference seam."""

from __future__ import annotations

from typing import Any

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.exit_runner import (
    RegistryAnalysisExecutor,
    ResumableExitRunner,
)
from envresearch.econometrics.report import LocalAnalysisReference
from envresearch.econometrics.service import LocalAnalysisService
from envresearch.econometrics.valuation_authority import valuation_authority_lease
from envresearch.econometrics.valuation_exit_models import (
    ValuationExitAnalysisBinding,
    ValuationExitCaseInput,
    ValuationExitCaseReceipt,
    ValuationExitManifest,
    ValuationExitRun,
)
from envresearch.models.artifact import ArtifactRef


class ValuationExitRunner:
    """Run the checked nine-case matrix without opening protected expectations."""

    def __init__(self, registry: ExitRegistry, executor: Any) -> None:
        self._runner = ResumableExitRunner(
            registry,
            executor,
            manifest_model=ValuationExitManifest,
            run_model=ValuationExitRun,
            receipt_model=ValuationExitCaseReceipt,
            schema_version="econometrics.valuation-exit-run.v1",
            subject_prefix="valuation-run-",
        )

    def run(self, manifest_ref: ArtifactRef) -> ArtifactRef:
        with valuation_authority_lease(self._runner.registry):
            return self._runner.run(manifest_ref)


class ValuationRegistryAnalysisExecutor(RegistryAnalysisExecutor):
    """Bind Valuation Core cases through the shared analysis executor."""

    def __init__(self, registry: ExitRegistry, service: LocalAnalysisService) -> None:
        super().__init__(
            registry,
            service,
            case_input_model=ValuationExitCaseInput,
            binding_model=ValuationExitAnalysisBinding,
            binding_schema_version="econometrics.valuation-exit-analysis-binding.v1",
            analysis_subject_prefix="valuation-analysis-",
            binding_artifact_prefix="valuation-analysis-ref-",
            data_suffix=".csv",
            require_snapshot=True,
            binding_data_hash=True,
        )

    def execute(self, case: Any) -> LocalAnalysisReference:
        """Publish a case binding beneath the complete valuation lease."""
        with valuation_authority_lease(self.registry):
            return super().execute(case)

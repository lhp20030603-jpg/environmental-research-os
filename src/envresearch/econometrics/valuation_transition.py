"""Authenticated V0.3.1 exit marker and read-only V0.4 handoff harness."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal, NoReturn, cast

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from envresearch.econometrics._file_evidence import read_regular
from envresearch.econometrics.exit_evaluator import ValuationExitEvaluator
from envresearch.econometrics.exit_registry import ExitRegistry, validate_separate_roots
from envresearch.econometrics.extension_registry import (
    FROZEN_EXTENSION_REGISTRY,
    FrozenExtensionRegistry,
)
from envresearch.econometrics.frozen_r_library import FrozenRLibrary
from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.econometrics.service import LocalAnalysisService
from envresearch.econometrics.valuation_authority import valuation_authority_lease
from envresearch.econometrics.valuation_exit_models import (
    ValuationExitCatalogBinding,
    ValuationExitExpectationCatalog,
    ValuationExitManifest,
    ValuationExitReport,
    ValuationExitRun,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.storage.research_artifacts import ResearchArtifactStore

STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TRANSITION_SUBJECT = "valuation-transition-v031"


class V031TransitionMarker(BaseModel):
    """Exact accepted artifact references handed from V0.3.1 to V0.4."""

    model_config = STRICT
    schema_version: Literal["econometrics.v031-transition.v1"]
    release: Literal["V0.3.1"]
    status: Literal["passed"]
    input_contract: Literal["local-analysis-report+artifact-reference.v1"]
    manifest_ref: ArtifactRef
    run_ref: ArtifactRef
    catalog_binding_ref: ArtifactRef
    catalog_ref: ArtifactRef
    report_ref: ArtifactRef
    runtime_relative_path: Path
    runtime_sha256: str
    frozen_pack_root: Path
    frozen_pack_hash: str

    @field_validator("runtime_sha256", "frozen_pack_hash")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not SHA256.fullmatch(value):
            raise ValueError("transition digest must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def canonical_paths(self) -> V031TransitionMarker:
        if (
            self.runtime_relative_path.is_absolute()
            or ".." in self.runtime_relative_path.parts
        ):
            raise ValueError("transition runtime path must be canonical and relative")
        if (
            not self.frozen_pack_root.is_absolute()
            or self.frozen_pack_root.is_symlink()
        ):
            raise ValueError(
                "transition frozen pack root must be absolute and non-symlink"
            )
        return self


class _StatusOnlyBackend:
    """Carry package authority for reconstruction; execution remains impossible."""

    def __init__(self, authorities: tuple[object, ...]) -> None:
        self.package_authorities = authorities

    def execute(self, *args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("V0.3.1 transition status is read-only")


class V031ExitHarness:
    """Reopen one exact current transition and independently reproduce its report."""

    def __init__(self, run_root: Path) -> None:
        self._initialize(run_root, marker_ref=None)

    @classmethod
    def open_exact(cls, run_root: Path, marker_ref: ArtifactRef) -> V031ExitHarness:
        """Open the caller-supplied exact current transition without substitution."""
        harness = cls._candidate(run_root, marker_ref)
        harness._require_current()
        return harness

    @classmethod
    def _candidate(cls, run_root: Path, marker_ref: ArtifactRef) -> V031ExitHarness:
        harness = cls.__new__(cls)
        harness._initialize(run_root, marker_ref=marker_ref)
        return harness

    def _initialize(self, run_root: Path, *, marker_ref: ArtifactRef | None) -> None:
        if not run_root.is_absolute() or run_root.is_symlink():
            raise ValueError("V0.3.1 acceptance root must be absolute and non-symlink")
        self.root = run_root.resolve(strict=True)
        self.runner = ExitRegistry(self.root / "runner", create=False)
        self.evaluator = ExitRegistry(self.root / "evaluator", create=False)
        self.analysis_root = (self.root / "analysis").resolve(strict=True)
        validate_separate_roots(self.runner.root, self.evaluator.root)
        validate_separate_roots(self.runner.root, self.analysis_root)
        validate_separate_roots(self.evaluator.root, self.analysis_root)
        self.extension_registry: FrozenExtensionRegistry = FROZEN_EXTENSION_REGISTRY
        current = self.evaluator.current(TRANSITION_SUBJECT)
        if marker_ref is None:
            if current is None:
                raise ValueError("V0.3.1 transition marker is not sealed")
            marker_ref = current
        self.marker_ref = marker_ref
        self.marker = self.evaluator.load(self.marker_ref, V031TransitionMarker)
        self._authenticate_runtime_and_pack()

    def run_and_evaluate(self) -> ValuationExitReport:
        """Reconstruct the exact report and every authority linking it to V0.4."""
        self._require_current()
        report = self._reconstruct()
        self._require_current()
        return report

    def _reconstruct(self) -> ValuationExitReport:
        """Reconstruct exact evidence; candidate publication calls this privately."""
        run = self.runner.load(self.marker.run_ref, ValuationExitRun)
        manifest = self.runner.load(self.marker.manifest_ref, ValuationExitManifest)
        self._require_exact_chain_current(manifest.manifest_id)
        service = self._status_service()
        report = cast(
            ValuationExitReport,
            ValuationExitEvaluator(self.runner, self.evaluator, service).status(
                self.marker.report_ref
            ),
        )
        if report.status != "passed" or len(report.outcomes) != 9:
            raise ValueError("V0.3.1 transition does not bind one 9/9 passed report")
        catalog = self.evaluator.load(
            self.marker.catalog_ref, ValuationExitExpectationCatalog
        )
        binding = self.evaluator.load(
            self.marker.catalog_binding_ref, ValuationExitCatalogBinding
        )
        if (
            report.run_ref != self.marker.run_ref
            or report.catalog_ref != self.marker.catalog_ref
            or run.manifest_ref != self.marker.manifest_ref
            or catalog.manifest_id != manifest.manifest_id
            or binding.manifest_ref != self.marker.manifest_ref
            or binding.catalog_ref != self.marker.catalog_ref
            or self.evaluator.current(f"valuation-catalog-{manifest.manifest_id}")
            != self.marker.catalog_binding_ref
        ):
            raise ValueError("V0.3.1 transition references are stale or inconsistent")
        self._require_exact_chain_current(manifest.manifest_id)
        return report

    def _require_exact_chain_current(self, manifest_id: str) -> None:
        """Require every mutable current pointer to retain the sealed exact refs."""
        if (
            self.runner.current(f"valuation-run-{manifest_id}") != self.marker.run_ref
            or self.evaluator.current(f"valuation-report-{manifest_id}")
            != self.marker.report_ref
            or self.evaluator.current(f"valuation-catalog-{manifest_id}")
            != self.marker.catalog_binding_ref
        ):
            raise ValueError("V0.3.1 transition current chain changed")

    def _require_current(self) -> None:
        if self.evaluator.current(TRANSITION_SUBJECT) != self.marker_ref:
            raise ValueError("V0.3.1 transition marker is not current")
        self._reauthenticate_authority()
        run = self.runner.load(self.marker.run_ref, ValuationExitRun)
        if run.manifest_ref != self.marker.manifest_ref:
            raise ValueError("V0.3.1 transition run is stale or inconsistent")
        manifest = self.runner.load(self.marker.manifest_ref, ValuationExitManifest)
        self._require_exact_chain_current(manifest.manifest_id)
        self._reauthenticate_current_authority()

    def _reauthenticate_current_authority(self) -> None:
        """Reauthenticate exact payloads, then close on the current marker ref."""
        self._reauthenticate_authority()
        if self.evaluator.current(TRANSITION_SUBJECT) != self.marker_ref:
            raise ValueError("V0.3.1 transition marker is not current")

    def _reauthenticate_authority(self) -> None:
        if self.evaluator.load(self.marker_ref, V031TransitionMarker) != self.marker:
            raise ValueError("V0.3.1 transition marker identity changed")
        self._authenticate_runtime_and_pack()

    def _authenticate_runtime_and_pack(self) -> None:
        runtime = self.root / self.marker.runtime_relative_path
        if (
            runtime.is_symlink()
            or hashlib.sha256(read_regular(runtime)).hexdigest()
            != self.marker.runtime_sha256
        ):
            raise ValueError("V0.3.1 reviewed runtime identity changed")
        FrozenRLibrary(self.marker.frozen_pack_root).load(self.marker.frozen_pack_hash)

    def _status_service(self) -> LocalAnalysisService:
        authorities = FrozenRLibrary(self.marker.frozen_pack_root).load(
            self.marker.frozen_pack_hash
        )
        return LocalAnalysisService(
            ResearchArtifactStore(self.analysis_root), _StatusOnlyBackend(authorities)
        )


def publish_v031_transition(
    run_root: Path,
    *,
    manifest_ref: ArtifactRef,
    run_ref: ArtifactRef,
    catalog_binding_ref: ArtifactRef,
    catalog_ref: ArtifactRef,
    report_ref: ArtifactRef,
    runtime_relative_path: Path,
    runtime_sha256: str,
    frozen_pack_root: Path,
    frozen_pack_hash: str,
) -> ArtifactRef:
    """Seal the minimum authenticated transition only after exact status passes."""
    runner = ExitRegistry(run_root.resolve() / "runner", create=False)
    evaluator = ExitRegistry(run_root.resolve() / "evaluator", create=False)
    with valuation_authority_lease(runner):
        return _publish_v031_transition(
            run_root=run_root,
            evaluator=evaluator,
            manifest_ref=manifest_ref,
            run_ref=run_ref,
            catalog_binding_ref=catalog_binding_ref,
            catalog_ref=catalog_ref,
            report_ref=report_ref,
            runtime_relative_path=runtime_relative_path,
            runtime_sha256=runtime_sha256,
            frozen_pack_root=frozen_pack_root,
            frozen_pack_hash=frozen_pack_hash,
        )


def _publish_v031_transition(
    *,
    run_root: Path,
    evaluator: ExitRegistry,
    manifest_ref: ArtifactRef,
    run_ref: ArtifactRef,
    catalog_binding_ref: ArtifactRef,
    catalog_ref: ArtifactRef,
    report_ref: ArtifactRef,
    runtime_relative_path: Path,
    runtime_sha256: str,
    frozen_pack_root: Path,
    frozen_pack_hash: str,
) -> ArtifactRef:
    """Publish while the caller owns the valuation authority lease."""
    marker = V031TransitionMarker(
        schema_version="econometrics.v031-transition.v1",
        release="V0.3.1",
        status="passed",
        input_contract="local-analysis-report+artifact-reference.v1",
        manifest_ref=manifest_ref,
        run_ref=run_ref,
        catalog_binding_ref=catalog_binding_ref,
        catalog_ref=catalog_ref,
        report_ref=report_ref,
        runtime_relative_path=runtime_relative_path,
        runtime_sha256=runtime_sha256,
        frozen_pack_root=frozen_pack_root.resolve(strict=True),
        frozen_pack_hash=frozen_pack_hash,
    )
    reference = evaluator.publish("valuation-transition-v031", marker)
    with evaluator.lock(TRANSITION_SUBJECT):
        current = evaluator.current(TRANSITION_SUBJECT)
        if current is not None:
            if current != reference:
                raise ValueError("V0.3.1 transition is already sealed")
            V031ExitHarness(run_root.resolve()).run_and_evaluate()
            return current
        _validate_transition_candidate(run_root.resolve(), reference)
        evaluator.set_current(TRANSITION_SUBJECT, reference)
        return reference


def _validate_transition_candidate(run_root: Path, reference: ArtifactRef) -> None:
    """Authenticate an unpromoted candidate only inside locked publication."""
    candidate = V031ExitHarness._candidate(run_root, reference)
    candidate._reconstruct()
    candidate._reauthenticate_authority()


def accepted_analysis_reports(
    harness: V031ExitHarness,
) -> tuple[tuple[LocalAnalysisReference, LocalAnalysisReport], ...]:
    """Expose exact accepted report refs and payloads to the future Paper Builder."""
    report = harness.run_and_evaluate()
    service = harness._status_service()
    accepted = tuple(
        (item.analysis_ref, service.status(item.analysis_ref))
        for item in report.outcomes
        if item.role == "green"
    )
    confirmed = harness.run_and_evaluate()
    confirmed_refs = tuple(
        item.analysis_ref for item in confirmed.outcomes if item.role == "green"
    )
    if confirmed != report or tuple(item[0] for item in accepted) != confirmed_refs:
        raise ValueError("accepted valuation reports changed during materialization")
    harness._require_current()
    return accepted

"""Shared genuine-report fixtures for Paper Builder ledger integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from econometrics_valuation_verifier_fixtures import (
    ValuationVerifierBackend,
    spec_for,
)

from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.econometrics.service import LocalAnalysisService
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.ledger import ClaimLedgerService
from envresearch.storage.research_artifacts import ResearchArtifactStore


@dataclass
class ResolverFixture:
    transition_ref: ArtifactRef
    reports: tuple[tuple[LocalAnalysisReference, LocalAnalysisReport], ...]
    current: bool = True
    current_checks: int = 0
    fail_current_check: int | None = None
    mutate_reports_on_current_check: int | None = None
    replacement_reports: (
        tuple[tuple[LocalAnalysisReference, LocalAnalysisReport], ...] | None
    ) = None

    def resolve(
        self, transition_ref: ArtifactRef
    ) -> tuple[tuple[LocalAnalysisReference, LocalAnalysisReport], ...]:
        if transition_ref != self.transition_ref or not self.current:
            raise ValueError("accepted evidence authority is not current")
        return self.reports

    def require_current(self, transition_ref: ArtifactRef) -> None:
        self.current_checks += 1
        if self.mutate_reports_on_current_check == self.current_checks:
            assert self.replacement_reports is not None
            self.reports = self.replacement_reports
        if (
            transition_ref != self.transition_ref
            or not self.current
            or self.fail_current_check == self.current_checks
        ):
            raise ValueError("accepted evidence authority changed")

    @contextmanager
    def authority_lease(self) -> Iterator[None]:
        yield


@dataclass(frozen=True)
class ProcessResolver:
    transition_ref: ArtifactRef
    analysis_ref: LocalAnalysisReference
    report: LocalAnalysisReport

    def resolve(
        self, transition_ref: ArtifactRef
    ) -> tuple[tuple[LocalAnalysisReference, LocalAnalysisReport], ...]:
        if transition_ref != self.transition_ref:
            raise ValueError("accepted evidence authority changed")
        return ((self.analysis_ref, self.report),)

    def require_current(self, transition_ref: ArtifactRef) -> None:
        if transition_ref != self.transition_ref:
            raise ValueError("accepted evidence authority changed")

    @contextmanager
    def authority_lease(self) -> Iterator[None]:
        yield


def build_worker(
    paper_root: str,
    transition_json: str,
    analysis_json: str,
    report_json: str,
    start: Any,
    results: Any,
) -> None:
    transition_ref = ArtifactRef.model_validate_json(transition_json)
    resolver = ProcessResolver(
        transition_ref=transition_ref,
        analysis_ref=LocalAnalysisReference.model_validate_json(analysis_json),
        report=LocalAnalysisReport.model_validate_json(report_json),
    )
    service = ClaimLedgerService.for_resolver(
        paper_root=Path(paper_root), resolver=resolver
    )
    start.wait()
    reference = service.build(transition_ref)
    results.put(("ok", reference.model_dump_json()))


def transition(digest: str = "a") -> ArtifactRef:
    return ArtifactRef(
        artifact_id="valuation-transition-v031",
        artifact_version=1,
        content_hash=digest * 64,
    )


def cv_resolver(tmp_path: Path) -> ResolverFixture:
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "analysis"),
        ValuationVerifierBackend("contingent-valuation"),
    )
    reference = service.run(spec_for("contingent-valuation"))
    report = service.status(reference)
    assert report.status == "passed"
    return ResolverFixture(transition(), ((reference, report),))


__all__ = ["ResolverFixture", "build_worker", "cv_resolver", "transition"]

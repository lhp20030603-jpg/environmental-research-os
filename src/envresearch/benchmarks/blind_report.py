"""Read-only reports over current authenticated blind benchmark state."""

from __future__ import annotations

import hashlib
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path

from pydantic import Field
from rich.table import Table

from envresearch.benchmarks.blind_authority import (
    VerifiedBlindEnrollment,
    canonical_json,
)
from envresearch.benchmarks.blind_enrollment_marker import require_frozen_enrollment
from envresearch.benchmarks.blind_registry import (
    BlindBenchmarkManifest,
    BlindBenchmarkRegistry,
)
from envresearch.benchmarks.blind_release import (
    CANONICAL_RELEASE_BLOCKER,
    CaseForRelease,
    ReleaseCohort,
    ReleaseEvaluator,
    ReleaseReadinessReport,
)
from envresearch.benchmarks.blind_release_authority import (
    authenticate_catalog_release,
    read_expected_release_authority,
)
from envresearch.benchmarks.blind_run import (
    BlindLineageInvalid as _BlindLineageInvalid,
)
from envresearch.benchmarks.blind_run import open_artifacts, snapshot_run_cases
from envresearch.benchmarks.blind_scoring import BlindScorer
from envresearch.benchmarks.blind_scoring_contracts import (
    DimensionMean,
    StrictScoringModel,
)
from envresearch.benchmarks.blind_status import BlindCaseStatus, case_status
from envresearch.benchmarks.claim_report import report_from_payload
from envresearch.benchmarks.leakage import LeakageScanner
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_blinding import LeakageReport
from envresearch.models.benchmark_claims import CuratorSourceSheet
from envresearch.models.benchmark_evaluation import ExpertScoreSheet

BlindLineageInvalid = _BlindLineageInvalid


class BlindCaseInvalid(ValueError):
    """The inert blind descriptor or leakage projection is invalid."""


class BlindReviewRequired(ValueError):
    """The two independent human reviews or adjudication are incomplete."""


class CitationIntegrityError(ValueError):
    """The current recommendation lacks a passing exact citation binding."""


class BlindCaseValidation(StrictScoringModel):
    case_id: str
    leakage_verdict: str
    tier: int
    valid: bool


class BlindRunStatus(StrictScoringModel):
    cases: tuple[BlindCaseStatus, ...]


class BlindCaseReport(StrictScoringModel):
    case_id: str
    method_family: str
    cohort: ReleaseCohort
    recommendation_ref: ArtifactRef
    expert_score_refs: tuple[ArtifactRef, ...]
    expert_scores: tuple[ExpertScoreSheet, ...]
    dimension_scores: tuple[DimensionMean, ...]
    weighted_score: Decimal
    passed: bool
    requires_adjudication: bool
    unresolved: bool
    adjudication_ref: ArtifactRef | None
    posthoc_comparison_ref: ArtifactRef
    citation_report_ref: ArtifactRef
    lineage_refs: tuple[ArtifactRef, ...]
    gate_failures: tuple[str, ...]
    release_case: CaseForRelease | None = Field(default=None, exclude=True)


class BlindCatalogReport(ReleaseReadinessReport):
    total_cases: int = Field(ge=1)
    calibration_ready: bool
    cases: tuple[BlindCaseReport, ...]
    unresolved_cases: tuple[str, ...]
    gate_failures: tuple[str, ...]


def validate_blind_case(case_root: Path) -> BlindCaseValidation:
    """Validate exactly one pinned Tier-1 case and recompute leakage offline."""
    try:
        manifests = BlindBenchmarkRegistry.discover(case_root)
        if len(manifests) != 1:
            raise ValueError("blind validation requires exactly one case")
        manifest = next(iter(manifests.values()))
        loaded = BlindBenchmarkRegistry.load_case(manifest)
        leakage = LeakageScanner().scan(
            loaded.source_sheet,
            loaded.blinded_brief,
            loaded.source_ref,
            loaded.brief_ref,
            "blind-cli-validator",
        )
    except (OSError, TypeError, UnicodeError, ValueError) as error:
        raise BlindCaseInvalid(str(error)) from error
    if leakage.verdict != "pass":
        raise BlindCaseInvalid("blind case failed leakage validation")
    return BlindCaseValidation(
        case_id=manifest.id,
        leakage_verdict=leakage.verdict,
        tier=manifest.tier,
        valid=True,
    )


def load_blind_status(run_root: Path) -> BlindRunStatus:
    """Inspect current lifecycle states without issuing or accepting work."""
    statuses: list[BlindCaseStatus] = []
    with snapshot_run_cases(run_root) as cases:
        for case_root, case_id in cases:
            try:
                with open_artifacts(case_root, case_id) as artifacts:
                    statuses.append(case_status(artifacts, case_id))
            except (OSError, TypeError, UnicodeError, ValueError) as error:
                raise BlindLineageInvalid(f"{case_id}: {error}") from error
    return BlindRunStatus(cases=tuple(statuses))


def evaluate_blind_catalog(
    catalog_root: Path,
    run_root: Path,
) -> ReleaseReadinessReport:
    """Evaluate catalog cases from current artifacts and authenticated queues only."""
    try:
        manifests = BlindBenchmarkRegistry.discover(catalog_root)
    except (OSError, TypeError, UnicodeError, ValueError) as error:
        raise BlindCaseInvalid(str(error)) from error
    if not manifests:
        raise BlindCaseInvalid("blind catalog contains no cases")
    with snapshot_run_cases(run_root) as run_cases:
        runs = {case_id: case_root for case_root, case_id in run_cases}
        if set(runs) != set(manifests):
            raise BlindLineageInvalid("blind catalog and run cases do not match")
        evaluated = tuple(
            _evaluate_case(
                runs[case_id], case_id, manifest,
            )
            for case_id, manifest in manifests.items()
        )
        with ExitStack() as stack:
            registries = tuple(
                (
                    case_id,
                    stack.enter_context(open_artifacts(runs[case_id], case_id))
                    .principals.registry,
                )
                for case_id in manifests
            )
            reports = tuple(item[0] for item in evaluated)
            result = _release_report(reports)
            expected = read_expected_release_authority(catalog_root, run_root)
            if expected is None:
                return result
            release_cases = tuple(
                report.release_case
                for report in reports
                if report.release_case is not None
            )
            authenticate_catalog_release(release_cases, registries, expected)
            blockers = tuple(
                blocker
                for blocker in result.blockers
                if blocker != CANONICAL_RELEASE_BLOCKER
            )
            return result.model_copy(
                update={
                    "released": not blockers,
                    "blockers": blockers,
                    "gate_failures": blockers,
                }
            )


def load_and_evaluate_blind_run(run_root: Path) -> ReleaseReadinessReport:
    """Evaluate every current case discoverable from a durable run root."""
    with snapshot_run_cases(run_root) as cases:
        evaluated = tuple(
            _evaluate_case(case_root, case_id, None) for case_root, case_id in cases
        )
    return _release_report(tuple(item[0] for item in evaluated))


def blind_validation_table(result: BlindCaseValidation) -> Table:
    table = Table(title="Blind case validation")
    for column in ("Case", "Tier", "Leakage", "Status"):
        table.add_column(column)
    table.add_row(result.case_id, str(result.tier), result.leakage_verdict, "VALID")
    return table


def blind_status_table(result: BlindRunStatus) -> Table:
    table = Table(title="Blind benchmark status")
    for column in ("Case", "Completed", "Stale", "Current"):
        table.add_column(column)
    for case in result.cases:
        table.add_row(
            case.case_id,
            str(len(case.completed_nodes)),
            ", ".join(case.stale_nodes) or "none",
            "yes" if case.current_lineage else "no",
        )
    return table


def blind_report_table(result: ReleaseReadinessReport) -> Table:
    report = BlindCatalogReport.model_validate(result.model_dump())
    table = Table(title="Blind benchmark release readiness")
    for column in ("Case", "Cohort", "Score", "Review", "Release"):
        table.add_column(column)
    verdict = "READY" if report.released else "BLOCKED"
    for case in report.cases:
        table.add_row(
            case.case_id,
            case.cohort.value,
            str(case.weighted_score),
            "pass" if case.passed else "fail",
            verdict,
        )
    return table


def _evaluate_case(
    run_root: Path,
    case_id: str,
    manifest: BlindBenchmarkManifest | None,
) -> tuple[BlindCaseReport, VerifiedBlindEnrollment]:
    with open_artifacts(run_root, case_id) as artifacts:
        paths = artifacts.paths(case_id)
        workspace = artifacts.lifecycle.workspace
        if not (workspace / paths.citation_report).is_file():
            raise CitationIntegrityError(f"{case_id}: citation report is missing")
        if not all(
            (workspace / path).is_file()
            for path in (paths.expert_one, paths.expert_two)
        ):
            raise BlindReviewRequired(f"{case_id}: two expert reviews are required")
        try:
            raw_citation = artifacts.lifecycle.read_artifact(paths.citation_report)
            citation = report_from_payload(raw_citation.payload)
            if citation != artifacts.lineage.recompute_citation_report(
                case_id, citation
            ):
                raise ValueError("citation integrity report is not current")
        except (OSError, TypeError, UnicodeError, ValueError) as error:
            raise CitationIntegrityError(f"{case_id}: {error}") from error
        try:
            evaluation = BlindScorer.from_case(artifacts, case_id).evaluate_case()
        except (FileNotFoundError, OSError, ValueError) as error:
            if "required" in str(error) or "has not been issued" in str(error):
                raise BlindReviewRequired(f"{case_id}: {error}") from error
            raise BlindLineageInvalid(f"{case_id}: {error}") from error
        try:
            enrollment = require_frozen_enrollment(
                artifacts.principals.registry,
                case_id,
            )
            enrolled = tuple(
                item for item in enrollment.payload.cases if item.case_id == case_id
            )
            if len(enrolled) != 1:
                raise ValueError("run case is absent from signed enrollment")
            sealed_case = enrolled[0]
            lineage = artifacts.require_current_chain(case_id)
            leakage = artifacts.lifecycle.read_payload(
                paths.leakage_report, LeakageReport
            )
            source = artifacts.lifecycle.read_payload(
                paths.source_sheet, CuratorSourceSheet
            )
            if leakage.verdict != "pass":
                raise ValueError("current leakage report does not pass")
            if manifest is not None and source.method_family != manifest.method_family:
                raise ValueError("run method family does not match blind descriptor")
            if source.source_generation != sealed_case.source_generation:
                raise ValueError("run source generation does not match enrollment")
            if source.method_family != sealed_case.method_family:
                raise ValueError("run method family does not match enrollment")
            if manifest is not None:
                loaded = BlindBenchmarkRegistry.load_case(manifest)
                descriptor = hashlib.sha256(
                    canonical_json(manifest.model_dump(mode="json"))
                ).hexdigest()
                if (
                    descriptor != sealed_case.descriptor_sha256
                    or loaded.source_ref != sealed_case.source_ref
                    or loaded.claim_fact_map_ref != sealed_case.claim_fact_map_ref
                    or loaded.brief_ref != sealed_case.blinded_brief_ref
                ):
                    raise ValueError("blind descriptor does not match enrollment")
            posthoc_ref = artifacts.lifecycle.artifact_ref(paths.posthoc_comparison)
            citation_ref = artifacts.lifecycle.artifact_ref(paths.citation_report)
        except (
            FileNotFoundError,
            OSError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as error:
            raise BlindLineageInvalid(f"{case_id}: {error}") from error
        adjudication_ref = (
            evaluation.adjudication.verdict_ref
            if evaluation.adjudication is not None
            else None
        )
        case_failures = () if evaluation.passed else ("case review did not pass",)
        unresolved = (
            evaluation.requires_adjudication and evaluation.adjudication is None
        )
        cohort = ReleaseCohort(sealed_case.cohort)
        return BlindCaseReport(
            case_id=case_id,
            method_family=source.method_family,
            cohort=cohort,
            recommendation_ref=evaluation.recommendation_ref,
            expert_score_refs=tuple(
                item.score_sheet_ref for item in evaluation.original_score_artifacts[:2]
            ),
            expert_scores=tuple(
                item.score_sheet for item in evaluation.original_score_artifacts
            ),
            dimension_scores=evaluation.dimension_scores,
            weighted_score=evaluation.weighted_score,
            passed=evaluation.passed,
            requires_adjudication=evaluation.requires_adjudication,
            unresolved=unresolved,
            adjudication_ref=adjudication_ref,
            posthoc_comparison_ref=posthoc_ref,
            citation_report_ref=citation_ref,
            lineage_refs=lineage,
            gate_failures=case_failures,
            release_case=CaseForRelease(
                case_id=case_id,
                method_family=source.method_family,
                recommendation_ref=evaluation.recommendation_ref,
                evaluation=evaluation,
                cohort=cohort,
                leakage_passed=leakage.verdict == "pass",
                citation_passed=citation.passed,
                unresolved=unresolved,
            ),
        ), enrollment


def _release_report(
    cases: tuple[BlindCaseReport, ...],
) -> BlindCatalogReport:
    if not cases:
        raise BlindLineageInvalid("blind run contains no cases")
    release_cases = tuple(case.release_case for case in cases)
    if any(case is None for case in release_cases):
        raise BlindLineageInvalid("blind case release evidence is missing")
    try:
        canonical = ReleaseEvaluator().evaluate(
            tuple(case for case in release_cases if case is not None),
        )
    except ValueError as error:
        raise BlindLineageInvalid(str(error)) from error
    unresolved = tuple(case.case_id for case in cases if case.unresolved)
    return BlindCatalogReport(
        **canonical.model_dump(),
        total_cases=len(cases),
        calibration_ready=all(
            case.cohort is ReleaseCohort.PILOT and case.passed and not case.unresolved
            for case in cases
        ),
        cases=cases,
        unresolved_cases=unresolved,
        gate_failures=canonical.blockers,
    )

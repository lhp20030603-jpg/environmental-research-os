"""Pinned read boundary for durable blind benchmark runs."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from envresearch.benchmarks.blind_artifacts import BlindArtifactLifecycle
from envresearch.benchmarks.design_files import PinnedFixtureRoot
from envresearch.research.principal_registry import PrincipalRegistry
from envresearch.workers import FilesystemWorkerQueue


class BlindLineageInvalid(ValueError):
    """The durable run does not contain one current authenticated lineage."""


def run_cases(run_root: Path) -> tuple[tuple[Path, str], ...]:
    if run_root.is_symlink():
        raise BlindLineageInvalid(f"blind run root is invalid: {run_root}")
    if _is_direct_run(run_root):
        return ((run_root, _single_case_id(run_root)),)
    if not run_root.is_dir() or run_root.is_symlink():
        raise BlindLineageInvalid(f"blind run root is invalid: {run_root}")
    children = tuple(sorted(run_root.iterdir()))
    if any(path.is_symlink() for path in children):
        raise BlindLineageInvalid("blind run children must not be aliases")
    cases = tuple(
        (path, _single_case_id(path))
        for path in children
        if path.is_dir() and _is_direct_run(path)
    )
    if not cases:
        raise BlindLineageInvalid("blind run contains no cases")
    return cases


@contextmanager
def snapshot_run_cases(
    run_root: Path,
) -> Iterator[tuple[tuple[Path, str], ...]]:
    """Discover cases only after copying one pinned, no-follow run snapshot."""
    try:
        source = PinnedFixtureRoot(run_root)
    except (OSError, ValueError) as error:
        raise BlindLineageInvalid(f"blind run root is invalid: {run_root}") from error
    with source, tempfile.TemporaryDirectory(prefix="envresearch-blind-run-") as temp:
        snapshot = Path(temp) / "run"
        try:
            source.snapshot_to(snapshot, validate_controls=True)
            cases = run_cases(snapshot)
        except (OSError, ValueError) as error:
            if isinstance(error, BlindLineageInvalid):
                raise
            raise BlindLineageInvalid(
                f"blind run root is invalid: {run_root}"
            ) from error
        yield cases


def _is_direct_run(run_root: Path) -> bool:
    artifacts = run_root / "artifacts/blind-benchmarks"
    return not artifacts.is_symlink() and artifacts.is_dir()


def _single_case_id(run_root: Path) -> str:
    root = run_root / "artifacts/blind-benchmarks"
    case_ids = tuple(
        path.name
        for path in sorted(root.iterdir())
        if path.is_dir() and not path.is_symlink()
    )
    if len(case_ids) != 1:
        raise BlindLineageInvalid("each blind run must contain exactly one case")
    return case_ids[0]


@contextmanager
def open_artifacts(run_root: Path, case_id: str) -> Iterator[BlindArtifactLifecycle]:
    """Open authenticated state from an immutable descriptor-relative snapshot."""
    try:
        source = PinnedFixtureRoot(run_root)
    except (OSError, ValueError) as error:
        raise BlindLineageInvalid(f"{case_id}: run snapshot is invalid") from error
    with source, tempfile.TemporaryDirectory(prefix="envresearch-blind-case-") as temp:
        snapshot = Path(temp) / "run"
        try:
            source.snapshot_to(snapshot, validate_controls=True)
        except (OSError, ValueError) as error:
            raise BlindLineageInvalid(f"{case_id}: run snapshot is invalid") from error
        with _open_snapshot_artifacts(snapshot, case_id) as artifacts:
            yield artifacts


@contextmanager
def _open_snapshot_artifacts(
    run_root: Path, case_id: str
) -> Iterator[BlindArtifactLifecycle]:
    exchange_rel = Path("exchanges/recommender") / case_id
    control_rel = Path("control/queues/recommender") / case_id
    artifact_rel = Path("artifacts/blind-benchmarks") / case_id
    directories = (
        exchange_rel,
        control_rel,
        control_rel / "locks",
        control_rel / "orders",
        control_rel / "receipts",
        control_rel / "principals/gates",
        control_rel / "principals/benchmark" / case_id,
        artifact_rel,
    )
    controls = (
        control_rel / "queue.key",
        control_rel / "principals/gate.capability",
        control_rel / "principals/revision.capability",
    )
    with ExitStack() as stack:
        try:
            run = stack.enter_context(PinnedFixtureRoot(run_root))
            pinned = {
                relative: stack.enter_context(run.pin_directory(relative))
                for relative in directories
            }
            for relative in controls:
                run.read(relative, description="authenticated run control")
            _preflight_artifacts(pinned[artifact_rel])
        except (OSError, ValueError) as error:
            raise BlindLineageInvalid(
                f"{case_id}: authenticated run control is missing or invalid"
            ) from error
        queue = FilesystemWorkerQueue(
            run_root / exchange_rel,
            control_root=run_root / control_rel,
            require_producer_context=True,
        )
        try:
            if not _same_directory(pinned[exchange_rel].fd, queue.exchange.fd):
                raise BlindLineageInvalid(f"{case_id}: run directory changed")
            if not _same_directory(pinned[control_rel].fd, queue.control.storage.fd):
                raise BlindLineageInvalid(f"{case_id}: run directory changed")
            registry = PrincipalRegistry(queue.control, f"blind-{case_id}")
            artifacts = BlindArtifactLifecycle(run_root, f"blind-{case_id}", registry)
            if not os.path.samestat(
                os.fstat(run.fd), os.stat(artifacts.lifecycle.workspace)
            ):
                raise BlindLineageInvalid(f"{case_id}: run root changed")
            if not os.path.samestat(
                os.fstat(pinned[artifact_rel].fd),
                os.stat(run_root / artifact_rel, follow_symlinks=False),
            ):
                raise BlindLineageInvalid(f"{case_id}: artifact directory changed")
            yield artifacts
            if not os.path.samestat(
                os.fstat(run.fd), os.stat(run_root, follow_symlinks=False)
            ):
                raise BlindLineageInvalid(f"{case_id}: run root changed")
        finally:
            queue.close()


def _preflight_artifacts(root: PinnedFixtureRoot) -> None:
    names = (
        "curator-source-sheet.yaml",
        "blinded-brief.yaml",
        "claim-fact-map.yaml",
        "leakage-report.yaml",
        "method-recommendation.yaml",
        "citation-integrity.yaml",
        "expert-score-1.signed.json",
        "expert-score-1.yaml",
        "expert-score-2.signed.json",
        "expert-score-2.yaml",
        "adjudicator-score.signed.json",
        "adjudicator-score.yaml",
        "adjudication.signed.json",
        "adjudication.yaml",
        "posthoc-comparison.yaml",
        "method-profiles.json",
        "expert-rubric.json",
    )
    for name in names:
        try:
            root.read(Path(name), description="blind artifact")
        except FileNotFoundError:
            pass


def _same_directory(first: int, second: int) -> bool:
    return os.path.samestat(os.fstat(first), os.fstat(second))

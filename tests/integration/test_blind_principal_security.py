"""Benchmark principal roles must remain owner-authenticated and separate."""

from __future__ import annotations

from pathlib import Path

import pytest

from envresearch.models.artifact import ProducerIdentity
from envresearch.models.principal import (
    PrincipalAssignment,
    PrincipalKind,
    PrincipalVerification,
)
from envresearch.research.principal_registry import PrincipalRegistry
from envresearch.workers import FilesystemWorkerQueue


@pytest.fixture
def queue(tmp_path: Path) -> FilesystemWorkerQueue:
    """Create one isolated queue with its protected owner control root."""
    return FilesystemWorkerQueue(tmp_path / "exchange")


def test_recommender_cannot_authenticate_as_leakage_validator(
    queue: FilesystemWorkerQueue,
) -> None:
    """Changing a recommender role to a validator must fail authentication."""
    registry = PrincipalRegistry(queue.control, "blind-run")
    recommender = registry.benchmark_worker("case-rct", PrincipalKind.RECOMMENDER, 1)
    validator = registry.benchmark_worker(
        "case-rct", PrincipalKind.LEAKAGE_VALIDATOR, 1
    )

    assert recommender.principal_id != validator.principal_id
    with pytest.raises(ValueError, match="principal role mismatch"):
        registry.require_benchmark_role(recommender, PrincipalKind.LEAKAGE_VALIDATOR)


def test_benchmark_human_bearer_capabilities_are_removed(
    queue: FilesystemWorkerQueue,
) -> None:
    """Reintroducing controller-readable human bearer APIs restores impersonation."""
    registry = PrincipalRegistry(queue.control, "blind-run")
    assert not hasattr(registry, "benchmark_capability_path")
    assert not hasattr(registry, "require_benchmark_human")
    assert not queue.control.storage.exists(
        Path("principals/benchmark/case-rct/expert-1.capability")
    )


def test_tampered_benchmark_assignment_cannot_authenticate(
    queue: FilesystemWorkerQueue,
) -> None:
    """Changing protected assignment bytes must invalidate its owner HMAC."""
    registry = PrincipalRegistry(queue.control, "blind-run")
    curator = registry.benchmark_worker("case-rct", PrincipalKind.CURATOR, 1)
    path = queue.control.path / "principals/benchmark/case-rct/curator-g1.json"
    path.write_text('{"assignment":{},"mac":"0"}', encoding="utf-8")

    with pytest.raises(ValueError, match="principal assignment authentication failed"):
        registry.require_benchmark_role(curator, PrincipalKind.CURATOR)


def test_unissued_benchmark_role_cannot_authenticate(
    queue: FilesystemWorkerQueue,
) -> None:
    """A caller-created assignment with the right role has no control anchor."""
    registry = PrincipalRegistry(queue.control, "blind-run")
    forged = PrincipalAssignment(
        assignment_id="assignment-forged",
        principal_id="principal-case-rct-curator",
        kind=PrincipalKind.CURATOR,
        producer=ProducerIdentity(
            component="assigned-curator",
            version="0.2.0",
            runtime="owner-control",
            context_id="context-forged",
        ),
        verification=PrincipalVerification.OWNER_CONTROL,
    )

    with pytest.raises(ValueError, match="principal assignment authentication failed"):
        registry.require_benchmark_role(forged, PrincipalKind.CURATOR)


def test_benchmark_worker_assignment_cannot_replay_in_another_run(
    queue: FilesystemWorkerQueue,
) -> None:
    """An HMAC-valid assignment from a different run is not this run's authority."""
    issued = PrincipalRegistry(queue.control, "run-one").benchmark_worker(
        "case-rct", PrincipalKind.CURATOR, 1
    )

    with pytest.raises(ValueError, match="principal assignment authentication failed"):
        PrincipalRegistry(queue.control, "run-two").require_benchmark_role(
            issued, PrincipalKind.CURATOR
        )


def test_missing_public_key_enrollment_does_not_mint_a_capability(
    queue: FilesystemWorkerQueue,
) -> None:
    """Missing external enrollment cannot create protected human state."""
    registry = PrincipalRegistry(queue.control, "blind-run")
    path = Path("principals/benchmark/case-rct/expert-1.capability")

    with pytest.raises(ValueError, match="participant enrollment"):
        registry.benchmark_human("case-rct", PrincipalKind.EXPERT, 1, 1)

    assert not queue.control.storage.exists(path)


@pytest.mark.parametrize("case_id", (".", "..", ".case-rct"))
def test_benchmark_case_ids_reject_path_aliases(
    queue: FilesystemWorkerQueue, case_id: str
) -> None:
    """Alias-like case IDs must fail before any protected path is constructed."""
    registry = PrincipalRegistry(queue.control, "blind-run")

    with pytest.raises(ValueError, match="benchmark case ID must be a canonical identifier"):
        registry.benchmark_worker(case_id, PrincipalKind.CURATOR, 1)

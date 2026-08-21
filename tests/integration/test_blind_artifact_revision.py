"""Atomic and authenticated revision of blind benchmark sources."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from threading import Condition, Event

import pytest
from blind_artifact_helpers import CaseHarness, brief, source_sheet

from envresearch.benchmarks.blind_revision import BlindRevisionTransaction
from envresearch.models.benchmark_claims import CuratorSourceSheet
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.principal import PrincipalKind
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle


def test_concurrent_revision_cannot_leave_stale_validated_publisher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = CaseHarness(tmp_path)
    service = harness.service
    curator = harness.worker(PrincipalKind.CURATOR)
    masker = harness.worker(PrincipalKind.MASKER)
    service.publish_source("case-rct", source_sheet(), curator)
    source_ref = service.ref("case-rct", "source_sheet")
    publisher_validated = Event()
    release_publisher = Event()
    original_validated_ref = service._validated_ref

    def paused_validated_ref(path: Path) -> object:
        result = original_validated_ref(path)
        if path == service.paths("case-rct").source_sheet:
            publisher_validated.set()
            assert release_publisher.wait(5)
        return result

    monkeypatch.setattr(service, "_validated_ref", paused_validated_ref)
    with ThreadPoolExecutor(max_workers=2) as executor:
        publisher = executor.submit(
            service.publish_brief,
            "case-rct",
            brief(source_ref, masker.principal_id),
            masker,
        )
        assert publisher_validated.wait(5)
        revision = executor.submit(
            service.revise_source,
            "case-rct",
            source_sheet(generation=2),
            revision_id="revision-publisher-race",
            reason="publisher concurrency",
            actor="curator-control",
            curator=curator,
        )
        try:
            revision.result(timeout=1)
        except FutureTimeout:
            pass
        finally:
            release_publisher.set()
        publisher.result(timeout=5)
        revision.result(timeout=5)

    paths = service.paths("case-rct")
    brief_envelope = service.lifecycle.current_envelope(paths.blinded_brief)
    assert brief_envelope.validation_status is ArtifactLifecycle.SUPERSEDED
    assert (
        service.lifecycle.read_payload(
            paths.source_sheet, CuratorSourceSheet
        ).source_generation
        == 2
    )


def test_concurrent_revisions_reject_loser_without_generation_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = CaseHarness(tmp_path)
    service = harness.service
    curator = harness.worker(PrincipalKind.CURATOR)
    service.publish_source("case-rct", source_sheet(), curator)
    original_execute = BlindRevisionTransaction.execute
    condition = Condition()
    arrivals = 0

    def synchronized_execute(
        transaction: BlindRevisionTransaction,
        source: CuratorSourceSheet,
        **kwargs: object,
    ) -> object:
        nonlocal arrivals
        with condition:
            arrivals += 1
            condition.notify_all()
            condition.wait_for(lambda: arrivals == 2, timeout=1)
        return original_execute(transaction, source, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(BlindRevisionTransaction, "execute", synchronized_execute)

    def revise(revision_id: str) -> object:
        return service.revise_source(
            "case-rct",
            source_sheet(generation=2),
            revision_id=revision_id,
            reason="revision concurrency",
            actor="curator-control",
            curator=curator,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(revise, "revision-race-one"),
            executor.submit(revise, "revision-race-two"),
        )
        outcomes: list[object] = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except ValueError as error:
                outcomes.append(error)

    assert sum(not isinstance(outcome, ValueError) for outcome in outcomes) == 1
    source = service.lifecycle.read_payload(
        service.paths("case-rct").source_sheet, CuratorSourceSheet
    )
    assert source.source_generation == 2
    assert (
        service.lifecycle.current_envelope(
            service.paths("case-rct").source_sheet
        ).artifact_version
        == 5
    )


def test_revised_source_supersedes_every_completed_descendant(tmp_path: Path) -> None:
    harness = CaseHarness(tmp_path)
    harness.populated()

    harness.service.revise_source(
        "case-rct",
        source_sheet(generation=2),
        revision_id="revision-source-2",
        reason="attachment hash changed",
        actor="curator-control",
        curator=harness.worker(PrincipalKind.CURATOR),
    )

    with pytest.raises(ValueError, match="blind benchmark lineage is stale"):
        harness.service.require_current_chain("case-rct")
    for path in harness.service.paths("case-rct").descendants:
        assert (
            harness.service.lifecycle.current_envelope(path).validation_status
            == "superseded"
        )
        current = harness.service.lifecycle.read_artifact(path)
        assert (
            harness.service.lifecycle.read_history(
                path, current.envelope.artifact_version
            )
            == current
        )


def test_committed_source_revision_recovers_after_post_exchange_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = CaseHarness(tmp_path)
    harness.populated()
    original_exchange = BlindRevisionTransaction._exchange_current_case

    def exchange_then_fail(
        transaction: BlindRevisionTransaction, shadow_root: Path
    ) -> None:
        original_exchange(transaction, shadow_root)
        raise OSError("injected post-exchange failure")

    monkeypatch.setattr(
        BlindRevisionTransaction, "_exchange_current_case", exchange_then_fail
    )
    with pytest.raises(OSError, match="post-exchange"):
        harness.service.revise_source(
            "case-rct",
            source_sheet(generation=2),
            revision_id="revision-post-exchange",
            reason="recover committed exchange",
            actor="curator-control",
            curator=harness.worker(PrincipalKind.CURATOR),
        )

    monkeypatch.setattr(
        BlindRevisionTransaction, "_exchange_current_case", original_exchange
    )
    recovered = harness.service.revise_source(
        "case-rct",
        source_sheet(generation=2),
        revision_id="revision-post-exchange",
        reason="recover committed exchange",
        actor="curator-control",
        curator=harness.worker(PrincipalKind.CURATOR),
    )
    assert recovered == harness.service.ref("case-rct", "source_sheet")


def test_source_revision_requires_exact_current_authenticated_curator(
    tmp_path: Path,
) -> None:
    harness = CaseHarness(tmp_path)
    harness.populated()
    paths = harness.service.paths("case-rct")
    source_ref = harness.service.ref("case-rct", "source_sheet")
    current = harness.worker(PrincipalKind.CURATOR)
    wrong_case = harness.registry.benchmark_worker("case-iv", PrincipalKind.CURATOR, 1)
    wrong_generation = harness.worker(PrincipalKind.CURATOR, 2)
    forged = current.model_copy(update={"assignment_id": "assignment-forged"})

    for principal in (
        harness.worker(PrincipalKind.MASKER),
        wrong_case,
        wrong_generation,
        forged,
    ):
        with pytest.raises(ValueError, match="principal|case|current source"):
            harness.service.revise_source(
                "case-rct",
                source_sheet(generation=2),
                revision_id="revision-unauthorized",
                reason="unauthorized replacement",
                actor="arbitrary-audit-label",
                curator=principal,
            )
        assert harness.service.ref("case-rct", "source_sheet") == source_ref
        assert all(
            harness.service.lifecycle.current_envelope(path).validation_status
            == "validated"
            for path in paths.descendants
        )


@pytest.mark.parametrize(
    "failure_point",
    ("middle-descendant", "source-supersede", "source-persistence"),
)
def test_failed_source_revision_leaves_complete_current_chain_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    harness = CaseHarness(tmp_path)
    harness.populated()
    service = harness.service
    paths = service.paths("case-rct")
    current_paths = (paths.source_sheet, *paths.descendants)
    before = {
        path: (tmp_path / path).read_bytes()
        for path in current_paths
        if (tmp_path / path).exists()
    }
    original_supersede = ResearchArtifactLifecycle.supersede
    original_persist = ResearchArtifactLifecycle.persist_structured
    descendant_calls = 0

    def failing_supersede(
        lifecycle: ResearchArtifactLifecycle, path: Path, **kwargs: object
    ) -> object:
        nonlocal descendant_calls
        if path in paths.descendants:
            descendant_calls += 1
            if failure_point == "middle-descendant" and descendant_calls == 3:
                raise OSError("injected middle descendant failure")
        if failure_point == "source-supersede" and path == paths.source_sheet:
            raise OSError("injected source supersede failure")
        return original_supersede(lifecycle, path, **kwargs)  # type: ignore[arg-type]

    def failing_persist(
        lifecycle: ResearchArtifactLifecycle,
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> object:
        if failure_point == "source-persistence" and path == paths.source_sheet:
            raise OSError("injected source persistence failure")
        return original_persist(lifecycle, path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ResearchArtifactLifecycle, "supersede", failing_supersede)
    monkeypatch.setattr(
        ResearchArtifactLifecycle, "persist_structured", failing_persist
    )
    with pytest.raises(OSError, match="injected"):
        service.revise_source(
            "case-rct",
            source_sheet(generation=2),
            revision_id=f"revision-{failure_point}",
            reason="failure injection",
            actor="curator-control",
            curator=harness.worker(PrincipalKind.CURATOR),
        )

    assert {
        path: (tmp_path / path).read_bytes()
        for path in current_paths
        if (tmp_path / path).exists()
    } == before
    service.require_current_chain("case-rct")

    monkeypatch.setattr(ResearchArtifactLifecycle, "supersede", original_supersede)
    monkeypatch.setattr(
        ResearchArtifactLifecycle, "persist_structured", original_persist
    )
    replacement = service.revise_source(
        "case-rct",
        source_sheet(generation=2),
        revision_id=f"revision-{failure_point}",
        reason="failure injection",
        actor="curator-control",
        curator=harness.worker(PrincipalKind.CURATOR),
    )
    assert replacement == service.ref("case-rct", "source_sheet")
    assert all(
        service.lifecycle.current_envelope(path).validation_status == "superseded"
        for path in paths.descendants
    )

"""Process contention and retry guarantees for argument-map publication."""

from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path

import pytest
from paper_argument_fixtures import build_worker, candidate
from paper_claim_fixtures import ResolverFixture, cv_resolver

from envresearch.models.artifact import ArtifactRef
from envresearch.paper import ArgumentMapService
from envresearch.paper.argument_contracts import ArgumentMapCandidate
from envresearch.paper.argument_map import ARGUMENT_MAP_SUBJECT
from envresearch.paper.errors import PaperAuthorityInvalid, PaperIntegrityInvalid
from envresearch.paper.ledger import CLAIM_LEDGER_SUBJECT, ClaimLedgerService


def _services(
    tmp_path: Path,
) -> tuple[ResolverFixture, ClaimLedgerService, ArgumentMapService, ArtifactRef]:
    resolver = cv_resolver(tmp_path)
    ledger_service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )
    ledger_ref = ledger_service.build(resolver.transition_ref)
    return (
        resolver,
        ledger_service,
        ArgumentMapService(ledger_service=ledger_service),
        ledger_ref,
    )


def _run_processes(
    tmp_path: Path,
    resolver: ResolverFixture,
    ledger_ref: ArtifactRef,
    candidates: tuple[ArgumentMapCandidate, ArgumentMapCandidate],
) -> tuple[tuple[str, str], tuple[str, str]]:
    analysis_ref, report = resolver.reports[0]
    context = get_context("spawn")
    start = context.Event()
    results = context.Queue()
    common = (
        str(tmp_path / "paper"),
        resolver.transition_ref.model_dump_json(),
        analysis_ref.model_dump_json(),
        report.model_dump_json(),
        ledger_ref.model_dump_json(),
    )
    processes = tuple(
        context.Process(
            target=build_worker,
            args=(*common, item.model_dump_json(), start, results),
        )
        for item in candidates
    )
    for process in processes:
        process.start()
    start.set()
    received = tuple(results.get(timeout=30) for _ in processes)
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    assert len(received) == 2
    return received[0], received[1]


def test_two_processes_publish_one_identical_current_map(tmp_path: Path) -> None:
    resolver, _, service, ledger_ref = _services(tmp_path)

    outcomes = _run_processes(
        tmp_path, resolver, ledger_ref, (candidate(), candidate(reverse=True))
    )

    assert tuple(status for status, _ in outcomes) == ("ok", "ok")
    references = tuple(ArtifactRef.model_validate_json(value) for _, value in outcomes)
    assert references[0] == references[1]
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) == references[0]
    assert service.status(references[0], ledger_ref).nodes


def test_two_conflicting_processes_leave_one_current_winner(tmp_path: Path) -> None:
    resolver, _, service, ledger_ref = _services(tmp_path)

    outcomes = _run_processes(
        tmp_path,
        resolver,
        ledger_ref,
        (candidate(suffix=" Alpha."), candidate(suffix=" Beta.")),
    )

    successes = tuple(value for status, value in outcomes if status == "ok")
    failures = tuple(status for status, _ in outcomes if status != "ok")
    assert len(successes) == 1
    assert failures == ("PAPER_AUTHORITY_INVALID",)
    winner = ArtifactRef.model_validate_json(successes[0])
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) == winner
    assert service.status(winner, ledger_ref).nodes


def test_failed_current_publication_retry_recovers_exact_map_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, service, ledger_ref = _services(tmp_path)
    original = service.registry.set_current

    def fail_once(_subject: str, _reference: ArtifactRef) -> None:
        raise OSError("injected current publication failure")

    monkeypatch.setattr(service.registry, "set_current", fail_once)
    with pytest.raises(PaperIntegrityInvalid, match="publication"):
        service.build(ledger_ref, candidate())
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) is None
    object_dir = (
        service.registry.root
        / "exit/objects"
        / (f"argument-map-{ledger_ref.content_hash[:12]}")
    )
    published = tuple(object_dir.glob("*.json"))
    assert len(published) == 1

    monkeypatch.setattr(service.registry, "set_current", original)
    recovered = service.build(ledger_ref, candidate())

    assert tuple(object_dir.glob("*.json")) == published
    assert recovered.content_hash in published[0].name
    assert service.status(recovered, ledger_ref).nodes


def test_conflicting_current_map_is_preserved(tmp_path: Path) -> None:
    _, _, service, ledger_ref = _services(tmp_path)
    current = service.build(ledger_ref, candidate(suffix=" Accepted."))

    with pytest.raises(PaperAuthorityInvalid, match="different argument map"):
        service.build(ledger_ref, candidate(suffix=" Conflict."))

    assert service.registry.current(ARGUMENT_MAP_SUBJECT) == current
    assert service.status(current, ledger_ref).nodes


def test_status_rejects_ledger_supersession(tmp_path: Path) -> None:
    resolver, ledger_service, service, ledger_ref = _services(tmp_path)
    map_ref = service.build(ledger_ref, candidate())
    ledger = ledger_service.status(ledger_ref, resolver.transition_ref)

    replacement = ledger.model_copy(update={"ledger_id": "valuation-core-replacement"})
    replacement_ref = service.registry.publish(replacement.ledger_id, replacement)
    service.registry.set_current(CLAIM_LEDGER_SUBJECT, replacement_ref)

    with pytest.raises(PaperAuthorityInvalid) as raised:
        service.status(map_ref, ledger_ref)

    assert raised.value.code == "PAPER_AUTHORITY_INVALID"
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) == map_ref


def test_status_rejects_transition_supersession(tmp_path: Path) -> None:
    resolver, _, service, ledger_ref = _services(tmp_path)
    map_ref = service.build(ledger_ref, candidate())
    resolver.current = False

    with pytest.raises(PaperAuthorityInvalid) as raised:
        service.status(map_ref, ledger_ref)

    assert raised.value.code == "PAPER_AUTHORITY_INVALID"
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) == map_ref

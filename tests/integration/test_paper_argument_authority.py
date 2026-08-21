"""Late authority, rollback, and concurrency checks for argument maps."""

from __future__ import annotations

from pathlib import Path
from types import MethodType

import pytest
from paper_argument_fixtures import candidate, services

from envresearch.models.artifact import ArtifactRef
from envresearch.paper import ArgumentMapService
from envresearch.paper.argument_map import ARGUMENT_MAP_SUBJECT
from envresearch.paper.contracts import ClaimEvidenceLedger
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperIntegrityInvalid,
    PaperSupportInvalid,
)
from envresearch.paper.ledger import ClaimLedgerService


def _map_path(service: ArgumentMapService, reference: ArtifactRef) -> Path:
    return (
        service.registry.root
        / "exit/objects"
        / reference.artifact_id
        / f"v{reference.artifact_version}-{reference.content_hash}.json"
    )


def _mutate_after_current_call(
    service: ArgumentMapService,
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_call: int,
) -> None:
    original = service._current
    calls = 0

    def mutate_after_authentication(_self: ArgumentMapService) -> ArtifactRef | None:
        nonlocal calls
        reference = original()
        calls += 1
        if calls == target_call:
            assert reference is not None
            path = _map_path(service, reference)
            data = path.read_bytes()
            path.chmod(0o600)
            path.write_bytes(data.replace(b"registered design", b"mutated design"))
        return reference

    monkeypatch.setattr(
        service,
        "_current",
        MethodType(mutate_after_authentication, service),
    )


def test_new_build_reopens_map_after_final_current_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger_service, service, transition_ref = services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    _mutate_after_current_call(service, monkeypatch, target_call=3)

    with pytest.raises(PaperIntegrityInvalid):
        service.build(ledger_ref, candidate())


def test_idempotent_build_reopens_map_after_final_current_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger_service, service, transition_ref = services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    service.build(ledger_ref, candidate())
    _mutate_after_current_call(service, monkeypatch, target_call=2)

    with pytest.raises(PaperIntegrityInvalid):
        service.build(ledger_ref, candidate())


def test_status_reopens_map_after_final_current_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger_service, service, transition_ref = services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    map_ref = service.build(ledger_ref, candidate())
    _mutate_after_current_call(service, monkeypatch, target_call=2)

    with pytest.raises(PaperIntegrityInvalid):
        service.status(map_ref, ledger_ref)


def test_rollback_preserves_a_newer_current_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger_service, service, transition_ref = services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    installed_ref = service.build(ledger_ref, candidate())
    installed = service.status(installed_ref, ledger_ref)
    replacement_nodes = tuple(
        node.model_copy(update={"proposition": f"{node.proposition} External."})
        if node.node_type == "contribution"
        else node
        for node in installed.nodes
    )
    replacement = installed.model_copy(update={"nodes": replacement_nodes})
    replacement_ref = service.registry.publish(replacement.map_id, replacement)
    service.registry.files.unlink(Path("exit/current") / f"{ARGUMENT_MAP_SUBJECT}.json")
    original = ledger_service.status
    calls = 0

    def supersede_before_failure(
        _self: ClaimLedgerService,
        requested_ledger_ref: ArtifactRef,
        requested_transition_ref: ArtifactRef,
    ) -> ClaimEvidenceLedger:
        nonlocal calls
        calls += 1
        ledger = original(requested_ledger_ref, requested_transition_ref)
        if calls == 4:
            service.registry.set_current(ARGUMENT_MAP_SUBJECT, replacement_ref)
            return ledger.model_copy(update={"ledger_id": "valuation-core-changed"})
        return ledger

    monkeypatch.setattr(
        ledger_service,
        "status",
        MethodType(supersede_before_failure, ledger_service),
    )

    with pytest.raises(PaperAuthorityInvalid, match="changed during publication"):
        service.build(ledger_ref, candidate())

    assert service.registry.current(ARGUMENT_MAP_SUBJECT) == replacement_ref


@pytest.mark.parametrize("claim_ids", ((), ("Invalid claim",)))
def test_build_revalidates_forged_nested_empirical_nodes(
    tmp_path: Path, claim_ids: tuple[str, ...]
) -> None:
    ledger_service, service, transition_ref = services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    valid = candidate()
    forged_nodes = tuple(
        node.model_copy(update={"claim_ids": claim_ids})
        if node.node_type == "empirical-claim"
        else node
        for node in valid.nodes
    )
    forged = valid.model_copy(update={"nodes": forged_nodes})

    with pytest.raises(PaperSupportInvalid) as raised:
        service.build(ledger_ref, forged)

    assert raised.value.code == "PAPER_SUPPORT_INVALID"
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) is None

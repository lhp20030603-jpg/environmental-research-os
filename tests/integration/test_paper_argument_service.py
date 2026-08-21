"""Exact-ledger publication and status checks for typed argument maps."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import MethodType

import pytest
from paper_argument_fixtures import candidate as _candidate
from paper_argument_fixtures import services as _services

from envresearch.models.artifact import ArtifactRef
from envresearch.paper.argument_map import ARGUMENT_MAP_SUBJECT
from envresearch.paper.contracts import ClaimEvidenceLedger
from envresearch.paper.errors import PaperAuthorityInvalid, PaperIntegrityInvalid
from envresearch.paper.ledger import CLAIM_LEDGER_SUBJECT, ClaimLedgerService


def test_builds_canonical_map_from_genuine_current_cv_ledger(tmp_path: Path) -> None:
    ledger_service, service, transition_ref = _services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    reference = service.build(ledger_ref, _candidate())
    argument_map = service.status(reference, ledger_ref)
    repeated = service.build(ledger_ref, _candidate(reverse=True))

    assert service.registry is ledger_service.registry
    assert repeated == reference
    assert argument_map.schema_version == "paper.argument-map.v1"
    assert argument_map.map_id == f"argument-map-{ledger_ref.content_hash[:12]}"
    assert argument_map.producer == "paper-builder-argument-map-v1"
    assert argument_map.ledger_ref == ledger_ref
    assert argument_map.transition_ref == transition_ref
    assert tuple(node.node_id for node in argument_map.nodes) == (
        "cv-results",
        "policy-boundary",
        "scope-limitation",
        "valuation-contribution",
    )
    assert argument_map.nodes[0].claim_ids == (
        "contingent-valuation-bid-yes-shares",
        "contingent-valuation-median-wtp",
        "contingent-valuation-probability-range",
    )
    assert tuple(
        (edge.source_id, edge.target_id, edge.edge_type) for edge in argument_map.edges
    ) == (
        ("cv-results", "policy-boundary", "conditional"),
        ("cv-results", "valuation-contribution", "evidence-backed"),
    )
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) == reference


def test_ledger_supersession_after_candidate_validation_prevents_promotion(
    tmp_path: Path,
) -> None:
    ledger_service, service, transition_ref = _services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    original = ledger_service.status
    calls = 0

    def supersede(
        _self: ClaimLedgerService,
        requested_ledger_ref,
        requested_transition_ref,
    ) -> ClaimEvidenceLedger:
        nonlocal calls
        calls += 1
        if calls == 2:
            ledger = original(ledger_ref, transition_ref)
            replacement = ledger.model_copy(
                update={"ledger_id": "valuation-core-superseded"}
            )
            replacement_ref = service.registry.publish(
                replacement.ledger_id, replacement
            )
            service.registry.set_current(CLAIM_LEDGER_SUBJECT, replacement_ref)
        return original(requested_ledger_ref, requested_transition_ref)

    ledger_service.status = MethodType(supersede, ledger_service)  # type: ignore[method-assign]

    with pytest.raises(PaperAuthorityInvalid) as raised:
        service.build(ledger_ref, _candidate())

    assert raised.value.code == "PAPER_AUTHORITY_INVALID"
    assert calls == 2
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) is None


def test_ledger_payload_change_after_candidate_validation_prevents_promotion(
    tmp_path: Path,
) -> None:
    ledger_service, service, transition_ref = _services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    original = ledger_service.status
    calls = 0

    def mutate_payload(
        _self: ClaimLedgerService,
        requested_ledger_ref,
        requested_transition_ref,
    ) -> ClaimEvidenceLedger:
        nonlocal calls
        calls += 1
        ledger = original(requested_ledger_ref, requested_transition_ref)
        if calls != 2:
            return ledger
        claims = tuple(
            claim.model_copy(
                update={"limitations": (*claim.limitations, "Changed upstream.")}
            )
            for claim in ledger.claims
        )
        return ledger.model_copy(update={"claims": claims})

    ledger_service.status = MethodType(  # type: ignore[method-assign]
        mutate_payload, ledger_service
    )

    with pytest.raises(PaperAuthorityInvalid, match="changed during publication"):
        service.build(ledger_ref, _candidate())

    assert calls == 2
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) is None


def test_real_ledger_byte_mutation_during_publication_is_integrity(
    tmp_path: Path,
) -> None:
    ledger_service, service, transition_ref = _services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    original = ledger_service.status
    calls = 0
    relative = (
        Path("exit/objects")
        / ledger_ref.artifact_id
        / f"v1-{ledger_ref.content_hash}.json"
    )
    path = tmp_path / "paper" / relative

    def mutate_real_bytes(
        _self: ClaimLedgerService,
        requested_ledger_ref,
        requested_transition_ref,
    ) -> ClaimEvidenceLedger:
        nonlocal calls
        calls += 1
        if calls == 2:
            data = path.read_bytes()
            path.chmod(0o600)
            path.write_bytes(
                data.replace(b"paper-builder-ledger-v1", b"attacker-ledger-v1")
            )
        return original(requested_ledger_ref, requested_transition_ref)

    ledger_service.status = MethodType(  # type: ignore[method-assign]
        mutate_real_bytes, ledger_service
    )

    with pytest.raises(PaperIntegrityInvalid) as raised:
        service.build(ledger_ref, _candidate())

    assert raised.value.code == "PAPER_INTEGRITY_INVALID"
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) is None


@pytest.mark.parametrize("failure_call", (3, 4))
def test_late_ledger_change_never_leaves_a_new_current_map(
    tmp_path: Path, failure_call: int
) -> None:
    ledger_service, service, transition_ref = _services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    original = ledger_service.status
    calls = 0

    def change_late(
        _self: ClaimLedgerService,
        requested_ledger_ref,
        requested_transition_ref,
    ) -> ClaimEvidenceLedger:
        nonlocal calls
        calls += 1
        ledger = original(requested_ledger_ref, requested_transition_ref)
        if calls == failure_call:
            return ledger.model_copy(update={"ledger_id": "valuation-core-changed"})
        return ledger

    ledger_service.status = MethodType(change_late, ledger_service)  # type: ignore[method-assign]

    with pytest.raises(PaperAuthorityInvalid, match="changed during publication"):
        service.build(ledger_ref, _candidate())

    assert calls == failure_call
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) is None
    map_id = f"argument-map-{ledger_ref.content_hash[:12]}"
    assert len(tuple((tmp_path / "paper/exit/objects" / map_id).glob("*.json"))) == 1


def test_build_classifies_noncurrent_ledger_reference_as_authority(
    tmp_path: Path,
) -> None:
    ledger_service, service, transition_ref = _services(tmp_path)
    ledger_service.build(transition_ref)
    stale = ArtifactRef(
        artifact_id="valuation-core-stale",
        artifact_version=1,
        content_hash="f" * 64,
    )

    with pytest.raises(PaperAuthorityInvalid, match="not current") as raised:
        service.build(stale, _candidate())

    assert raised.value.code == "PAPER_AUTHORITY_INVALID"
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) is None


def test_idempotent_build_rechecks_map_pointer_after_ledger_status(
    tmp_path: Path,
) -> None:
    ledger_service, service, transition_ref = _services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    map_ref = service.build(ledger_ref, _candidate())
    argument_map = service.status(map_ref, ledger_ref)
    replacement = argument_map.model_copy(update={"map_id": "argument-map-superseded"})
    replacement_ref = service.registry.publish(replacement.map_id, replacement)
    original = ledger_service.status
    calls = 0

    def redirect_map(
        _self: ClaimLedgerService,
        requested_ledger_ref,
        requested_transition_ref,
    ) -> ClaimEvidenceLedger:
        nonlocal calls
        calls += 1
        ledger = original(requested_ledger_ref, requested_transition_ref)
        if calls == 3:
            service.registry.set_current(ARGUMENT_MAP_SUBJECT, replacement_ref)
        return ledger

    ledger_service.status = MethodType(redirect_map, ledger_service)  # type: ignore[method-assign]

    with pytest.raises(PaperAuthorityInvalid, match="changed during publication"):
        service.build(ledger_ref, _candidate())

    assert calls == 3
    assert service.registry.current(ARGUMENT_MAP_SUBJECT) == replacement_ref


@pytest.mark.parametrize("fault", ("artifact-version", "map-id"))
def test_status_rejects_forged_service_owned_map_identity(
    tmp_path: Path, fault: str
) -> None:
    ledger_service, service, transition_ref = _services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    map_ref = service.build(ledger_ref, _candidate())
    argument_map = service.status(map_ref, ledger_ref)
    if fault == "artifact-version":
        forged = service.registry.publish(argument_map.map_id, argument_map, version=2)
    else:
        changed = argument_map.model_copy(update={"map_id": "attacker-map"})
        forged = service.registry.publish(changed.map_id, changed)
    service.registry.set_current(ARGUMENT_MAP_SUBJECT, forged)

    with pytest.raises(PaperIntegrityInvalid, match="identity") as raised:
        service.status(forged, ledger_ref)

    assert raised.value.code == "PAPER_INTEGRITY_INVALID"


@pytest.mark.parametrize("fault", ("hash", "canonical"))
def test_status_classifies_argument_map_byte_faults_as_integrity(
    tmp_path: Path, fault: str
) -> None:
    ledger_service, service, transition_ref = _services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    map_ref = service.build(ledger_ref, _candidate())
    argument_map = service.status(map_ref, ledger_ref)
    if fault == "hash":
        relative = (
            Path("exit/objects")
            / map_ref.artifact_id
            / f"v1-{map_ref.content_hash}.json"
        )
        path = tmp_path / "paper" / relative
        data = path.read_bytes()
        path.chmod(0o600)
        path.write_bytes(data.replace(b"registered design", b"tampered design"))
        fault_ref = map_ref
    else:
        raw = (
            argument_map.model_dump_json()
            .replace(',"map_id"', ', "map_id"', 1)
            .encode()
        )
        digest = hashlib.sha256(raw).hexdigest()
        fault_ref = ArtifactRef(
            artifact_id=argument_map.map_id,
            artifact_version=1,
            content_hash=digest,
        )
        relative = Path("exit/objects") / argument_map.map_id / f"v1-{digest}.json"
        service.registry.files.persist_exact(relative, raw)
        service.registry.set_current(ARGUMENT_MAP_SUBJECT, fault_ref)

    with pytest.raises(PaperIntegrityInvalid) as raised:
        service.status(fault_ref, ledger_ref)

    assert raised.value.code == "PAPER_INTEGRITY_INVALID"


def test_status_rejects_noncanonical_semantic_order_as_integrity(
    tmp_path: Path,
) -> None:
    ledger_service, service, transition_ref = _services(tmp_path)
    ledger_ref = ledger_service.build(transition_ref)
    map_ref = service.build(ledger_ref, _candidate())
    argument_map = service.status(map_ref, ledger_ref)
    empirical = argument_map.nodes[0].model_copy(
        update={"claim_ids": tuple(reversed(argument_map.nodes[0].claim_ids))}
    )
    forged = argument_map.model_copy(
        update={
            "nodes": (*reversed(argument_map.nodes[1:]), empirical),
            "edges": tuple(reversed(argument_map.edges)),
        }
    )
    forged_ref = service.registry.publish(forged.map_id, forged)
    service.registry.set_current(ARGUMENT_MAP_SUBJECT, forged_ref)

    with pytest.raises(PaperIntegrityInvalid, match="bytes") as raised:
        service.status(forged_ref, ledger_ref)

    assert raised.value.code == "PAPER_INTEGRITY_INVALID"

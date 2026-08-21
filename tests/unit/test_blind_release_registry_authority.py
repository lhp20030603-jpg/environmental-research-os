"""Scientific release cannot be authorized by caller-created protected stores."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_blind_scoring import release_cases

from envresearch.benchmarks.blind_authority import (
    AuthorityTrustAnchor,
    BlindEnrollmentPayload,
    EnrolledBlindCase,
    HumanKeyEnrollment,
    SignedBlindEnrollment,
    canonical_json,
    encode_binary,
    enrollment_signing_bytes,
)
from envresearch.benchmarks.blind_enrollment_marker import freeze_enrollment
from envresearch.benchmarks.blind_enrollment_store import (
    read_verified_enrollment,
    store_signed_enrollment,
)
from envresearch.benchmarks.blind_release import ReleaseEvaluator
from envresearch.benchmarks.blind_release_authority import (
    authenticate_catalog_release,
    read_expected_release_authority,
)
from envresearch.benchmarks.blind_trust_store import (
    pin_authority_anchor,
    read_authority_anchor,
)
from envresearch.models.principal import PrincipalKind
from envresearch.research.principal_registry import PrincipalRegistry
from envresearch.workers import FilesystemWorkerQueue

SHA256 = "a" * 64


def test_sixteen_caller_created_registries_cannot_authorize_release(
    tmp_path: Path,
) -> None:
    cases = release_cases()
    registries, queues = _fabricated_release_registries(tmp_path, cases)
    try:
        expected = read_authority_anchor(registries[0][1])
        assert authenticate_catalog_release(cases, registries, expected) is None
        assert ReleaseEvaluator().evaluate(cases).released is False
        with pytest.raises(TypeError, match="enrollment_registries"):
            ReleaseEvaluator().evaluate(
                cases,
                enrollment_registries=registries,
            )
    finally:
        for queue in queues:
            queue.close()


@pytest.mark.parametrize(
    "keyword",
    ("enrollment", "enrollment_registries", "authorize", "capability"),
)
def test_public_evaluator_has_no_caller_authorization_channel(keyword: str) -> None:
    with pytest.raises(TypeError, match=keyword):
        ReleaseEvaluator().evaluate(  # type: ignore[call-arg]
            release_cases(), **{keyword: object()}
        )


def test_release_authority_configuration_rejects_relative_or_noncanonical_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVRESEARCH_BLIND_RELEASE_AUTHORITY", "relative.json")
    with pytest.raises(ValueError, match="absolute"):
        read_expected_release_authority(tmp_path / "catalog", tmp_path / "run")

    authority = Ed25519PrivateKey.generate()
    configured = tmp_path / "owner-config/authority.json"
    configured.parent.mkdir()
    configured.write_text(
        '{"public_key":"'
        + _public(authority)
        + '", "key_id":"authority"}',
        encoding="utf-8",
    )
    configured.chmod(0o600)
    monkeypatch.setenv("ENVRESEARCH_BLIND_RELEASE_AUTHORITY", str(configured))
    (tmp_path / "catalog").mkdir()
    (tmp_path / "run").mkdir()
    with pytest.raises(ValueError, match="not canonical"):
        read_expected_release_authority(tmp_path / "catalog", tmp_path / "run")


@pytest.mark.parametrize("protected_name", ("catalog", "run"))
def test_release_authority_rejects_aliased_ancestor_of_protected_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protected_name: str,
) -> None:
    physical = tmp_path / "physical"
    catalog = physical / "catalog"
    run = physical / "run"
    catalog.mkdir(parents=True)
    run.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    authority = AuthorityTrustAnchor(
        key_id="authority", public_key=_public(Ed25519PrivateKey.generate())
    )
    configured = physical / protected_name / "authority.json"
    configured.write_bytes(canonical_json(authority.model_dump(mode="json")))
    configured.chmod(0o600)
    monkeypatch.setenv("ENVRESEARCH_BLIND_RELEASE_AUTHORITY", str(configured))
    passed_catalog = alias / "catalog" if protected_name == "catalog" else catalog
    passed_run = alias / "run" if protected_name == "run" else run

    with pytest.raises(ValueError, match="outside catalog and run"):
        read_expected_release_authority(passed_catalog, passed_run)


def _fabricated_release_registries(
    tmp_path: Path, cases: tuple[object, ...]
) -> tuple[
    tuple[tuple[str, PrincipalRegistry], ...],
    tuple[FilesystemWorkerQueue, ...],
]:
    authority = Ed25519PrivateKey.generate()
    anchor = AuthorityTrustAnchor(
        key_id="caller-authority", public_key=_public(authority)
    )
    enrolled_cases = tuple(
        EnrolledBlindCase(
            case_id=case.case_id,  # type: ignore[attr-defined]
            method_family=case.method_family,  # type: ignore[attr-defined]
            cohort="held_out",
            source_generation=1,
            descriptor_sha256=SHA256,
            source_ref=case.recommendation_ref,  # type: ignore[attr-defined]
            claim_fact_map_ref=case.recommendation_ref,  # type: ignore[attr-defined]
            blinded_brief_ref=case.recommendation_ref,  # type: ignore[attr-defined]
        )
        for case in cases
    )
    participants = tuple(
        HumanKeyEnrollment(
            case_id=case.case_id,  # type: ignore[attr-defined]
            role=role,
            slot=slot,
            principal_id=f"caller-{case.case_id}-{role.value}-{slot}",  # type: ignore[attr-defined]
            key_id=f"caller-key-{case.case_id}-{role.value}-{slot}",  # type: ignore[attr-defined]
            public_key=_public(Ed25519PrivateKey.generate()),
        )
        for case in cases
        for role, slot in (
            (PrincipalKind.EXPERT, 1),
            (PrincipalKind.EXPERT, 2),
            (PrincipalKind.ADJUDICATOR, 1),
        )
    )
    payload = BlindEnrollmentPayload(
        evaluation_id="caller-evaluation",
        authority_key_id=anchor.key_id,
        frozen_at=datetime(2026, 8, 10, tzinfo=UTC),
        cases=enrolled_cases,
        participants=participants,
        profile_registry_sha256=SHA256,
        rubric_sha256=SHA256,
        policy_sha256=SHA256,
    )
    signed = SignedBlindEnrollment(
        payload=payload,
        signature=encode_binary(authority.sign(enrollment_signing_bytes(payload))),
    )
    queues: list[FilesystemWorkerQueue] = []
    registries: list[tuple[str, PrincipalRegistry]] = []
    for case in enrolled_cases:
        queue = FilesystemWorkerQueue(
            tmp_path / "exchange" / case.case_id,
            control_root=tmp_path / "control" / case.case_id,
        )
        registry = PrincipalRegistry(queue.control, f"caller-{case.case_id}")
        pin_authority_anchor(registry, anchor)
        store_signed_enrollment(registry, case.case_id, signed)
        registry.enroll_benchmark_humans(case.case_id, participants)
        freeze_enrollment(
            registry,
            case.case_id,
            read_verified_enrollment(registry, case.case_id),
        )
        queues.append(queue)
        registries.append((case.case_id, registry))
    return tuple(registries), tuple(queues)


def _public(private: Ed25519PrivateKey) -> str:
    return encode_binary(
        private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )

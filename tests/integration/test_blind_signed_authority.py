"""External signature and frozen-enrollment security boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError
from test_blind_cli import _reviewed_case
from test_blind_workflow import (
    _raw_score_for,
    _score_for,
    ready_for_expert_scoring,
)

from envresearch.benchmarks.blind_authority import (
    VerifiedBlindEnrollment,
    canonical_json,
    encode_binary,
)
from envresearch.benchmarks.blind_report import (
    evaluate_blind_catalog,
)
from envresearch.models.principal import PrincipalKind


def test_verified_enrollment_has_no_importable_authorization_factory() -> None:
    """Verified data cannot mint release authorization inside the process."""
    import envresearch.benchmarks.blind_authority as authority

    assert not hasattr(authority, "_verified_enrollment")


def test_release_rejects_object_new_enrollment_forgery() -> None:
    from envresearch.benchmarks.blind_release import ReleaseEvaluator

    forged = object.__new__(VerifiedBlindEnrollment)
    with pytest.raises(TypeError, match="unexpected keyword argument 'enrollment'"):
        ReleaseEvaluator().evaluate((), enrollment=forged)


def test_participant_cannot_reuse_controller_principal_namespace() -> None:
    from envresearch.benchmarks.blind_authority import HumanKeyEnrollment

    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    with pytest.raises(ValidationError, match="reserved controller principal"):
        HumanKeyEnrollment(
            case_id="case-1",
            role=PrincipalKind.EXPERT,
            slot=1,
            principal_id="principal-case-1-recommender",
            key_id="key-one",
            public_key=encode_binary(public),
        )


def test_canonical_release_uses_only_the_externally_configured_authority_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from envresearch.benchmarks.blind_authority import AuthorityTrustAnchor
    from envresearch.benchmarks.blind_trust_store import (
        pin_authority_anchor,
        read_authority_anchor,
    )

    case_root, run_root, controller = _reviewed_case(tmp_path)
    expected = tmp_path / "owner-config/release-authority.json"
    expected.parent.mkdir()
    expected.write_bytes(
        canonical_json(
            read_authority_anchor(controller.registry).model_dump(mode="json")
        )
    )
    expected.chmod(0o600)
    monkeypatch.setenv("ENVRESEARCH_BLIND_RELEASE_AUTHORITY", str(expected))
    valid = evaluate_blind_catalog(case_root, run_root)
    assert "verified authority enrollment is required" not in valid.blockers

    wrong = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    expected.write_bytes(
        canonical_json(
            AuthorityTrustAnchor(
                key_id="authority-test", public_key=encode_binary(wrong)
            ).model_dump(mode="json")
        )
    )
    with pytest.raises(ValueError, match="run authority does not match"):
        evaluate_blind_catalog(case_root, run_root)

    with pytest.raises(ValueError, match="cannot be replaced"):
        pin_authority_anchor(
            controller.registry,
            AuthorityTrustAnchor(
                key_id="authority-test", public_key=encode_binary(wrong)
            ),
        )


def test_tampered_pinned_authority_anchor_is_rejected(tmp_path: Path) -> None:
    import json

    from blind_signing_helpers import enroll_controller
    from test_blind_registry_security import write_case

    from envresearch.benchmarks.blind_trust_store import read_authority_anchor
    from envresearch.benchmarks.blind_workflow import BlindEvaluationController

    controller = BlindEvaluationController.from_case(
        write_case(tmp_path / "case"), tmp_path / "run"
    )
    enroll_controller(controller)
    path = controller.queue.control.path / "principals/blind-authority-trust-anchor.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["anchor"]["key_id"] = "forged-authority"
    path.write_text(json.dumps(record, separators=(",", ":"), sort_keys=True))

    with pytest.raises(ValueError, match="authentication failed"):
        read_authority_anchor(controller.registry)


def test_cross_slot_signed_evidence_replay_is_rejected(tmp_path: Path) -> None:
    controller = ready_for_expert_scoring(tmp_path)
    controller.issue_expert_orders()
    expert_one = _score_for(controller, 1)

    with pytest.raises(ValueError, match="current assignment"):
        controller.accept_expert_score(2, expert_one)  # type: ignore[arg-type]


def test_changed_candidate_invalidates_external_signature(tmp_path: Path) -> None:
    controller = ready_for_expert_scoring(tmp_path)
    controller.issue_expert_orders()
    evidence = _score_for(controller, 1)
    candidate = dict(evidence.candidate)  # type: ignore[attr-defined,arg-type]
    candidate["verdict"] = "fail"
    changed = evidence.model_copy(update={"candidate": candidate})  # type: ignore[attr-defined]

    with pytest.raises(ValidationError, match="candidate digest mismatch"):
        controller.accept_expert_score(1, changed)


def test_controller_rejects_unsigned_human_payload(tmp_path: Path) -> None:
    """Restoring payload submission would let one controller impersonate both experts."""
    controller = ready_for_expert_scoring(tmp_path)
    controller.issue_expert_orders()

    with pytest.raises(ValueError, match="externally signed human evidence"):
        controller.accept_expert_score(1, _raw_score_for(controller, 1))


def test_controller_run_contains_no_human_bearer_or_private_key(tmp_path: Path) -> None:
    """Generating a bearer capability beneath the run recreates controller self-signing."""
    from test_blind_workflow import ready_for_recommendation, valid_recommendation

    controller = ready_for_recommendation(tmp_path)
    controller.accept_recommendation(valid_recommendation(controller))
    controller.issue_expert_orders()

    names = tuple(path.name for path in controller.run_root.rglob("*"))
    assert not any("expert-1.capability" in name for name in names)
    assert not any("private" in name.casefold() for name in names)


def test_participant_enrollment_is_rejected_after_recommendation_order(
    tmp_path: Path,
) -> None:
    """The signer roster is frozen before controller work can be persisted."""
    from blind_signing_helpers import enroll_controller
    from test_blind_registry_security import write_case

    from envresearch.benchmarks.blind_workflow import BlindEvaluationController

    controller = BlindEvaluationController.from_case(
        write_case(tmp_path / "case"), tmp_path / "run"
    )
    exchange = controller.run_root / "exchanges/recommender/pilot-001/orders"
    exchange.mkdir(parents=True)
    (exchange / "legacy-order.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="first.*transition|pre-existing.*state"):
        enroll_controller(controller)


def test_source_publication_requires_frozen_enrollment(tmp_path: Path) -> None:
    from blind_signing_helpers import enroll_controller
    from test_blind_registry_security import write_case

    from envresearch.benchmarks.blind_workflow import BlindEvaluationController
    from envresearch.models.principal import PrincipalKind

    case = write_case(tmp_path / "case")
    controller = BlindEvaluationController.from_case(case, tmp_path / "run")
    curator = controller.registry._benchmark_assignment(
        controller.case_id, PrincipalKind.CURATOR, 1, human=False
    )

    with pytest.raises(ValueError, match="frozen.*enrollment|enrollment.*required"):
        controller.artifacts.publish_source(
            controller.case_id, controller.loaded.source_sheet, curator
        )
    restarted = BlindEvaluationController.from_case(case, tmp_path / "run")
    enroll_controller(restarted)


def test_legacy_source_prestate_blocks_enrollment_after_restart(tmp_path: Path) -> None:
    from blind_signing_helpers import enroll_controller
    from test_blind_registry_security import write_case

    from envresearch.benchmarks.blind_workflow import BlindEvaluationController
    from envresearch.models.artifact import ProducerIdentity

    case = write_case(tmp_path / "case")
    run = tmp_path / "run"
    controller = BlindEvaluationController.from_case(case, run)
    paths = controller.artifacts.paths(controller.case_id)
    controller.artifacts.lifecycle.persist_structured(
        paths.source_sheet,
        controller.loaded.source_sheet,
        ProducerIdentity(component="legacy-curator", version="0.2.0"),
        (),
    )
    restarted = BlindEvaluationController.from_case(case, run)

    with pytest.raises(ValueError, match="first.*transition|pre-existing.*state"):
        enroll_controller(restarted)


def test_archiving_a_recommendation_order_cannot_reopen_enrollment(
    tmp_path: Path,
) -> None:
    from blind_signing_helpers import enroll_controller
    from test_blind_registry_security import write_case

    from envresearch.benchmarks.blind_workflow import BlindEvaluationController

    controller = BlindEvaluationController.from_case(
        write_case(tmp_path / "case"), tmp_path / "run"
    )
    archive = controller.run_root / "control/queues/recommender/pilot-001/orders/archive"
    archive.mkdir(parents=True)
    (archive / "legacy-order.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="first.*transition|pre-existing.*state"):
        enroll_controller(controller)


def test_ed25519_private_key_cannot_be_serialized_by_controller_model() -> None:
    """Enrollment contracts expose public commitments, never signing secrets."""
    from envresearch.benchmarks.blind_authority import HumanKeyEnrollment

    private = Ed25519PrivateKey.generate()
    with pytest.raises(ValidationError):
        HumanKeyEnrollment(
            case_id="case-1",
            role="expert",
            slot=1,
            principal_id="expert-one",
            key_id="key-one",
            public_key="A" * 43,
            private_key=private,
        )

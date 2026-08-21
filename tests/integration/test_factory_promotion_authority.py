"""Promotion concurrency, protected authority, and rollback boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_factory_promotion import _capability, _decision
from test_factory_run import connected_factory

from envresearch.factory.errors import FactoryAuthorityInvalid, FactoryIntegrityInvalid
from envresearch.models.enums import GateStatus


def test_identical_request_and_decision_writers_converge(
    tmp_path: Path,
) -> None:
    """Catch idempotent retries producing multiple exact contexts or promotions."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        first_context = service.request_promotion(run_ref, requested_by="factory-agent")
        assert (
            service.request_promotion(run_ref, requested_by="factory-agent")
            == first_context
        )
        decision = _decision()
        first_promotion = service.record_promotion(
            first_context, decision, principal_capability=_capability(fixture)
        )
        assert (
            service.record_promotion(
                first_context, decision, principal_capability=_capability(fixture)
            )
            == first_promotion
        )
    finally:
        fixture.close()


def test_conflicting_request_and_decision_writers_fail_closed(
    tmp_path: Path,
) -> None:
    """Catch a pending intent or terminal decision being overwritten by a peer."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        with pytest.raises(FactoryIntegrityInvalid, match="conflict"):
            service.request_promotion(run_ref, requested_by="other-agent")
        approved = _decision()
        promotion_ref = service.record_promotion(
            context_ref, approved, principal_capability=_capability(fixture)
        )
        rejected = approved.model_copy(update={"status": GateStatus.REJECTED})
        with pytest.raises(FactoryIntegrityInvalid, match="terminal"):
            service.record_promotion(
                context_ref, rejected, principal_capability=_capability(fixture)
            )
        assert service.promotion_status(promotion_ref, run_ref).state == "promoted"
    finally:
        fixture.close()


def test_raw_principal_store_mutation_invalidates_decision_authority(
    tmp_path: Path,
) -> None:
    """Catch possession of a bearer token bypassing the protected assignment MAC."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        capability = _capability(fixture)
        principal_path = (
            fixture.orchestrators[0].queue.control.path / "principals/gate.json"
        )
        data = principal_path.read_bytes()
        principal_path.write_bytes(data.replace(b"human-reviewer", b"human-reviewex"))

        with pytest.raises(FactoryAuthorityInvalid) as caught:
            service.record_promotion(
                context_ref, _decision(), principal_capability=capability
            )
        assert caught.value.code == "FACTORY_AUTHORITY_INVALID"
        assert caught.value.finding_kind == "promotion-principal-invalid"
    finally:
        fixture.close()


def test_committed_retry_revalidates_protected_promotion_authority(
    tmp_path: Path,
) -> None:
    """Catch an idempotent retry bypassing a missing protected decision anchor."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        decision = _decision()
        service.record_promotion(
            context_ref, decision, principal_capability=_capability(fixture)
        )
        anchor = service._promotions.principals.control.path / (
            "principals/factory-promotions/decisions/"
            f"{context_ref.artifact_id}.json"
        )
        anchor.unlink()

        with pytest.raises(FactoryIntegrityInvalid, match="authentication"):
            service.record_promotion(
                context_ref, decision, principal_capability=_capability(fixture)
            )
    finally:
        fixture.close()


def test_promotion_final_window_failure_restores_only_installed_pointers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch post-commit authority failure leaving an accepted partial decision."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        original = service._promotions._validate_promotion_locked
        calls = 0

        def validate(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = original(*args, **kwargs)
            if calls == 2:
                raise FactoryIntegrityInvalid(
                    "injected final mutation", finding_kind="promotion-final-invalid"
                )
            return result

        monkeypatch.setattr(service._promotions, "_validate_promotion_locked", validate)
        with pytest.raises(FactoryIntegrityInvalid):
            service.record_promotion(
                context_ref, _decision(), principal_capability=_capability(fixture)
            )

        assert service._promotions.store.promotion_prepared() is None
        assert service._promotions.store.promotion_committed() is None
        assert service.store.current() == run_ref
    finally:
        fixture.close()


@pytest.mark.parametrize("subject_kind", ("context", "promotion"))
def test_pointer_write_then_raise_is_recognized_as_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    subject_kind: str,
) -> None:
    """Catch an acknowledged exact pointer write being treated as an unknown failure."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        store = service._promotions.store
        original = store.registry.set_current
        targets = (
            {store.context_prepared_subject, store.context_committed_subject}
            if subject_kind == "context"
            else {store.promotion_prepared_subject, store.promotion_committed_subject}
        )
        raised: set[str] = set()

        def write_then_raise(subject, reference):
            original(subject, reference)
            if subject in targets and subject not in raised:
                raised.add(subject)
                raise OSError("injected post-write failure")

        monkeypatch.setattr(store.registry, "set_current", write_then_raise)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        if subject_kind == "promotion":
            promotion_ref = service.record_promotion(
                context_ref, _decision(), principal_capability=_capability(fixture)
            )
            assert service.promotion_status(promotion_ref, run_ref).state == "promoted"
        assert raised == targets
    finally:
        fixture.close()

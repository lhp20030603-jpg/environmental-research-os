"""Fresh request-authority validation at accepted promotion boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_factory_promotion import _capability, _decision
from test_factory_run import connected_factory

from envresearch.factory.errors import FactoryIntegrityInvalid


def test_record_final_window_reopens_request_anchor_and_recovers_exact_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a committed promotion returning after its request anchor disappears."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        decision = _decision()
        promotions = service._promotions
        relative = Path("principals/factory-promotions/requests") / (
            f"{context_ref.artifact_id}.json"
        )
        control = promotions.principals.control
        anchor = control.path / relative
        saved = anchor.read_bytes()
        original = promotions.store.install

        def remove_after_commit(subject, reference, *, previous):
            original(subject, reference, previous=previous)
            if subject == promotions.store.promotion_committed_subject:
                anchor.unlink()

        monkeypatch.setattr(promotions.store, "install", remove_after_commit)
        with pytest.raises(FactoryIntegrityInvalid) as caught:
            service.record_promotion(
                context_ref, decision, principal_capability=_capability(fixture)
            )
        assert caught.value.code == "FACTORY_INTEGRITY_INVALID"
        assert caught.value.finding_kind == "promotion-request-invalid"
        assert promotions.store.promotion_prepared() is None
        assert promotions.store.promotion_committed() is None
        terminal_ref = promotions.store.terminal_ref(context_ref)
        assert terminal_ref is not None
        terminal = promotions.store.load_promotion(terminal_ref)
        promotions.store.require_decision_event(terminal_ref, terminal)

        control.storage.write_file_noreplace(relative, saved, mode=0o600)
        monkeypatch.setattr(promotions.store, "install", original)
        recovered = service.record_promotion(
            context_ref, decision, principal_capability=_capability(fixture)
        )
        assert recovered == terminal_ref
        assert service.promotion_status(recovered, run_ref).state == "promoted"
    finally:
        fixture.close()


@pytest.mark.parametrize("operation", ("status", "idempotent-record"))
def test_accepted_paths_reopen_request_anchor_instead_of_using_cached_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Catch status or retry accepting an event whose protected anchor vanished."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        decision = _decision()
        promotion_ref = service.record_promotion(
            context_ref, decision, principal_capability=_capability(fixture)
        )
        promotions = service._promotions
        relative = Path("principals/factory-promotions/requests") / (
            f"{context_ref.artifact_id}.json"
        )
        control = promotions.principals.control
        anchor = control.path / relative
        saved = anchor.read_bytes()
        original = promotions.store.require_request_event
        removed = False

        def read_then_remove(*args, **kwargs):
            nonlocal removed
            event = original(*args, **kwargs)
            if not removed:
                anchor.unlink()
                removed = True
            return event

        monkeypatch.setattr(
            promotions.store, "require_request_event", read_then_remove
        )
        with pytest.raises(FactoryIntegrityInvalid) as caught:
            if operation == "status":
                service.promotion_status(promotion_ref, run_ref)
            else:
                service.record_promotion(
                    context_ref, decision, principal_capability=_capability(fixture)
                )
        assert caught.value.finding_kind == "promotion-request-invalid"

        control.storage.write_file_noreplace(relative, saved, mode=0o600)
        monkeypatch.setattr(promotions.store, "require_request_event", original)
        assert service.record_promotion(
            context_ref, decision, principal_capability=_capability(fixture)
        ) == promotion_ref
        assert service.promotion_status(promotion_ref, run_ref).state == "promoted"
    finally:
        fixture.close()

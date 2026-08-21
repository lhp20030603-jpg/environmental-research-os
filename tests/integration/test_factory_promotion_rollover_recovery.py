"""Crash recovery while promotion generations roll forward."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_factory_promotion import _capability, _decision
from test_factory_run import connected_factory

from envresearch.factory.errors import FactoryAuthorityInvalid, FactoryIntegrityInvalid
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import GateStatus


def test_context_rollover_resumes_exact_prepared_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch gen2 context preflight rejecting its crashed prepared pointer."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        store = service._promotions.store
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        first_context = service.request_promotion(run_ref, "factory-agent")
        rejected = service.record_promotion(
            first_context,
            _decision(GateStatus.REJECTED),
            principal_capability=_capability(fixture),
        )
        original = store.install

        def crash_after_prepare(
            subject: str,
            reference: ArtifactRef,
            *,
            previous: ArtifactRef | None,
        ) -> None:
            original(subject, reference, previous=previous)
            if subject == store.context_prepared_subject and reference != previous:
                raise SystemExit(95)

        monkeypatch.setattr(store, "install", crash_after_prepare)
        with pytest.raises(SystemExit, match="95"):
            service.request_promotion(run_ref, "factory-agent")
        prepared = store.context_prepared()
        assert prepared is not None and prepared != first_context
        assert store.context_committed() == first_context
        assert store.promotion_prepared() == store.promotion_committed() == rejected

        monkeypatch.setattr(store, "install", original)
        recovered = service.request_promotion(run_ref, "factory-agent")

        assert recovered == prepared
        assert store.context_current() == recovered
        assert store.load_context(recovered).generation == 2
        assert service.promotion_status(rejected, run_ref).state == "promotion-rejected"
    finally:
        fixture.close()


def test_context_rollover_requires_prior_rejection_principal_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a torn gen2 context bypassing its rejected principal authority."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        store = service._promotions.store
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        first_context = service.request_promotion(run_ref, "factory-agent")
        rejected = service.record_promotion(
            first_context,
            _decision(GateStatus.REJECTED),
            principal_capability=_capability(fixture),
        )
        original = store.install

        def crash_after_prepare(
            subject: str,
            reference: ArtifactRef,
            *,
            previous: ArtifactRef | None,
        ) -> None:
            original(subject, reference, previous=previous)
            if subject == store.context_prepared_subject and reference != previous:
                raise SystemExit(97)

        monkeypatch.setattr(store, "install", crash_after_prepare)
        with pytest.raises(SystemExit, match="97"):
            service.request_promotion(run_ref, "factory-agent")
        prepared = store.context_prepared()
        principal = fixture.orchestrators[0].queue.control.path / "principals/gate.json"
        principal.unlink()

        monkeypatch.setattr(store, "install", original)
        with pytest.raises(FactoryAuthorityInvalid) as caught:
            service.request_promotion(run_ref, "factory-agent")

        assert caught.value.finding_kind == "promotion-principal-invalid"
        assert store.context_prepared() == prepared
        assert store.context_committed() == first_context
        assert store.promotion_current() == rejected
        assert not principal.exists()
    finally:
        fixture.close()


def test_promotion_rollover_resumes_exact_prepared_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch gen2 promotion preflight rejecting its crashed prepared pointer."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        store = service._promotions.store
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        first_context = service.request_promotion(run_ref, "factory-agent")
        rejected = service.record_promotion(
            first_context,
            _decision(GateStatus.REJECTED),
            principal_capability=_capability(fixture),
        )
        second_context = service.request_promotion(run_ref, "factory-agent")
        approved_decision = _decision(GateStatus.APPROVED)
        capability = _capability(fixture)
        original = store.install

        def crash_after_prepare(
            subject: str,
            reference: ArtifactRef,
            *,
            previous: ArtifactRef | None,
        ) -> None:
            original(subject, reference, previous=previous)
            if subject == store.promotion_prepared_subject and reference != previous:
                raise SystemExit(96)

        monkeypatch.setattr(store, "install", crash_after_prepare)
        with pytest.raises(SystemExit, match="96"):
            service.record_promotion(
                second_context,
                approved_decision,
                principal_capability=capability,
            )
        prepared = store.promotion_prepared()
        assert prepared is not None and prepared != rejected
        assert store.promotion_committed() == rejected
        assert store.context_prepared() == store.context_committed() == second_context

        monkeypatch.setattr(store, "install", original)
        with pytest.raises(FactoryIntegrityInvalid, match="terminal"):
            service.record_promotion(
                second_context,
                _decision(GateStatus.REJECTED),
                principal_capability=capability,
            )
        assert store.promotion_prepared() == prepared
        assert store.promotion_committed() == rejected

        recovered = service.record_promotion(
            second_context,
            approved_decision,
            principal_capability=capability,
        )

        assert recovered == prepared
        assert store.promotion_current() == recovered
        assert store.terminal_ref(first_context) == rejected
        assert store.terminal_ref(second_context) == recovered
        assert service.promotion_status(rejected, run_ref).state == "promotion-rejected"
        assert service.promotion_status(recovered, run_ref).state == "promoted"
    finally:
        fixture.close()

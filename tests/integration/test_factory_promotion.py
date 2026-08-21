"""Public independent human promotion over one genuine governed run."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_factory_run import connected_factory

from envresearch.factory.errors import (
    FactoryAuthorityInvalid,
    FactoryIntegrityInvalid,
    FactoryScopeExceeded,
)
from envresearch.factory.promotion_contracts import FactoryPromotionContext
from envresearch.kernel.gates import GateDecision
from envresearch.models.enums import GateStatus, WorkflowStatus


def _capability(fixture) -> str:
    return (
        fixture.orchestrators[0]
        .queue.control.storage.read_file(
            Path("principals/gate.capability"),
            description="test gate capability",
            required_mode=0o600,
        )
        .decode()
    )


def _decision(
    status: GateStatus = GateStatus.APPROVED,
    *,
    decided_by: str = "human-reviewer",
    decided_at: datetime | None = None,
    conditions: dict[str, object] | None = None,
) -> GateDecision:
    return GateDecision(
        status=status,
        decided_by=decided_by,
        rationale="Reviewed the exact retrospective run and its limitations.",
        conditions=conditions or {},
        decided_at=decided_at or datetime.now(UTC) + timedelta(seconds=1),
    )


def test_facade_requests_and_records_one_exact_independent_approval(
    tmp_path: Path,
) -> None:
    """Catch facade bypass, detached decisions, or release-boundary inflation."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        decision = _decision()
        promotion_ref = service.record_promotion(
            context_ref,
            decision,
            principal_capability=_capability(fixture),
        )
        status = service.promotion_status(promotion_ref, run_ref)
        context = service._promotions.store.load_context(context_ref)
        promotion = service._promotions.store.load_promotion(promotion_ref)

        assert status.state == "promoted"
        assert status.run_ref == run_ref
        assert context.run == status.run
        assert context.hidden_evaluation_status == "not-run"
        assert context.product_release_status == "scientific_release_pending"
        assert "requested_at" not in FactoryPromotionContext.model_fields
        assert promotion.decision.decided_at == decision.decided_at
        assert promotion.context_ref == context_ref
    finally:
        fixture.close()


def test_rejection_is_terminal_and_next_request_uses_new_generation(
    tmp_path: Path,
) -> None:
    """Catch rejected context mutation or a rejected promotion changing the run."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        first = service.request_promotion(run_ref, requested_by="factory-agent")
        rejected_ref = service.record_promotion(
            first,
            _decision(GateStatus.REJECTED),
            principal_capability=_capability(fixture),
        )

        assert (
            service.promotion_status(rejected_ref, run_ref).state
            == "promotion-rejected"
        )
        second = service.request_promotion(run_ref, requested_by="factory-agent")
        assert second != first
        assert service._promotions.store.load_context(second).generation == 2
        assert service.store.current() == run_ref
    finally:
        fixture.close()


@pytest.mark.parametrize(
    "decided_by",
    ("factory-agent", "research-factory-run-v1"),
)
def test_requester_or_run_producer_cannot_decide(
    tmp_path: Path, decided_by: str
) -> None:
    """Catch caller labels bypassing independent human authority checks."""
    fixture = connected_factory(tmp_path)
    try:
        run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = fixture.service.request_promotion(
            run_ref, requested_by="factory-agent"
        )
        with pytest.raises(FactoryAuthorityInvalid) as caught:
            fixture.service.record_promotion(
                context_ref,
                _decision(decided_by=decided_by),
                principal_capability=_capability(fixture),
            )
        assert caught.value.code == "FACTORY_AUTHORITY_INVALID"
        assert caught.value.finding_kind == "promotion-principal-independent"
    finally:
        fixture.close()


def test_record_rejects_forged_capability_predated_or_broadened_decision(
    tmp_path: Path,
) -> None:
    """Catch unauthenticated, temporally impossible, or product-expanding approval."""
    fixture = connected_factory(tmp_path)
    try:
        run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = fixture.service.request_promotion(
            run_ref, requested_by="factory-agent"
        )
        with pytest.raises(FactoryAuthorityInvalid) as capability:
            fixture.service.record_promotion(
                context_ref, _decision(), principal_capability="00"
            )
        assert capability.value.code == "FACTORY_AUTHORITY_INVALID"
        assert capability.value.finding_kind == "promotion-principal-invalid"
        with pytest.raises(FactoryAuthorityInvalid) as time:
            fixture.service.record_promotion(
                context_ref,
                _decision(decided_at=datetime(2000, 1, 1, tzinfo=UTC)),
                principal_capability=_capability(fixture),
            )
        assert time.value.code == "FACTORY_AUTHORITY_INVALID"
        assert time.value.finding_kind == "promotion-decision-time"
        with pytest.raises(FactoryScopeExceeded) as scope:
            fixture.service.record_promotion(
                context_ref,
                _decision(conditions={"product_release": "approved"}),
                principal_capability=_capability(fixture),
            )
        assert scope.value.code == "FACTORY_SCOPE_EXCEEDED"
        assert scope.value.finding_kind == "promotion-scope"
    finally:
        fixture.close()


def test_promotion_rejects_stale_run_and_changed_context_after_decision(
    tmp_path: Path,
) -> None:
    """Catch exact promotion remaining accepted after either bound authority changes."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        promotion_ref = service.record_promotion(
            context_ref, _decision(), principal_capability=_capability(fixture)
        )
        path = service._promotions.store.context_object_path(context_ref)
        data = service.registry.files.read(path)
        service.registry.files.write(
            path, data.replace(b"factory-agent", b"factory-agenu")
        )

        with pytest.raises(FactoryIntegrityInvalid):
            service.promotion_status(promotion_ref, run_ref)
    finally:
        fixture.close()


def test_status_rejects_a_decision_event_with_changed_transition(
    tmp_path: Path,
) -> None:
    """Catch partial event checks accepting a changed terminal transition."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        promotion_ref = service.record_promotion(
            context_ref, _decision(), principal_capability=_capability(fixture)
        )
        event_id = f"{context_ref.artifact_id}.decided"
        events = [
            event.model_copy(update={"to_status": WorkflowStatus.REJECTED})
            if event.event_id == event_id
            else event
            for event in service._promotions.store.events.read_all()
        ]
        service._promotions.store.events.path.write_text(
            "".join(f"{event.model_dump_json()}\n" for event in events)
        )

        with pytest.raises(FactoryIntegrityInvalid, match="event"):
            service.promotion_status(promotion_ref, run_ref)
    finally:
        fixture.close()


def test_status_reports_malformed_event_log_as_typed_integrity_failure(
    tmp_path: Path,
) -> None:
    """Catch truncated append-only history escaping the factory error boundary."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        promotion_ref = service.record_promotion(
            context_ref, _decision(), principal_capability=_capability(fixture)
        )
        service._promotions.store.events.path.write_text("{malformed\n")

        with pytest.raises(FactoryIntegrityInvalid) as caught:
            service.promotion_status(promotion_ref, run_ref)
        assert caught.value.code == "FACTORY_INTEGRITY_INVALID"
        assert caught.value.finding_kind == "promotion-event-log-invalid"
    finally:
        fixture.close()


@pytest.mark.parametrize("mutation", ("missing", "corrupt"))
def test_status_reports_principal_corruption_as_typed_invalid_authority(
    tmp_path: Path, mutation: str
) -> None:
    """Catch read-side human authority failures escaping as builtin errors."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        promotion_ref = service.record_promotion(
            context_ref, _decision(), principal_capability=_capability(fixture)
        )
        principal = fixture.orchestrators[0].queue.control.path / "principals/gate.json"
        if mutation == "missing":
            principal.unlink()
        else:
            principal.write_bytes(
                principal.read_bytes().replace(b"human-reviewer", b"human-reviewex")
            )

        with pytest.raises(FactoryAuthorityInvalid) as caught:
            service.promotion_status(promotion_ref, run_ref)
        assert caught.value.code == "FACTORY_AUTHORITY_INVALID"
        assert caught.value.finding_kind == "promotion-principal-invalid"
        assert principal.exists() is (mutation == "corrupt")
    finally:
        fixture.close()


@pytest.mark.parametrize("phase", ("request", "decision"))
def test_event_identity_collision_is_typed_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Catch append-only identity conflicts escaping or leaving torn pointers."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = None
        if phase == "decision":
            context_ref = service.request_promotion(
                run_ref, requested_by="factory-agent"
            )

        def collide(*_args, **_kwargs):
            raise RuntimeError("event identity collision")

        monkeypatch.setattr(
            "envresearch.factory._promotion_events.append_event_once", collide
        )
        with pytest.raises(FactoryIntegrityInvalid, match="event"):
            if context_ref is None:
                service.request_promotion(run_ref, requested_by="factory-agent")
            else:
                service.record_promotion(
                    context_ref, _decision(), principal_capability=_capability(fixture)
                )
        if phase == "request":
            assert service._promotions.store.context_prepared() is None
            assert service._promotions.store.context_committed() is None
        else:
            assert service._promotions.store.promotion_prepared() is None
            assert service._promotions.store.promotion_committed() is None
    finally:
        fixture.close()


@pytest.mark.parametrize("phase", ("context", "promotion"))
def test_promotion_object_publish_failure_is_typed_and_leaves_no_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Catch immutable-object publication failure leaking an untyped exception."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = None
        if phase == "promotion":
            context_ref = service.request_promotion(
                run_ref, requested_by="factory-agent"
            )

        def fail_publish(*_args, **_kwargs):
            raise OSError("injected immutable publish failure")

        monkeypatch.setattr(service._promotions.store.registry, "publish", fail_publish)
        with pytest.raises(FactoryIntegrityInvalid, match="publication"):
            if context_ref is None:
                service.request_promotion(run_ref, requested_by="factory-agent")
            else:
                service.record_promotion(
                    context_ref, _decision(), principal_capability=_capability(fixture)
                )
        if phase == "context":
            assert service._promotions.store.context_prepared() is None
            assert service._promotions.store.context_committed() is None
        else:
            assert service._promotions.store.promotion_prepared() is None
            assert service._promotions.store.promotion_committed() is None
        assert service.store.current() == run_ref
    finally:
        fixture.close()


@pytest.mark.parametrize("phase", ("context", "promotion"))
def test_compare_and_restore_preserves_a_foreign_pointer(
    tmp_path: Path, phase: str
) -> None:
    """Catch recovery overwriting a pointer already replaced by another writer."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        promotion_ref = service.record_promotion(
            context_ref, _decision(), principal_capability=_capability(fixture)
        )
        subject, installed = (
            (service._promotions.store.context_prepared_subject, context_ref)
            if phase == "context"
            else (service._promotions.store.promotion_prepared_subject, promotion_ref)
        )
        service._promotions.store.registry.set_current(subject, run_ref)

        with pytest.raises(FactoryIntegrityInvalid, match="lost pointer ownership"):
            service._promotions.store.compare_and_restore(
                subject, installed=installed, previous=None
            )
        assert service._promotions.store.registry.current(subject) == run_ref
    finally:
        fixture.close()


def test_request_rejects_a_stale_current_run(tmp_path: Path) -> None:
    """Catch a context being issued after the exact run current pointer is torn."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        service.registry.files.unlink(
            Path("exit/current") / f"{service.store.committed_subject}.json"
        )

        with pytest.raises(FactoryIntegrityInvalid):
            service.request_promotion(run_ref, requested_by="factory-agent")
    finally:
        fixture.close()

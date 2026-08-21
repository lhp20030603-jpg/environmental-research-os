"""Controller-review regressions for exact promotion authority."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from test_factory_promotion import _capability, _decision
from test_factory_run import connected_factory

import envresearch.factory as factory_package
from envresearch.factory.errors import FactoryAuthorityInvalid, FactoryError
from envresearch.factory.service import FactoryRunService
from envresearch.models.enums import GateStatus


def _internal(service: FactoryRunService):
    return service._promotions


def _object_path(reference) -> Path:
    return (
        Path("exit/objects")
        / reference.artifact_id
        / (f"v{reference.artifact_version}-{reference.content_hash}.json")
    )


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_request_final_window_reopens_raw_current_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a context returning after its bound run bytes change post-commit."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        promotions = _internal(service)
        original = promotions.store.install

        def install(subject, reference, *, previous):
            original(subject, reference, previous=previous)
            if subject == promotions.store.context_committed_subject:
                path = service.store.object_path(run_ref)
                data = service.registry.files.read(path)
                service.registry.files.write(path, data + b"\n")

        monkeypatch.setattr(promotions.store, "install", install)
        with pytest.raises(FactoryError):
            service.request_promotion(run_ref, requested_by="factory-agent")
        assert promotions.store.context_prepared() is None
        assert promotions.store.context_committed() is None
    finally:
        fixture.close()


def test_record_final_window_reopens_raw_upstream_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a decision returning after upstream evidence changes post-commit."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        promotions = _internal(service)
        run = service.store.load(run_ref)
        ledgers = service.release_service.audit_service.ledger_service
        path = _object_path(run.release.ledger_ref)
        original = promotions.store.install

        def install(subject, reference, *, previous):
            original(subject, reference, previous=previous)
            if subject == promotions.store.promotion_committed_subject:
                ledgers.registry.files.write(
                    path, ledgers.registry.files.read(path) + b"\n"
                )

        monkeypatch.setattr(promotions.store, "install", install)
        with pytest.raises(FactoryError):
            service.record_promotion(
                context_ref, _decision(), principal_capability=_capability(fixture)
            )
        assert promotions.store.promotion_prepared() is None
        assert promotions.store.promotion_committed() is None
    finally:
        fixture.close()


def test_opposite_decision_after_final_failure_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch rolled-back pointers allowing a second terminal context decision."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        promotions = _internal(service)
        original = promotions._validate_promotion_locked
        calls = 0

        def fail_final(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = original(*args, **kwargs)
            if calls == 2:
                raise FactoryAuthorityInvalid(
                    "injected final failure", finding_kind="injected-final"
                )
            return result

        monkeypatch.setattr(promotions, "_validate_promotion_locked", fail_final)
        with pytest.raises(FactoryAuthorityInvalid):
            service.record_promotion(
                context_ref, _decision(), principal_capability=_capability(fixture)
            )
        monkeypatch.setattr(promotions, "_validate_promotion_locked", original)
        rejected = _decision(GateStatus.REJECTED)
        with pytest.raises(FactoryError) as caught:
            service.record_promotion(
                context_ref, rejected, principal_capability=_capability(fixture)
            )
        assert caught.value.finding_kind == "promotion-terminal"
    finally:
        fixture.close()


def test_decision_time_must_be_strictly_after_request(tmp_path: Path) -> None:
    """Catch equality bypassing the authenticated request-before-decision order."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        event = _internal(service).store.require_request_event(
            context_ref, "factory-agent"
        )
        with pytest.raises(FactoryAuthorityInvalid) as caught:
            service.record_promotion(
                context_ref,
                _decision(decided_at=event.timestamp),
                principal_capability=_capability(fixture),
            )
        assert caught.value.finding_kind == "promotion-decision-time"
    finally:
        fixture.close()


@pytest.mark.parametrize("missing", ("assignment", "capability"))
def test_record_requires_existing_principal_state_without_healing(
    tmp_path: Path, missing: str
) -> None:
    """Catch capability authentication recreating deleted human authority."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        control = fixture.orchestrators[0].queue.control.path
        relative = (
            Path("principals/gate.json")
            if missing == "assignment"
            else Path("principals/gate.capability")
        )
        target = control / relative
        supplied = _capability(fixture) if missing == "assignment" else "missing"
        target.unlink()

        with pytest.raises(FactoryAuthorityInvalid) as caught:
            service.record_promotion(
                context_ref, _decision(), principal_capability=supplied
            )
        assert caught.value.finding_kind == "promotion-principal-invalid"
        assert not target.exists()
    finally:
        fixture.close()


@pytest.mark.parametrize("phase", ("request", "decision"))
def test_append_rejects_malformed_event_history_with_typed_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Catch event-log corruption escaping during append or leaving intent."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        promotions = _internal(service)
        context_ref = None
        if phase == "decision":
            context_ref = service.request_promotion(
                run_ref, requested_by="factory-agent"
            )
            original = promotions.store.ensure_decision_event

            def corrupt_then_append(reference, promotion):
                promotions.store.events.path.write_bytes(
                    promotions.store.events.path.read_bytes() + b"{malformed\n"
                )
                return original(reference, promotion)

            monkeypatch.setattr(
                promotions.store, "ensure_decision_event", corrupt_then_append
            )
        else:
            promotions.store.events.path.write_bytes(
                promotions.store.events.path.read_bytes() + b"{malformed\n"
            )

        with pytest.raises(FactoryError) as caught:
            if context_ref is None:
                service.request_promotion(run_ref, requested_by="factory-agent")
            else:
                service.record_promotion(
                    context_ref, _decision(), principal_capability=_capability(fixture)
                )
        assert caught.value.finding_kind == "promotion-event-log-invalid"
        current = (
            promotions.store.context_prepared()
            if phase == "request"
            else promotions.store.promotion_prepared()
        )
        assert current is None
    finally:
        fixture.close()


def test_service_construction_is_read_only_and_request_bootstraps_controls(
    tmp_path: Path,
) -> None:
    """Catch a fresh facade silently recreating missing promotion controls."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        promotions = _internal(service)
        protected = promotions.principals.control.path / "principals/factory-promotions"
        if protected.exists():
            shutil.rmtree(protected)
        lock_paths = tuple(
            service.registry.root / "exit/locks" / f"{subject}.lock"
            for subject in (
                promotions.store.context_prepared_subject,
                promotions.store.context_committed_subject,
                promotions.store.promotion_prepared_subject,
                promotions.store.promotion_committed_subject,
            )
        )
        for path in lock_paths:
            if path.exists():
                path.unlink()
        before_factory = _snapshot(service.registry.root)
        before_control = _snapshot(promotions.principals.control.path)

        fresh = FactoryRunService(
            design_resolver=service.design_resolver,
            release_service=service.release_service,
        )
        assert _snapshot(service.registry.root) == before_factory
        assert _snapshot(promotions.principals.control.path) == before_control
        assert not protected.exists()
        assert not any(path.exists() for path in lock_paths)
        assert fresh.status(run_ref).run_ref == run_ref
        context_ref = fresh.request_promotion(run_ref, requested_by="factory-agent")
        assert context_ref.artifact_id.startswith("factory-promotion-context-")
        assert protected.is_dir()
        assert all(path.is_file() for path in lock_paths)
    finally:
        fixture.close()


def test_factory_run_service_is_the_only_public_promotion_facade(
    tmp_path: Path,
) -> None:
    """Catch the package or facade exposing its internal promotion service."""
    fixture = connected_factory(tmp_path)
    try:
        assert "FactoryPromotionService" not in factory_package.__all__
        assert not hasattr(factory_package, "FactoryPromotionService")
        assert not hasattr(fixture.service, "promotions")
        assert hasattr(fixture.service, "_promotions")
    finally:
        fixture.close()


def test_new_context_after_rejection_has_stable_idempotent_retry(
    tmp_path: Path,
) -> None:
    """Catch a prior rejected terminal being validated against the next context."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        first = service.request_promotion(run_ref, requested_by="factory-agent")
        service.record_promotion(
            first,
            _decision(GateStatus.REJECTED),
            principal_capability=_capability(fixture),
        )
        second = service.request_promotion(run_ref, requested_by="factory-agent")

        assert (
            service.request_promotion(run_ref, requested_by="factory-agent") == second
        )
        assert _internal(service).store.load_context(second).generation == 2
    finally:
        fixture.close()


def test_rejected_generation_rolls_to_one_exact_approved_successor(
    tmp_path: Path,
) -> None:
    """Catch the prior rejected terminal blocking its approved successor."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        promotions = _internal(service)
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        first_context = service.request_promotion(run_ref, "factory-agent")
        rejected = service.record_promotion(
            first_context,
            _decision(GateStatus.REJECTED),
            principal_capability=_capability(fixture),
        )
        second_context = service.request_promotion(run_ref, "factory-agent")

        approved = service.record_promotion(
            second_context,
            _decision(GateStatus.APPROVED),
            principal_capability=_capability(fixture),
        )

        assert promotions.store.load_context(second_context).generation == 2
        assert service.promotion_status(rejected, run_ref).state == "promotion-rejected"
        assert service.promotion_status(approved, run_ref).state == "promoted"
        assert promotions.store.context_current() == second_context
        assert promotions.store.promotion_current() == approved
        assert promotions.store.terminal_ref(first_context) == rejected
        assert promotions.store.terminal_ref(second_context) == approved
    finally:
        fixture.close()

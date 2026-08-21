"""Formal-review regressions for the governed factory acceptance boundary."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from factory_event_process_fixtures import crash_factory_event_append
from factory_process_fixtures import write_config
from test_factory_concurrency import _crash
from test_factory_promotion import _capability, _decision
from test_factory_run import connected_factory

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.factory.errors import (
    FactoryAuthorityInvalid,
    FactoryError,
    FactoryIntegrityInvalid,
)
from envresearch.models.artifact import ArtifactRef


@pytest.mark.parametrize("mutation", ("missing", "replaced"))
def test_status_and_retry_require_the_sealed_promotion_capability(
    tmp_path: Path, mutation: str
) -> None:
    """Catch promotion status trusting assignment state without capability bytes."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
        capability = _capability(fixture)
        decision = _decision()
        promotion_ref = service.record_promotion(
            context_ref, decision, principal_capability=capability
        )
        path = (
            fixture.orchestrators[0].queue.control.path / "principals/gate.capability"
        )
        retry_capability = capability
        if mutation == "missing":
            path.unlink()
        else:
            retry_capability = "replacement-capability"
            path.write_text(retry_capability, encoding="utf-8")

        with pytest.raises(FactoryAuthorityInvalid) as status_error:
            service.promotion_status(promotion_ref, run_ref)
        assert status_error.value.finding_kind == "promotion-principal-invalid"

        with pytest.raises(FactoryError):
            service.record_promotion(
                context_ref, decision, principal_capability=retry_capability
            )
        assert path.exists() is (mutation == "replaced")
        if path.exists():
            assert path.read_text(encoding="utf-8") == retry_capability
        assert service._promotions.store.promotion_current() == promotion_ref
    finally:
        fixture.close()


@pytest.mark.parametrize("operation", ("run", "request", "decision"))
def test_partial_factory_event_write_recovers_the_exact_intent(
    tmp_path: Path, operation: str
) -> None:
    """Catch process death during event bytes permanently corrupting retry."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        config = write_config(service, tmp_path / "process.json")
        if operation == "run":
            refs = (fixture.design_ref, fixture.release_ref)
        else:
            run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
            refs = (run_ref,)
            if operation == "decision":
                refs = (
                    service.request_promotion(run_ref, requested_by="factory-agent"),
                )
        decision = _decision()
        _crash(
            crash_factory_event_append,
            (
                str(config),
                operation,
                json.dumps([item.model_dump(mode="json") for item in refs]),
                decision.model_dump_json() if operation == "decision" else None,
                _capability(fixture) if operation == "decision" else None,
            ),
            95,
        )
        if operation == "run":
            intent = service.store.prepared()
            recovered = service.assemble(*refs)
        elif operation == "request":
            intent = service._promotions.store.context_prepared()
            recovered = service.request_promotion(refs[0], requested_by="factory-agent")
        else:
            intent = service._promotions.store.promotion_prepared()
            recovered = service.record_promotion(
                refs[0], decision, principal_capability=_capability(fixture)
            )
        assert intent is not None
        assert recovered == intent
        event_ids = [
            event.event_id for event in service._promotions.store.events.read_all()
        ]
        assert len(event_ids) == len(set(event_ids))
    finally:
        fixture.close()


@pytest.mark.parametrize("mutation", ("artifact-id", "version"))
def test_design_prepared_recovery_rejects_wrong_reference_identity(
    tmp_path: Path, mutation: str
) -> None:
    """Catch prepared-only recovery accepting a non-v1 design handoff ref."""
    fixture = connected_factory(tmp_path)
    try:
        resolver = fixture.service.design_resolver
        handoff = resolver.resolve(fixture.design_ref)
        registry = ExitRegistry(resolver.factory_root, create=False)
        prepared = resolver._prepared_subject(handoff.design_id)
        committed = resolver._subject(handoff.design_id)
        for subject in (prepared, committed):
            registry.files.unlink(Path("exit/current") / f"{subject}.json")
        forged = fixture.design_ref.model_copy(
            update=(
                {"artifact_id": "approved-design-handoff-forged"}
                if mutation == "artifact-id"
                else {"artifact_version": 2}
            )
        )
        original_path = (
            Path("exit/objects")
            / fixture.design_ref.artifact_id
            / (
                f"v{fixture.design_ref.artifact_version}-"
                f"{fixture.design_ref.content_hash}.json"
            )
        )
        forged_path = (
            Path("exit/objects")
            / forged.artifact_id
            / (f"v{forged.artifact_version}-{forged.content_hash}.json")
        )
        registry.files.write(forged_path, registry.files.read(original_path))
        registry.set_current(prepared, forged)

        with pytest.raises(FactoryIntegrityInvalid):
            resolver.build(handoff.plan_ref, handoff.final_context_ref)
        assert registry.current(prepared) == ArtifactRef.model_validate(forged)
        assert registry.current(committed) is None
    finally:
        fixture.close()


@pytest.mark.parametrize("operation", ("status", "mutation"))
def test_factory_root_inode_swap_fails_before_composite_authority(
    tmp_path: Path, operation: str
) -> None:
    """Catch path-based factory authority accepting a copied replacement root."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
        root = service.design_resolver.factory_root
        displaced = root.parent / f"{root.name}-displaced"
        root.rename(displaced)
        shutil.copytree(displaced, root)

        with pytest.raises(FactoryAuthorityInvalid) as caught:
            if operation == "status":
                service.status(run_ref)
            else:
                service.request_promotion(run_ref, requested_by="factory-agent")
        assert caught.value.finding_kind == "root-authority-invalid"
        assert service._promotions.store.context_prepared() is None
        assert service._promotions.store.context_committed() is None
    finally:
        fixture.close()

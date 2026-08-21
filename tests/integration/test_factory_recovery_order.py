"""Intent-first recovery before mutable upstream reconstruction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from factory_process_fixtures import crash_design, crash_operation, write_config
from test_factory_concurrency import _crash
from test_factory_promotion import _capability, _decision
from test_factory_run import connected_factory

from envresearch.factory.errors import FactoryIntegrityInvalid
from envresearch.models.artifact import ArtifactRef


def _corrupt(root: Path, relative: Path) -> None:
    path = root / relative
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b"\n")


def _object_path(reference: ArtifactRef) -> Path:
    return (
        Path("exit/objects")
        / reference.artifact_id
        / (f"v{reference.artifact_version}-{reference.content_hash}.json")
    )


def _pointer_bytes(root: Path, subjects: tuple[str, str]) -> tuple[bytes | None, ...]:
    return tuple(
        path.read_bytes() if path.exists() else None
        for subject in subjects
        for path in (root / "exit/current" / f"{subject}.json",)
    )


def test_design_crash_probes_authenticated_intent_before_stale_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a torn design retry reopening stale upstream before its intent bytes."""
    fixture = connected_factory(tmp_path)
    try:
        resolver = fixture.service.design_resolver
        handoff = resolver.resolve(fixture.design_ref)
        registry = resolver._registry(create=False)
        prepared_subject = resolver._prepared_subject(handoff.design_id)
        committed_subject = resolver._subject(handoff.design_id)
        for subject in (prepared_subject, committed_subject):
            (registry.root / "exit/current" / f"{subject}.json").unlink()
        config = write_config(fixture.service, tmp_path / "process.json")
        _crash(
            crash_design,
            (
                str(config),
                handoff.plan_ref.model_dump_json(),
                handoff.final_context_ref.model_dump_json(),
                "prepared",
            ),
            92,
        )
        prepared = registry.current(prepared_subject)
        assert prepared is not None and registry.current(committed_subject) is None
        subjects = (prepared_subject, committed_subject)
        before = _pointer_bytes(registry.root, subjects)
        _corrupt(registry.root, _object_path(prepared))

        def stale(*args: object, **kwargs: object) -> object:
            raise AssertionError("upstream reopened before design intent probe")

        monkeypatch.setattr(resolver, "_reopen", stale)
        with pytest.raises(FactoryIntegrityInvalid):
            resolver.build(handoff.plan_ref, handoff.final_context_ref)
        assert _pointer_bytes(registry.root, subjects) == before
    finally:
        fixture.close()


@pytest.mark.parametrize("operation", ("run", "context", "promotion"))
def test_writer_crash_probes_authenticated_intent_before_stale_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Catch run/context/promotion retry reopening upstream before torn intent."""
    fixture = connected_factory(tmp_path)
    try:
        service = fixture.service
        config = write_config(service, tmp_path / "process.json")
        decision = _decision(decided_at=datetime.now(UTC) + timedelta(minutes=1))
        if operation == "run":
            refs = (fixture.design_ref, fixture.release_ref)
            store = service.store
            subjects = (store.prepared_subject, store.committed_subject)
        else:
            run_ref = service.assemble(fixture.design_ref, fixture.release_ref)
            refs = (run_ref,)
            store = service._promotions.store
            subjects = (
                (store.context_prepared_subject, store.context_committed_subject)
                if operation == "context"
                else (
                    store.promotion_prepared_subject,
                    store.promotion_committed_subject,
                )
            )
            if operation == "promotion":
                refs = (service.request_promotion(run_ref, "factory-agent"),)
        _crash(
            crash_operation,
            (
                str(config),
                operation,
                "prepared",
                "[" + ",".join(item.model_dump_json() for item in refs) + "]",
                decision.model_dump_json() if operation == "promotion" else None,
                _capability(fixture) if operation == "promotion" else None,
            ),
            92,
        )
        prepared = store.registry.current(subjects[0])
        assert prepared is not None and store.registry.current(subjects[1]) is None
        before = _pointer_bytes(store.registry.root, subjects)
        _corrupt(store.registry.root, _object_path(prepared))

        def stale(*args: object, **kwargs: object) -> object:
            raise AssertionError("upstream reopened before writer intent probe")

        if operation == "run":
            monkeypatch.setattr(service.design_resolver, "resolve", stale)
            invoke = lambda: service.assemble(*refs)
        else:
            monkeypatch.setattr(service, "_status_locked", stale)
            invoke = (
                (lambda: service.request_promotion(refs[0], "factory-agent"))
                if operation == "context"
                else lambda: service.record_promotion(
                    refs[0], decision, principal_capability=_capability(fixture)
                )
            )
        with pytest.raises(FactoryIntegrityInvalid):
            invoke()
        assert _pointer_bytes(store.registry.root, subjects) == before
    finally:
        fixture.close()

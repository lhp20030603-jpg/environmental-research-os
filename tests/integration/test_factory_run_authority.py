"""Factory authority ordering and fail-closed immutable-store attacks."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from test_factory_run import connected_factory

from envresearch.factory.design_resolver import V02ApprovedDesignResolver
from envresearch.factory.errors import (
    FactoryAuthorityInvalid,
    FactoryError,
    FactoryIntegrityInvalid,
)
from envresearch.factory.service import FactoryRunService
from envresearch.models.artifact import ArtifactRef


def test_factory_authority_fails_closed_when_design_lease_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch assembly proceeding without the first V0.2 research authority."""
    fixture = connected_factory(tmp_path)

    @contextmanager
    def unavailable():
        raise OSError("design lease unavailable")
        yield

    try:
        monkeypatch.setattr(fixture.service.design_resolver, "authority_lease", unavailable)
        with pytest.raises(FactoryAuthorityInvalid):
            fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        assert fixture.service.store.prepared() is None
        assert fixture.service.store.committed() is None
    finally:
        fixture.close()


def test_status_rejects_torn_pointer_pair_without_repair(
    tmp_path: Path,
) -> None:
    """Catch read-only status exposing or healing a prepared-only run."""
    fixture = connected_factory(tmp_path)
    try:
        reference = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        fixture.service.registry.files.unlink(
            Path("exit/current")
            / f"{fixture.service.store.committed_subject}.json"
        )

        with pytest.raises(FactoryIntegrityInvalid):
            fixture.service.status(reference)

        assert fixture.service.store.prepared() == reference
        assert fixture.service.store.committed() is None
    finally:
        fixture.close()


@pytest.mark.parametrize("attack", ("bytes", "noncanonical", "wrong-reference"))
def test_status_rejects_immutable_object_attacks(
    tmp_path: Path, attack: str
) -> None:
    """Catch one-read loading that trusts swapped, noncanonical, or wrong-ID bytes."""
    fixture = connected_factory(tmp_path)
    try:
        reference = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        path = fixture.service.store.object_path(reference)
        data = fixture.service.registry.files.read(path)
        if attack == "bytes":
            replacement = data.replace(b'"assembled"', b'"assemblex"')
            fixture.service.registry.files.write(path, replacement)
        elif attack == "noncanonical":
            fixture.service.registry.files.write(path, data + b"\n")
        else:
            wrong = ArtifactRef(
                artifact_id="factory-run-" + "0" * 64,
                artifact_version=2,
                content_hash="0" * 64,
            )
            fixture.service.registry.set_current(
                fixture.service.store.prepared_subject, wrong
            )

        with pytest.raises(FactoryIntegrityInvalid):
            fixture.service.status(reference)
    finally:
        fixture.close()


def test_conflicting_prepared_intent_is_not_overwritten(tmp_path: Path) -> None:
    """Catch recovery replacing an authenticated different pending assembly."""
    fixture = connected_factory(tmp_path)
    try:
        reference = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        foreign = ArtifactRef(
            artifact_id="factory-run-" + "f" * 64,
            artifact_version=1,
            content_hash=reference.content_hash,
        )
        fixture.service.registry.set_current(
            fixture.service.store.prepared_subject, foreign
        )

        with pytest.raises(FactoryIntegrityInvalid):
            fixture.service.assemble(fixture.design_ref, fixture.release_ref)
    finally:
        fixture.close()


@pytest.mark.parametrize("failed_reconstruction", (2, 3))
def test_assembly_restores_only_its_pointer_writes_after_validation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_reconstruction: int,
) -> None:
    """Catch failed pre/post-commit validation leaving a promoted partial run."""
    fixture = connected_factory(tmp_path)
    original = fixture.service._reconstruct
    calls = 0

    def reconstruct(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == failed_reconstruction:
            raise FactoryIntegrityInvalid(
                "injected upstream mutation", finding_kind="injected-mutation"
            )
        return original(*args, **kwargs)

    try:
        monkeypatch.setattr(fixture.service, "_reconstruct", reconstruct)
        with pytest.raises(FactoryIntegrityInvalid):
            fixture.service.assemble(fixture.design_ref, fixture.release_ref)

        assert fixture.service.store.prepared() is None
        assert fixture.service.store.committed() is None
    finally:
        fixture.close()


@pytest.mark.parametrize("write_subject", ("prepared", "committed"))
def test_pointer_write_then_raise_is_recognized_as_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_subject: str,
) -> None:
    """Catch an acknowledged pointer write being mistaken for a failed write."""
    fixture = connected_factory(tmp_path)
    original = fixture.service.registry.set_current
    target = getattr(fixture.service.store, f"{write_subject}_subject")
    raised = False

    def write_then_raise(subject: str, reference: ArtifactRef) -> None:
        nonlocal raised
        original(subject, reference)
        if subject == target and not raised:
            raised = True
            raise OSError("injected post-write failure")

    try:
        monkeypatch.setattr(fixture.service.registry, "set_current", write_then_raise)
        reference = fixture.service.assemble(fixture.design_ref, fixture.release_ref)

        assert raised is True
        assert fixture.service.store.current() == reference
    finally:
        fixture.close()


def test_assembly_rejects_committed_only_pointer_without_repair(tmp_path: Path) -> None:
    """Catch assembly silently healing a pointer state commit cannot produce."""
    fixture = connected_factory(tmp_path)
    try:
        reference = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        fixture.service.registry.files.unlink(
            Path("exit/current")
            / f"{fixture.service.store.prepared_subject}.json"
        )

        with pytest.raises(FactoryIntegrityInvalid):
            fixture.service.assemble(fixture.design_ref, fixture.release_ref)

        assert fixture.service.store.prepared() is None
        assert fixture.service.store.committed() == reference
    finally:
        fixture.close()


@pytest.mark.parametrize("reconstruction", (1, 2, 3))
@pytest.mark.parametrize(
    ("dimension", "expected_code"),
    (
        ("design", "FACTORY_SCOPE_EXCEEDED"),
        ("release", "FACTORY_AUTHORITY_INVALID"),
        ("transition", "FACTORY_AUTHORITY_INVALID"),
        ("output", "FACTORY_INTEGRITY_INVALID"),
    ),
)
def test_exact_upstream_mutation_fails_at_each_publication_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reconstruction: int,
    dimension: str,
    expected_code: str,
) -> None:
    """Catch exact upstream drift before publish, after prepare, or after commit."""
    fixture = connected_factory(tmp_path)
    service = fixture.service
    original_reconstruct = service._reconstruct
    calls = 0

    def reconstruct(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls != reconstruction:
            return original_reconstruct(*args, **kwargs)
        with monkeypatch.context() as patch:
            if dimension == "design":
                original_resolve = service.design_resolver.resolve

                def changed_design(reference):
                    design = original_resolve(reference)
                    estimand = design.plan.estimand
                    assert estimand is not None
                    changed = estimand.model_copy(update={"population": "outside scope"})
                    return design.model_copy(
                        update={"plan": design.plan.model_copy(update={"estimand": changed})}
                    )

                patch.setattr(service.design_resolver, "resolve", changed_design)
            elif dimension == "release":
                original_status = service.release_service._status_locked

                def changed_release(*status_args, **status_kwargs):
                    release = original_status(*status_args, **status_kwargs)
                    return release.model_copy(update={"output_refs": ()})

                patch.setattr(
                    service.release_service, "_status_locked", changed_release
                )
            else:
                ledgers = service.release_service.audit_service.ledger_service
                original_load = ledgers._load

                def changed_ledger(reference):
                    ledger = original_load(reference)
                    claim = ledger.claims[0]
                    if dimension == "transition":
                        transition_ref = ledger.transition_ref.model_copy(
                            update={"content_hash": "0" * 64}
                        )
                        claim = claim.model_copy(
                            update={"transition_ref": transition_ref}
                        )
                        return ledger.model_copy(
                            update={
                                "transition_ref": transition_ref,
                                "claims": (claim,),
                            }
                        )
                    output = claim.output_evidence[0].model_copy(
                        update={"sha256": "0" * 64}
                    )
                    claim = claim.model_copy(update={"output_evidence": (output,)})
                    return ledger.model_copy(update={"claims": (claim,)})

                patch.setattr(ledgers, "_load", changed_ledger)
            return original_reconstruct(*args, **kwargs)

    try:
        monkeypatch.setattr(service, "_reconstruct", reconstruct)
        with pytest.raises(FactoryError) as caught:
            service.assemble(fixture.design_ref, fixture.release_ref)

        assert caught.value.code == expected_code
        assert service.store.prepared() is None
        assert service.store.committed() is None
    finally:
        fixture.close()


def _authority_root(fixture, target: str) -> Path:
    manifest_name = {
        "v031": "accepted-evidence",
        "design-control": "research-control",
    }.get(target, target)
    return dict(fixture.service.authority.root_manifest.roots)[manifest_name]


def _replace_authority_root(
    fixture, target: str, replacement: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if target == "paper":
        monkeypatch.setattr(
            fixture.service.release_service.registry, "root", replacement
        )
        return
    if target == "v031":
        resolver = fixture.service.release_service.audit_service.ledger_service.resolver
        monkeypatch.setattr(resolver, "authority_root", replacement)
        return
    if target == "research":
        monkeypatch.setattr(fixture.service.design_resolver, "workspace", replacement)
        return
    if target == "citation-source-0":
        citations = fixture.service.release_service.audit_service.citation_authority
        attestations = citations.attestations
        monkeypatch.setattr(attestations, "authorized_catalog_roots", (replacement,))
        return
    orchestrator = fixture.orchestrators[0 if target == "design-control" else 1]
    if target in {"design-control", "citation-control"}:
        monkeypatch.setattr(orchestrator.queue.control.storage, "path", replacement)
    else:
        monkeypatch.setattr(orchestrator, "workspace", replacement)
        monkeypatch.setattr(
            fixture.service.release_service.audit_service.citation_authority.lifecycle,
            "workspace",
            replacement,
        )


@pytest.mark.parametrize(
    "target",
    (
        "paper", "v031", "citation", "citation-control", "design-control",
        "research", "citation-source-0",
    ),
)
@pytest.mark.parametrize("alias_kind", ("same", "nested", "symlink"))
def test_factory_service_rejects_every_authority_root_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    alias_kind: str,
) -> None:
    """Catch any mutation authority sharing, nesting, or aliasing factory state."""
    fixture = connected_factory(tmp_path)
    try:
        if alias_kind == "symlink":
            link = tmp_path / f"{target}-factory-alias"
            link.symlink_to(
                fixture.service.design_resolver.factory_root, target_is_directory=True
            )
            _replace_authority_root(fixture, target, link, monkeypatch)
            resolver = fixture.service.design_resolver
        elif target == "research":
            factory_root = fixture.service.design_resolver.factory_root
            replacement = factory_root / "nested-research"
            if alias_kind == "nested":
                replacement.mkdir()
            _replace_authority_root(
                fixture, target, factory_root if alias_kind == "same" else replacement,
                monkeypatch,
            )
            resolver = fixture.service.design_resolver
        else:
            target_root = _authority_root(fixture, target)
            factory_root = (
                target_root if alias_kind == "same" else target_root / "nested-factory"
            )
            if alias_kind == "nested":
                factory_root.mkdir()
            resolver = V02ApprovedDesignResolver(
                fixture.orchestrators[0], factory_root
            )

        factory_root = resolver.factory_root
        before = tuple(sorted(path.relative_to(factory_root) for path in factory_root.rglob("*")))
        with pytest.raises(FactoryAuthorityInvalid, match="root") as caught:
            FactoryRunService(
                design_resolver=resolver,
                release_service=fixture.service.release_service,
            )
        assert caught.value.code == "FACTORY_AUTHORITY_INVALID"
        assert caught.value.finding_kind == "root-authority-invalid"
        assert tuple(sorted(path.relative_to(factory_root) for path in factory_root.rglob("*"))) == before
    finally:
        fixture.close()


def test_factory_service_fails_closed_on_missing_authority_root(tmp_path: Path) -> None:
    """Catch an injected accepted-evidence authority with no physical identity."""
    fixture = connected_factory(tmp_path)
    resolver = fixture.service.release_service.audit_service.ledger_service.resolver
    try:
        del resolver.authority_root
        with pytest.raises(FactoryAuthorityInvalid, match="root"):
            FactoryRunService(
                design_resolver=fixture.service.design_resolver,
                release_service=fixture.service.release_service,
            )
    finally:
        fixture.close()

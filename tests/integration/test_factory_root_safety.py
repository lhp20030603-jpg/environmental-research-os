"""Read-only construction and complete physical-root separation gates."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_factory_run import connected_factory

from envresearch.factory.authority import FACTORY_RUN_LOCK_SUBJECT
from envresearch.factory.errors import FactoryAuthorityInvalid, FactoryError
from envresearch.factory.service import FactoryRunService


def _tree_state(roots: tuple[Path, ...]) -> tuple[tuple[object, ...], ...]:
    state: list[tuple[object, ...]] = []
    for root in roots:
        for path in (root, *sorted(root.rglob("*"))):
            metadata = path.lstat()
            state.append(
                (
                    str(path),
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_nlink,
                    metadata.st_uid,
                    metadata.st_mode,
                    path.read_bytes() if path.is_file() else None,
                )
            )
    return tuple(state)


def test_factory_service_construction_is_byte_and_metadata_read_only(
    tmp_path: Path,
) -> None:
    """Catch service construction chmodding roots or bootstrapping locks."""
    fixture = connected_factory(tmp_path)
    roots = tuple(path for _, path in fixture.service.authority.root_manifest.roots)
    try:
        before = _tree_state(roots)

        reopened = FactoryRunService(
            design_resolver=fixture.service.design_resolver,
            release_service=fixture.service.release_service,
        )

        assert _tree_state(roots) == before
        assert reopened.store.current() is None
    finally:
        fixture.close()


def test_existing_research_authority_opens_without_recovery_or_writes(
    tmp_path: Path,
) -> None:
    """Catch CLI composition replaying initialization instead of exact reopening."""
    fixture = connected_factory(tmp_path)
    reopened = None
    try:
        import envresearch.factory.authority as factory_authority

        roots = (
            fixture.orchestrators[0].workspace,
            fixture.orchestrators[0].queue.control_root,
        )
        before = _tree_state(roots)

        reopened = factory_authority.open_existing_research_authority(roots[0])
        resolver = fixture.service.design_resolver.__class__(
            reopened, fixture.service.registry.root
        )

        assert resolver.resolve(fixture.design_ref).design_id
        assert _tree_state(roots) == before
    finally:
        if reopened is not None:
            reopened.close()
        fixture.close()


def test_service_for_roots_composes_one_read_only_public_facade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch root composition copying services or reopening through writer APIs."""
    fixture = connected_factory(tmp_path)
    reopened = None
    try:
        import envresearch.factory._cli_composition as cli_composition
        import envresearch.factory.cli as factory_cli

        run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        roots_by_name = dict(fixture.service.authority.root_manifest.roots)
        envelope = (tmp_path / "cli-research").resolve()
        for derived in (
            envelope / "design",
            envelope / ".design.worker-queue-control",
            envelope / "citation/research",
            envelope / "citation/.research.worker-queue-control",
        ):
            derived.mkdir(parents=True)
        explicit = (
            envelope,
            roots_by_name["accepted-evidence"],
            roots_by_name["paper"],
            roots_by_name["factory"],
        )
        monkeypatch.setattr(
            cli_composition,
            "open_existing_research_authority",
            lambda root: fixture.orchestrators[0],
        )
        monkeypatch.setattr(
            cli_composition,
            "paper_service_for_roots",
            lambda *args, **kwargs: fixture.service.release_service,
        )
        before = _tree_state((*explicit, roots_by_name["research-control"]))

        reopened = factory_cli.service_for_roots(*explicit, create=False)

        assert reopened.status(run_ref).run_ref == run_ref
        assert _tree_state((*explicit, roots_by_name["research-control"])) == before
    finally:
        if reopened is not None:
            factory_cli._close(reopened)
        fixture.close()


def test_status_fails_closed_when_factory_lock_is_missing_without_recreation(
    tmp_path: Path,
) -> None:
    """Catch read-only status silently healing a missing factory authority lock."""
    fixture = connected_factory(tmp_path)
    try:
        run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        lock = (
            fixture.service.registry.root
            / "exit/locks"
            / f"{FACTORY_RUN_LOCK_SUBJECT}.lock"
        )
        lock.unlink(missing_ok=True)
        before = _tree_state(
            (
                fixture.service.design_resolver.workspace,
                fixture.service.registry.root,
            )
        )

        with pytest.raises(FactoryError):
            fixture.service.status(run_ref)

        assert not lock.exists()
        assert (
            _tree_state(
                (
                    fixture.service.design_resolver.workspace,
                    fixture.service.registry.root,
                )
            )
            == before
        )
    finally:
        fixture.close()


def test_service_construction_does_not_recreate_a_missing_factory_lock(
    tmp_path: Path,
) -> None:
    """Catch the facade constructor pre-healing state needed only by writers."""
    fixture = connected_factory(tmp_path)
    lock = (
        fixture.service.registry.root
        / "exit/locks"
        / f"{FACTORY_RUN_LOCK_SUBJECT}.lock"
    )
    try:
        lock.unlink(missing_ok=True)
        before = _tree_state((fixture.service.registry.root,))

        FactoryRunService(
            design_resolver=fixture.service.design_resolver,
            release_service=fixture.service.release_service,
        )

        assert not lock.exists()
        assert _tree_state((fixture.service.registry.root,)) == before
    finally:
        fixture.close()


def test_create_false_missing_root_fails_without_creating_any_state(
    tmp_path: Path,
) -> None:
    """Catch a read-only root factory creating a missing root or its parents."""
    import envresearch.factory.cli as factory_cli

    research, v031, paper = (
        (tmp_path / name).resolve() for name in ("research", "v031", "paper")
    )
    for root in (research, v031, paper):
        root.mkdir()
    factory = (tmp_path / "missing" / "factory").resolve()

    with pytest.raises(FactoryAuthorityInvalid):
        factory_cli.service_for_roots(research, v031, paper, factory, create=False)

    assert not factory.parent.exists()


@pytest.mark.parametrize(
    "alias_kind",
    (
        "same",
        "ancestor",
        "descendant",
        "symlink",
        "design-control",
        "citation-control",
    ),
)
def test_root_validation_rejects_explicit_and_derived_overlaps(
    tmp_path: Path, alias_kind: str
) -> None:
    """Catch lexical, physical, nested, and protected-control authority aliases."""
    import envresearch.factory.cli as factory_cli

    research = tmp_path / "research"
    v031 = tmp_path / "v031"
    paper = tmp_path / "paper"
    factory = tmp_path / "factory"
    for root in (research, v031, paper, factory):
        root.mkdir()
    design = research / "design"
    citation = research / "citation/research"
    design_control = research / ".design.worker-queue-control"
    citation_control = research / "citation/.research.worker-queue-control"
    for derived in (design, citation, design_control, citation_control):
        derived.mkdir(parents=True)
    assert factory_cli._validated_roots(research, v031, paper, factory) == (
        research.resolve(),
        v031.resolve(),
        paper.resolve(),
        factory.resolve(),
    )
    if alias_kind == "same":
        paper = v031
    elif alias_kind == "ancestor":
        paper = tmp_path
    elif alias_kind == "descendant":
        paper = v031 / "paper"
        paper.mkdir()
    elif alias_kind == "symlink":
        linked = tmp_path / "linked-paper"
        linked.symlink_to(paper, target_is_directory=True)
        paper = linked
    elif alias_kind == "design-control":
        v031 = design_control
    else:
        v031 = citation_control

    with pytest.raises(FactoryAuthorityInvalid) as caught:
        factory_cli._validated_roots(research, v031, paper, factory)

    assert caught.value.finding_kind == "root-authority-overlap"
    assert caught.value.code == "FACTORY_AUTHORITY_INVALID"


def test_read_only_protected_hardlink_is_rejected_without_healing(
    tmp_path: Path,
) -> None:
    """Catch a protected queue key with a second hardlink being trusted or replaced."""
    fixture = connected_factory(tmp_path)
    queue = None
    try:
        from envresearch.workers.queue import FilesystemWorkerQueue

        public = fixture.orchestrators[0].workspace
        control = fixture.orchestrators[0].queue.control_root
        key = control / "queue.key"
        alias = control / "queue-key-alias"
        os.link(key, alias)
        before = _tree_state((public, control))

        with pytest.raises(ValueError):
            queue = FilesystemWorkerQueue.open_existing(
                public, control_root=control, require_producer_context=True
            )

        assert _tree_state((public, control)) == before
    finally:
        if queue is not None:
            queue.close()
        fixture.close()

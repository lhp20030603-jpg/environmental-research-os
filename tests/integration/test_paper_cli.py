"""Exact-reference deterministic JSON CLI for the V0.4 release boundary."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from paper_audit_integration_support import (
    audit_service,
    forge_numeric_draft,
    publish_draft,
)
from paper_draft_integration_fixtures import build_stack
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.service import LocalAnalysisService
from envresearch.paper.errors import PaperAuthorityInvalid
from envresearch.paper.release import PaperReleaseService
from envresearch.research.citation_attestations import ProtectedCitationAttestations
from envresearch.workers.queue import FilesystemWorkerQueue


def _write_ref(path: Path, reference) -> Path:  # type: ignore[no-untyped-def]
    path.write_text(reference.model_dump_json(), encoding="utf-8")
    return path


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True)
    roots = tuple((tmp_path / name).resolve() for name in ("v031", "paper", "research"))
    for root in roots:
        root.mkdir()
    return roots  # type: ignore[return-value]


def _args(roots: tuple[Path, Path, Path]) -> list[str]:
    v031, paper, research = roots
    return [
        "--v031-root",
        str(v031),
        "--paper-root",
        str(paper),
        "--research-root",
        str(research),
    ]


def _bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_paper_build_and_status_accept_only_explicit_refs_and_emit_stable_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch latest scanning, untyped refs, nondeterministic output, or status writes."""
    stack = build_stack(tmp_path / "stack")
    try:
        draft_ref = publish_draft(stack)
        audits = audit_service(stack)
        audit_ref = audits.audit(draft_ref)
        service = PaperReleaseService(audit_service=audits)
        import envresearch.paper.cli as paper_cli

        monkeypatch.setattr(
            paper_cli, "service_for_roots", lambda *args, **kwargs: service
        )
        roots = _roots(tmp_path / "cli")
        audit_path = _write_ref(tmp_path / "audit.json", audit_ref)
        draft_path = _write_ref(tmp_path / "draft.json", draft_ref)
        runner = CliRunner()

        built = runner.invoke(
            app, ["paper", "build", str(audit_path), str(draft_path), *_args(roots)]
        )
        assert built.exit_code == 0, built.stdout
        build_payload = json.loads(built.stdout)
        release_path = tmp_path / "release.json"
        release_path.write_text(
            json.dumps(build_payload["release_reference"]), encoding="utf-8"
        )
        before = _bytes(stack.draft_service.registry.root)

        first = runner.invoke(
            app, ["paper", "status", str(release_path), *_args(roots)]
        )
        second = runner.invoke(
            app, ["paper", "status", str(release_path), *_args(roots)]
        )

        assert first.exit_code == second.exit_code == 0
        assert first.stdout == second.stdout == built.stdout
        assert json.loads(first.stdout)["release"]["verdict"] == "current-green"
        assert _bytes(stack.draft_service.registry.root) == before
    finally:
        stack.orchestrator.close()


def test_paper_build_maps_audited_non_release_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch open audit findings being mislabeled as input or success."""
    stack = build_stack(tmp_path / "stack")
    try:
        clean = publish_draft(stack)
        draft_ref = forge_numeric_draft(stack, clean)
        audits = audit_service(stack)
        audit_ref = audits.audit(draft_ref)
        service = PaperReleaseService(audit_service=audits)
        import envresearch.paper.cli as paper_cli

        monkeypatch.setattr(
            paper_cli, "service_for_roots", lambda *args, **kwargs: service
        )
        roots = _roots(tmp_path / "cli")
        result = CliRunner().invoke(
            app,
            [
                "paper",
                "build",
                str(_write_ref(tmp_path / "audit.json", audit_ref)),
                str(_write_ref(tmp_path / "draft.json", draft_ref)),
                *_args(roots),
            ],
        )

        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "PAPER_SUPPORT_INVALID"
        assert service.store.current() is None
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize("payload", ("{}", "not-json"))
def test_paper_cli_maps_malformed_explicit_reference_to_exit_two(
    tmp_path: Path, payload: str
) -> None:
    """Catch malformed input falling through to discovery or execution."""
    roots = _roots(tmp_path / "cli")
    invalid = tmp_path / "invalid.json"
    invalid.write_text(payload, encoding="utf-8")
    result = CliRunner().invoke(app, ["paper", "status", str(invalid), *_args(roots)])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "PAPER_AUTHORITY_INVALID"


def test_paper_cli_requires_reference_arguments_and_never_scans_latest() -> None:
    """Catch an implicit latest/default Paper Builder entry point."""
    result = CliRunner().invoke(app, ["paper", "build"])

    assert result.exit_code == 2
    assert json.loads(result.stdout) == {
        "error": {
            "code": "PAPER_AUTHORITY_INVALID",
            "finding_kind": "reference-input-invalid",
            "message": "explicit Paper Builder inputs are required",
        }
    }


def test_production_root_factory_reopens_status_without_writes_or_estimator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch factory-only behavior hidden by command composition test doubles."""
    stack = build_stack(tmp_path / "stack")
    try:
        draft_ref = publish_draft(stack)
        audits = audit_service(stack)
        release_ref = PaperReleaseService(audit_service=audits).build(
            audits.audit(draft_ref), draft_ref
        )
        v031_root = (tmp_path / "sealed-v031").resolve()
        v031_root.mkdir()
        paper_root = stack.draft_service.registry.root
        research_root = stack.orchestrator.workspace
        control_root = stack.orchestrator.queue.control_root
        (research_root / "research-run-config.yaml").write_bytes(b"fixture-config\n")
        import envresearch.paper.cli as paper_cli

        monkeypatch.setattr(
            paper_cli, "verify_bound_config_data", lambda data, config: None
        )

        service = paper_cli.service_for_roots(
            v031_root,
            paper_root,
            research_root,
            create=False,
            resolver_factory=lambda root: stack.ledger_service.resolver,
        )

        def forbidden_estimator(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise AssertionError("paper status must not execute an estimator")

        monkeypatch.setattr(LocalAnalysisService, "run", forbidden_estimator)
        before = {
            str(root): (_bytes(root), root.stat().st_mode)
            for root in (v031_root, paper_root, research_root, control_root)
        }

        assert service.status(release_ref).verdict == "current-green"
        assert {
            str(root): (_bytes(root), root.stat().st_mode)
            for root in (v031_root, paper_root, research_root, control_root)
        } == before
        paper_cli._close(service)
    finally:
        stack.orchestrator.close()


def test_production_root_factory_rejects_lexical_symlink(tmp_path: Path) -> None:
    """Catch root resolution erasing a caller-supplied symlink authority."""
    import envresearch.paper.cli as paper_cli

    v031 = tmp_path / "v031"
    paper = tmp_path / "paper"
    research = tmp_path / "research"
    for root in (v031, paper, research):
        root.mkdir()
    linked = tmp_path / "linked-v031"
    linked.symlink_to(v031, target_is_directory=True)

    with pytest.raises(PaperAuthorityInvalid):
        paper_cli._validated_roots(linked, paper, research)


def test_read_only_citation_open_does_not_create_chmod_or_mint_control_state(
    tmp_path: Path,
) -> None:
    """Catch status composition mutating either public or protected research roots."""
    stack = build_stack(tmp_path / "stack")
    queue = None
    try:
        public = stack.orchestrator.workspace
        control = stack.orchestrator.queue.control_root
        before = {
            str(root): (_bytes(root), root.stat().st_mode) for root in (public, control)
        }

        queue = FilesystemWorkerQueue.open_existing(
            public,
            control_root=control,
            require_producer_context=True,
        )
        attestations = ProtectedCitationAttestations.open_existing(queue)

        assert attestations.authorized_catalog_roots
        assert {
            str(root): (_bytes(root), root.stat().st_mode) for root in (public, control)
        } == before
    finally:
        if queue is not None:
            queue.close()
        stack.orchestrator.close()


def test_citation_authority_closes_queue_when_attestation_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch descriptor leakage after the queue opens but citation auth fails."""
    stack = build_stack(tmp_path / "stack")
    closed = False

    class QueueSpy:
        def close(self) -> None:
            nonlocal closed
            closed = True

    try:
        research = stack.orchestrator.workspace
        (research / "research-run-config.yaml").write_bytes(b"fixture-config\n")
        import envresearch.paper.cli as paper_cli

        monkeypatch.setattr(
            paper_cli, "verify_bound_config_data", lambda data, config: None
        )
        monkeypatch.setattr(
            FilesystemWorkerQueue,
            "open_existing",
            classmethod(lambda cls, *args, **kwargs: QueueSpy()),
        )

        def fail_attestation(cls: type, queue: object) -> object:
            del cls, queue
            raise ValueError("forged attestation")

        monkeypatch.setattr(
            ProtectedCitationAttestations,
            "open_existing",
            classmethod(fail_attestation),
        )

        with pytest.raises(PaperAuthorityInvalid):
            paper_cli._citation_authority(research)
        assert closed
    finally:
        stack.orchestrator.close()


def test_read_only_locks_never_recreate_missing_control_or_registry_state(
    tmp_path: Path,
) -> None:
    """Catch status leases using O_CREAT after opening read-only authorities."""
    stack = build_stack(tmp_path / "stack")
    queue = None
    try:
        public = stack.orchestrator.workspace
        control = stack.orchestrator.queue.control_root
        mutation = (
            control
            / "locks"
            / (f"research-mutation-{hashlib.sha256(b'run').hexdigest()}.filelock")
        )
        mutation.unlink()
        registry = stack.draft_service.registry
        subject = "paper-release-publication"
        registry_lock = registry.root / "exit/locks" / f"{subject}.lock"
        registry_lock.touch(mode=0o600)
        registry_lock.unlink()
        before = {str(root): _bytes(root) for root in (public, control, registry.root)}

        queue = FilesystemWorkerQueue.open_existing(public, control_root=control)
        with (
            pytest.raises(FileNotFoundError),
            queue.control.transaction_lock("mutation"),
        ):
            pass
        with (
            pytest.raises(FileNotFoundError),
            ExitRegistry(registry.root, create=False).lock(subject),
        ):
            pass

        assert {
            str(root): _bytes(root) for root in (public, control, registry.root)
        } == before
    finally:
        if queue is not None:
            queue.close()
        stack.orchestrator.close()


@pytest.mark.parametrize("target", ("root", "directory", "key", "key-hardlink"))
def test_read_only_control_open_rejects_unsafe_protected_metadata(
    tmp_path: Path, target: str
) -> None:
    """Catch read-only open trusting unsafe protected owner/mode/link metadata."""
    stack = build_stack(tmp_path / "stack")
    try:
        public = stack.orchestrator.workspace
        control = stack.orchestrator.queue.control_root
        key = control / "queue.key"
        if target == "root":
            control.chmod(0o755)
        elif target == "directory":
            (control / "locks").chmod(0o755)
        elif target == "key":
            key.chmod(0o644)
        else:
            os.link(key, control / "queue-key-alias")
        before = _bytes(control)

        with pytest.raises(ValueError):
            FilesystemWorkerQueue.open_existing(public, control_root=control)
        assert _bytes(control) == before
    finally:
        stack.orchestrator.close()


def test_root_validation_rejects_derived_control_root_alias(tmp_path: Path) -> None:
    """Catch a worker control authority aliasing any caller-provided root."""
    import envresearch.paper.cli as paper_cli

    research = tmp_path / "research"
    paper = tmp_path / "paper"
    control = tmp_path / ".research.worker-queue-control"
    for root in (research, paper, control):
        root.mkdir()

    with pytest.raises(PaperAuthorityInvalid):
        paper_cli._validated_roots(control, paper, research)

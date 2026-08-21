"""Real-process boundaries for the V0.3.1 valuation authority lease."""

from __future__ import annotations

from collections.abc import Callable
from multiprocessing import get_context
from pathlib import Path
from threading import Event, Thread
from typing import Any

import pytest

import envresearch.econometrics.valuation_exit_corpus as corpus_module
from envresearch.econometrics.exit_evaluator import V03ExitEvaluator
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.exit_runner import RegistryAnalysisExecutor
from envresearch.econometrics.valuation_authority import valuation_authority_lease
from envresearch.econometrics.valuation_exit_corpus import freeze_valuation_exit_corpus
from envresearch.econometrics.valuation_exit_runner import (
    ValuationExitRunner,
    ValuationRegistryAnalysisExecutor,
)
from envresearch.econometrics.valuation_transition import publish_v031_transition
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.ledger import V031AcceptedEvidenceResolver

CHAIN_SUBJECT = "valuation-v031-authority"


class _ProbeComplete(Exception):
    """Stop a writer after observing its outer lease."""


def _lock_worker(
    registry_root: str,
    subject: str,
    start: Any,
    attempting: Any,
    acquired: Any,
    release: Any,
) -> None:
    registry = ExitRegistry(Path(registry_root), create=False)
    start.wait()
    attempting.set()
    with registry.lock(subject):
        acquired.set()
        release.wait()


def _assert_operation_holds_chain_lease(
    runner: ExitRegistry,
    operation: Callable[[Callable[[], None]], None],
) -> None:
    context = get_context("spawn")
    start = context.Event()
    attempting = context.Event()
    acquired = context.Event()
    release = context.Event()
    process = context.Process(
        target=_lock_worker,
        args=(
            str(runner.root),
            CHAIN_SUBJECT,
            start,
            attempting,
            acquired,
            release,
        ),
    )

    def gate() -> None:
        start.set()
        assert attempting.wait(timeout=5)
        entered = acquired.wait(timeout=0.5)
        if entered:
            release.set()
        assert not entered
        raise _ProbeComplete

    try:
        process.start()
        with pytest.raises(_ProbeComplete):
            operation(gate)
        assert acquired.wait(timeout=5)
    finally:
        release.set()
        process.join(timeout=10)
    assert process.exitcode == 0


def _registries(tmp_path: Path) -> tuple[Path, ExitRegistry, ExitRegistry]:
    root = (tmp_path / "v031").resolve()
    root.mkdir()
    runner = ExitRegistry(root / "runner")
    evaluator = ExitRegistry(root / "evaluator")
    # Provision the global writer lock before read-only V0.3.1 authorities open it.
    with runner.lock(CHAIN_SUBJECT):
        pass
    return root, runner, evaluator


def _ref(name: str) -> ArtifactRef:
    return ArtifactRef(artifact_id=name, artifact_version=1, content_hash="a" * 64)


def test_valuation_run_writer_holds_global_chain_lease(tmp_path: Path) -> None:
    _, runner, _ = _registries(tmp_path)
    service = ValuationExitRunner(runner, object())

    def operation(gate: Callable[[], None]) -> None:
        service._runner.run = lambda reference: gate()  # type: ignore[method-assign]
        service.run(_ref("manifest"))

    _assert_operation_holds_chain_lease(runner, operation)


def test_valuation_evaluate_writer_holds_global_chain_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, runner, evaluator = _registries(tmp_path)

    def operation(gate: Callable[[], None]) -> None:
        monkeypatch.setattr(
            V03ExitEvaluator,
            "evaluate_reference",
            lambda *args, **kwargs: gate(),
        )
        from envresearch.econometrics.exit_evaluator import ValuationExitEvaluator

        ValuationExitEvaluator(runner, evaluator, object()).evaluate_reference(
            _ref("run"), _ref("catalog")
        )

    _assert_operation_holds_chain_lease(runner, operation)


def test_valuation_status_reader_holds_global_chain_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, runner, evaluator = _registries(tmp_path)

    def operation(gate: Callable[[], None]) -> None:
        monkeypatch.setattr(V03ExitEvaluator, "status", lambda *args: gate())
        from envresearch.econometrics.exit_evaluator import ValuationExitEvaluator

        ValuationExitEvaluator(runner, evaluator, object()).status(_ref("report"))

    _assert_operation_holds_chain_lease(runner, operation)


def test_valuation_analysis_writer_holds_global_chain_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, runner, _ = _registries(tmp_path)
    executor = ValuationRegistryAnalysisExecutor(runner, object())

    def operation(gate: Callable[[], None]) -> None:
        monkeypatch.setattr(RegistryAnalysisExecutor, "execute", lambda *args: gate())
        executor.execute(object())

    _assert_operation_holds_chain_lease(runner, operation)


def test_valuation_freeze_writer_holds_global_chain_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, runner, evaluator = _registries(tmp_path)

    def operation(gate: Callable[[], None]) -> None:
        monkeypatch.setattr(corpus_module, "_directory", lambda _: gate())
        freeze_valuation_exit_corpus(tmp_path.resolve(), runner, evaluator)

    _assert_operation_holds_chain_lease(runner, operation)


def test_valuation_transition_writer_holds_global_chain_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, runner, _ = _registries(tmp_path)
    pack = (tmp_path / "pack").resolve()
    pack.mkdir()

    def operation(gate: Callable[[], None]) -> None:
        original = ExitRegistry.publish

        def publish(
            registry: ExitRegistry, artifact_id: str, *args: Any, **kwargs: Any
        ):
            if artifact_id == "valuation-transition-v031":
                gate()
            return original(registry, artifact_id, *args, **kwargs)

        monkeypatch.setattr(ExitRegistry, "publish", publish)
        publish_v031_transition(
            root,
            manifest_ref=_ref("manifest"),
            run_ref=_ref("run"),
            catalog_binding_ref=_ref("binding"),
            catalog_ref=_ref("catalog"),
            report_ref=_ref("report"),
            runtime_relative_path=Path("reviewed/Rscript"),
            runtime_sha256="b" * 64,
            frozen_pack_root=pack,
            frozen_pack_hash="c" * 64,
        )

    _assert_operation_holds_chain_lease(runner, operation)


def test_valuation_resolver_holds_global_chain_lease(tmp_path: Path) -> None:
    root, runner, _ = _registries(tmp_path)
    resolver = V031AcceptedEvidenceResolver(root)

    def operation(gate: Callable[[], None]) -> None:
        with resolver.authority_lease():
            gate()

    _assert_operation_holds_chain_lease(runner, operation)


def test_chain_lease_does_not_block_unrelated_runner_subject(tmp_path: Path) -> None:
    _, runner, _ = _registries(tmp_path)
    with runner.lock(CHAIN_SUBJECT), runner.lock("valuation-unrelated"):
        assert runner.current("valuation-unrelated") is None


def test_chain_lease_is_root_keyed_reentrant_in_one_thread(tmp_path: Path) -> None:
    class CountingRegistry:
        root = tmp_path.resolve()
        entries = 0

        def lock(self, subject: str):  # type: ignore[no-untyped-def]
            from contextlib import contextmanager

            @contextmanager
            def counted():  # type: ignore[no-untyped-def]
                assert subject == CHAIN_SUBJECT
                self.entries += 1
                yield

            return counted()

    registry = CountingRegistry()
    with (
        valuation_authority_lease(registry),  # type: ignore[arg-type]
        valuation_authority_lease(registry),  # type: ignore[arg-type]
    ):
        pass

    assert registry.entries == 1


def test_chain_lease_blocks_another_thread_until_outer_exit(tmp_path: Path) -> None:
    _, runner, _ = _registries(tmp_path)
    attempting = Event()
    acquired = Event()

    def contender() -> None:
        attempting.set()
        with valuation_authority_lease(runner):
            acquired.set()

    with valuation_authority_lease(runner):
        thread = Thread(target=contender)
        thread.start()
        assert attempting.wait(timeout=1)
        assert not acquired.wait(timeout=0.1)
    assert acquired.wait(timeout=1)
    thread.join(timeout=1)
    assert not thread.is_alive()

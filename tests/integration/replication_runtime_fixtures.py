"""Fake runtime lifecycle boundaries shared by service runtime tests."""

from dataclasses import replace
from datetime import UTC, datetime

from replication_service_fixtures import FakeEngine, publish_runtime_owner

from envresearch.replication.container import ContainerCleanupError
from envresearch.replication.contracts import ReplicationRunState


class InactiveEngine(FakeEngine):
    def run(self, plan, *, on_progress=None, on_started=None, on_stopped=None):  # type: ignore[no-untyped-def]
        del on_progress
        publish_runtime_owner(plan, on_started)
        try:
            partial = plan.output_root / "partial.checkpoint"
            partial.write_text("durable progress", encoding="utf-8")
            raise TimeoutError(
                "container produced no progress within approved inactivity"
            )
        finally:
            if on_stopped is not None:
                on_stopped()


class GrowthEngine(FakeEngine):
    def run(self, plan, *, on_progress=None, on_started=None, on_stopped=None):  # type: ignore[no-untyped-def]
        return replace(
            super().run(
                plan,
                on_progress=on_progress,
                on_started=on_started,
                on_stopped=on_stopped,
            ),
            storage_bytes=8,
        )


class InvalidRuntimeEngine(FakeEngine):
    def preflight(self, profile: object):  # type: ignore[no-untyped-def]
        return replace(super().preflight(profile), version="", stdout_sha256="raw")


class BlockingGrowthEngine(FakeEngine):
    def run(self, plan, *, on_progress=None, on_started=None, on_stopped=None):  # type: ignore[no-untyped-def]
        if on_progress is None:
            raise RuntimeError("progress callback is missing")
        publish_runtime_owner(plan, on_started)
        try:
            partial = plan.output_root / "blocking-growth.bin"
            partial.write_bytes(b"growth")
            on_progress(datetime.now(UTC), 3, partial.stat().st_size)
            raise AssertionError("a paused progress callback must stop execution")
        finally:
            if on_stopped is not None:
                on_stopped()


class HardBudgetInactiveEngine(FakeEngine):
    def run(self, plan, *, on_progress=None, on_started=None, on_stopped=None):  # type: ignore[no-untyped-def]
        del on_progress
        publish_runtime_owner(plan, on_started)
        try:
            partial = plan.output_root / "over-budget.bin"
            partial.write_bytes(b"x" * (plan.budget.max_storage_bytes + 1))
            raise TimeoutError(
                "container produced no progress within approved inactivity"
            )
        finally:
            if on_stopped is not None:
                on_stopped()


class CleanupFailureEngine(FakeEngine):
    def run(self, plan, *, on_progress=None, on_started=None, on_stopped=None):  # type: ignore[no-untyped-def]
        del on_progress, on_stopped
        publish_runtime_owner(plan, on_started)
        raise ContainerCleanupError("container containment cleanup verification failed")


class CleanupOrderingEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.state_probe = None
        self.cleaned = False

    def run(self, plan, *, on_progress=None, on_started=None, on_stopped=None):  # type: ignore[no-untyped-def]
        if on_progress is None or self.state_probe is None:
            raise RuntimeError("cleanup-order test is not configured")
        publish_runtime_owner(plan, on_started)
        try:
            on_progress(datetime.now(UTC), 3, plan.budget.max_storage_bytes + 1)
        except BaseException:
            assert self.state_probe() is ReplicationRunState.RUNNING
            self.cleaned = True
            if on_stopped is not None:
                on_stopped()
            raise
        raise AssertionError("resource stop was not requested")


class SimulatedCrash(RuntimeError):
    """Test-only process loss that bypasses typed boundary handling."""


class ProcessLoss(BaseException):
    """Model abrupt service death after the runtime child has started."""


class OwnerCrashEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.contained = []

    def run(self, plan, *, on_progress=None, on_started=None, on_stopped=None):  # type: ignore[no-untyped-def]
        del on_progress, on_stopped
        if on_started is None:
            raise AssertionError("runtime ownership callback is missing")
        publish_runtime_owner(plan, on_started)
        raise ProcessLoss

    def contain(self, owner, names):  # type: ignore[no-untyped-def]
        self.contained.append((owner, names))

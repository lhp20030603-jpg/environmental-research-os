"""V0.3.1-only lease for the complete mutable valuation authority chain."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock, RLock, local

from envresearch.econometrics.exit_registry import ExitRegistry

VALUATION_AUTHORITY_SUBJECT = "valuation-v031-authority"
_LOCKS_GUARD = Lock()
_THREAD_LOCKS: dict[tuple[int, str], RLock] = {}
_LOCAL = local()


def _reset_after_fork() -> None:
    """Discard inherited thread state before the child can acquire a lease."""
    global _LOCAL, _LOCKS_GUARD, _THREAD_LOCKS
    _LOCKS_GUARD = Lock()
    _THREAD_LOCKS = {}
    _LOCAL = local()


os.register_at_fork(after_in_child=_reset_after_fork)


def _thread_lock(key: tuple[int, str]) -> RLock:
    with _LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, RLock())


@contextmanager
def valuation_authority_lease(runner: ExitRegistry) -> Iterator[None]:
    """Exclude writers cross-process while allowing same-thread composition."""
    key = (os.getpid(), str(runner.root.resolve()))
    lock = _thread_lock(key)
    with lock:
        depths: dict[tuple[int, str], int] = getattr(_LOCAL, "depths", {})
        _LOCAL.depths = depths
        depth = depths.get(key, 0)
        depths[key] = depth + 1
        try:
            if depth:
                yield
            else:
                with runner.lock(VALUATION_AUTHORITY_SUBJECT):
                    yield
        finally:
            if depth:
                depths[key] = depth
            else:
                depths.pop(key, None)


__all__ = ["VALUATION_AUTHORITY_SUBJECT", "valuation_authority_lease"]

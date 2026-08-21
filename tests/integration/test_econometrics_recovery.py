"""Local analysis publication recovery tests."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import pytest

from envresearch.econometrics._store_files import StoreFiles
from envresearch.econometrics.service import EvidenceTampered


def test_interrupted_current_publication_recovers_idempotently(local_service) -> None:
    """A sealed pending report is completed on deterministic retry."""
    local_service.service.publisher.fail_after_history = True
    try:
        local_service.service.run(local_service.spec)
    except OSError:
        pass
    local_service.service.publisher.fail_after_history = False

    reference = local_service.service.run(local_service.spec)
    report = local_service.service.status(reference)

    assert report.status == "passed"
    assert local_service.backend.calls == 1
    assert not tuple((local_service.store.root / "analyses").rglob("pending.json"))


def test_concurrent_same_analysis_executes_backend_once(local_service) -> None:
    """Cooperating callers serialize the full content-addressed transition."""
    entered = Event()
    release = Event()
    original = local_service.backend.execute

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return original(*args, **kwargs)

    local_service.backend.execute = delayed
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(local_service.service.run, local_service.spec)
        assert entered.wait(timeout=3)
        second = pool.submit(local_service.service.run, local_service.spec)
        assert not second.done()
        release.set()
        assert second.result(timeout=3) == first.result(timeout=3)

    assert local_service.backend.calls == 1


def test_current_pointer_cannot_redirect_to_another_analysis(local_service) -> None:
    """Current lookup authenticates the requested analysis identity."""
    first = local_service.service.run(local_service.spec)
    source = local_service.spec.data_path
    original = source.read_bytes()
    source.write_bytes(original.replace(b"A,2018,10", b"A,2018,20"))
    second = local_service.service.run(local_service.spec)
    source.write_bytes(original)
    first_current = (
        local_service.store.root / "analyses" / first.analysis_id / "current.json"
    )
    second_current = (
        local_service.store.root / "analyses" / second.analysis_id / "current.json"
    )
    first_current.chmod(0o644)
    first_current.write_bytes(second_current.read_bytes())

    with pytest.raises(EvidenceTampered):
        local_service.service.run(local_service.spec)


def test_different_analyses_can_create_shared_directories_concurrently(
    tmp_path, monkeypatch
) -> None:
    """A first-write mkdir race reopens and authenticates the winning directory."""
    import os

    barrier = Barrier(2)
    original_mkdir = os.mkdir

    def synchronized(path, mode=0o777, *, dir_fd=None):
        if path == "analyses":
            barrier.wait(timeout=3)
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", synchronized)
    files = StoreFiles(tmp_path / "store")
    paths = (Path("analyses/a/value.json"), Path("analyses/b/value.json"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(files.write, path, b"{}") for path in paths]
        for future in futures:
            future.result(timeout=3)

    assert tuple(files.read(path) for path in paths) == (b"{}", b"{}")

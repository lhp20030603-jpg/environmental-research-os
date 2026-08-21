"""Production-boundary regressions for the disabled external runtime."""

from pathlib import Path

import pytest

from envresearch.replication import cli as replication_cli


def test_stock_service_never_discovers_a_live_container_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production construction must stay unavailable until a later approval."""

    def unexpected_selection(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("production attempted live container discovery")

    monkeypatch.setattr(replication_cli, "select_container_engine", unexpected_selection)

    service = replication_cli._service_for_root(tmp_path)

    assert service.engine.identity == "unavailable"

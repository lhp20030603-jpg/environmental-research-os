"""Local CSV snapshot and panel-shape boundary tests."""

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from envresearch.econometrics.contracts import LocalAnalysisSpec
from envresearch.econometrics.data_snapshot import (
    LocalDataChanged,
    LocalDataInvalid,
    snapshot_csv,
)
from envresearch.storage.research_artifacts import ResearchArtifactStore

PANEL = """unit,year,emissions,first_treated,population
a,2020,10,2021,100
a,2021,8,2021,101
b,2020,12,,90
b,2021,11,,92
"""


def _spec(path: Path) -> LocalAnalysisSpec:
    return LocalAnalysisSpec.model_validate(
        {
            "schema_version": "econometrics.local-analysis.v1",
            "method_id": "did-event-study",
            "data_path": path,
            "columns": {
                "unit": "unit",
                "time": "year",
                "outcome": "emissions",
                "treatment_cohort": "first_treated",
                "covariates": ("population",),
            },
            "comparison_group": "not-yet-treated",
            "reference_period": -1,
            "inference": {
                "confidence_level": 0.95,
                "cluster_column": "unit",
                "interval_mode": "simultaneous",
                "bootstrap_seed": 20260811,
            },
            "budget": {
                "inactivity_seconds": 120,
                "max_output_bytes": 2_000_000,
                "max_workspace_bytes": 20_000_000,
            },
        }
    )


def test_snapshot_is_content_addressed_and_source_is_unchanged(tmp_path: Path) -> None:
    """The operator's CSV remains immutable while the run gets owned bytes."""
    source = tmp_path / "panel.csv"
    source.write_text(PANEL, encoding="utf-8")
    before = source.read_bytes()
    store = ResearchArtifactStore(tmp_path / "store")

    snapshot = snapshot_csv(_spec(source), store)

    digest = hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == before
    assert snapshot.sha256 == digest
    assert snapshot.reference.content_hash == digest
    assert snapshot.relative_path == Path(f"artifacts/econometrics/data/{digest}.csv")
    assert (store.root / snapshot.relative_path).read_bytes() == before
    assert snapshot.row_count == 4
    assert snapshot.missing_count("first_treated") == 2


def test_snapshot_rejects_symlink_input(tmp_path: Path) -> None:
    """A local-data selector cannot redirect through a symlink."""
    source = tmp_path / "panel.csv"
    source.write_text(PANEL, encoding="utf-8")
    alias = tmp_path / "alias.csv"
    alias.symlink_to(source)

    with pytest.raises(LocalDataInvalid, match="regular non-symlink"):
        snapshot_csv(_spec(alias), ResearchArtifactStore(tmp_path / "store"))


@pytest.mark.parametrize(
    ("csv_text", "message"),
    [
        ("unit,unit,emissions,first_treated\na,2020,1,\n", "duplicate columns"),
        ("unit,year,emissions\na,2020,1\n", "required columns"),
        (PANEL + "a,2021,7,2021,101\n", "unit-time rows"),
        (PANEL.replace("a,2020,10", "a,not-a-year,10"), "time values"),
        (PANEL.replace("a,2020,10", "a,2020,not-a-number"), "outcome values"),
        (PANEL.replace("a,2020,10,2021", "a,2020,10,2022"), "constant by unit"),
        (PANEL.replace("a,2020,10,2021,100", "a,2020,10,2021,"), "covariate values"),
        (
            PANEL.replace("a,2020,10,2021,100", "a,2020,10,2021,unknown"),
            "covariate values",
        ),
    ],
)
def test_snapshot_rejects_invalid_panel_shape(
    tmp_path: Path, csv_text: str, message: str
) -> None:
    """Invalid panel structure fails before any R process is started."""
    source = tmp_path / "panel.csv"
    source.write_text(csv_text, encoding="utf-8")

    with pytest.raises(LocalDataInvalid, match=message):
        snapshot_csv(_spec(source), ResearchArtifactStore(tmp_path / "store"))


def test_snapshot_rejects_source_replacement_during_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed source cannot be reported under the earlier digest."""
    source = tmp_path / "panel.csv"
    source.write_text(PANEL, encoding="utf-8")

    from envresearch.econometrics import data_snapshot

    original = data_snapshot._persist_snapshot

    original_bytes = source.read_bytes()

    def replace_then_persist(*args: object, **kwargs: object) -> Path:
        persisted = original(*args, **kwargs)  # type: ignore[arg-type]
        source.write_text(PANEL.replace("10", "99", 1), encoding="utf-8")
        return persisted

    monkeypatch.setattr(data_snapshot, "_persist_snapshot", replace_then_persist)

    with pytest.raises(LocalDataChanged, match="changed during snapshot"):
        snapshot_csv(_spec(source), ResearchArtifactStore(tmp_path / "store"))
    persisted = tuple((tmp_path / "store").rglob("*.csv"))
    assert len(persisted) == 1
    assert persisted[0].read_bytes() == original_bytes


def test_snapshot_reauthentication_never_follows_a_replacement_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-copy authentication must reopen with no-follow semantics."""
    source = tmp_path / "panel.csv"
    source.write_text(PANEL, encoding="utf-8")
    replacement = tmp_path / "replacement.csv"
    replacement.write_text(PANEL, encoding="utf-8")

    from envresearch.econometrics import data_snapshot

    original_persist = data_snapshot._persist_snapshot

    def replace_with_symlink(*args: object, **kwargs: object) -> Path:
        persisted = original_persist(*args, **kwargs)  # type: ignore[arg-type]
        source.unlink()
        source.symlink_to(replacement)
        return persisted

    original_read_bytes = Path.read_bytes

    def reject_source_follow(path: Path) -> bytes:
        if path == source:
            raise AssertionError("source path was followed during reauthentication")
        return original_read_bytes(path)

    monkeypatch.setattr(data_snapshot, "_persist_snapshot", replace_with_symlink)
    monkeypatch.setattr(Path, "read_bytes", reject_source_follow)

    with pytest.raises(LocalDataChanged, match="changed during snapshot"):
        snapshot_csv(_spec(source), ResearchArtifactStore(tmp_path / "store"))


def test_snapshot_enforces_input_size_budget(tmp_path: Path) -> None:
    """The local CSV cannot consume more memory than its approved workspace."""
    source = tmp_path / "panel.csv"
    source.write_text(PANEL, encoding="utf-8")
    payload = _spec(source).model_dump()
    payload["budget"] = {
        "inactivity_seconds": 120,
        "max_output_bytes": 2_000_000,
        "max_workspace_bytes": 32,
    }
    spec = LocalAnalysisSpec.model_validate(payload)

    with pytest.raises(LocalDataInvalid, match="workspace budget"):
        snapshot_csv(spec, ResearchArtifactStore(tmp_path / "store"))


def test_snapshot_rejects_preexisting_destination_symlink(tmp_path: Path) -> None:
    """A content-addressed reference must resolve to owned regular bytes."""
    source = tmp_path / "panel.csv"
    source.write_text(PANEL, encoding="utf-8")
    digest = hashlib.sha256(PANEL.encode()).hexdigest()
    store = ResearchArtifactStore(tmp_path / "store")
    destination = store.root / "artifacts/econometrics/data" / f"{digest}.csv"
    destination.parent.mkdir(parents=True)
    alias_target = store.root / "artifacts/replaceable.csv"
    alias_target.write_text(PANEL, encoding="utf-8")
    destination.symlink_to(alias_target)

    with pytest.raises(LocalDataInvalid, match="owned regular file"):
        snapshot_csv(_spec(source), store)


def test_snapshot_does_not_create_through_intermediate_symlink(tmp_path: Path) -> None:
    """Directory validation must precede every root-relative mutation."""
    source = tmp_path / "panel.csv"
    source.write_text(PANEL, encoding="utf-8")
    store = ResearchArtifactStore(tmp_path / "store")
    outside = tmp_path / "outside"
    outside.mkdir()
    store.root.mkdir()
    (store.root / "artifacts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(LocalDataInvalid, match="authenticated store hierarchy"):
        snapshot_csv(_spec(source), store)
    assert not (outside / "econometrics").exists()


def test_snapshot_detects_parent_replacement_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An opened parent descriptor cannot authorize a replacement path."""
    source = tmp_path / "panel.csv"
    source.write_text(PANEL, encoding="utf-8")
    store = ResearchArtifactStore(tmp_path / "store")

    from envresearch.econometrics import data_snapshot

    original_publish = data_snapshot._publish_snapshot

    def replace_parent_then_publish(
        parent_descriptor: int, leaf_name: str, data: bytes
    ) -> None:
        parent = store.root / "artifacts/econometrics/data"
        moved = store.root / "artifacts/econometrics/original-data"
        parent.rename(moved)
        parent.mkdir()
        original_publish(parent_descriptor, leaf_name, data)

    monkeypatch.setattr(data_snapshot, "_publish_snapshot", replace_parent_then_publish)

    with pytest.raises(LocalDataChanged, match="store hierarchy changed"):
        snapshot_csv(_spec(source), store)


def test_snapshot_rejects_fifo_destination_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonregular existing leaf must be opened in nonblocking mode."""
    source = tmp_path / "panel.csv"
    source.write_text(PANEL, encoding="utf-8")
    digest = hashlib.sha256(PANEL.encode()).hexdigest()
    store = ResearchArtifactStore(tmp_path / "store")
    destination = store.root / "artifacts/econometrics/data" / f"{digest}.csv"
    destination.parent.mkdir(parents=True)
    os.mkfifo(destination)

    original_open = os.open

    def require_nonblocking_leaf(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        *args: Any,
        **kwargs: Any,
    ) -> int:
        if path == destination.name and kwargs.get("dir_fd") is not None:
            assert flags & os.O_NONBLOCK
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", require_nonblocking_leaf)

    with pytest.raises(LocalDataInvalid, match="owned regular file"):
        snapshot_csv(_spec(source), store)


def test_snapshot_opens_source_nonblocking_before_type_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raced-in FIFO cannot block between source lstat and fstat."""
    source = tmp_path / "panel.csv"
    source.write_text(PANEL, encoding="utf-8")
    original_open = os.open

    def require_nonblocking_source(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        *args: Any,
        **kwargs: Any,
    ) -> int:
        if str(path) == str(source) and kwargs.get("dir_fd") is None:
            assert flags & os.O_NONBLOCK
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", require_nonblocking_source)

    snapshot = snapshot_csv(_spec(source), ResearchArtifactStore(tmp_path / "store"))
    assert snapshot.row_count == 4

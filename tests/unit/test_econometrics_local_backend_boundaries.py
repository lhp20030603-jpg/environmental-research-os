"""Typed failure translation for the trusted local R backend."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from test_econometrics_valuation_contracts import contingent_valuation, hedonic

from envresearch.econometrics._causal_outputs import CausalOutputInvalid
from envresearch.econometrics.data_snapshot import snapshot_csv
from envresearch.econometrics.local_backend import (
    TrustedLocalRBackend,
    _materialize_exact,
)
from envresearch.econometrics.r_runtime import (
    RExecutionFailed,
    RPackageAuthorityInvalid,
    RRuntimeInvalid,
    TrustedLocalRRunner,
)
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.service import LocalExecutionError
from envresearch.storage.research_artifacts import ResearchArtifactStore


class _Executor:
    pass


def _backend() -> TrustedLocalRBackend:
    return TrustedLocalRBackend(
        executable=Path("/reviewed/Rscript"),
        expected_sha256="0" * 64,
        executor=_Executor(),  # type: ignore[arg-type]
    )


def _snapshot(spec, tmp_path: Path):
    return snapshot_csv(spec, ResearchArtifactStore(tmp_path / "snapshot-store"))


def test_backend_translates_unsupported_render_and_missing_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = contingent_valuation(
        Path(__file__).parents[1] / "fixtures/econometrics/contingent_valuation.csv"
    )
    snapshot = _snapshot(spec, tmp_path)

    class BadRecipe:
        def render(self, *args: object) -> None:
            raise CausalOutputInvalid("unsupported design", code="BAD")

    monkeypatch.setattr(
        "envresearch.econometrics.local_backend.recipe_for",
        lambda *args, **kwargs: BadRecipe(),
    )
    with pytest.raises(LocalExecutionError, match="unsupported design") as caught:
        _backend().execute(
            spec, snapshot, spec.data_path.read_bytes(), tmp_path / "render"
        )
    assert caught.value.code == "DESIGN_UNSUPPORTED"

    hedonic_spec = hedonic(
        Path(__file__).parents[1] / "fixtures/econometrics/hedonic_pricing.csv"
    )
    hedonic_snapshot = _snapshot(hedonic_spec, tmp_path / "hedonic")
    monkeypatch.setattr("envresearch.econometrics.local_backend.recipe_for", recipe_for)
    with pytest.raises(LocalExecutionError, match="frozen package authority") as caught:
        _backend().execute(
            hedonic_spec,
            hedonic_snapshot,
            hedonic_spec.data_path.read_bytes(),
            tmp_path / "package",
        )
    assert caught.value.code == "R_PACKAGE_AUTHORITY_INVALID"


@pytest.mark.parametrize(
    ("error", "code"),
    (
        (RPackageAuthorityInvalid("package changed"), "R_PACKAGE_AUTHORITY_INVALID"),
        (RRuntimeInvalid("runtime changed"), "R_RUNTIME_UNAVAILABLE"),
        (
            RExecutionFailed("R failed", code="CV_MONOTONICITY_FAILED"),
            "CV_MONOTONICITY_FAILED",
        ),
    ),
)
def test_backend_translates_review_and_execution_failures(
    error: Exception,
    code: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = contingent_valuation(
        Path(__file__).parents[1] / "fixtures/econometrics/contingent_valuation.csv"
    )
    snapshot = _snapshot(spec, tmp_path)

    def reject(**kwargs: object):
        del kwargs
        raise error

    monkeypatch.setattr(TrustedLocalRRunner, "review", reject)
    with pytest.raises(LocalExecutionError) as caught:
        _backend().execute(spec, snapshot, spec.data_path.read_bytes(), tmp_path / code)
    assert caught.value.code == code


def test_backend_translates_parse_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = contingent_valuation(
        Path(__file__).parents[1] / "fixtures/econometrics/contingent_valuation.csv"
    )
    snapshot = _snapshot(spec, tmp_path)
    real = recipe_for(spec.method_id, workspace=tmp_path / "recipe")

    class BadParse:
        def render(self, *args: object):
            return real.render(*args)

        def parse(self, *args: object):
            raise CausalOutputInvalid("bad output", code="SCIENTIFIC_OUTPUT_BAD")

    monkeypatch.setattr(
        "envresearch.econometrics.local_backend.recipe_for",
        lambda *args, **kwargs: BadParse(),
    )
    monkeypatch.setattr(
        TrustedLocalRRunner,
        "review",
        lambda **kwargs: SimpleNamespace(
            run=lambda script: SimpleNamespace(package_authorities=())
        ),
    )
    with pytest.raises(LocalExecutionError, match="bad output") as caught:
        _backend().execute(
            spec, snapshot, spec.data_path.read_bytes(), tmp_path / "parse"
        )
    assert caught.value.code == "SCIENTIFIC_OUTPUT_BAD"


def test_materialize_exact_rejects_changed_bytes_and_collision(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    destination = workspace / "input/data.csv"
    with pytest.raises(LocalExecutionError) as caught:
        _materialize_exact(b"data", workspace, destination, "0" * 64)
    assert caught.value.code == "EVIDENCE_TAMPERED"

    destination.parent.mkdir()
    destination.write_bytes(b"other")
    import hashlib

    with pytest.raises(LocalExecutionError, match="workspace input collision"):
        _materialize_exact(
            b"data", workspace, destination, hashlib.sha256(b"data").hexdigest()
        )

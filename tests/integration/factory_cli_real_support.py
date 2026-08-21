"""Production V0.3.1 authority fixture for real factory CLI tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.frozen_r_library import FrozenRLibrary
from envresearch.econometrics.valuation_exit_corpus import freeze_valuation_exit_corpus
from envresearch.econometrics.valuation_exit_models import ValuationExitManifest
from envresearch.econometrics.valuation_transition import publish_v031_transition
from envresearch.models.artifact import ArtifactRef

REPOSITORY = Path(__file__).parents[2]
CORPUS = REPOSITORY / "benchmarks/econometrics/valuation-core"


def build_v031_authority(root: Path) -> ArtifactRef:
    """Publish one real nine-case transition through production writer APIs."""
    root.mkdir()
    runner = ExitRegistry(root / "runner")
    evaluator = ExitRegistry(root / "evaluator")
    corpus = freeze_valuation_exit_corpus(CORPUS.resolve(), runner, evaluator)
    source_runtime, version, libraries = _r_authority()
    runtime = root / "reviewed/Rscript"
    runtime.parent.mkdir()
    shutil.copyfile(source_runtime, runtime)
    runtime.chmod(0o500)
    frozen = FrozenRLibrary((root / "frozen-pack").resolve())
    authorities = frozen.freeze(
        libraries,
        required_packages=("fixest", "MASS", "survival"),
        r_version=version,
    )
    pack_hash = authorities[0].pack_hash
    runtime_sha256 = hashlib.sha256(runtime.read_bytes()).hexdigest()
    run_ref = _valuation_run(
        root, corpus.manifest_ref, runtime, runtime_sha256, frozen, pack_hash
    )
    report_ref = _valuation_evaluate(
        root, run_ref, corpus.catalog_ref, frozen, pack_hash
    )
    manifest = runner.load(corpus.manifest_ref, ValuationExitManifest)
    binding_ref = evaluator.current(f"valuation-catalog-{manifest.manifest_id}")
    if binding_ref is None:
        raise AssertionError("valuation catalog binding was not published")
    with evaluator.lock("valuation-transition-v031"):
        pass
    return publish_v031_transition(
        root,
        manifest_ref=corpus.manifest_ref,
        run_ref=run_ref,
        catalog_binding_ref=binding_ref,
        catalog_ref=corpus.catalog_ref,
        report_ref=report_ref,
        runtime_relative_path=runtime.relative_to(root),
        runtime_sha256=runtime_sha256,
        frozen_pack_root=frozen.store_root,
        frozen_pack_hash=pack_hash,
    )


def _r_authority() -> tuple[Path, str, tuple[Path, ...]]:
    executable = shutil.which("Rscript")
    if executable is None:
        raise AssertionError("real V0.3.1 fixture requires reviewed Rscript")
    runtime = Path(executable).resolve(strict=True)
    result = subprocess.run(
        [
            str(runtime),
            "--vanilla",
            "-e",
            (
                "cat(paste(R.version$major,R.version$minor,sep='.'),'\\n',sep='');"
                "cat(.libPaths(),sep='\\n')"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    lines = tuple(line for line in result.stdout.splitlines() if line)
    if len(lines) < 2:
        raise AssertionError("reviewed R runtime did not expose package libraries")
    return (
        runtime,
        lines[0],
        tuple(Path(value).resolve(strict=True) for value in lines[1:]),
    )


def _valuation_run(
    root: Path,
    manifest_ref: ArtifactRef,
    runtime: Path,
    runtime_sha256: str,
    frozen: FrozenRLibrary,
    pack_hash: str,
) -> ArtifactRef:
    result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "valuation-exit-run",
            str(_write_ref(root / "manifest.json", manifest_ref)),
            "--runner-root",
            str(root / "runner"),
            "--evaluator-root",
            str(root / "evaluator"),
            "--analysis-root",
            str(root / "analysis"),
            "--r-executable",
            str(runtime),
            "--r-sha256",
            runtime_sha256,
            "--frozen-r-pack-root",
            str(frozen.store_root),
            "--frozen-r-pack-hash",
            pack_hash,
        ],
    )
    if result.exit_code != 0:
        raise AssertionError(result.stdout or str(result.exception))
    return ArtifactRef.model_validate(json.loads(result.stdout)["run_reference"])


def _valuation_evaluate(
    root: Path,
    run_ref: ArtifactRef,
    catalog_ref: ArtifactRef,
    frozen: FrozenRLibrary,
    pack_hash: str,
) -> ArtifactRef:
    result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "valuation-exit-evaluate",
            str(_write_ref(root / "run.json", run_ref)),
            str(_write_ref(root / "catalog.json", catalog_ref)),
            "--runner-root",
            str(root / "runner"),
            "--evaluator-root",
            str(root / "evaluator"),
            "--analysis-root",
            str(root / "analysis"),
            "--frozen-r-pack-root",
            str(frozen.store_root),
            "--frozen-r-pack-hash",
            pack_hash,
        ],
    )
    if result.exit_code != 0:
        raise AssertionError(result.stdout or str(result.exception))
    payload = json.loads(result.stdout)
    if payload["report"]["status"] != "passed":
        raise AssertionError(result.stdout)
    return ArtifactRef.model_validate(payload["report_reference"])


def _write_ref(path: Path, reference: ArtifactRef) -> Path:
    path.write_text(reference.model_dump_json(), encoding="utf-8")
    return path


__all__ = ["build_v031_authority"]

"""Real four-root factory CLI composition and descriptor lifecycle."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from factory_cli_real_support import build_v031_authority
from factory_fixtures import final_context_ref
from test_factory_run import _hedonic_final_gate, _hedonic_release
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.factory.authority import FACTORY_RUN_LOCK_SUBJECT
from envresearch.factory.design_resolver import V02ApprovedDesignResolver
from envresearch.factory.errors import FactorySupportInvalid
from envresearch.factory.service import FactoryRunService
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.errors import PaperIntegrityInvalid
from envresearch.paper.ledger import V031AcceptedEvidenceResolver


def _write_ref(path: Path, reference: ArtifactRef) -> Path:
    path.write_text(reference.model_dump_json(), encoding="utf-8")
    return path


def _root_args(roots: tuple[Path, Path, Path, Path]) -> list[str]:
    research, v031, paper, factory = roots
    return [
        "--research-root",
        str(research),
        "--v031-root",
        str(v031),
        "--paper-root",
        str(paper),
        "--factory-root",
        str(factory),
    ]


def _json(result: object) -> dict[str, object]:
    stdout = result.stdout  # type: ignore[attr-defined]
    payload = json.loads(stdout)
    assert stdout.strip() == json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return payload  # type: ignore[no-any-return]


def _pointers(roots: tuple[Path, ...]) -> dict[str, bytes]:
    return {
        f"{root.name}/{path.relative_to(root)}": path.read_bytes()
        for root in roots
        for path in sorted(root.rglob("current/*.json"))
    }


def _reviewed_r_closure_available(executable: str) -> bool:
    """Return whether the optional reviewed R package closure is available."""
    expression = (
        "packages <- c('fixest','MASS','survival');"
        "available <- vapply(packages, requireNamespace, logical(1), quietly=TRUE);"
        "quit(status=if (all(available)) 0L else 1L)"
    )
    try:
        result = subprocess.run(
            [executable, "--vanilla", "-e", expression],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@pytest.fixture(scope="module")
def real_v031_authority(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[tuple[Path, Path, Path, Path], ArtifactRef, ArtifactRef]:
    """Build genuine V0.2, V0.3.1, V0.4, and factory roots once."""
    rscript = shutil.which("Rscript")
    if rscript is None:
        pytest.skip("Rscript is not installed; the real V0.3.1 fixture is optional")
    if not _reviewed_r_closure_available(rscript):
        pytest.skip(
            "fixest, MASS, and survival are not all installed; "
            "the real V0.3.1 fixture is optional"
        )
    root = tmp_path_factory.mktemp("real-v031-factory")
    research = (root / "research-authorities").resolve()
    v031 = (root / "v031").resolve()
    paper = (root / "paper").resolve()
    factory = (root / "factory").resolve()
    design = _hedonic_final_gate(research / "design")
    resolver = V02ApprovedDesignResolver(design, factory)
    design_ref = resolver.build(
        design.lifecycle.artifact_ref(Path("artifacts/analysis-plan.yaml")),
        final_context_ref(design),
    )
    transition_ref = build_v031_authority(v031)
    releases, release_ref, citation = _hedonic_release(
        root / "release-build",
        citation_root=research / "citation",
        paper_root=paper,
        accepted_resolver=V031AcceptedEvidenceResolver(v031),
        accepted_transition_ref=transition_ref,
        bind_citation_config=True,
    )
    service = FactoryRunService(design_resolver=resolver, release_service=releases)
    try:
        with pytest.raises(FactorySupportInvalid) as caught:
            service.assemble(design_ref, release_ref)
        assert caught.value.finding_kind == "method-binding-invalid"
    finally:
        design.close()
        citation.close()
    return (research, v031, paper, factory), design_ref, release_ref


def test_real_v031_assemble_rejects_incompatible_estimands_without_pointer_change(
    real_v031_authority: tuple[tuple[Path, Path, Path, Path], ArtifactRef, ArtifactRef],
    tmp_path: Path,
) -> None:
    """Catch genuine multi-estimand evidence being widened into one design."""
    roots, design_ref, release_ref = real_v031_authority
    arguments = [
        "factory",
        "assemble",
        str(_write_ref(tmp_path / "design.json", design_ref)),
        str(_write_ref(tmp_path / "release.json", release_ref)),
        *_root_args(roots),
    ]
    pointers_before = _pointers(roots)
    descriptors_before = len(os.listdir("/dev/fd"))

    first = CliRunner().invoke(app, arguments)
    second = CliRunner().invoke(app, arguments)

    expected = {
        "error": {
            "code": "FACTORY_SUPPORT_INVALID",
            "finding_kind": "method-binding-invalid",
            "message": "paper method is not one of the approved registered profiles",
        }
    }
    assert first.exit_code == second.exit_code == 2
    assert _json(first) == expected
    assert first.stdout == second.stdout
    assert _pointers(roots) == pointers_before
    assert len(os.listdir("/dev/fd")) == descriptors_before


def test_real_four_root_read_only_composition_does_not_heal_missing_locks(
    real_v031_authority: tuple[tuple[Path, Path, Path, Path], ArtifactRef, ArtifactRef],
) -> None:
    """Catch actual four-root composition healing either protected lock."""
    import envresearch.factory.cli as factory_cli

    roots, design_ref, release_ref = real_v031_authority
    _, _, _, factory = roots
    service = factory_cli.service_for_roots(*roots, create=False)
    try:
        design_id = service.design_resolver.resolve(design_ref).design_id
        locks = (
            factory / "exit/locks" / f"approved-design-{design_id}.lock",
            factory / "exit/locks" / f"{FACTORY_RUN_LOCK_SUBJECT}.lock",
        )
        for lock in locks:
            original = lock.read_bytes()
            original_mode = lock.stat().st_mode & 0o777
            lock.unlink()
            pointers_before = _pointers(roots)
            try:
                with (
                    pytest.raises(PaperIntegrityInvalid) as caught,
                    service.authority.lease(
                        design_id=design_id, release_ref=release_ref
                    ),
                ):
                    pytest.fail("missing lock unexpectedly acquired")
                assert caught.value.finding_kind == "audit-authority-lease-invalid"
                assert not lock.exists()
                assert _pointers(roots) == pointers_before
            finally:
                lock.write_bytes(original)
                lock.chmod(original_mode)
    finally:
        factory_cli._close(service)

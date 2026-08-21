"""End-to-end persistence and verification for valuation core recipes."""

from __future__ import annotations

import hashlib
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Never

import pytest
from econometrics_valuation_fixtures import (
    cv_spec,
    dce_spec,
    hedonic_spec,
    travel_spec,
    write_hedonic_outputs,
    write_travel_outputs,
)
from econometrics_valuation_verifier_fixtures import package_authority

from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.frozen_r_library import FrozenRLibrary
from envresearch.econometrics.local_backend import (
    TrustedLocalRBackend,
    _required_package_names,
)
from envresearch.econometrics.r_evidence import RExecutionEvidence, RRuntimeIdentity
from envresearch.econometrics.r_subprocess import BoundedRSubprocessExecutor
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.service import (
    BackendResult,
    EvidenceTampered,
    LocalAnalysisService,
)
from envresearch.econometrics.valuation_contracts import HedonicSpec, TravelCostSpec
from envresearch.storage.research_artifacts import ResearchArtifactStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "econometrics"


class _ValuationBackend:
    def __init__(self, method_id: str, forged_diagnostic: bool = False) -> None:
        self.forged_diagnostic = forged_diagnostic
        package = "fixest" if method_id == "hedonic-pricing" else "MASS"
        self.package_authorities = (package_authority(package),)

    def execute(
        self,
        spec: HedonicSpec | TravelCostSpec,
        snapshot: LocalDataSnapshot,
        snapshot_bytes: bytes,
        workspace: Path,
    ) -> BackendResult:
        assert hashlib.sha256(snapshot_bytes).hexdigest() == snapshot.sha256
        recipe = recipe_for(spec.method_id, workspace=workspace)
        script = recipe.render(spec, snapshot)
        output = workspace / "output"
        if isinstance(spec, HedonicSpec):
            write_hedonic_outputs(output)
            if self.forged_diagnostic:
                diagnostic = output / "collinearity.csv"
                diagnostic.write_text(
                    diagnostic.read_text(encoding="utf-8").replace(
                        "145.505300137852,300", "144.505300137852,300"
                    ),
                    encoding="utf-8",
                )
        else:
            write_travel_outputs(output)
            if self.forged_diagnostic:
                diagnostic = output / "dispersion.csv"
                diagnostic.write_text(
                    diagnostic.read_text(encoding="utf-8").replace(
                        "-48.86315757515838", "-49.86315757515838"
                    ),
                    encoding="utf-8",
                )
        authorities = self.package_authorities
        result = recipe.parse(output, tuple(item.ref() for item in authorities))
        runtime = workspace / "runtime-fixture"
        runtime.write_bytes(b"R fixture 4.4.3")
        runtime.chmod(0o555)
        identity = RRuntimeIdentity(
            source_executable=runtime,
            executable=runtime,
            sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
            version="Rscript (R) version 4.4.3 (2025-02-28)",
            device=runtime.stat().st_dev,
            inode=runtime.stat().st_ino,
            size_bytes=runtime.stat().st_size,
        )
        execution = RExecutionEvidence(
            runtime=identity,
            script=script,
            argv=(str(runtime), "--vanilla", str(script.path)),
            environment=(),
            return_code=0,
            stdout_sha256=hashlib.sha256(b"").hexdigest(),
            stderr_sha256=hashlib.sha256(b"").hexdigest(),
            redacted_stdout="",
            redacted_stderr="",
            workspace_bytes=4096,
            package_authorities=authorities,
        )
        return BackendResult(
            script=script,
            execution=execution,
            result=result,
            output_root=output,
        )


class _NeverExecutor:
    def execute(self, *args: object, **kwargs: object) -> Never:
        raise AssertionError("authority preflight must precede R execution")


@pytest.mark.parametrize("method_id", ["hedonic-pricing", "travel-cost"])
def test_valuation_service_reopens_verified_result(
    method_id: str, tmp_path: Path
) -> None:
    source = FIXTURES / (
        "hedonic_pricing.csv" if method_id == "hedonic-pricing" else "travel_cost.csv"
    )
    spec = (
        hedonic_spec(source) if method_id == "hedonic-pricing" else travel_spec(source)
    )
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "store"), _ValuationBackend(method_id)
    )

    report = service.status(service.run(spec))

    assert report.status == "passed", (report.code, report.verification_findings)
    assert report.result is not None
    assert report.result.method_id == method_id


@pytest.mark.parametrize("method_id", ["hedonic-pricing", "travel-cost"])
def test_valuation_service_rejects_forged_diagnostics(
    method_id: str, tmp_path: Path
) -> None:
    source = FIXTURES / (
        "hedonic_pricing.csv" if method_id == "hedonic-pricing" else "travel_cost.csv"
    )
    spec = (
        hedonic_spec(source) if method_id == "hedonic-pricing" else travel_spec(source)
    )
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "store"), _ValuationBackend(method_id, True)
    )

    report = service.status(service.run(spec))

    assert report.status == "exception"
    assert "CONFIGURATION_MISMATCH" in report.verification_findings


def test_valuation_status_rejects_output_byte_mutation(tmp_path: Path) -> None:
    spec = travel_spec(FIXTURES / "travel_cost.csv")
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "store"), _ValuationBackend("travel-cost")
    )
    reference = service.run(spec)
    report = service.status(reference)
    assert report.output_root is not None
    output = service.store.root / report.output_root / "consumer_surplus.csv"
    output.chmod(0o600)
    output.write_bytes(output.read_bytes().replace(b"2,0.4", b"9,0.4"))

    with pytest.raises(EvidenceTampered, match="OUTPUT_TAMPERED"):
        service.status(reference)


def test_trusted_valuation_backend_requires_frozen_package_authority(
    tmp_path: Path,
) -> None:
    spec = hedonic_spec(FIXTURES / "hedonic_pricing.csv")
    backend = TrustedLocalRBackend(
        executable=tmp_path / "missing-Rscript",
        expected_sha256="a" * 64,
        executor=_NeverExecutor(),
    )
    service = LocalAnalysisService(ResearchArtifactStore(tmp_path / "store"), backend)

    report = service.status(service.run(spec))

    assert report.status == "exception"
    assert report.code == "R_PACKAGE_AUTHORITY_INVALID"


@pytest.mark.parametrize(
    ("spec_factory", "expected"),
    ((hedonic_spec, {"fixest"}), (travel_spec, {"MASS"})),
)
def test_valuation_preflight_requires_method_selected_package(
    spec_factory: object, expected: set[str], tmp_path: Path
) -> None:
    spec = spec_factory(tmp_path / "source.csv")  # type: ignore[operator]
    assert _required_package_names(spec) == expected


@pytest.mark.parametrize("method_id", ["hedonic-pricing", "travel-cost"])
def test_real_frozen_r_valuation_recipe_when_explicitly_enabled(
    method_id: str, tmp_path: Path
) -> None:
    if os.getenv("ENVRESEARCH_RUN_OPTIONAL_R_VALUATION") != "1":
        pytest.skip("set ENVRESEARCH_RUN_OPTIONAL_R_VALUATION=1 for local R smoke")
    discovered = shutil.which("Rscript")
    if discovered is None:
        pytest.skip("Rscript is not installed; installation is never automatic")
    source_runtime = Path(discovered).resolve(strict=True)
    source_library = source_runtime.parent.parent / "library"
    if not source_library.is_dir():
        pytest.skip("reviewed local R library could not be discovered")
    reviewed = tmp_path / "reviewed" / "Rscript"
    reviewed.parent.mkdir()
    reviewed.write_bytes(source_runtime.read_bytes())
    reviewed.chmod(0o555)
    required = ("fixest",) if method_id == "hedonic-pricing" else ("MASS",)
    frozen = FrozenRLibrary((tmp_path / "frozen").resolve())
    authorities = frozen.freeze(
        (source_library.resolve(),),
        required_packages=required,
        r_version="4.4.3",
    )
    backend = TrustedLocalRBackend(
        executable=reviewed,
        expected_sha256=hashlib.sha256(reviewed.read_bytes()).hexdigest(),
        executor=BoundedRSubprocessExecutor(),
        managed_library=frozen,
        package_authorities=authorities,
    )
    source = FIXTURES / (
        "hedonic_pricing.csv" if method_id == "hedonic-pricing" else "travel_cost.csv"
    )
    spec = (
        hedonic_spec(source) if method_id == "hedonic-pricing" else travel_spec(source)
    )
    if isinstance(spec, HedonicSpec):
        spec = spec.model_copy(
            update={"max_condition_number": 1000.0, "max_sensitivity_change": 1000.0}
        )
    else:
        spec = spec.model_copy(update={"max_sensitivity_change": 100.0})
    service = LocalAnalysisService(ResearchArtifactStore(tmp_path / "store"), backend)

    report = service.status(service.run(spec))

    assert report.status == "passed", (report.code, report.verification_findings)
    assert report.execution is not None
    assert report.execution.package_authorities == authorities
    assert report.result is not None
    assert report.result.method_id == method_id
    assert report.output_root is not None
    figure = (
        "hedonic_plot.svg" if method_id == "hedonic-pricing" else "travel_cost_plot.svg"
    )
    root = ET.parse(service.store.root / report.output_root / figure).getroot()
    assert len(root.findall(".//*[@class='x-tick']")) == 5
    if method_id == "dce-clogit":
        assert len(root.findall(".//*[@class='wtp-estimate']")) == 2
    if method_id == "hedonic-pricing":
        assert report.result.sensitivities[0].baseline_estimate == pytest.approx(
            report.result.welfare[0].estimate
        )


@pytest.mark.parametrize(
    ("method_id", "factory", "fixture", "required"),
    (
        ("contingent-valuation", cv_spec, "contingent_valuation.csv", ()),
        ("dce-clogit", dce_spec, "dce_clogit.csv", ("survival",)),
    ),
)
def test_real_frozen_r_stated_preference_recipe_when_explicitly_enabled(
    method_id: str,
    factory: object,
    fixture: str,
    required: tuple[str, ...],
    tmp_path: Path,
) -> None:
    if os.getenv("ENVRESEARCH_RUN_OPTIONAL_R_VALUATION") != "1":
        pytest.skip("set ENVRESEARCH_RUN_OPTIONAL_R_VALUATION=1 for local R smoke")
    discovered = shutil.which("Rscript")
    if discovered is None:
        pytest.skip("Rscript is not installed; installation is never automatic")
    source_runtime = Path(discovered).resolve(strict=True)
    source_library = source_runtime.parent.parent / "library"
    reviewed = tmp_path / "reviewed" / "Rscript"
    reviewed.parent.mkdir()
    reviewed.write_bytes(source_runtime.read_bytes())
    reviewed.chmod(0o555)
    frozen = FrozenRLibrary((tmp_path / "frozen").resolve())
    authorities = (
        frozen.freeze(
            (source_library.resolve(),),
            required_packages=required,
            r_version="4.4.3",
        )
        if required
        else ()
    )
    backend = TrustedLocalRBackend(
        executable=reviewed,
        expected_sha256=hashlib.sha256(reviewed.read_bytes()).hexdigest(),
        executor=BoundedRSubprocessExecutor(),
        managed_library=frozen if required else None,
        package_authorities=authorities,
    )
    spec = factory(FIXTURES / fixture)  # type: ignore[operator]
    service = LocalAnalysisService(ResearchArtifactStore(tmp_path / "store"), backend)

    report = service.status(service.run(spec))

    assert report.status == "passed", (report.code, report.verification_findings)
    assert report.result is not None
    assert report.result.method_id == method_id
    assert report.execution is not None
    assert report.execution.package_authorities == authorities
    assert report.output_root is not None
    figure = "cv_plot.svg" if method_id == "contingent-valuation" else "dce_plot.svg"
    root = ET.parse(service.store.root / report.output_root / figure).getroot()
    assert len(root.findall(".//*[@class='x-tick']")) == 5

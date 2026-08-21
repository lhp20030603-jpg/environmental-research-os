"""Production trusted-local-R backend for registered econometrics recipes."""

from __future__ import annotations

from pathlib import Path

from envresearch.econometrics._causal_outputs import CausalOutputInvalid
from envresearch.econometrics._store_files import StoreFiles
from envresearch.econometrics._valuation_authority import required_package_names
from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.contracts import LocalAnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.did import DidOutputInvalid
from envresearch.econometrics.did_diagnostics import pretrend_exceeded
from envresearch.econometrics.did_models import DidResult
from envresearch.econometrics.method_authority import MethodAuthority
from envresearch.econometrics.r_evidence import PackageAuthority
from envresearch.econometrics.r_runtime import (
    ManagedAuthorityLibrary,
    RCommandExecutor,
    RExecutionFailed,
    RPackageAuthorityInvalid,
    RRuntimeInvalid,
    TrustedLocalRRunner,
)
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.service import BackendResult, LocalExecutionError


class TrustedLocalRBackend:
    """Materialize one snapshot and execute its registered generated R script."""

    def __init__(
        self,
        *,
        executable: Path,
        expected_sha256: str,
        executor: RCommandExecutor,
        managed_library: ManagedAuthorityLibrary | None = None,
        package_authorities: tuple[PackageAuthority, ...] = (),
    ) -> None:
        self.executable = executable
        self.expected_sha256 = expected_sha256
        self.executor = executor
        self.managed_library = managed_library
        self.package_authorities = package_authorities

    def execute(
        self,
        spec: AnalysisSpec,
        snapshot: LocalDataSnapshot,
        snapshot_bytes: bytes,
        workspace: Path,
    ) -> BackendResult:
        """Run the exact snapshot through the registered trusted recipe."""
        workspace.mkdir(parents=True, exist_ok=True)
        input_path = workspace / "input" / "data.csv"
        _materialize_exact(snapshot_bytes, workspace, input_path, snapshot.sha256)
        recipe = recipe_for(spec.method_id, workspace=workspace)
        try:
            script = recipe.render(spec, snapshot)
        except (CausalOutputInvalid, DidOutputInvalid) as error:
            raise LocalExecutionError("DESIGN_UNSUPPORTED", str(error)) from error
        required_packages = _required_package_names(spec)
        actual_packages = {_package_name(item) for item in self.package_authorities}
        if required_packages and (
            self.managed_library is None
            or not required_packages.issubset(actual_packages)
        ):
            raise LocalExecutionError(
                "R_PACKAGE_AUTHORITY_INVALID",
                "valuation execution requires frozen package authority",
            )
        try:
            runner = TrustedLocalRRunner.review(
                executable=self.executable,
                expected_sha256=self.expected_sha256,
                workspace=workspace,
                executor=self.executor,
                budget=spec.budget,
                approved_scripts={script.template_id: script.sha256},
                managed_library=self.managed_library,
                package_authorities=self.package_authorities,
            )
            execution = runner.run(script)
        except RPackageAuthorityInvalid as error:
            raise LocalExecutionError(error.code, str(error)) from error
        except RRuntimeInvalid as error:
            raise LocalExecutionError("R_RUNTIME_UNAVAILABLE", str(error)) from error
        except RExecutionFailed as error:
            raise LocalExecutionError(error.code, str(error)) from error
        try:
            authority_refs = tuple(item.ref() for item in execution.package_authorities)
            if spec.method_id in {
                "rct-itt",
                "environmental-measurement",
                "synthetic-control",
                "meta-analysis",
                "hedonic-pricing",
                "travel-cost",
                "contingent-valuation",
                "dce-clogit",
            }:
                result = recipe.parse(workspace / "output", authority_refs)
            else:
                result = recipe.parse(workspace / "output")
            if (
                isinstance(spec, LocalAnalysisSpec)
                and isinstance(result, DidResult)
                and pretrend_exceeded(spec, result)
            ):
                raise LocalExecutionError(
                    "DID_PRETREND_EXCEEDED",
                    "DiD pre-treatment leads exceed the declared threshold",
                )
        except (OSError, ValueError) as error:
            code = (
                error.code
                if isinstance(error, CausalOutputInvalid)
                else "OUTPUT_INVALID"
            )
            raise LocalExecutionError(code, str(error)) from error
        return BackendResult(
            script=script,
            execution=execution,
            result=result,
            output_root=workspace / "output",
        )


TrustedLocalDidBackend = TrustedLocalRBackend


def _required_package_names(spec: AnalysisSpec) -> set[str]:
    return required_package_names(spec)


def _package_name(authority: PackageAuthority) -> str:
    return (
        authority.proposal.package
        if isinstance(authority, MethodAuthority)
        else authority.package
    )


def _materialize_exact(
    data: bytes, workspace: Path, destination: Path, expected: str
) -> None:
    """Copy exact immutable snapshot bytes into the isolated local workspace."""
    import hashlib

    if hashlib.sha256(data).hexdigest() != expected:
        raise LocalExecutionError("EVIDENCE_TAMPERED", "snapshot identity changed")
    try:
        StoreFiles(workspace).persist_exact(destination.relative_to(workspace), data)
    except (OSError, ValueError) as error:
        raise LocalExecutionError(
            "EVIDENCE_TAMPERED", "workspace input collision"
        ) from error

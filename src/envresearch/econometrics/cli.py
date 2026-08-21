"""Method-neutral operator CLI for trusted local econometrics."""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from typing import Annotated, NoReturn

import typer
import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError
from yaml import YAMLError
from yaml.constructor import ConstructorError  # type: ignore[import-untyped]
from yaml.nodes import MappingNode  # type: ignore[import-untyped]

from envresearch.econometrics.analysis_specs import (
    ANALYSIS_SPEC_ADAPTER,
    AnalysisSpec,
    required_columns_for,
)
from envresearch.econometrics.exit_evaluator import V03ExitEvaluator
from envresearch.econometrics.exit_registry import ExitRegistry, validate_separate_roots
from envresearch.econometrics.exit_runner import RegistryAnalysisExecutor, V03ExitRunner
from envresearch.econometrics.frozen_r_library import FrozenRLibrary
from envresearch.econometrics.local_backend import TrustedLocalRBackend
from envresearch.econometrics.r_evidence import PackageAuthority
from envresearch.econometrics.r_subprocess import BoundedRSubprocessExecutor
from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.econometrics.service import (
    EvidenceTampered,
    LocalAnalysisBackend,
    LocalAnalysisService,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.storage.research_artifacts import ResearchArtifactStore

econometrics_app = typer.Typer(
    help="Validate and run registered econometric recipes on explicit local data."
)
JsonOption = Annotated[bool, typer.Option("--json", help="Emit JSON output.")]
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _UnavailableBackend:
    def __init__(self, package_authorities: tuple[PackageAuthority, ...] = ()) -> None:
        self.package_authorities = package_authorities

    def execute(self, *args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("local R execution was not configured")


def _service_for(
    run_root: Path,
    *,
    r_executable: Path | None = None,
    r_sha256: str | None = None,
    frozen_pack_root: Path | None = None,
    frozen_pack_hash: str | None = None,
) -> LocalAnalysisService:
    """Build the production service; R is available only with exact authority."""
    if bool(frozen_pack_root) != bool(frozen_pack_hash):
        raise ValueError("frozen R pack root and hash must be supplied together")
    library = FrozenRLibrary(frozen_pack_root) if frozen_pack_root is not None else None
    authorities = (
        library.load(frozen_pack_hash)
        if library is not None and frozen_pack_hash is not None
        else ()
    )
    backend: LocalAnalysisBackend = _UnavailableBackend(authorities)
    if r_executable is not None and r_sha256 is not None:
        backend = TrustedLocalRBackend(
            executable=r_executable,
            expected_sha256=r_sha256,
            executor=BoundedRSubprocessExecutor(),
            managed_library=library,
            package_authorities=authorities,
        )
    return LocalAnalysisService(ResearchArtifactStore(run_root), backend)


def _emit(payload: object) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _fail(code: str, message: str, *, exit_code: int = 2) -> NoReturn:
    _emit({"error": {"code": code, "message": message}})
    raise typer.Exit(code=exit_code)


def _load_spec(path: Path) -> AnalysisSpec:
    try:
        payload = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)
        if not isinstance(payload, dict):
            raise TypeError("analysis spec must contain one YAML mapping")
        return ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))
    except (
        OSError,
        UnicodeError,
        YAMLError,
        ValidationError,
        TypeError,
        ValueError,
    ) as error:
        _fail("LOCAL_SPEC_INVALID", str(error))


class _StrictLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Safe YAML loader that rejects ambiguous duplicate or merge authority."""


def _strict_mapping(
    loader: _StrictLoader, node: MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            raise ConstructorError(
                None, None, "YAML merge keys are not allowed", key_node.start_mark
            )
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                None, None, f"duplicate YAML key: {key}", key_node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_mapping
)


def _load_reference(path: Path) -> LocalAnalysisReference:
    try:
        return LocalAnalysisReference.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError) as error:
        _fail("ANALYSIS_REFERENCE_INVALID", str(error))


def _load_artifact_reference(path: Path) -> ArtifactRef:
    try:
        return ArtifactRef.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError) as error:
        _fail("EXIT_REFERENCE_INVALID", str(error))


def _validated_root(path: Path) -> Path:
    if path.is_symlink():
        _fail("ANALYSIS_ROOT_INVALID", "analysis root must not be a symlink")
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or (
        resolved.exists() and not resolved.is_dir()
    ):
        _fail("ANALYSIS_ROOT_INVALID", "analysis root must be a dedicated directory")
    return resolved


def _validated_runtime(path: Path, digest: str) -> tuple[Path, str]:
    """Reject malformed runtime authority before any snapshot is persisted."""
    if not path.is_absolute() or path.is_symlink() or not SHA256.fullmatch(digest):
        _fail(
            "R_RUNTIME_AUTHORITY_INVALID",
            "Rscript must be an absolute non-symlink path with lowercase SHA-256",
        )
    try:
        metadata = path.lstat()
    except OSError as error:
        _fail("R_RUNTIME_AUTHORITY_INVALID", str(error))
    if not stat.S_ISREG(metadata.st_mode):
        _fail("R_RUNTIME_AUTHORITY_INVALID", "Rscript must be a regular file")
    return path, digest


def _payload(
    reference: LocalAnalysisReference, report: LocalAnalysisReport
) -> dict[str, object]:
    return {
        "reference": reference.model_dump(mode="json"),
        "report": report.model_dump(mode="json"),
    }


@econometrics_app.command("validate")
def econometrics_validate(
    spec_path: Annotated[Path, typer.Argument(help="Local analysis YAML spec.")],
    json_output: JsonOption = False,
) -> None:
    """Validate exact local CSV bytes without persistence or execution."""
    del json_output
    spec = _load_spec(spec_path)
    try:
        validation = _service_for(Path.cwd() / ".envresearch-local").validate(spec)
    except (OSError, TypeError, ValueError) as error:
        _fail("LOCAL_DATA_INVALID", str(error))
    _emit(
        {
            "method_id": spec.method_id,
            "required_columns": required_columns_for(spec),
            "valid": True,
            **validation.model_dump(mode="json"),
        }
    )


@econometrics_app.command("run")
def econometrics_run(
    spec_path: Annotated[Path, typer.Argument(help="Local analysis YAML spec.")],
    run_root: Annotated[
        Path, typer.Option("--run-root", help="Dedicated durable analysis root.")
    ],
    r_executable: Annotated[
        Path, typer.Option("--r-executable", help="Reviewed absolute Rscript path.")
    ],
    r_sha256: Annotated[
        str, typer.Option("--r-sha256", help="Reviewed Rscript SHA-256.")
    ],
    frozen_pack_root: Annotated[
        Path | None, typer.Option("--frozen-r-pack-root")
    ] = None,
    frozen_pack_hash: Annotated[
        str | None, typer.Option("--frozen-r-pack-hash")
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Run one registered recipe using explicit local runtime authority."""
    del json_output
    spec = _load_spec(spec_path)
    root = _validated_root(run_root)
    r_executable, r_sha256 = _validated_runtime(r_executable, r_sha256)
    try:
        service = _service_for(
            root,
            r_executable=r_executable,
            r_sha256=r_sha256,
            frozen_pack_root=frozen_pack_root,
            frozen_pack_hash=frozen_pack_hash,
        )
        reference = service.run(spec)
        report = service.status(reference)
    except (EvidenceTampered, OSError, TypeError, ValueError) as error:
        _fail("LOCAL_ANALYSIS_INVALID", str(error))
    _emit(_payload(reference, report))
    if report.status != "passed":
        raise typer.Exit(code=1)


@econometrics_app.command("status")
def econometrics_status(
    reference_path: Annotated[
        Path, typer.Argument(help="Exact LocalAnalysisReference JSON.")
    ],
    run_root: Annotated[
        Path, typer.Option("--run-root", help="Dedicated durable analysis root.")
    ],
    frozen_pack_root: Annotated[
        Path | None, typer.Option("--frozen-r-pack-root")
    ] = None,
    frozen_pack_hash: Annotated[
        str | None, typer.Option("--frozen-r-pack-hash")
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Reopen and independently verify one exact persisted report."""
    del json_output
    reference = _load_reference(reference_path)
    try:
        report = _service_for(
            _validated_root(run_root),
            frozen_pack_root=frozen_pack_root,
            frozen_pack_hash=frozen_pack_hash,
        ).status(reference)
    except (EvidenceTampered, OSError, TypeError, ValueError) as error:
        _fail("ANALYSIS_EVIDENCE_INVALID", str(error))
    _emit(_payload(reference, report))


@econometrics_app.command("exit-run")
def econometrics_exit_run(
    manifest_path: Annotated[
        Path, typer.Argument(help="Exact manifest reference JSON.")
    ],
    catalog_path: Annotated[Path, typer.Argument(help="Exact catalog reference JSON.")],
    runner_root: Annotated[Path, typer.Option("--runner-root")],
    evaluator_root: Annotated[Path, typer.Option("--evaluator-root")],
    analysis_root: Annotated[Path, typer.Option("--analysis-root")],
    r_executable: Annotated[Path, typer.Option("--r-executable")],
    r_sha256: Annotated[str, typer.Option("--r-sha256")],
    frozen_pack_root: Annotated[
        Path | None, typer.Option("--frozen-r-pack-root")
    ] = None,
    frozen_pack_hash: Annotated[
        str | None, typer.Option("--frozen-r-pack-hash")
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Run and independently evaluate one exact blinded V0.3 manifest."""
    del json_output
    manifest_ref = _load_artifact_reference(manifest_path)
    catalog_ref = _load_artifact_reference(catalog_path)
    runner_path = _validated_root(runner_root)
    evaluator_path = _validated_root(evaluator_root)
    analysis_path = _validated_root(analysis_root)
    validate_separate_roots(runner_path, evaluator_path)
    validate_separate_roots(runner_path, analysis_path)
    validate_separate_roots(evaluator_path, analysis_path)
    executable, digest = _validated_runtime(r_executable, r_sha256)
    try:
        service = _service_for(
            analysis_path,
            r_executable=executable,
            r_sha256=digest,
            frozen_pack_root=frozen_pack_root,
            frozen_pack_hash=frozen_pack_hash,
        )
        runner_registry = ExitRegistry(runner_path)
        evaluator_registry = ExitRegistry(evaluator_path)
        executor = RegistryAnalysisExecutor(runner_registry, service)
        run_ref = V03ExitRunner(runner_registry, executor).run(manifest_ref)
        evaluator = V03ExitEvaluator(runner_registry, evaluator_registry, service)
        report_ref, report = evaluator.evaluate_reference(run_ref, catalog_ref)
    except (EvidenceTampered, OSError, TypeError, ValueError) as error:
        _fail("V03_EXIT_INVALID", str(error))
    _emit(
        {
            "run_reference": run_ref.model_dump(mode="json"),
            "report_reference": report_ref.model_dump(mode="json"),
            "report": report.model_dump(mode="json"),
        }
    )
    if report.status != "passed":
        raise typer.Exit(code=1)


@econometrics_app.command("exit-status")
def econometrics_exit_status(
    reference_path: Annotated[
        Path, typer.Argument(help="Exact exit report reference JSON.")
    ],
    runner_root: Annotated[Path, typer.Option("--runner-root")],
    evaluator_root: Annotated[Path, typer.Option("--evaluator-root")],
    analysis_root: Annotated[Path, typer.Option("--analysis-root")],
    frozen_pack_root: Annotated[
        Path | None, typer.Option("--frozen-r-pack-root")
    ] = None,
    frozen_pack_hash: Annotated[
        str | None, typer.Option("--frozen-r-pack-hash")
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Read one exact current exit report without executing or discovering latest."""
    del json_output
    reference = _load_artifact_reference(reference_path)
    try:
        runner = ExitRegistry(_validated_root(runner_root), create=False)
        evaluator = ExitRegistry(_validated_root(evaluator_root), create=False)
        service = _service_for(
            _validated_root(analysis_root),
            frozen_pack_root=frozen_pack_root,
            frozen_pack_hash=frozen_pack_hash,
        )
        report = V03ExitEvaluator(runner, evaluator, service).status(reference)
    except (OSError, TypeError, ValueError) as error:
        _fail("V03_EXIT_STATUS_INVALID", str(error))
    _emit(
        {
            "report_reference": reference.model_dump(mode="json"),
            "report": report.model_dump(mode="json"),
        }
    )


from envresearch.econometrics.valuation_exit_cli import register_valuation_exit_commands

register_valuation_exit_commands(
    econometrics_app,
    load_reference=_load_artifact_reference,
    validated_root=_validated_root,
    validated_runtime=_validated_runtime,
    service_for=lambda *args, **kwargs: _service_for(*args, **kwargs),
    emit=_emit,
    fail=_fail,
)

"""Exact-reference CLI commands for the compact Valuation Core exit."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from envresearch.econometrics.exit_evaluator import ValuationExitEvaluator
from envresearch.econometrics.exit_registry import ExitRegistry, validate_separate_roots
from envresearch.econometrics.service import EvidenceTampered
from envresearch.econometrics.valuation_exit_runner import (
    ValuationExitRunner,
    ValuationRegistryAnalysisExecutor,
)


def register_valuation_exit_commands(
    app: typer.Typer,
    *,
    load_reference: Callable[[Path], Any],
    validated_root: Callable[[Path], Path],
    validated_runtime: Callable[[Path, str], tuple[Path, str]],
    service_for: Callable[..., Any],
    emit: Callable[[object], None],
    fail: Callable[..., Any],
) -> None:
    """Attach separate run, evaluate, and status operations to the shared CLI."""

    @app.command("valuation-exit-run")
    def valuation_exit_run(
        manifest_path: Annotated[Path, typer.Argument(help="Exact valuation manifest reference JSON.")],
        runner_root: Annotated[Path, typer.Option("--runner-root")],
        evaluator_root: Annotated[Path, typer.Option("--evaluator-root")],
        analysis_root: Annotated[Path, typer.Option("--analysis-root")],
        r_executable: Annotated[Path, typer.Option("--r-executable")],
        r_sha256: Annotated[str, typer.Option("--r-sha256")],
        frozen_pack_root: Annotated[Path | None, typer.Option("--frozen-r-pack-root")] = None,
        frozen_pack_hash: Annotated[str | None, typer.Option("--frozen-r-pack-hash")] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
    ) -> None:
        """Run one exact Valuation Core manifest without accessing expectations."""
        del json_output
        manifest_ref = load_reference(manifest_path)
        runner_path = validated_root(runner_root)
        evaluator_path = validated_root(evaluator_root)
        analysis_path = validated_root(analysis_root)
        validate_separate_roots(runner_path, evaluator_path)
        validate_separate_roots(runner_path, analysis_path)
        validate_separate_roots(evaluator_path, analysis_path)
        executable, digest = validated_runtime(r_executable, r_sha256)
        try:
            service = service_for(
                analysis_path,
                r_executable=executable,
                r_sha256=digest,
                frozen_pack_root=frozen_pack_root,
                frozen_pack_hash=frozen_pack_hash,
            )
            registry = ExitRegistry(runner_path)
            reference = ValuationExitRunner(
                registry, ValuationRegistryAnalysisExecutor(registry, service)
            ).run(manifest_ref)
        except (EvidenceTampered, OSError, TypeError, ValueError) as error:
            fail("VALUATION_EXIT_INVALID", str(error))
        emit({"run_reference": reference.model_dump(mode="json")})

    @app.command("valuation-exit-evaluate")
    def valuation_exit_evaluate(
        run_path: Annotated[Path, typer.Argument(help="Exact valuation run reference JSON.")],
        catalog_path: Annotated[Path, typer.Argument(help="Exact valuation catalog reference JSON.")],
        runner_root: Annotated[Path, typer.Option("--runner-root")],
        evaluator_root: Annotated[Path, typer.Option("--evaluator-root")],
        analysis_root: Annotated[Path, typer.Option("--analysis-root")],
        frozen_pack_root: Annotated[Path | None, typer.Option("--frozen-r-pack-root")] = None,
        frozen_pack_hash: Annotated[str | None, typer.Option("--frozen-r-pack-hash")] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
    ) -> None:
        """Independently evaluate one exact completed Valuation Core run."""
        del json_output
        run_ref = load_reference(run_path)
        catalog_ref = load_reference(catalog_path)
        try:
            runner_path = validated_root(runner_root)
            evaluator_path = validated_root(evaluator_root)
            analysis_path = validated_root(analysis_root)
            validate_separate_roots(runner_path, evaluator_path)
            validate_separate_roots(runner_path, analysis_path)
            validate_separate_roots(evaluator_path, analysis_path)
            service = service_for(
                analysis_path,
                frozen_pack_root=frozen_pack_root,
                frozen_pack_hash=frozen_pack_hash,
            )
            reference, report = ValuationExitEvaluator(
                ExitRegistry(runner_path), ExitRegistry(evaluator_path), service
            ).evaluate_reference(run_ref, catalog_ref)
        except (EvidenceTampered, OSError, TypeError, ValueError) as error:
            fail("VALUATION_EXIT_INVALID", str(error))
        emit(
            {
                "report_reference": reference.model_dump(mode="json"),
                "report": report.model_dump(mode="json"),
            }
        )
        if report.status != "passed":
            raise typer.Exit(code=1)

    @app.command("valuation-exit-status")
    def valuation_exit_status(
        reference_path: Annotated[Path, typer.Argument(help="Exact valuation report reference JSON.")],
        runner_root: Annotated[Path, typer.Option("--runner-root")],
        evaluator_root: Annotated[Path, typer.Option("--evaluator-root")],
        analysis_root: Annotated[Path, typer.Option("--analysis-root")],
        frozen_pack_root: Annotated[Path | None, typer.Option("--frozen-r-pack-root")] = None,
        frozen_pack_hash: Annotated[str | None, typer.Option("--frozen-r-pack-hash")] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
    ) -> None:
        """Read one exact current Valuation Core report without writing state."""
        del json_output
        reference = load_reference(reference_path)
        try:
            runner_path = validated_root(runner_root)
            evaluator_path = validated_root(evaluator_root)
            analysis_path = validated_root(analysis_root)
            validate_separate_roots(runner_path, evaluator_path)
            validate_separate_roots(runner_path, analysis_path)
            validate_separate_roots(evaluator_path, analysis_path)
            runner = ExitRegistry(runner_path, create=False)
            evaluator = ExitRegistry(evaluator_path, create=False)
            service = service_for(
                analysis_path,
                frozen_pack_root=frozen_pack_root,
                frozen_pack_hash=frozen_pack_hash,
            )
            report = ValuationExitEvaluator(runner, evaluator, service).status(reference)
        except (OSError, TypeError, ValueError) as error:
            fail("VALUATION_EXIT_STATUS_INVALID", str(error))
        emit(
            {
                "report_reference": reference.model_dump(mode="json"),
                "report": report.model_dump(mode="json"),
            }
        )

"""Independent recomputation for persisted local econometrics evidence."""

from __future__ import annotations

from pathlib import Path

from envresearch.econometrics._causal_support import support_matches_snapshot
from envresearch.econometrics._store_files import StoreFiles
from envresearch.econometrics._valuation_authority import (
    external_package_authority_matches,
)
from envresearch.econometrics._valuation_verification import (
    is_valuation_pair,
    same_r_version,
    valuation_configuration_matches,
)
from envresearch.econometrics._verification_evidence import check_hash
from envresearch.econometrics._wave1_support import wave_result_matches_snapshot
from envresearch.econometrics.causal_contracts import Iv2slsSpec, PanelFeSpec, RddSpec
from envresearch.econometrics.causal_models import (
    Iv2slsResult,
    PanelFeResult,
    RddResult,
)
from envresearch.econometrics.contracts import LocalAnalysisSpec
from envresearch.econometrics.data_snapshot import snapshot_metadata_matches
from envresearch.econometrics.did_diagnostics import pretrend_exceeded
from envresearch.econometrics.did_models import DidResult
from envresearch.econometrics.r_evidence import PackageAuthority
from envresearch.econometrics.recipes import expected_script_for, recipe_for
from envresearch.econometrics.report import LocalAnalysisReport
from envresearch.econometrics.wave1_contracts import (
    EnvironmentalMeasurementSpec,
    MetaAnalysisSpec,
    RctSpec,
    SyntheticControlSpec,
)
from envresearch.econometrics.wave1_results import (
    MeasurementResult,
    MetaAnalysisResult,
    RctResult,
    SyntheticControlResult,
)


class LocalAnalysisVerifier:
    """Reopen raw bytes and recompute typed outputs without trusting PASSED."""

    def __init__(
        self,
        root: Path,
        expected_package_authorities: tuple[PackageAuthority, ...] | None = None,
    ) -> None:
        self.root = root
        self.files = StoreFiles(root)
        self.expected_package_authorities = expected_package_authorities

    def verify(self, report: LocalAnalysisReport) -> tuple[str, ...]:
        """Return deterministic findings; an empty tuple alone permits PASSED."""
        findings: list[str] = []
        if report.status != "passed":
            return report.verification_findings
        if report.snapshot is None:
            return ("SNAPSHOT_MISSING",)
        if report.script_path is None or report.script_sha256 is None:
            findings.append("SCRIPT_MISSING")
        else:
            script_data = check_hash(
                self.files,
                report.script_path,
                report.script_sha256,
                "SCRIPT_TAMPERED",
                findings,
            )
            _check_script(report, script_data, findings)
        snapshot_data = check_hash(
            self.files,
            report.snapshot.relative_path,
            report.snapshot.sha256,
            "SNAPSHOT_TAMPERED",
            findings,
        )
        if snapshot_data is not None:
            try:
                matches = snapshot_metadata_matches(
                    snapshot_data, report.spec, report.snapshot
                )
            except ValueError:
                matches = False
            if not matches:
                findings.append("SNAPSHOT_METADATA_MISMATCH")
        try:
            recipe = recipe_for(
                report.spec.method_id,
                workspace=self.root / "verification" / report.analysis_id,
            )
        except (KeyError, ValueError):
            recipe = None
            findings.append("METHOD_NOT_REGISTERED")
        if (
            recipe is not None
            and {item.name for item in report.outputs} != recipe.expected_outputs
        ):
            findings.append("OUTPUT_SET_INVALID")
        for item in report.outputs:
            data = check_hash(
                self.files,
                item.relative_path,
                item.sha256,
                f"OUTPUT_TAMPERED:{item.name}",
                findings,
            )
            if data is not None and len(data) != item.size_bytes:
                findings.append(f"OUTPUT_TAMPERED:{item.name}")
        log_data: dict[str, bytes] = {}
        for item in report.logs:
            data = check_hash(
                self.files,
                item.relative_path,
                item.sha256,
                f"LOG_TAMPERED:{item.name}",
                findings,
            )
            if data is not None:
                log_data[item.name] = data
                if len(data) != item.size_bytes:
                    findings.append(f"LOG_TAMPERED:{item.name}")
        if report.runtime_path is None or report.execution is None:
            findings.append("RUNTIME_EVIDENCE_MISSING")
        else:
            runtime_data = check_hash(
                self.files,
                report.runtime_path,
                report.execution.runtime.sha256,
                "RUNTIME_TAMPERED",
                findings,
            )
            if (
                runtime_data is not None
                and len(runtime_data) != report.execution.runtime.size_bytes
            ):
                findings.append("RUNTIME_TAMPERED")
        if report.output_root is None or report.result is None or recipe is None:
            findings.append("RESULT_MISSING")
        else:
            try:
                authorities = (
                    ()
                    if report.execution is None
                    else tuple(
                        item.ref() for item in report.execution.package_authorities
                    )
                )
                if report.spec.method_id in {
                    "rct-itt",
                    "environmental-measurement",
                    "synthetic-control",
                    "meta-analysis",
                    "hedonic-pricing",
                    "travel-cost",
                    "contingent-valuation",
                    "dce-clogit",
                }:
                    recomputed = recipe.parse(
                        self.root / report.output_root, authorities
                    )
                else:
                    recomputed = recipe.parse(self.root / report.output_root)
            except (ValueError, OSError):
                findings.append("OUTPUT_INVALID")
            else:
                if recomputed != report.result:
                    findings.append("RESULT_MISMATCH")
                _check_configuration(
                    report,
                    snapshot_data,
                    findings,
                    self.root,
                    self.expected_package_authorities,
                )
        if report.execution is None:
            findings.append("EXECUTION_EVIDENCE_INVALID")
        else:
            _check_execution(report, log_data, findings)
        return tuple(sorted(set(findings)))


def _check_script(
    report: LocalAnalysisReport, data: bytes | None, findings: list[str]
) -> None:
    if data is None or report.execution is None:
        return
    try:
        expected, digest, template_id = expected_script_for(report.spec)
    except (KeyError, ValueError):
        findings.append("SCRIPT_NOT_REGISTERED")
        return
    script = report.execution.script
    if (
        data != expected
        or report.script_sha256 != digest
        or script.sha256 != digest
        or script.template_id != template_id
    ):
        findings.append("SCRIPT_NOT_REGISTERED")


def _check_execution(
    report: LocalAnalysisReport,
    logs: dict[str, bytes],
    findings: list[str],
) -> None:
    execution = report.execution
    assert execution is not None
    expected_logs = {
        "stdout.log": execution.redacted_stdout.encode("utf-8"),
        "stderr.log": execution.redacted_stderr.encode("utf-8"),
    }
    if (
        execution.return_code != 0
        or set(logs) != set(expected_logs)
        or any(logs.get(name) != data for name, data in expected_logs.items())
        or execution.script.sha256 != report.script_sha256
        or len(execution.argv) != 3
        or execution.argv[0] != str(execution.runtime.executable)
        or execution.argv[1] != "--vanilla"
        or not (
            execution.argv[2] == str(execution.script.path)
            or (
                execution.argv[2].startswith("/dev/fd/")
                and execution.argv[2][8:].isdigit()
            )
        )
    ):
        findings.append("EXECUTION_EVIDENCE_INVALID")


def _check_configuration(
    report: LocalAnalysisReport,
    snapshot_data: bytes | None,
    findings: list[str],
    root: Path,
    expected_package_authorities: tuple[PackageAuthority, ...] | None,
) -> None:
    """Bind emitted package configuration to the approved analysis spec."""
    if report.result is None:
        return
    spec = report.spec
    result = report.result
    mismatch = False
    if isinstance(spec, LocalAnalysisSpec) and isinstance(result, DidResult):
        if pretrend_exceeded(spec, result):
            findings.append("DID_PRETREND_EXCEEDED")
        packages = result.packages
        mismatch = (
            packages.bootstrap_seed != spec.inference.bootstrap_seed
            or packages.comparison_group != spec.comparison_group
            or packages.reference_period != spec.reference_period
            or packages.confidence_level != spec.inference.confidence_level
            or packages.interval_mode != spec.inference.interval_mode
            or packages.cluster_column != spec.inference.cluster_column
            or packages.anticipation != 0
        )
    elif isinstance(spec, PanelFeSpec) and isinstance(result, PanelFeResult):
        mismatch = (
            tuple(item.term for item in result.coefficients) != spec.columns.regressors
            or snapshot_data is None
            or not support_matches_snapshot(snapshot_data, spec, result.support)
            or result.configuration.fixed_effects != spec.columns.fixed_effects
            or result.configuration.estimator_label != "fixest::feols-panel-fe"
            or not _causal_configuration_matches(spec, result)
        )
    elif isinstance(spec, Iv2slsSpec) and isinstance(result, Iv2slsResult):
        mismatch = (
            tuple(item.term for item in result.structural) != spec.columns.endogenous
            or snapshot_data is None
            or not support_matches_snapshot(snapshot_data, spec, result.support)
            or tuple(item.term for item in result.reduced_form)
            != spec.columns.instruments
            or any(
                item.instruments != spec.columns.instruments
                or item.threshold != spec.weak_instrument_f_threshold
                for item in result.first_stage
            )
            or result.configuration.fixed_effects != spec.columns.fixed_effects
            or result.configuration.estimator_label != "fixest::feols-2sls"
            or (result.overidentification is not None)
            != (len(spec.columns.instruments) > len(spec.columns.endogenous))
            or not _causal_configuration_matches(spec, result)
        )
    elif isinstance(spec, RddSpec) and isinstance(result, RddResult):
        mismatch = (
            tuple(item.term for item in result.covariate_continuity)
            != spec.columns.covariates
            or snapshot_data is None
            or not support_matches_snapshot(snapshot_data, spec, result.support)
            or result.configuration.estimator_label != "sharp-local-linear"
            or result.configuration.cutoff != spec.design.cutoff
            or result.configuration.bandwidth != spec.design.bandwidth
            or result.configuration.kernel != spec.design.kernel
            or result.configuration.donut_radius != spec.design.donut_radius
            or not _causal_configuration_matches(spec, result)
        )
    elif isinstance(spec, RctSpec) and isinstance(result, RctResult):
        mismatch = (
            result.unadjusted.term != spec.columns.assignment
            or result.ancova.term != spec.columns.assignment
            or result.max_attrition_rate != spec.max_attrition_rate
            or result.balance_smd_threshold != spec.balance_smd_threshold
            or snapshot_data is None
            or not wave_result_matches_snapshot(snapshot_data, spec, result)
            or result.configuration.method_id != spec.method_id
            or result.configuration.confidence_level != spec.inference.confidence_level
            or report.execution is None
            or result.configuration.package_authorities
            != tuple(item.ref() for item in report.execution.package_authorities)
        )
    elif isinstance(spec, EnvironmentalMeasurementSpec) and isinstance(
        result, MeasurementResult
    ):
        mismatch = (
            result.max_missing_rate != spec.max_missing_rate
            or result.declared_unit != spec.declared_unit
            or result.exceedance_threshold != spec.exceedance_threshold
            or snapshot_data is None
            or not wave_result_matches_snapshot(snapshot_data, spec, result)
            or result.configuration.method_id != spec.method_id
            or report.execution is None
            or result.configuration.package_authorities
            != tuple(item.ref() for item in report.execution.package_authorities)
        )
    elif isinstance(spec, SyntheticControlSpec) and isinstance(
        result, SyntheticControlResult
    ):
        mismatch = (
            result.max_pre_rmspe != spec.max_pre_rmspe
            or result.leave_one_out_threshold != spec.max_leave_one_out_change
            or result.intervention_time != spec.intervention_time
            or snapshot_data is None
            or not wave_result_matches_snapshot(snapshot_data, spec, result)
            or result.configuration.method_id != spec.method_id
            or report.execution is None
            or not same_r_version(
                result.configuration.r_version, report.execution.runtime.version
            )
            or result.configuration.package_authorities
            != tuple(item.ref() for item in report.execution.package_authorities)
        )
    elif isinstance(spec, MetaAnalysisSpec) and isinstance(result, MetaAnalysisResult):
        mismatch = (
            result.leave_one_out_threshold != spec.max_leave_one_out_change
            or result.model != spec.model
            or result.configuration.confidence_level != spec.confidence_level
            or snapshot_data is None
            or not wave_result_matches_snapshot(snapshot_data, spec, result)
            or result.configuration.method_id != spec.method_id
            or report.execution is None
            or not same_r_version(
                result.configuration.r_version, report.execution.runtime.version
            )
            or result.configuration.package_authorities
            != tuple(item.ref() for item in report.execution.package_authorities)
        )
    elif is_valuation_pair(spec, result):
        mismatch = not valuation_configuration_matches(
            snapshot_data,
            spec,  # type: ignore[arg-type]
            result,  # type: ignore[arg-type]
            None if report.execution is None else report.execution.runtime.version,
            ()
            if report.execution is None
            else tuple(item.ref() for item in report.execution.package_authorities),
            () if report.execution is None else report.execution.package_authorities,
            None if report.output_root is None else root / report.output_root,
        ) or not external_package_authority_matches(
            spec,  # type: ignore[arg-type]
            () if report.execution is None else report.execution.package_authorities,
            expected_package_authorities,
        )
    else:
        mismatch = True
    if mismatch:
        findings.append("CONFIGURATION_MISMATCH")


def _causal_configuration_matches(
    spec: PanelFeSpec | Iv2slsSpec | RddSpec,
    result: PanelFeResult | Iv2slsResult | RddResult,
) -> bool:
    configuration = result.configuration
    return (
        configuration.method_id == spec.method_id
        and configuration.confidence_level == spec.inference.confidence_level
        and configuration.cluster_column == spec.inference.cluster_column
    )

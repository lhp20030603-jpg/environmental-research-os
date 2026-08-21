"""Disposable case-root guards, synthetic evidence, and correct-stop execution."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator

from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER, AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.installed_package_authority import (
    InstalledPackageAuthority,
)
from envresearch.econometrics.r_evidence import RExecutionEvidence, RRuntimeIdentity
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.service import BackendResult
from envresearch.econometrics.valuation_contracts import HedonicSpec
from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import STRICT, require_nonblank
from envresearch.personal_validation.contracts import (
    PERSONAL_ATTEMPT_ROOTS_V1,
    InputSnapshot,
    PersonalValidationProtocol,
)
from envresearch.personal_validation.roots import (
    RootExclusionSet,
    require_exact_root_authority_manifest,
)
from envresearch.research.stop_contracts import ResearchStopInspection

if TYPE_CHECKING:
    from envresearch.personal_validation.canonical_cases import (
        CaseExecutionContext,
        DisposableAttemptRoots,
    )


class CanonicalPolicyRule(BaseModel):
    model_config = STRICT
    rule_id: str
    requirement: str

    @field_validator("rule_id", "requirement")
    @classmethod
    def require_text(cls, value: str) -> str:
        return require_nonblank(value)


class CanonicalPolicyArtifact(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.policy-artifact.v1"]
    policy_kind: Literal[
        "scientific",
        "evidence",
        "synthesis",
        "external-access",
        "rubric",
        "report-schema",
    ]
    policy_version: str
    rules: tuple[CanonicalPolicyRule, ...] = Field(min_length=1)

    @field_validator("policy_version")
    @classmethod
    def require_version(cls, value: str) -> str:
        return require_nonblank(value)

    @field_validator("rules")
    @classmethod
    def require_rule_order(
        cls, value: tuple[CanonicalPolicyRule, ...]
    ) -> tuple[CanonicalPolicyRule, ...]:
        keys = tuple(item.rule_id for item in value)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("policy rules must be unique and canonically sorted")
        return value


@dataclass(frozen=True, slots=True)
class ProtocolPolicyArtifacts:
    scientific: CanonicalPolicyArtifact
    evidence: CanonicalPolicyArtifact
    synthesis: CanonicalPolicyArtifact
    external_access: CanonicalPolicyArtifact
    rubric: CanonicalPolicyArtifact
    report_schema: CanonicalPolicyArtifact


class SyntheticHedonicBackend:
    """Deterministic repository-fixture backend exercised by real verification."""

    package_authorities = (
        InstalledPackageAuthority(
            schema_version="econometrics.frozen-r-package.v1",
            authority_kind="frozen-local-tree",
            package="fixest",
            version="1.0.0",
            observed_license="GPL-3",
            description_sha256=hashlib.sha256(b"fixest").hexdigest(),
            installed_tree_sha256=hashlib.sha256(b"fixest").hexdigest(),
            package_relative_path=Path("authorities/frozen-r-pack/library/fixest"),
            dependencies=(),
            r_version="4.4.3",
            pack_hash=hashlib.sha256(b"fixest").hexdigest(),
            observed_at=datetime(2026, 8, 12, tzinfo=UTC),
        ),
    )

    def execute(
        self,
        spec: AnalysisSpec,
        snapshot: LocalDataSnapshot,
        snapshot_bytes: bytes,
        workspace: Path,
    ) -> BackendResult:
        if (
            spec.method_id != "hedonic-pricing"
            or hashlib.sha256(snapshot_bytes).hexdigest() != snapshot.sha256
        ):
            raise ValueError("canonical Hedonic input changed")
        recipe = recipe_for(spec.method_id, workspace=workspace)
        script = recipe.render(spec, snapshot)
        output = workspace / "output"
        _write_hedonic_outputs(output, spec)
        result = recipe.parse(
            output, tuple(item.ref() for item in self.package_authorities)
        )
        runtime = workspace / "runtime-fixture"
        runtime.write_bytes(b"repository synthetic R 4.4.3")
        runtime.chmod(0o555)
        metadata = runtime.stat()
        identity = RRuntimeIdentity(
            source_executable=runtime,
            executable=runtime,
            sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
            version="Rscript (R) version 4.4.3 (2025-02-28)",
            device=metadata.st_dev,
            inode=metadata.st_ino,
            size_bytes=metadata.st_size,
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
            package_authorities=self.package_authorities,
        )
        return BackendResult(
            script=script, execution=execution, result=result, output_root=output
        )


def hedonic_input(repository: Path) -> tuple[HedonicSpec, bytes]:
    root = repository / "benchmarks/econometrics/valuation-core/runner"
    raw = json.loads((root / "green-hedonic.yaml").read_bytes())["spec"]
    raw["data_path"] = root / raw["data_path"]
    raw["columns"]["controls"] = tuple(raw["columns"]["controls"])
    raw["columns"]["fixed_effects"] = tuple(raw["columns"]["fixed_effects"])
    spec = ANALYSIS_SPEC_ADAPTER.validate_python(raw)
    if not isinstance(spec, HedonicSpec):
        raise TypeError("canonical Hedonic descriptor has the wrong method")
    return spec, spec.data_path.read_bytes()


def require_disposable_roots(roots: DisposableAttemptRoots) -> None:
    require_exact_root_authority_manifest(roots.store.root, roots.exclusions)
    if not roots._case_pin.is_exact_descendant_of(
        roots.store.root, roots.case_namespace
    ):
        raise ValueError("case root is detached from Personal store authority")
    expected = roots.logical_roots
    if set(expected) != set(PERSONAL_ATTEMPT_ROOTS_V1):
        raise ValueError("case root set is incomplete")
    pins = dict(roots._root_pins)
    if set(pins) != set(PERSONAL_ATTEMPT_ROOTS_V1):
        raise ValueError("case root pin set is incomplete")
    physical = physical_attempt_roots()
    if roots._case_pin.list_directory(Path()) != tuple(
        sorted(path.name for path in physical.values())
    ):
        raise ValueError("case root child inventory changed")
    values = tuple(expected.values())
    if len(set(values)) != len(values):
        raise ValueError("case roots overlap")
    for name, pin in pins.items():
        if (
            not pin.is_exact_descendant_of(roots._case_pin, physical[name])
            or pin.lexical_path != expected[name]
        ):
            raise ValueError("case writer escaped its pinned namespace")


def snapshot_exclusion_trees(
    exclusions: RootExclusionSet,
) -> tuple[tuple[object, ...], ...]:
    entries: list[tuple[object, ...]] = []
    roots = sorted({path.resolve(strict=True) for path in exclusion_paths(exclusions)})
    for root in roots:
        pending = [root]
        while pending:
            path = pending.pop()
            metadata = path.lstat()
            relative = "." if path == root else path.relative_to(root).as_posix()
            kind = _file_kind(metadata.st_mode)
            content = (
                os.readlink(path)
                if kind == "symlink"
                else hashlib.sha256(path.read_bytes()).hexdigest()
                if kind == "file"
                else None
            )
            entries.append(
                (
                    str(root),
                    relative,
                    kind,
                    metadata.st_dev,
                    metadata.st_ino,
                    stat.S_IMODE(metadata.st_mode),
                    metadata.st_uid,
                    metadata.st_gid,
                    metadata.st_nlink,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    content,
                )
            )
            if kind == "directory":
                pending.extend(sorted(path.iterdir(), reverse=True))
    return tuple(entries)


def case_namespace(
    session_nonce: str, protocol_ref: ArtifactRef, case_ref: ArtifactRef
) -> Path:
    payload = json.dumps(
        {
            "session_nonce": session_nonce,
            "protocol_ref": protocol_ref.model_dump(mode="json"),
            "case_ref": case_ref.model_dump(mode="json"),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return Path("attempt-runs") / hashlib.sha256(payload).hexdigest()


def physical_attempt_roots() -> dict[str, Path]:
    return {
        name: (
            Path(".research-design.worker-queue-control")
            if name == "valuation-control"
            else Path(name)
        )
        for name in PERSONAL_ATTEMPT_ROOTS_V1
    }


def _file_kind(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def verify_input_entries(snapshot: InputSnapshot, root: Path, repository: Path) -> None:
    prefix = "benchmarks/personal-validation/v1/"
    for entry in snapshot.entries:
        path = (
            root / entry.logical_name.removeprefix(prefix)
            if entry.logical_name.startswith(prefix)
            else repository / entry.logical_name
        )
        metadata = path.lstat()
        data = path.read_bytes()
        if (
            entry.kind != "file"
            or not stat.S_ISREG(metadata.st_mode)
            or entry.sha256 != hashlib.sha256(data).hexdigest()
            or entry.size_bytes != len(data)
            or entry.mode != stat.S_IMODE(metadata.st_mode)
        ):
            raise ValueError("declared input bytes or metadata differ")


def load_policies(
    root: Path, protocol: PersonalValidationProtocol
) -> ProtocolPolicyArtifacts:
    entries = (
        (
            "scientific",
            root / "policies/scientific.json",
            protocol.scientific_policy_sha256,
        ),
        ("evidence", root / "policies/evidence.json", protocol.evidence_policy_sha256),
        (
            "synthesis",
            root / "policies/synthesis.json",
            protocol.synthesis_policy_sha256,
        ),
        (
            "external-access",
            root / "policies/external-access.json",
            protocol.external_access_policy_sha256,
        ),
        ("rubric", root / "rubric.json", protocol.rubric_sha256),
        ("report-schema", root / "report-schema.json", protocol.report_schema_sha256),
    )
    loaded: dict[str, CanonicalPolicyArtifact] = {}
    for kind, path, digest in entries:
        data = path.read_bytes()
        artifact = CanonicalPolicyArtifact.model_validate_json(data)
        if (
            data != artifact.model_dump_json().encode()
            or artifact.policy_kind != kind
            or hashlib.sha256(data).hexdigest() != digest
        ):
            raise ValueError("protocol policy bytes, digest, or kind differs")
        loaded[kind] = artifact
    return ProtocolPolicyArtifacts(
        loaded["scientific"],
        loaded["evidence"],
        loaded["synthesis"],
        loaded["external-access"],
        loaded["rubric"],
        loaded["report-schema"],
    )


def require_no_oracle_leak(
    reviewer_bytes: bytes, behavior: Any, expected_ref: ArtifactRef
) -> None:
    payload = behavior.model_dump(mode="json")
    tokens = {behavior.behavior_id, expected_ref.artifact_id, expected_ref.content_hash}
    for key, value in _string_items(payload):
        if key not in {"schema_version", "case_kind"}:
            tokens.add(value)
    normalized = reviewer_bytes.decode().casefold()
    if any(token.casefold() in normalized for token in tokens):
        raise ValueError("reviewer contract leaks expected behavior oracle")


def _string_items(value: object, key: str = "") -> tuple[tuple[str, str], ...]:
    if isinstance(value, dict):
        return tuple(
            item
            for child_key, child in value.items()
            for item in _string_items(child, str(child_key))
        )
    if isinstance(value, (list, tuple)):
        return tuple(item for child in value for item in _string_items(child, key))
    return ((key, value),) if isinstance(value, str) else ()


def exclusion_paths(exclusions: RootExclusionSet) -> tuple[Path, ...]:
    return (
        exclusions.repository,
        exclusions.git_common_dir,
        *exclusions.worktrees,
        *exclusions.obsidian_roots,
    )


def _write_hedonic_outputs(root: Path, spec: HedonicSpec) -> None:
    values = {
        "coefficients.csv": "term,estimate,std_error,confidence_low,confidence_high\npm25,-0.5,0.1,-0.6959963984540054,-0.3040036015459946\n",
        "covariance.csv": "row_term,column_term,value\npm25,pm25,0.01\n",
        "implicit_price.csv": f"name,estimate,std_error,confidence_low,confidence_high,currency,price_base,time_basis,population_basis,transformation,numerator_term,denominator_term\nimplicit-price,-84645.83333333335,16929.16666666667,-117826.39028827602,-51465.27637839068,{spec.currency},{spec.price_base},{spec.time_basis},{spec.population_basis},marginal-implicit-price,pm25,price\n",
        "support.csv": "observations,primary_units,groups,zero_or_no_count\n24,24,6,0\n",
        "collinearity.csv": f"condition_number,max_condition_number,max_vif,reference_price,reference_environment\n145.505300137852,{spec.max_condition_number:g},71.6041001906775,169291.6666666667,25.9166666666667\n",
        "sensitivity.csv": f"label,estimate,baseline_estimate,absolute_change,max_sensitivity_change,raw_coefficient,model_form\nalternative-functional-form,-84645.73333333335,-84645.83333333335,0.1,{spec.max_sensitivity_change:g},-84645.73333333335,{spec.sensitivity_form}\n",
        "package_configuration.csv": f"method_id,r_version,confidence_level,cluster_column,fixed_effects,functional_form,family,link\nhedonic-pricing,R version 4.4.3,{spec.confidence_level:g},{spec.cluster_column},{';'.join(spec.columns.fixed_effects)},{spec.functional_form},,\n",
        "hedonic_plot.svg": '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"><text>0</text></g></svg>\n',
    }
    root.mkdir(parents=True, exist_ok=True)
    for name, data in values.items():
        (root / name).write_text(data, encoding="utf-8")


def run_correct_stop_case(context: CaseExecutionContext) -> ResearchStopInspection:
    """Stop after the durable blocking review and reconstruct it read-only."""
    context.research.execute_until_blocking_review()
    return context.research.inspect_research_stop()

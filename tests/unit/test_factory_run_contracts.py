"""Behavioral contracts for one immutable governed research-factory run."""

from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

import pytest
from paper_draft_fixtures import evidence
from pydantic import ValidationError
from test_factory_design_contracts import _handoff
from test_paper_audit import _report

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.factory._store import _FactoryRunStore
from envresearch.factory.contracts import (
    BindingField,
    CapabilityProfileBinding,
    CrossStageBindingReport,
    FactoryRunStatus,
    ResearchFactoryRun,
    factory_run_id,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.release import PaperReleaseService

_A705_RUN_ID = "factory-run-39a3bd4fc20fac1f21c476f009c970a215bc78c6b8068a0729a886db9a3c704b"
_A705_RUN_SHA256 = "e45e7e3d2e6bbefe57b5a4a848e31d568496dc8abc72883dad96be698a748ddc"
_A705_RUN_GZIP_BASE64 = "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"


def _ref(artifact_id: str, payload: object) -> ArtifactRef:
    data = payload.model_dump_json().encode()  # type: ignore[attr-defined]
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_version=1,
        content_hash=hashlib.sha256(data).hexdigest(),
    )


def _run() -> ResearchFactoryRun:
    design = _handoff()
    report = _report(blocked=False)
    audit_ref = _ref(report.audit_id, report)
    release = PaperReleaseService._materialize(audit_ref, report)
    design_ref = _ref("approved-design-handoff", design)
    release_ref = _ref(release.release_id, release)
    claim = evidence()[0].claims[0]
    fields = tuple(
        BindingField(
            dimension=dimension,
            claim_id=claim.claim_id,
            design_value=design_value,
            release_value=release_value,
            relation="exact",
        )
        for dimension, design_value, release_value in (
            ("method", claim.method_id, claim.method_id),
            ("estimand", claim.quantity, claim.quantity),
            ("unit", claim.unit, claim.unit),
            ("population", claim.population_basis, claim.population_basis),
            ("time", claim.time_basis, claim.time_basis),
            ("price", claim.price_base, claim.price_base),
            ("strength", claim.allowed_strength, claim.allowed_strength),
            (
                "limitation",
                " | ".join(sorted(design.plan.fallback_rules)),
                " | ".join(sorted(claim.limitations)),
            ),
        )
    )
    binding = CrossStageBindingReport(
        schema_version="factory.cross-stage-binding.v1",
        producer="research-factory-coherence-v1",
        provenance_claim="retrospective-coherence",
        design_id=design.design_id,
        release_id=release.release_id,
        fields=fields,
        limitations=tuple(sorted({*design.plan.fallback_rules, *claim.limitations})),
        verdict="coherent",
    )
    refs = tuple(
        sorted(
            {
                design_ref,
                release_ref,
                design.manifest.intake_artifact,
                design.plan_ref,
                design.final_context_ref,
                *design.final_context.artifact_refs,
                release.audit_ref,
                release.draft_ref,
                release.map_ref,
                release.ledger_ref,
                release.citation_report_ref,
                *release.transitive_refs,
            },
            key=lambda item: (
                item.artifact_id,
                item.artifact_version,
                item.content_hash,
            ),
        )
    )
    profiles = tuple(
        CapabilityProfileBinding(
            profile_id=profile_id,
            registered_version=design.manifest.method_profiles[profile_id],
            sha256=digest,
        )
        for profile_id, digest in design.method_profile_sha256.items()
    )
    return ResearchFactoryRun(
        schema_version="factory.research-run.v1",
        factory_run_id=factory_run_id(design_ref, release_ref),
        producer="research-factory-run-v1",
        design_ref=design_ref,
        design=design,
        release_ref=release_ref,
        release=release,
        binding_report=binding,
        artifact_refs=refs,
        analysis_refs=release.analysis_refs,
        output_refs=release.output_refs,
        capability_profiles=profiles,
        assembly_verdict="assembled",
    )


def test_run_is_strict_frozen_and_identity_uses_both_exact_handoffs() -> None:
    """Catch mutable or extensible runs and IDs that omit a full input reference."""
    run = _run()
    other_ref = run.release_ref.model_copy(update={"content_hash": "0" * 64})

    assert run.model_copy(update={"release_ref": other_ref}) != run
    assert factory_run_id(run.design_ref, other_ref) != run.factory_run_id
    with pytest.raises(ValidationError):
        ResearchFactoryRun.model_validate(run.model_dump() | {"extra": 1})
    with pytest.raises(ValidationError):
        run.assembly_verdict = "changed"  # type: ignore[misc]


def test_run_revalidates_nested_models_and_requires_complete_canonical_lineage() -> None:
    """Catch forged nested instances and omitted or reordered exact lineage."""
    run = _run()
    forged = run.release_ref.model_construct(
        artifact_id=run.release_ref.artifact_id,
        artifact_version=1,
        content_hash="not-a-digest",
    )

    with pytest.raises(ValidationError):
        ResearchFactoryRun.model_validate({**run.model_dump(), "release_ref": forged})
    with pytest.raises(ValidationError, match="lineage"):
        ResearchFactoryRun.model_validate(
            {**run.model_dump(), "artifact_refs": run.artifact_refs[:-1]}
        )
    with pytest.raises(ValidationError, match="canonical"):
        ResearchFactoryRun.model_validate(
            {**run.model_dump(), "artifact_refs": tuple(reversed(run.artifact_refs))}
        )


def test_binding_report_rejects_forward_provenance_and_blocked_coherent_fields() -> None:
    """Catch forward execution claims or a coherent verdict over a blocked field."""
    report = _run().binding_report

    with pytest.raises(ValidationError):
        CrossStageBindingReport.model_validate(
            {**report.model_dump(), "provenance_claim": "produced-by"}
        )
    with pytest.raises(ValidationError, match="blocked"):
        CrossStageBindingReport.model_validate(
            {
                **report.model_dump(),
                "fields": (
                    report.fields[0].model_copy(update={"relation": "blocked"}),
                    *report.fields[1:],
                ),
            }
        )


def test_run_has_no_service_timestamp_and_binds_full_profile_digests() -> None:
    """Catch operational time or truncated registry authority in canonical bytes."""
    run = _run()
    payload = run.model_dump_json()

    assert "timestamp" not in payload
    assert all(len(item.sha256) == 64 for item in run.capability_profiles)
    with pytest.raises(ValidationError, match="capability"):
        ResearchFactoryRun.model_validate(
            {**run.model_dump(), "capability_profiles": ()}
        )


def test_binding_report_requires_exact_canonical_limitation_union() -> None:
    """Catch removing one release-only limitation from the exported union."""
    run = _run()
    report = run.binding_report
    design_limitations = set(run.design.plan.fallback_rules)
    release_only = next(
        item for item in report.limitations if item not in design_limitations
    )

    tampered_report = {
        **report.model_dump(),
        "limitations": tuple(
            item for item in report.limitations if item != release_only
        ),
    }
    with pytest.raises(ValidationError, match="limitation"):
        CrossStageBindingReport.model_validate(tampered_report)
    with pytest.raises(ValidationError, match="limitation"):
        ResearchFactoryRun.model_validate(
            {**run.model_dump(), "binding_report": tampered_report}
        )
    forged = "Forged design limitation."
    forged_report = CrossStageBindingReport.model_validate(
        {
            **report.model_dump(),
            "fields": tuple(
                item.model_copy(
                    update={
                        "design_value": " | ".join(
                            sorted((*run.design.plan.fallback_rules, forged))
                        )
                    }
                )
                if item.dimension == "limitation"
                else item
                for item in report.fields
            ),
            "limitations": tuple(sorted((*report.limitations, forged))),
        }
    )
    with pytest.raises(ValidationError, match="coherent"):
        ResearchFactoryRun.model_validate(
            {**run.model_dump(), "binding_report": forged_report}
        )


def test_blocked_report_and_assembled_run_verdicts_are_consistent() -> None:
    """Catch blocked reports without findings and assembled runs over blockers."""
    run = _run()
    report = run.binding_report
    with pytest.raises(ValidationError, match="blocked"):
        CrossStageBindingReport.model_validate(
            {**report.model_dump(), "verdict": "blocked"}
        )
    blocked = CrossStageBindingReport.model_validate(
        {
            **report.model_dump(),
            "fields": (
                report.fields[0].model_copy(update={"relation": "blocked"}),
                *report.fields[1:],
            ),
            "verdict": "blocked",
        }
    )
    with pytest.raises(ValidationError, match="coherent"):
        ResearchFactoryRun.model_validate(
            {**run.model_dump(), "binding_report": blocked}
        )


@pytest.mark.parametrize(
    "run_ref",
    (
        lambda run: run.design_ref,
        lambda run: run.model_copy().design_ref.model_copy(
            update={
                "artifact_id": run.factory_run_id,
                "artifact_version": 2,
                "content_hash": hashlib.sha256(run.model_dump_json().encode()).hexdigest(),
            }
        ),
        lambda run: run.model_copy().design_ref.model_copy(
            update={
                "artifact_id": run.factory_run_id,
                "artifact_version": 1,
                "content_hash": "0" * 64,
            }
        ),
    ),
)
def test_status_reference_must_bind_exact_canonical_run(run_ref) -> None:
    """Catch status snapshots with a wrong run ID, version, or content hash."""
    run = _run()
    with pytest.raises(ValidationError, match="reference"):
        FactoryRunStatus(
            state="promotion-required", run_ref=run_ref(run), run=run
        )


def test_pre_fix_v1_run_bytes_remain_canonical_and_status_readable(
    tmp_path: Path,
) -> None:
    """Catch a schema-compatible fix stranding already committed V1 run bytes."""
    legacy_bytes = gzip.decompress(base64.b64decode(_A705_RUN_GZIP_BASE64))
    assert len(legacy_bytes) == 31282
    assert hashlib.sha256(legacy_bytes).hexdigest() == _A705_RUN_SHA256
    legacy = ResearchFactoryRun.model_validate_json(legacy_bytes)
    assert legacy.model_dump_json().encode() == legacy_bytes
    reference = ArtifactRef(
        artifact_id=_A705_RUN_ID,
        artifact_version=1,
        content_hash=_A705_RUN_SHA256,
    )
    status = FactoryRunStatus(
        state="promotion-required", run_ref=reference, run=legacy
    )
    store = _FactoryRunStore(ExitRegistry(tmp_path))
    assert store.prepare(legacy) == reference
    assert store.prepared() == reference
    assert store.committed() is None
    store.commit(reference)

    assert status.run == legacy
    assert store.current() == reference

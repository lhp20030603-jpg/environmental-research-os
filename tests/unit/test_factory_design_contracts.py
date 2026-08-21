"""Behavioral contracts for the immutable V0.2 approved-design handoff."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from envresearch.factory.design_contracts import (
    ApprovedDesignHandoff,
    ResearchFileEvidence,
    approved_design_id,
)
from envresearch.kernel.gates import GateDecision, GateRequest
from envresearch.kernel.node_checkpoint_schema import NodeCheckpoint
from envresearch.models.artifact import ArtifactRef
from envresearch.models.design import ClaimMode, EstimandType
from envresearch.models.design_plan import AnalysisPlanPayload
from envresearch.models.enums import GateStatus
from envresearch.research.audit_state import ResearchRunManifest
from envresearch.research.final_binding import terminal_refs
from envresearch.research.gate_context import BoundGateContext

NOW = datetime(2026, 8, 14, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _plan() -> AnalysisPlanPayload:
    return AnalysisPlanPayload(
        estimand_ref="estimand-air",
        estimand_type=EstimandType.CAUSAL,
        primary_method_profile_ref="did-event-study@0.2.0",
        alternative_method_profile_refs=("synthetic-control@0.2.0",),
        data_boundaries=("Use public data.",),
        assumptions=("Parallel trends.",),
        diagnostics=("Event-study pre-trends.",),
        exclusion_rules=("Exclude incomplete cities.",),
        robustness_plan=("Change comparison groups.",),
        fallback_rules=("Use descriptive claims.",),
        claim_mode=ClaimMode.CAUSAL,
    )


def _handoff() -> ApprovedDesignHandoff:
    plan_ref = ArtifactRef(
        artifact_id="analysis-plan", artifact_version=3, content_hash=SHA_A
    )
    reviewed_ref = ArtifactRef(
        artifact_id="analysis-plan", artifact_version=2, content_hash=SHA_B
    )
    context = BoundGateContext(
        base_gate_id="final-gate",
        gate_id="final-gate",
        revision=1,
        artifact_refs=(
            ArtifactRef(
                artifact_id="design-review-findings",
                artifact_version=1,
                content_hash=SHA_C,
            ),
            reviewed_ref,
        ),
        requested_at=NOW,
    )
    assert context.context_hash is not None
    context_ref = ArtifactRef(
        artifact_id="final-gate-context",
        artifact_version=context.revision,
        content_hash=context.context_hash,
    )
    gate = GateRequest(
        id="final-gate",
        name="Research design",
        requested_by="research-orchestrator",
        requested_at=NOW,
        status=GateStatus.APPROVED,
        decision=GateDecision(
            status=GateStatus.APPROVED,
            decided_by="human-reviewer",
            decided_at=NOW,
            rationale="Approved fixture decision.",
            conditions={"gate_context": context.model_dump(mode="json")},
        ),
    )
    inputs = terminal_refs(
        plan_ref,
        reviewed_ref,
        context.context_hash,
        context.revision,
    )
    checkpoint = NodeCheckpoint(
        node_id="final-approval",
        node_version="v1",
        definition_hash=SHA_D,
        input_hashes=dict(
            sorted(
                (
                    f"{item.artifact_id}@{item.artifact_version}",
                    item.content_hash,
                )
                for item in inputs
            )
        ),
        output_hashes={"artifacts/analysis-plan.yaml": SHA_A},
        completed_at=NOW,
        checkpoint_hash=SHA_C,
    )
    manifest = ResearchRunManifest(
        run_id="run-approved-design",
        input_mode="broad_topic",
        config_sha256=SHA_D,
        intake_artifact=ArtifactRef(
            artifact_id="research-brief", artifact_version=1, content_hash=SHA_C
        ),
        method_profiles={"did-event-study": "0.2.0"},
        method_profile_sha256={"did-event-study": SHA_D},
    )
    manifest_bytes = manifest.model_dump_json().encode()
    return ApprovedDesignHandoff(
        schema_version="factory.approved-design.v1",
        design_id=approved_design_id(plan_ref, context_ref),
        producer="research-factory-design-adapter-v1",
        manifest=manifest,
        manifest_evidence=ResearchFileEvidence(
            relative_path="research-run-manifest.json",
            sha256=SHA_A,
            size_bytes=len(manifest_bytes),
        ),
        plan_ref=plan_ref,
        plan=_plan(),
        final_context_ref=context_ref,
        final_context=context,
        final_gate=gate,
        terminal_checkpoint=checkpoint,
        decision_log_evidence=ResearchFileEvidence(
            relative_path="decision-log.jsonl", sha256=SHA_B, size_bytes=12
        ),
        method_profile_sha256=manifest.method_profile_sha256,
    )


def test_handoff_is_frozen_and_forbids_extra_fields() -> None:
    """Catch a handoff that silently accepts ungoverned publication metadata."""
    handoff = _handoff()

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ApprovedDesignHandoff.model_validate(
            {**handoff.model_dump(), "assembly_timestamp": "2026-08-14T00:00:00Z"}
        )
    with pytest.raises(ValidationError):
        handoff.design_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("value", ("A" * 64, "a" * 63, "a" * 65))
def test_evidence_rejects_noncanonical_sha256(value: str) -> None:
    """Catch evidence whose digest cannot identify immutable bytes exactly."""
    with pytest.raises(ValidationError, match="lowercase SHA-256"):
        ResearchFileEvidence(
            relative_path="decision-log.jsonl", sha256=value, size_bytes=1
        )


@pytest.mark.parametrize("path", ("/tmp/manifest.json", "../manifest.json", "a/../../b"))
def test_evidence_rejects_unsafe_relative_path(path: str) -> None:
    """Catch evidence paths that could escape the research-root snapshot."""
    with pytest.raises(ValidationError, match="safe relative"):
        ResearchFileEvidence(relative_path=path, sha256=SHA_A, size_bytes=1)


def test_handoff_revalidates_constructed_nested_reference() -> None:
    """Catch forged nested Pydantic instances that bypass ArtifactRef validation."""
    handoff = _handoff()
    forged_ref = ArtifactRef.model_construct(
        artifact_id="analysis-plan", artifact_version=3, content_hash="not-a-sha"
    )

    with pytest.raises(ValidationError, match="64-character lowercase SHA-256"):
        ApprovedDesignHandoff.model_validate(
            {**handoff.model_dump(), "plan_ref": forged_ref}
        )


def test_handoff_requires_exact_approved_gate_context_and_terminal_inputs() -> None:
    """Catch a plan paired with a different context or incomplete final checkpoint."""
    handoff = _handoff()
    mismatched_context_ref = handoff.final_context_ref.model_copy(
        update={"artifact_version": 2}
    )
    with pytest.raises(ValidationError, match="final context reference"):
        ApprovedDesignHandoff.model_validate(
            {
                **handoff.model_dump(),
                "design_id": approved_design_id(handoff.plan_ref, mismatched_context_ref),
                "final_context_ref": mismatched_context_ref,
            }
        )

    wrong_checkpoint = handoff.terminal_checkpoint.model_copy(
        update={"input_hashes": {"analysis-plan@3": SHA_A}}
    )
    with pytest.raises(ValidationError, match="terminal checkpoint"):
        ApprovedDesignHandoff.model_validate(
            {**handoff.model_dump(), "terminal_checkpoint": wrong_checkpoint}
        )


def test_handoff_binds_manifest_method_profile_digests_and_full_reference_identity() -> None:
    """Catch scientific-rule drift or an ID derived from incomplete references."""
    handoff = _handoff()
    with pytest.raises(ValidationError, match="method profile"):
        ApprovedDesignHandoff.model_validate(
            {**handoff.model_dump(), "method_profile_sha256": {}}
        )

    changed_context_ref = handoff.final_context_ref.model_copy(
        update={"content_hash": SHA_D}
    )
    with pytest.raises(ValidationError, match="design ID"):
        ApprovedDesignHandoff.model_validate(
            {**handoff.model_dump(), "final_context_ref": changed_context_ref}
        )

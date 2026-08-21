"""Strict citation-node, Final Gate, recovery, and revision regressions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from orchestrator_fixtures import (
    approve,
    broad_brief,
    config,
    plan,
    ready_for_final_gate,
    revision_capability,
    submit,
)
from test_blind_registry_security import write_case

from envresearch.benchmarks.blind_registry import BlindBenchmarkRegistry
from envresearch.benchmarks.claim_report import (
    AcceptedArtifactBinding,
    CitationIntegrityReport,
    binding_sha256,
    payload_leaf_hashes,
    report_input_refs,
    report_payload,
)
from envresearch.models.benchmark_claims import ClaimUsage
from envresearch.models.benchmark_evaluation import AcceptedArtifactClaims
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.intake import ResearchIntakeMode
from envresearch.research.citation_gate import citation_node_inputs
from envresearch.research.orchestrator import ResearchOrchestrator, ResearchRunPhase

REPORT_PATH = Path("artifacts/citation-integrity-report.json")
PLAN_PATH = Path("artifacts/analysis-plan.yaml")


def _case(root: Path) -> Path:
    return write_case(root)


def _loaded(root: Path):  # type: ignore[no-untyped-def]
    manifest = BlindBenchmarkRegistry.discover(root)["pilot-001"]
    return BlindBenchmarkRegistry.load_case(manifest)


def _accepted_plan(orchestrator: ResearchOrchestrator) -> AcceptedArtifactClaims:
    artifact = orchestrator.lifecycle.read_artifact(PLAN_PATH)
    assert isinstance(artifact.payload, dict)
    pointers = (
        "/alternative_method_profile_refs/0",
        "/estimand_ref",
        "/primary_method_profile_ref",
    )
    usages = tuple(
        ClaimUsage(
            claim_id="claim-001",
            json_pointer=pointer,
            statement_sha256=hashlib.sha256(
                _pointer_value(artifact.payload, pointer).encode()
            ).hexdigest(),
        )
        for pointer in pointers
    )
    return AcceptedArtifactClaims(
        artifact_ref=orchestrator.lifecycle.artifact_ref(PLAN_PATH),
        payload=artifact.payload,
        usages=usages,
    )


def _pointer_value(payload: dict[str, object], pointer: str) -> str:
    value: object = payload
    for segment in pointer.removeprefix("/").split("/"):
        value = value[int(segment)] if isinstance(value, list) else value[segment]  # type: ignore[index]
    assert isinstance(value, str)
    return value


def _record(
    orchestrator: ResearchOrchestrator, case_root: Path
) -> AcceptedArtifactClaims:
    accepted = _accepted_plan(orchestrator)
    orchestrator.record_citation_integrity_report(
        case_roots=(case_root,), artifacts=(accepted,)
    )
    return accepted


def _strict_ready(tmp_path: Path, case_root: Path) -> ResearchOrchestrator:
    return ready_for_final_gate(
        tmp_path,
        require_claim_verified_citations=True,
        citation_catalog_roots=(case_root,),
    )


def _advance_case_generation(root: Path, generation: int) -> None:
    source_path = root / "curator-source-sheet.yaml"
    source = yaml.safe_load(source_path.read_bytes())
    source["source_generation"] = generation
    source_path.write_text(yaml.safe_dump(source), encoding="utf-8")
    source_ref = {
        "artifact_id": "curator-source-sheet",
        "artifact_version": 1,
        "content_hash": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    }
    brief_path = root / "blinded-brief.yaml"
    brief = yaml.safe_load(brief_path.read_bytes())
    brief["source_sheet_ref"] = source_ref
    brief_path.write_text(yaml.safe_dump(brief), encoding="utf-8")
    brief_ref = {
        "artifact_id": "blinded-brief",
        "artifact_version": 1,
        "content_hash": hashlib.sha256(brief_path.read_bytes()).hexdigest(),
    }
    map_path = root / "claim-fact-map.yaml"
    claim_map = yaml.safe_load(map_path.read_bytes())
    claim_map["source_sheet_ref"] = source_ref
    claim_map["blinded_brief_ref"] = brief_ref
    map_path.write_text(yaml.safe_dump(claim_map), encoding="utf-8")


def test_strict_gate_requires_current_checkpointed_report_and_recovers(
    tmp_path: Path,
) -> None:
    """No report can open Final Gate; one exact sealed report survives recovery."""
    case_root = _case(tmp_path / "blind-case")
    orchestrator = _strict_ready(tmp_path, case_root)
    assert orchestrator.bound_gates.active_context("final-gate") is None
    assert not (tmp_path / REPORT_PATH).exists()
    with pytest.raises(ValueError, match="citation integrity report"):
        orchestrator.semantics.validate_final()

    _record(orchestrator, case_root)
    loaded = _loaded(case_root)
    waiting = orchestrator.advance()
    assert waiting.pending_gate_ids == ("final-gate",)
    report_ref = orchestrator.lifecycle.artifact_ref(REPORT_PATH)
    report = orchestrator.lifecycle.read_artifact(REPORT_PATH)
    assert report.envelope.input_artifacts == (
        loaded.source_ref,
        loaded.claim_fact_map_ref,
        loaded.brief_ref,
        orchestrator.lifecycle.artifact_ref(PLAN_PATH),
    )
    context = orchestrator.bound_gates.active_context("final-gate")
    assert context is not None and context.artifact_refs[-1] == report_ref

    approve(orchestrator, "final-gate", accepted_major_ids=[])
    assert orchestrator.advance().phase is ResearchRunPhase.COMPLETE
    checkpoint = json.loads(
        (tmp_path / "node-checkpoints/final-approval.json").read_text()
    )
    assert any(
        key.startswith("citation-integrity-report@")
        for key in checkpoint["input_hashes"]
    )
    orchestrator.close()

    recovered = ResearchOrchestrator()
    strict = config(tmp_path, ResearchIntakeMode.BROAD_TOPIC).model_copy(
        update={
            "require_claim_verified_citations": True,
            "citation_catalog_roots": (case_root,),
        }
    )
    assert (
        recovered.initialize(strict, broad_brief()).phase is ResearchRunPhase.COMPLETE
    )


def test_plan_revision_invalidates_report_and_requires_recomputation(
    tmp_path: Path,
) -> None:
    """A revised accepted generation must retire and replace citation validation."""
    case_root = _case(tmp_path / "blind-case")
    orchestrator = _strict_ready(tmp_path, case_root)
    stale = _record(orchestrator, case_root)
    assert orchestrator.advance().pending_gate_ids == ("final-gate",)
    token = orchestrator.lifecycle.read_payload(
        PLAN_PATH, type(plan("token"))
    ).estimand_ref

    revision = orchestrator.request_revision(
        "compose-plan",
        reason="Refresh accepted plan",
        actor="reviewer",
        principal_capability=revision_capability(orchestrator),
    )
    assert "validate-citations" in revision.affected_nodes
    assert (
        orchestrator.lifecycle.current_envelope(REPORT_PATH).validation_status
        is ArtifactLifecycle.SUPERSEDED
    )
    submit(orchestrator, "compose-plan", plan(token))
    assert orchestrator.advance().pending_gate_ids == ()

    with pytest.raises(ValueError, match="current accepted artifact"):
        orchestrator.record_citation_integrity_report(
            case_roots=(case_root,), artifacts=(stale,)
        )
    _record(orchestrator, case_root)
    assert orchestrator.advance().pending_gate_ids == ("final-gate-r2",)


def test_strict_recording_rejects_current_ref_with_mismatched_payload(
    tmp_path: Path,
) -> None:
    """A current ref cannot be replayed over payload bytes it does not identify."""
    case_root = _case(tmp_path / "blind-case")
    orchestrator = _strict_ready(tmp_path, case_root)
    accepted = _accepted_plan(orchestrator)
    assert isinstance(accepted.payload, dict)
    mismatched = accepted.model_copy(
        update={"payload": {**accepted.payload, "stop_rule": "forged"}}
    )

    with pytest.raises(ValueError, match="payload.*current accepted artifact"):
        orchestrator.record_citation_integrity_report(
            case_roots=(case_root,), artifacts=(mismatched,)
        )


def test_detached_self_consistent_report_cannot_authorize_final_gate(
    tmp_path: Path,
) -> None:
    """Lifecycle access and a producer label cannot forge validator authority."""
    case_root = _case(tmp_path / "blind-case")
    orchestrator = _strict_ready(tmp_path, case_root)
    accepted = _accepted_plan(orchestrator)
    loaded = _loaded(case_root)
    source_ref = loaded.source_ref
    map_ref = loaded.claim_fact_map_ref
    brief_ref = loaded.brief_ref
    with pytest.raises(ValueError, match="validation did not pass"):
        orchestrator.citation_attestations.validate_and_seal(
            lifecycle=orchestrator.lifecycle,
            case_roots=(case_root,),
            artifacts=(accepted.model_copy(update={"usages": ()}),),
            before_persist=lambda *_: None,
        )
    assert not (tmp_path / REPORT_PATH).exists()
    binding = AcceptedArtifactBinding(
        artifact_ref=accepted.artifact_ref,
        payload_leaf_hashes=payload_leaf_hashes(accepted.payload),
        usages=(),
    )
    forged = CitationIntegrityReport(
        findings=(),
        passed=True,
        validator_version="claim-integrity-v1",
        source_sheet_refs=(source_ref,),
        claim_fact_map_refs=(map_ref,),
        blinded_brief_refs=(brief_ref,),
        accepted_artifact_refs=(accepted.artifact_ref,),
        accepted_artifact_bindings=(binding,),
        binding_sha256=binding_sha256(
            (source_ref,),
            (map_ref,),
            (brief_ref,),
            (binding,),
            "claim-integrity-v1",
        ),
    )
    orchestrator.lifecycle.persist_structured(
        REPORT_PATH,
        report_payload(forged),
        "citation-integrity-validator",
        report_input_refs(forged),
    )
    report_ref = orchestrator.lifecycle.artifact_ref(REPORT_PATH)
    signer = getattr(orchestrator.citation_attestations, "attest_report", None)
    if signer is not None:
        source_generation = orchestrator.citation_attestations.load_and_bind(
            (case_root,)
        )[1]
        signer(report_ref, forged, source_generation)
    node = orchestrator._nodes["validate-citations"]
    orchestrator.checkpoints.publish(
        node, citation_node_inputs(orchestrator.lifecycle), node.output_paths
    )
    assert orchestrator.advance().pending_gate_ids == ("final-gate",)
    approve(orchestrator, "final-gate", accepted_major_ids=[])

    with pytest.raises(ValueError, match="citation.*authentication"):
        orchestrator.advance()
    assert not hasattr(orchestrator.citation_attestations, "attest_report")


def test_first_catalog_use_rejects_unbound_substitution(tmp_path: Path) -> None:
    """A fabricated catalog cannot become authoritative on first report use."""
    authorized = _case(tmp_path / "authorized-catalog")
    alternate = _case(tmp_path / "alternate-catalog")
    orchestrator = _strict_ready(tmp_path, authorized)

    with pytest.raises(ValueError, match="catalog.*authorized"):
        _record(orchestrator, alternate)

    assert orchestrator.citation_attestations.latest_sources(required=False) is None


def test_new_registered_source_generation_retires_prior_report(
    tmp_path: Path,
) -> None:
    """A newer trusted source/map generation must invalidate the old gate input."""
    case_root = _case(tmp_path / "blind-case")
    orchestrator = _strict_ready(tmp_path, case_root)
    _record(orchestrator, case_root)
    old_report = orchestrator.lifecycle.artifact_ref(REPORT_PATH)
    assert orchestrator.advance().pending_gate_ids == ("final-gate",)
    _advance_case_generation(case_root, 2)
    _record(orchestrator, case_root)

    assert orchestrator.lifecycle.artifact_ref(REPORT_PATH) != old_report
    assert orchestrator.advance().pending_gate_ids == ("final-gate-r2",)


def test_superseded_registry_generation_cannot_be_replayed(tmp_path: Path) -> None:
    """A -> B -> A replay must not turn a revoked generation authoritative again."""
    case_root = _case(tmp_path / "blind-case")
    orchestrator = _strict_ready(tmp_path, case_root)
    original = {path.name: path.read_bytes() for path in case_root.glob("*.yaml")}
    _record(orchestrator, case_root)
    _advance_case_generation(case_root, 2)
    _record(orchestrator, case_root)
    current_report = orchestrator.lifecycle.artifact_ref(REPORT_PATH)

    for name, content in original.items():
        (case_root / name).write_bytes(content)
    with pytest.raises(ValueError, match="source generation.*advance"):
        _record(orchestrator, case_root)

    assert orchestrator.lifecycle.artifact_ref(REPORT_PATH) == current_report
    orchestrator.semantics.validate_final()

"""Real local authority and CV fixtures for Paper Builder integration tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from orchestrator_fixtures import ready_for_final_gate
from paper_argument_fixtures import candidate as argument_candidate
from paper_argument_fixtures import services as argument_services
from test_blind_registry_security import write_case

from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import ClaimUsage
from envresearch.models.benchmark_evaluation import AcceptedArtifactClaims
from envresearch.paper.argument_map import ArgumentMapService
from envresearch.paper.citation_authority import LifecycleCitationAuthority
from envresearch.paper.draft_builder import DraftService, deterministic_draft_candidate
from envresearch.paper.draft_contracts import PaperDraftCandidate
from envresearch.paper.ledger import ClaimLedgerService
from envresearch.research.orchestrator import ResearchOrchestrator

PLAN_PATH = Path("artifacts/analysis-plan.yaml")
REPORT_PATH = Path("artifacts/citation-integrity-report.json")


@dataclass(frozen=True, slots=True)
class DraftStack:
    """One complete always-local Paper Builder authority stack."""

    orchestrator: ResearchOrchestrator
    case_root: Path
    ledger_service: ClaimLedgerService
    map_service: ArgumentMapService
    citation_authority: LifecycleCitationAuthority
    draft_service: DraftService
    transition_ref: ArtifactRef
    ledger_ref: ArtifactRef
    map_ref: ArtifactRef
    report_ref: ArtifactRef
    candidate: PaperDraftCandidate


def build_stack(tmp_path: Path, *, bind_explicit_config: bool = False) -> DraftStack:
    """Build genuine CV evidence plus one lifecycle-sealed verified source."""
    case_root = write_case(tmp_path / "blind-case")
    explicit_config = (
        _strict_explicit_config(case_root) if bind_explicit_config else None
    )
    orchestrator = ready_for_final_gate(
        tmp_path / "research",
        require_claim_verified_citations=True,
        citation_catalog_roots=(case_root,),
        explicit_config=explicit_config,
    )
    accepted = _accepted_plan(orchestrator)
    orchestrator.record_citation_integrity_report(
        case_roots=(case_root,), artifacts=(accepted,)
    )
    report_ref = orchestrator.lifecycle.artifact_ref(REPORT_PATH)
    authority = LifecycleCitationAuthority(
        lifecycle=orchestrator.lifecycle,
        attestations=orchestrator.citation_attestations,
    )

    ledger_service, map_service, transition_ref = argument_services(
        tmp_path / "valuation"
    )
    ledger_ref = ledger_service.build(transition_ref)
    map_ref = map_service.build(ledger_ref, argument_candidate())
    ledger = ledger_service.status(ledger_ref, transition_ref)
    argument_map = map_service.status(map_ref, ledger_ref)
    snapshot = authority.reopen(report_ref)
    candidate = deterministic_draft_candidate(
        argument_map=argument_map,
        ledger=ledger,
        citation_snapshot=snapshot,
    )
    service = DraftService(
        map_service=map_service,
        citation_authority=authority,
    )
    return DraftStack(
        orchestrator=orchestrator,
        case_root=case_root,
        ledger_service=ledger_service,
        map_service=map_service,
        citation_authority=authority,
        draft_service=service,
        transition_ref=transition_ref,
        ledger_ref=ledger_ref,
        map_ref=map_ref,
        report_ref=report_ref,
        candidate=candidate,
    )


def _strict_explicit_config(case_root: Path) -> bytes:
    config_path = Path(__file__).parents[2] / "configs/research-default.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["require_claim_verified_citations"] = True
    payload["citation_catalog_roots"] = [str(case_root.resolve())]
    return yaml.safe_dump(payload, sort_keys=False).encode()


def advance_citation_generation(stack: DraftStack) -> ArtifactRef:
    """Publish one coherent replacement source generation and report."""
    source_path = stack.case_root / "curator-source-sheet.yaml"
    payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    payload["source_generation"] = int(payload["source_generation"]) + 1
    source_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    source_ref = _file_ref("curator-source-sheet", source_path)

    brief_path = stack.case_root / "blinded-brief.yaml"
    brief = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
    brief["source_sheet_ref"] = source_ref
    brief_path.write_text(yaml.safe_dump(brief), encoding="utf-8")
    brief_ref = _file_ref("blinded-brief", brief_path)

    map_path = stack.case_root / "claim-fact-map.yaml"
    claim_map = yaml.safe_load(map_path.read_text(encoding="utf-8"))
    claim_map["source_sheet_ref"] = source_ref
    claim_map["blinded_brief_ref"] = brief_ref
    map_path.write_text(yaml.safe_dump(claim_map), encoding="utf-8")
    accepted = _accepted_plan(stack.orchestrator)
    stack.orchestrator.record_citation_integrity_report(
        case_roots=(stack.case_root,), artifacts=(accepted,)
    )
    return stack.orchestrator.lifecycle.artifact_ref(REPORT_PATH)


def _file_ref(artifact_id: str, path: Path) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "artifact_version": 1,
        "content_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


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


__all__ = ["DraftStack", "advance_citation_generation", "build_stack"]

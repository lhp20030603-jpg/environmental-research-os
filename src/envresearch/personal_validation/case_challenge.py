"""Real Paper services, local evidence, and oracle-independent review contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import yaml  # type: ignore[import-untyped]
from pydantic import JsonValue

from envresearch.benchmarks.claim_report import (
    CitationIntegrityReport,
    accepted_artifact_binding,
    binding_sha256,
    report_payload,
)
from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.econometrics.service import LocalAnalysisService
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import ClaimUsage, CuratorSourceSheet
from envresearch.models.benchmark_evaluation import AcceptedArtifactClaims
from envresearch.paper.argument_contracts import (
    ArgumentEdge,
    ArgumentMapCandidate,
    ArgumentNode,
)
from envresearch.paper.argument_map import ArgumentMapService
from envresearch.paper.auditor import PaperAuditService
from envresearch.paper.citation_authority import (
    CitationAuthoritySnapshot,
    CitationGenerationToken,
)
from envresearch.paper.draft_builder import DraftService, deterministic_draft_candidate
from envresearch.paper.draft_contracts import PaperDraftCandidate
from envresearch.paper.ledger import ClaimLedgerService
from envresearch.paper.release import PaperReleaseService
from envresearch.paper.revision import RevisionService
from envresearch.personal_validation.case_stops import (
    SyntheticHedonicBackend,
    hedonic_input,
)
from envresearch.storage.research_artifacts import ResearchArtifactStore

if TYPE_CHECKING:
    from envresearch.personal_validation.canonical_cases import CaseExecutionContext


@dataclass(frozen=True, slots=True)
class _AcceptedResolver:
    service: LocalAnalysisService
    transition_ref: ArtifactRef
    analysis_ref: LocalAnalysisReference
    authority_root: Path

    def resolve(
        self, transition_ref: ArtifactRef
    ) -> tuple[tuple[LocalAnalysisReference, LocalAnalysisReport], ...]:
        self.require_current(transition_ref)
        return ((self.analysis_ref, self.service.status(self.analysis_ref)),)

    def require_current(self, transition_ref: ArtifactRef) -> None:
        if transition_ref != self.transition_ref:
            raise ValueError("accepted evidence authority changed")
        self.service.status(self.analysis_ref)

    @contextmanager
    def authority_lease(self) -> Iterator[None]:
        yield


@dataclass(frozen=True, slots=True)
class _RepositoryCitationAuthority:
    case_root: Path
    lifecycle_root: Path
    control_root: Path
    _snapshot: CitationAuthoritySnapshot = field(init=False, repr=False)
    lifecycle: object = field(init=False, repr=False)
    attestations: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_snapshot", self._reconstruct())
        object.__setattr__(
            self, "lifecycle", SimpleNamespace(workspace=self.lifecycle_root)
        )
        object.__setattr__(
            self,
            "attestations",
            SimpleNamespace(
                authorized_catalog_roots=(self.case_root,),
                queue=SimpleNamespace(control=SimpleNamespace(path=self.control_root)),
            ),
        )

    @contextmanager
    def authority_lease(self) -> Iterator[None]:
        yield

    def reopen(self, report_ref: ArtifactRef) -> CitationAuthoritySnapshot:
        current = self._reconstruct()
        if report_ref != current.report[0] or current != self._snapshot:
            raise ValueError("repository citation authority changed")
        return current

    def require_current(self, token: CitationGenerationToken) -> None:
        if self._reconstruct().token != token:
            raise ValueError("repository citation generation changed")

    def _reconstruct(self) -> CitationAuthoritySnapshot:
        source_path = self.case_root / "curator-source-sheet.yaml"
        map_path = self.case_root / "claim-fact-map.yaml"
        brief_path = self.case_root / "blinded-brief.yaml"
        source_payload = yaml.safe_load(source_path.read_bytes())
        source = CuratorSourceSheet.model_validate_json(
            json.dumps(source_payload, default=lambda value: value.isoformat())
        )
        source_ref = _file_ref("curator-source-sheet", source_path)
        map_ref = _file_ref("claim-fact-map", map_path)
        brief_ref = _file_ref("blinded-brief", brief_path)
        accepted_ref = ArtifactRef(
            artifact_id="analysis-plan",
            artifact_version=1,
            content_hash=hashlib.sha256(b"hedonic@0.2.0").hexdigest(),
        )
        payload = {"primary_method_profile_ref": "hedonic@0.2.0"}
        usage = ClaimUsage(
            claim_id="claim-001",
            json_pointer="/primary_method_profile_ref",
            statement_sha256=hashlib.sha256(b"hedonic@0.2.0").hexdigest(),
        )
        binding = accepted_artifact_binding(
            AcceptedArtifactClaims(
                artifact_ref=accepted_ref,
                payload=cast(JsonValue, payload),
                usages=(usage,),
            )
        )
        report = CitationIntegrityReport(
            findings=(),
            passed=True,
            validator_version="claim-integrity-v1",
            source_sheet_refs=(source_ref,),
            claim_fact_map_refs=(map_ref,),
            blinded_brief_refs=(brief_ref,),
            accepted_artifact_refs=(accepted_ref,),
            accepted_artifact_bindings=(binding,),
            binding_sha256=binding_sha256(
                (source_ref,),
                (map_ref,),
                (brief_ref,),
                (binding,),
                "claim-integrity-v1",
            ),
        )
        report_bytes = json.dumps(
            report_payload(report), separators=(",", ":"), sort_keys=True
        ).encode()
        report_ref = ArtifactRef(
            artifact_id="citation-integrity-report",
            artifact_version=1,
            content_hash=hashlib.sha256(report_bytes).hexdigest(),
        )
        token = CitationGenerationToken(
            report_ref=report_ref,
            report_payload_sha256=hashlib.sha256(report_bytes).hexdigest(),
            source_generation=source.source_generation,
            source_anchor_sha256=hashlib.sha256(
                source_path.read_bytes()
                + map_path.read_bytes()
                + brief_path.read_bytes()
            ).hexdigest(),
        )
        return CitationAuthoritySnapshot(
            report=(report_ref, report),
            source_sheets=((source_ref, source),),
            token=token,
        )


@dataclass(frozen=True, slots=True)
class PaperCaseServices:
    """Production-owned Paper builder over explicit disposable roots."""

    repository_root: Path
    analysis_root: Path
    paper_root: Path
    citation_root: Path
    citation_control_root: Path
    _state: dict[str, object] = field(default_factory=dict, init=False, repr=False)

    @property
    def release_service(self) -> PaperReleaseService:
        self._ensure_stack()
        return cast(PaperReleaseService, self._state["releases"])

    @property
    def audit_service(self) -> PaperAuditService:
        self._ensure_stack()
        return cast(PaperAuditService, self._state["audits"])

    @property
    def revision_service(self) -> RevisionService:
        self._ensure_stack()
        return cast(RevisionService, self._state["revisions"])

    def build_clean_hedonic_release(self) -> ArtifactRef:
        prior = self._state.get("clean_release")
        if isinstance(prior, ArtifactRef):
            return prior
        self._ensure_stack()
        candidate = cast(PaperDraftCandidate, self._state["candidate"])
        draft_ref = self._publish(candidate)
        audits = self.audit_service
        audit_ref = audits.audit(draft_ref)
        if audits.status(audit_ref, draft_ref).verdict != "clean":
            raise RuntimeError("canonical clean draft did not pass audit")
        release_ref = self.release_service.build(audit_ref, draft_ref)
        self.release_service.status(release_ref)
        self._state.update(
            clean_draft=draft_ref, clean_audit=audit_ref, clean_release=release_ref
        )
        return release_ref

    def publish_auditable_overclaim(self) -> tuple[ArtifactRef, ArtifactRef]:
        prior = self._state.get("blocked_pair")
        if isinstance(prior, tuple):
            return cast(tuple[ArtifactRef, ArtifactRef], prior)
        self._ensure_stack()
        clean = cast(PaperDraftCandidate, self._state["candidate"])
        title = clean.paragraphs[0].model_copy(
            update={"text": "Regulators ought to implement this program nationwide."}
        )
        dirty = type(clean).model_validate(
            {
                **clean.model_dump(mode="python"),
                "paragraphs": (title, *clean.paragraphs[1:]),
            }
        )
        draft_ref = self._publish(dirty)
        audit_ref = self.audit_service.audit(draft_ref)
        audit = self.audit_service.status(audit_ref, draft_ref)
        if audit.verdict != "blocked" or not audit.findings:
            raise RuntimeError("canonical overclaim did not produce a blocked audit")
        self._state["blocked_pair"] = (draft_ref, audit_ref)
        return draft_ref, audit_ref

    def revise_to_clean(
        self, draft_ref: ArtifactRef, audit_ref: ArtifactRef
    ) -> ArtifactRef:
        prior = self._state.get("revision_ref")
        if isinstance(prior, ArtifactRef):
            return prior
        blocked = self.audit_service.status(audit_ref, draft_ref)
        if blocked.verdict != "blocked":
            raise RuntimeError("revision predecessor is not blocked")
        revision_ref = self.revision_service.revise(
            draft_ref, cast(PaperDraftCandidate, self._state["candidate"])
        )
        revision = self.revision_service.status(revision_ref, draft_ref)
        if not revision.closure_witnesses:
            raise RuntimeError("canonical revision has no closure witnesses")
        self._state["revision_ref"] = revision_ref
        return revision_ref

    def build_revision_release(self, revision_ref: ArtifactRef) -> ArtifactRef:
        prior = self._state.get("revision_release")
        if isinstance(prior, ArtifactRef):
            return prior
        predecessor, _ = cast(
            tuple[ArtifactRef, ArtifactRef], self._state["blocked_pair"]
        )
        revision = self.revision_service.status(revision_ref, predecessor)
        release_ref = self.release_service.build(
            revision.successor_audit_ref,
            revision.successor_ref,
            revision_ref=revision_ref,
        )
        self.release_service.status(release_ref)
        self._state["revision_release"] = release_ref
        return release_ref

    def _publish(self, candidate: PaperDraftCandidate) -> ArtifactRef:
        drafts = cast(DraftService, self._state["drafts"])
        return drafts.publish(
            candidate,
            map_ref=cast(ArtifactRef, self._state["map_ref"]),
            ledger_ref=cast(ArtifactRef, self._state["ledger_ref"]),
            citation_report_ref=cast(ArtifactRef, self._state["report_ref"]),
        )

    def _ensure_stack(self) -> None:
        if self._state:
            return
        spec, data = hedonic_input(self.repository_root)
        service = LocalAnalysisService(
            ResearchArtifactStore(self.analysis_root), SyntheticHedonicBackend()
        )
        analysis_ref = service.run_exact(spec, data, hashlib.sha256(data).hexdigest())
        report = service.status(analysis_ref)
        if report.status != "passed":
            raise RuntimeError("canonical local analysis is not green")
        transition = ArtifactRef(
            artifact_id="valuation-transition-v031",
            artifact_version=1,
            content_hash=hashlib.sha256(
                data + spec.model_dump_json().encode()
            ).hexdigest(),
        )
        resolver = _AcceptedResolver(
            service, transition, analysis_ref, service.store.root
        )
        ledgers = ClaimLedgerService.for_resolver(
            paper_root=self.paper_root, resolver=resolver
        )
        maps = ArgumentMapService(ledger_service=ledgers)
        ledger_ref = ledgers.build(transition)
        ledger = ledgers.status(ledger_ref, transition)
        claim = ledger.claims[0]
        map_ref = maps.build(
            ledger_ref,
            ArgumentMapCandidate(
                nodes=(
                    ArgumentNode(
                        node_id="hedonic-result",
                        node_type="empirical-claim",
                        proposition=None,
                        claim_ids=(claim.claim_id,),
                    ),
                    ArgumentNode(
                        node_id="valuation-contribution",
                        node_type="contribution",
                        proposition="The registered design estimates an implicit price.",
                        claim_ids=(),
                    ),
                ),
                edges=(
                    ArgumentEdge(
                        source_id="hedonic-result",
                        target_id="valuation-contribution",
                        edge_type="evidence-backed",
                    ),
                ),
            ),
        )
        authority = _RepositoryCitationAuthority(
            self.repository_root / "benchmarks/blind/pilot/pilot-rct-energy-feedback",
            self.citation_root,
            self.citation_control_root,
        )
        report_ref = authority._snapshot.report[0]
        drafts = DraftService(map_service=maps, citation_authority=authority)
        candidate = deterministic_draft_candidate(
            argument_map=maps.status(map_ref, ledger_ref),
            ledger=ledger,
            citation_snapshot=authority.reopen(report_ref),
        )
        audits = PaperAuditService(draft_service=drafts)
        revisions = RevisionService(audit_service=audits)
        releases = PaperReleaseService(audit_service=audits)
        self._state.update(
            service=service,
            ledger_ref=ledger_ref,
            map_ref=map_ref,
            report_ref=report_ref,
            drafts=drafts,
            audits=audits,
            revisions=revisions,
            releases=releases,
            candidate=candidate,
        )


def run_evidence_challenge_case(context: CaseExecutionContext) -> ArtifactRef:
    design_ref = context.research.build_hedonic_approved_design(False)
    blocked_draft, blocked_audit = context.paper.publish_auditable_overclaim()
    revision_ref = context.paper.revise_to_clean(blocked_draft, blocked_audit)
    release_ref = context.paper.build_revision_release(revision_ref)
    return context.research.factory_service(context.paper.release_service).assemble(
        design_ref, release_ref
    )


def _file_ref(identity: str, path: Path) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=identity,
        artifact_version=1,
        content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


__all__ = ["PaperCaseServices", "run_evidence_challenge_case"]

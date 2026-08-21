"""Evidence-bound paper construction for V0.4."""

from envresearch.paper.argument_contracts import (
    ArgumentEdge,
    ArgumentEdgeType,
    ArgumentMap,
    ArgumentMapCandidate,
    ArgumentNode,
    ArgumentNodeType,
)
from envresearch.paper.argument_map import ArgumentMapService
from envresearch.paper.audit_contracts import (
    AuditCode,
    AuditTarget,
    DraftBindingTarget,
    FindingKind,
    OutputBindingTarget,
    PaperAuditFinding,
    PaperAuditReport,
    TextSpan,
)
from envresearch.paper.auditor import (
    PaperAuditService,
    audit_subject,
    reconstruct_audit_findings,
)
from envresearch.paper.citation_authority import (
    CitationAuthority,
    CitationAuthoritySnapshot,
    CitationGenerationToken,
    LifecycleCitationAuthority,
)
from envresearch.paper.contracts import (
    AnalysisOutputRef,
    ClaimEvidenceLedger,
    ClaimEvidenceRow,
    ClaimUncertainty,
    DescriptiveRangeValue,
    DescriptiveSeriesPoint,
    DescriptiveSeriesValue,
    EstimatedClaimValue,
)
from envresearch.paper.draft_builder import DraftService, deterministic_draft_candidate
from envresearch.paper.draft_contracts import (
    CitationBinding,
    ClaimSpanBinding,
    FigureBinding,
    PaperDraft,
    PaperDraftCandidate,
    PaperParagraph,
    TableBinding,
)
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperBuilderError,
    PaperIntegrityInvalid,
    PaperScopeExceeded,
    PaperSupportInvalid,
)
from envresearch.paper.ledger import ClaimLedgerService
from envresearch.paper.release import PaperReleaseCandidate, PaperReleaseService
from envresearch.paper.revision import RevisionService
from envresearch.paper.revision_contracts import (
    DraftRevision,
    FindingClosureWitness,
    revision_id,
)

__all__ = [
    "AnalysisOutputRef",
    "ArgumentEdge",
    "ArgumentEdgeType",
    "ArgumentMap",
    "ArgumentMapCandidate",
    "ArgumentMapService",
    "ArgumentNode",
    "ArgumentNodeType",
    "AuditCode",
    "AuditTarget",
    "CitationAuthority",
    "CitationAuthoritySnapshot",
    "CitationBinding",
    "CitationGenerationToken",
    "ClaimEvidenceLedger",
    "ClaimEvidenceRow",
    "ClaimLedgerService",
    "ClaimSpanBinding",
    "ClaimUncertainty",
    "DescriptiveRangeValue",
    "DescriptiveSeriesPoint",
    "DescriptiveSeriesValue",
    "DraftBindingTarget",
    "DraftRevision",
    "DraftService",
    "EstimatedClaimValue",
    "FigureBinding",
    "FindingClosureWitness",
    "FindingKind",
    "LifecycleCitationAuthority",
    "OutputBindingTarget",
    "PaperAuditFinding",
    "PaperAuditReport",
    "PaperAuditService",
    "PaperAuthorityInvalid",
    "PaperBuilderError",
    "PaperDraft",
    "PaperDraftCandidate",
    "PaperIntegrityInvalid",
    "PaperParagraph",
    "PaperReleaseCandidate",
    "PaperReleaseService",
    "PaperScopeExceeded",
    "PaperSupportInvalid",
    "RevisionService",
    "TableBinding",
    "TextSpan",
    "audit_subject",
    "deterministic_draft_candidate",
    "reconstruct_audit_findings",
    "revision_id",
]

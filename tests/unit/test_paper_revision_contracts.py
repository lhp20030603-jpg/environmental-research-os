"""Strict generation and closure-envelope contracts for paper revisions."""

from __future__ import annotations

import pytest
from paper_draft_fixtures import materialized_draft
from pydantic import ValidationError
from test_paper_audit import _report

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_store import audit_id
from envresearch.paper.audit_contracts import PaperAuditFinding, TextSpan
from envresearch.paper.draft_contracts import PaperDraft, PaperDraftCandidate
from envresearch.paper.revision_contracts import (
    DraftRevision,
    FindingClosureWitness,
    revision_id,
)


def _ref(artifact_id: str, version: int, fill: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_version=version,
        content_hash=fill * 64,
    )


def _generation_one() -> PaperDraft:
    draft = materialized_draft()[0]
    return PaperDraft.model_validate(
        {
            **draft.model_dump(mode="python"),
            "generation": 1,
            "predecessor_ref": None,
        }
    )


def _revision() -> DraftRevision:
    draft = _generation_one()
    predecessor = _ref(draft.draft_id, 1, "1")
    successor = _ref(draft.draft_id, 2, "2")
    findings = _findings(predecessor)
    witnesses = tuple(_witness(item) for item in findings)
    return DraftRevision(
        schema_version="paper.draft-revision.v1",
        revision_id=revision_id(predecessor),
        producer="paper-builder-revision-v1",
        predecessor_ref=predecessor,
        predecessor_audit_ref=_ref(audit_id(predecessor), 1, "3"),
        successor_ref=successor,
        successor_audit_ref=_ref(audit_id(successor), 1, "4"),
        predecessor_generation=1,
        successor_generation=2,
        map_ref=draft.map_ref,
        ledger_ref=draft.ledger_ref,
        citation_report_ref=draft.citation_report_ref,
        closed_finding_ids=tuple(item.finding_id for item in findings),
        closure_witnesses=witnesses,
    )


def _witness(finding: PaperAuditFinding) -> FindingClosureWitness:
    return FindingClosureWitness(
        finding_id=finding.finding_id,
        finding_kind=finding.finding_kind,
        code=finding.code,
        predecessor_target=finding.target,
        claim_ids=finding.claim_ids,
        successor_validation="clean-independent-audit",
    )


def _findings(draft_ref: ArtifactRef) -> tuple[PaperAuditFinding, ...]:
    first = _report().findings[0].model_copy(update={"draft_ref": draft_ref})
    second = first.model_copy(
        update={
            "finding_id": "policy-overclaim-" + "b" * 64,
            "finding_kind": "policy-overclaim",
            "code": "PAPER_SCOPE_EXCEEDED",
            "target": TextSpan(
                target_type="text-span",
                paragraph_id="paper-title",
                start=0,
                end=5,
                text_sha256="c" * 64,
            ),
        }
    )
    return tuple(sorted((first, second), key=lambda item: item.finding_id))


def test_draft_generation_and_predecessor_contract_is_closed() -> None:
    first = _generation_one()
    predecessor = _ref(first.draft_id, 1, "1")
    second = PaperDraft.model_validate(
        {
            **first.model_dump(mode="python"),
            "generation": 2,
            "predecessor_ref": predecessor,
        }
    )

    assert first.generation == 1 and first.predecessor_ref is None
    assert second.generation == 2 and second.predecessor_ref == predecessor
    third = PaperDraft.model_validate(
        {
            **first.model_dump(mode="python"),
            "generation": 3,
            "predecessor_ref": _ref(first.draft_id, 2, "2"),
        }
    )
    assert third.generation == 3 and third.predecessor_ref is not None
    for generation, prior in ((1, predecessor), (2, None)):
        with pytest.raises(ValidationError):
            PaperDraft.model_validate(
                {
                    **first.model_dump(mode="python"),
                    "generation": generation,
                    "predecessor_ref": prior,
                }
            )
    invalid_predecessors = (
        predecessor,
        _ref("other-draft", 2, "2"),
        predecessor.model_copy(update={"artifact_version": "2"}),
    )
    for prior in invalid_predecessors:
        with pytest.raises(ValidationError):
            PaperDraft.model_validate(
                {
                    **first.model_dump(mode="python"),
                    "generation": 3,
                    "predecessor_ref": prior,
                }
            )


def test_revision_contract_is_strict_frozen_and_service_derived() -> None:
    revision = _revision()

    assert revision.closed_finding_ids == tuple(
        item.finding_id for item in revision.closure_witnesses
    )
    with pytest.raises(ValidationError):
        revision.successor_generation = 3  # type: ignore[misc]
    with pytest.raises(ValidationError):
        DraftRevision.model_validate(
            {**revision.model_dump(mode="python"), "caller_declared_closed": True}
        )
    candidate = materialized_draft()[0]
    with pytest.raises(ValidationError):
        PaperDraftCandidate.model_validate(
            {
                "paragraphs": candidate.paragraphs,
                "claim_bindings": candidate.claim_bindings,
                "citation_bindings": candidate.citation_bindings,
                "tables": candidate.tables,
                "figures": candidate.figures,
                "closed_finding_ids": revision.closed_finding_ids,
            }
        )


def test_revision_rejects_generation_gaps_forged_refs_and_witnesses() -> None:
    revision = _revision()
    forged_ref = revision.successor_ref.model_copy(update={"artifact_version": "2"})
    updates = (
        {"revision_id": "paper-revision-" + "f" * 64},
        {"successor_generation": 3},
        {
            "successor_ref": revision.successor_ref.model_copy(
                update={"artifact_version": 3}
            )
        },
        {"successor_ref": _ref("other-draft", 2, "2")},
        {
            "successor_ref": revision.successor_ref.model_copy(
                update={"content_hash": revision.predecessor_ref.content_hash}
            )
        },
        {"successor_ref": forged_ref},
        {
            "predecessor_audit_ref": revision.predecessor_audit_ref.model_copy(
                update={"artifact_version": 2}
            )
        },
        {
            "successor_audit_ref": revision.successor_audit_ref.model_copy(
                update={"artifact_version": 2}
            )
        },
        {"predecessor_audit_ref": revision.successor_audit_ref},
        {"successor_audit_ref": revision.predecessor_audit_ref},
        {"map_ref": _ref("not-an-argument-map", 1, "5")},
        {"ledger_ref": _ref("not-a-ledger", 1, "6")},
        {"citation_report_ref": _ref("not-a-citation-report", 1, "7")},
        {"closed_finding_ids": (revision.closed_finding_ids[0],) * 2},
        {"closure_witnesses": ()},
    )
    for update in updates:
        with pytest.raises(ValidationError):
            DraftRevision.model_validate(
                {**revision.model_dump(mode="python"), **update}
            )


def test_revision_requires_canonical_exact_witnesses_for_every_finding() -> None:
    revision = _revision()
    first, second = revision.closure_witnesses
    updates = (
        {"closed_finding_ids": tuple(reversed(revision.closed_finding_ids))},
        {"closure_witnesses": (second, first)},
        {
            "closure_witnesses": (
                first.model_copy(update={"finding_id": "other-finding"}),
                second,
            )
        },
        {
            "closure_witnesses": (
                first.model_copy(update={"finding_kind": "policy-overclaim"}),
                second,
            )
        },
        {
            "closure_witnesses": (
                first.model_copy(update={"code": "PAPER_SCOPE_EXCEEDED"}),
                second,
            )
        },
    )
    for update in updates:
        with pytest.raises(ValidationError):
            DraftRevision.model_validate(
                {**revision.model_dump(mode="python"), **update}
            )


@pytest.mark.parametrize(
    "forged",
    (
        lambda witness: witness.model_copy(
            update={
                "predecessor_target": witness.predecessor_target.model_copy(
                    update={"start": "0"}
                )
            }
        ),
        lambda witness: witness.model_copy(update={"claim_ids": ("BAD ID",)}),
    ),
)
def test_revision_revalidates_preconstructed_nested_witnesses(forged) -> None:  # type: ignore[no-untyped-def]
    revision = _revision()
    first, second = revision.closure_witnesses

    with pytest.raises(ValidationError):
        DraftRevision.model_validate(
            {
                **revision.model_dump(mode="python"),
                "closure_witnesses": (forged(first), second),
            }
        )

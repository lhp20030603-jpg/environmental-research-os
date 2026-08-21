"""Authenticated Task 5 protocol and exact case-source handoff to Task 6."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, model_validator

from envresearch.models.artifact import ArtifactRef, ResearchArtifact, verify_artifact
from envresearch.models.design import (
    DesignFinding,
    EstimandSpecPayload,
    MethodCandidatesPayload,
)
from envresearch.paper.audit_contracts import PaperAuditReport
from envresearch.personal_validation._strict import (
    STRICT,
    ReviewerBehavioralContract,
    StrictArtifactRef,
    materialize_id,
    model_payload,
    require_materialized_id,
    strict_model_input,
)
from envresearch.personal_validation.case_stops import CanonicalPolicyArtifact
from envresearch.personal_validation.contracts import (
    CompletedFactoryRunTarget,
    PersonalCanonicalCase,
    PersonalValidationAttempt,
)
from envresearch.personal_validation.errors import (
    PersonalValidationAuthorityInvalid,
    PersonalValidationIntegrityInvalid,
)
from envresearch.personal_validation.private_store import PersonalValidationStore
from envresearch.research.artifact_lifecycle_support import artifact_ref
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.stop_inspection import load_open_design_findings

if TYPE_CHECKING:
    from envresearch.personal_validation.canonical_cases import (
        DisposableAttemptRoots,
        LoadedProtocol,
    )

StrictResearchArtifact = Annotated[
    ResearchArtifact[object], BeforeValidator(strict_model_input)
]
StrictDesignFinding = Annotated[DesignFinding, BeforeValidator(strict_model_input)]
StrictPaperAudit = Annotated[PaperAuditReport, BeforeValidator(strict_model_input)]
_OBJECT = re.compile(r"^v([1-9][0-9]*)-([0-9a-f]{64})\.json$")


class CanonicalCaseSourceAuthority(BaseModel):
    """Orchestration-observed sources bound to one exact completed attempt."""

    model_config = STRICT
    schema_version: Literal["personal.canonical-case-source.v1"]
    authority_id: str
    attempt_ref: StrictArtifactRef
    target_ref: StrictArtifactRef
    case_kind: Literal[
        "successful-end-to-end",
        "correct-stop",
        "data-method-incompatibility",
        "evidence-citation-challenge",
    ]
    method_candidates_artifact: StrictResearchArtifact | None = None
    estimand_artifact: StrictResearchArtifact | None = None
    blocker_findings: tuple[StrictDesignFinding, ...] = ()
    predecessor_audit: StrictPaperAudit | None = None

    def identity_payload(self) -> dict[str, object]:
        return model_payload(self, exclude="authority_id")

    @model_validator(mode="after")
    def require_exact_source_shape(self) -> CanonicalCaseSourceAuthority:
        research = (
            self.method_candidates_artifact is not None
            and self.estimand_artifact is not None
        )
        if (self.method_candidates_artifact is None) != (
            self.estimand_artifact is None
        ):
            raise ValueError("research source authority must be paired")
        if research:
            assert self.method_candidates_artifact is not None
            assert self.estimand_artifact is not None
            for source in (
                self.method_candidates_artifact,
                self.estimand_artifact,
            ):
                verify_artifact(source)
            MethodCandidatesPayload.model_validate(
                self.method_candidates_artifact.payload
            )
            EstimandSpecPayload.model_validate(self.estimand_artifact.payload)
        expected = {
            "successful-end-to-end": (False, False, False),
            "correct-stop": (False, True, False),
            "data-method-incompatibility": (True, False, False),
            "evidence-citation-challenge": (False, False, True),
        }[self.case_kind]
        actual = (
            research,
            bool(self.blocker_findings),
            self.predecessor_audit is not None,
        )
        if actual != expected:
            raise ValueError("case source authority shape differs from case kind")
        if self.predecessor_audit is not None:
            audit = self.predecessor_audit
            if audit.verdict != "blocked" or not audit.findings:
                raise ValueError("predecessor audit authority is not blocked")
        require_materialized_id(
            self.authority_id, "personal-case-source-", self.identity_payload()
        )
        return self


def publish_protocol_handoff(
    store: PersonalValidationStore, loaded: LoadedProtocol
) -> None:
    """Inject the complete canonical protocol closure into the private store."""
    for snapshot in loaded.input_snapshots:
        store.publish(snapshot.snapshot_id, snapshot)
    for case in loaded.cases:
        store.publish(case.case_id, case)
    store.publish(loaded.protocol.protocol_id, loaded.protocol)
    for expected, reviewer in zip(
        loaded.expected_behaviors, loaded.reviewer_contracts, strict=True
    ):
        store.publish(expected.behavior_id, expected)
        store.publish(reviewer.contract_id, reviewer)
    policy_digests = {
        "scientific": loaded.protocol.scientific_policy_sha256,
        "evidence": loaded.protocol.evidence_policy_sha256,
        "synthesis": loaded.protocol.synthesis_policy_sha256,
        "external-access": loaded.protocol.external_access_policy_sha256,
        "rubric": loaded.protocol.rubric_sha256,
        "report-schema": loaded.protocol.report_schema_sha256,
    }
    policies = (
        ("scientific", loaded.policy_artifacts.scientific),
        ("evidence", loaded.policy_artifacts.evidence),
        ("synthesis", loaded.policy_artifacts.synthesis),
        ("external-access", loaded.policy_artifacts.external_access),
        ("rubric", loaded.policy_artifacts.rubric),
        ("report-schema", loaded.policy_artifacts.report_schema),
    )
    for normalized, policy in policies:
        if normalized not in policy_digests:
            raise _integrity("canonical policy kind is unknown")
        reference = store.publish(policy_identity(normalized, policy), policy)
        if reference.content_hash != policy_digests[normalized]:
            raise _integrity("canonical policy bytes differ from protocol digest")


def policy_identity(kind: str, policy: CanonicalPolicyArtifact) -> str:
    digest = hashlib.sha256(policy.model_dump_json().encode()).hexdigest()
    return f"personal-policy-{kind}-{digest}"


def reopen_policy(
    store: PersonalValidationStore, kind: str, digest: str
) -> tuple[ArtifactRef, CanonicalPolicyArtifact]:
    identity = f"personal-policy-{kind}-{digest}"
    reference = ArtifactRef(
        artifact_id=identity, artifact_version=1, content_hash=digest
    )
    policy = store.load(reference, CanonicalPolicyArtifact)
    if policy.policy_kind != kind:
        raise _integrity("canonical policy kind differs from requested role")
    return reference, policy


def reopen_reviewer_contract(
    store: PersonalValidationStore, case: PersonalCanonicalCase
) -> ReviewerBehavioralContract:
    contract = store.load(case.reviewer_contract_ref, ReviewerBehavioralContract)
    if contract.case_kind != case.kind:
        raise _integrity("reviewer contract differs from canonical case")
    return contract


def publish_case_source_handoff(
    store: PersonalValidationStore,
    roots: DisposableAttemptRoots,
    attempt_ref: ArtifactRef,
) -> ArtifactRef:
    """Publish exact Research/Paper source authority observed by Task 5."""
    attempt = store.load(attempt_ref, PersonalValidationAttempt)
    target_ref = _target_ref(attempt)
    payload: dict[str, object] = {
        "schema_version": "personal.canonical-case-source.v1",
        "attempt_ref": attempt_ref,
        "target_ref": target_ref,
        "case_kind": store.load(attempt.case_ref, PersonalCanonicalCase).kind,
        "method_candidates_artifact": None,
        "estimand_artifact": None,
        "blocker_findings": (),
        "predecessor_audit": None,
    }
    kind = payload["case_kind"]
    if kind == "data-method-incompatibility":
        observed = roots.research._state.get("orchestrator")
        if not isinstance(observed, ResearchOrchestrator):
            raise _integrity("research source authority is unavailable")
        orchestrator = observed
        payload.update(
            method_candidates_artifact=orchestrator.lifecycle.read_artifact(
                Path("artifacts/method-candidates.json")
            ),
            estimand_artifact=orchestrator.lifecycle.read_artifact(
                Path("artifacts/estimand-spec.yaml")
            ),
        )
    elif kind == "correct-stop":
        observed = roots.research._state.get("orchestrator")
        if not isinstance(observed, ResearchOrchestrator):
            raise _integrity("correct-stop source authority is unavailable")
        orchestrator = observed
        payload["blocker_findings"] = load_open_design_findings(orchestrator)
    elif kind == "evidence-citation-challenge":
        if not isinstance(attempt.target, CompletedFactoryRunTarget):
            raise _integrity("challenge source target is not completed")
        revision = attempt.target.run.release.revision
        if revision is None:
            raise _integrity("challenge revision source is unavailable")
        payload["predecessor_audit"] = roots.paper.audit_service.store.load(
            revision.predecessor_audit_ref
        )
    payload["authority_id"] = materialize_id("personal-case-source-", payload)
    authority = CanonicalCaseSourceAuthority.model_validate(payload)
    return store.publish(authority.authority_id, authority)


def reopen_case_source(
    store: PersonalValidationStore, attempt_ref: ArtifactRef
) -> CanonicalCaseSourceAuthority:
    matches: list[CanonicalCaseSourceAuthority] = []
    for identity in store.objects.list_directory(Path("exit/objects")):
        if not identity.startswith("personal-case-source-"):
            continue
        parent = Path("exit/objects") / identity
        for name in store.objects.list_directory(parent):
            matched = _OBJECT.fullmatch(name)
            if matched is None:
                raise _integrity("case source object filename is invalid")
            reference = ArtifactRef(
                artifact_id=identity,
                artifact_version=int(matched.group(1)),
                content_hash=matched.group(2),
            )
            authority = store.load(reference, CanonicalCaseSourceAuthority)
            if authority.attempt_ref == attempt_ref:
                matches.append(authority)
    if len(matches) != 1:
        raise _integrity("exact case source authority is missing or ambiguous")
    return matches[0]


def source_ref(source: ResearchArtifact[object]) -> ArtifactRef:
    verify_artifact(source)
    return artifact_ref(source.envelope)


def require_safe_bytes(
    store: PersonalValidationStore, case: PersonalCanonicalCase, data: bytes
) -> None:
    forbidden = (
        case.expected_behavior_ref.artifact_id.encode(),
        case.expected_behavior_ref.content_hash.encode(),
        str(store.root.lexical_path).encode(),
    )
    if any(token and token in data for token in forbidden):
        raise PersonalValidationAuthorityInvalid(
            "private oracle or canary disclosure is forbidden",
            finding_kind="review-oracle-disclosure",
        )


def require_safe_reference(
    store: PersonalValidationStore,
    case: PersonalCanonicalCase,
    reference: ArtifactRef,
) -> None:
    relative = (
        Path("exit/objects")
        / reference.artifact_id
        / f"v{reference.artifact_version}-{reference.content_hash}.json"
    )
    data = store.objects.read_file(relative, description="review projection object")
    if hashlib.sha256(data).hexdigest() != reference.content_hash:
        raise _integrity("review projection reference bytes are invalid")
    require_safe_bytes(store, case, data)


def require_safe_projection_reference(
    store: PersonalValidationStore,
    case: PersonalCanonicalCase,
    attempt: PersonalValidationAttempt,
    reference: ArtifactRef,
) -> None:
    if (
        isinstance(attempt.target, CompletedFactoryRunTarget)
        and reference == attempt.target.run_ref
    ):
        require_safe_bytes(store, case, attempt.target.run.model_dump_json().encode())
        return
    require_safe_reference(store, case, reference)


def require_allowlisted_findings(
    store: PersonalValidationStore, bundle: Any, response: Any
) -> None:
    from envresearch.personal_validation.report import authority_error
    from envresearch.personal_validation.review_bundle import projection_refs
    from envresearch.personal_validation.review_contracts import (
        PersonalFinding,
        ReviewPublication,
    )

    allowed = {
        bundle.behavioral_contract_ref,
        *bundle.target_refs,
        *bundle.evidence_refs,
        *projection_refs(bundle.projection),
    }
    if bundle.role == "synthesis":
        for publication_ref in bundle.primary_publication_refs:
            publication = store.load(publication_ref, ReviewPublication)
            allowed.update(
                {
                    publication_ref,
                    publication.assignment_ref,
                    publication.review_ref,
                    *publication.finding_refs,
                    *publication.external_access_record_refs,
                }
            )
            for finding_ref in publication.finding_refs:
                finding = store.load(finding_ref, PersonalFinding)
                allowed.update((*finding.target_refs, *finding.evidence_refs))
    if any(
        set(item.target_refs) - allowed or set(item.evidence_refs) - allowed
        for item in response.findings
    ):
        raise authority_error("review finding escaped the bundle allowlist")


def _target_ref(attempt: PersonalValidationAttempt) -> ArtifactRef:
    return (
        attempt.target.run_ref
        if isinstance(attempt.target, CompletedFactoryRunTarget)
        else attempt.target.inspection_ref
    )


def _integrity(message: str) -> PersonalValidationIntegrityInvalid:
    return PersonalValidationIntegrityInvalid(
        message, finding_kind="case-source-authority-invalid"
    )


__all__ = [
    "CanonicalCaseSourceAuthority",
    "policy_identity",
    "publish_case_source_handoff",
    "publish_protocol_handoff",
    "reopen_case_source",
    "reopen_policy",
    "reopen_reviewer_contract",
    "require_allowlisted_findings",
    "require_safe_bytes",
    "require_safe_projection_reference",
    "require_safe_reference",
    "source_ref",
]

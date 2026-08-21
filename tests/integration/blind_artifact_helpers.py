"""Reusable authenticated blind-case builders for integration tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from blind_payload_helpers import (
    NOW,
    SHA256,
    brief,
    expert_sheet,
    fact_map,
    leakage,
    recommendation,
    source_sheet,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from envresearch.benchmarks.blind_artifacts import BlindArtifactLifecycle
from envresearch.benchmarks.blind_authority import (
    AuthorityTrustAnchor,
    BlindEnrollmentPayload,
    EnrolledBlindCase,
    HumanKeyEnrollment,
    SignedBlindEnrollment,
    SignedHumanEvidence,
    canonical_json,
    encode_binary,
    enrollment_signing_bytes,
    evidence_signing_bytes,
)
from envresearch.benchmarks.blind_enrollment_marker import freeze_enrollment
from envresearch.benchmarks.blind_enrollment_store import (
    read_verified_enrollment,
    store_signed_enrollment,
)
from envresearch.benchmarks.blind_evidence_artifacts import persist_signed_evidence
from envresearch.benchmarks.blind_trust_store import pin_authority_anchor
from envresearch.benchmarks.claim_integrity import CitationIntegrityValidator
from envresearch.models.artifact import ArtifactRef, ProducerIdentity
from envresearch.models.benchmark_claims import (
    ClaimUsage,
)
from envresearch.models.benchmark_evaluation import (
    AcceptedArtifactClaims,
    AdjudicationVerdict,
    ExpertScoreSheet,
    PosthocComparison,
)
from envresearch.models.principal import PrincipalAssignment, PrincipalKind
from envresearch.research.order_policy import blind_expert_rubric
from envresearch.research.principal_registry import PrincipalRegistry
from envresearch.workers import FilesystemWorkerQueue


class CaseHarness:
    """Issue authenticated principals and publish one real artifact chain."""

    def __init__(self, root: Path) -> None:
        queue = FilesystemWorkerQueue(root / "exchange")
        self.registry = PrincipalRegistry(queue.control, "blind-run")
        self.signers = {
            (kind, slot): Ed25519PrivateKey.generate()
            for kind, slot in (
                (PrincipalKind.EXPERT, 1),
                (PrincipalKind.EXPERT, 2),
                (PrincipalKind.ADJUDICATOR, 1),
            )
        }
        participants = tuple(
            HumanKeyEnrollment(
                case_id="case-rct",
                role=kind,
                slot=slot,
                principal_id=f"external-case-rct-{kind.value}-{slot}",
                key_id=f"key-case-rct-{kind.value}-{slot}",
                public_key=_public_key(private),
            )
            for (kind, slot), private in self.signers.items()
        )
        self.registry.enroll_benchmark_humans("case-rct", participants)
        _freeze_harness_enrollment(self.registry, participants)
        self.service = BlindArtifactLifecycle(root, "blind-run", self.registry)

    def worker(self, kind: PrincipalKind, generation: int = 1) -> PrincipalAssignment:
        return self.registry.benchmark_worker("case-rct", kind, generation)

    def human(self, kind: PrincipalKind, slot: int) -> PrincipalAssignment:
        return self.registry.benchmark_human("case-rct", kind, slot, 1)

    def signed_evidence(
        self,
        payload: ExpertScoreSheet | AdjudicationVerdict,
        kind: PrincipalKind,
        slot: int,
        inputs: tuple[ArtifactRef, ...],
        schema: str,
    ) -> ArtifactRef:
        assignment = self.human(kind, slot)
        candidate = payload.model_dump(mode="json")
        placeholder = SignedHumanEvidence(
            case_id="case-rct",
            role=kind,
            slot=slot,
            source_generation=1,
            assignment_id=assignment.assignment_id,
            order_hash=f"direct-order-{kind.value}-{slot}-{schema}",
            candidate_schema=schema,
            candidate_sha256=hashlib.sha256(canonical_json(candidate)).hexdigest(),
            key_id=assignment.key_id or "",
            candidate=candidate,
            signature=encode_binary(b"\0" * 64),
        )
        signed = placeholder.model_copy(update={
            "signature": encode_binary(
                self.signers[(kind, slot)].sign(evidence_signing_bytes(placeholder))
            )
        })
        return persist_signed_evidence(
            self.service, "case-rct", signed, assignment, inputs
        )

    def through_recommendation(self) -> None:
        service = self.service
        curator = self.worker(PrincipalKind.CURATOR)
        masker = self.worker(PrincipalKind.MASKER)
        validator = self.worker(PrincipalKind.LEAKAGE_VALIDATOR)
        recommender = self.worker(PrincipalKind.RECOMMENDER)
        service.publish_source("case-rct", source_sheet(), curator)
        service.publish_brief(
            "case-rct",
            brief(service.ref("case-rct", "source_sheet"), masker.principal_id),
            masker,
        )
        service.publish_fact_map(
            "case-rct",
            fact_map(
                service.ref("case-rct", "source_sheet"),
                service.ref("case-rct", "blinded_brief"),
                masker.principal_id,
            ),
            masker,
        )
        service.publish_leakage(
            "case-rct",
            leakage(
                service.ref("case-rct", "source_sheet"),
                service.ref("case-rct", "blinded_brief"),
                validator.principal_id,
            ),
            validator,
        )
        service.publish_recommendation(
            "case-rct",
            recommendation(
                service.ref("case-rct", "blinded_brief"),
                service.ref("case-rct", "leakage_report"),
                recommender.principal_id,
            ),
            recommender,
        )
        service.lifecycle.persist_structured(
            service.paths("case-rct").source_sheet.parent / "expert-rubric.json",
            blind_expert_rubric(),
            ProducerIdentity(component="blind-expert-rubric", version="1.0"),
            (),
        )

    def populated(self) -> None:
        self.through_recommendation()
        recommendation_ref = self.service.ref("case-rct", "recommendation")
        expert_one = self.human(PrincipalKind.EXPERT, 1)
        expert_two = self.human(PrincipalKind.EXPERT, 2)
        brief_ref = self.service.ref("case-rct", "blinded_brief")
        rubric_ref = self.service.lifecycle.artifact_ref(self.service.paths(
            "case-rct"
        ).source_sheet.parent / "expert-rubric.json")
        first = expert_sheet(recommendation_ref, expert_one.principal_id)
        second = expert_sheet(recommendation_ref, expert_two.principal_id)
        self.signed_evidence(
            first, PrincipalKind.EXPERT, 1,
            (brief_ref, recommendation_ref, rubric_ref),
            "envresearch.ExpertScoreSheet",
        )
        self.service.publish_expert_score(
            "case-rct", first, expert_one, slot=1,
        )
        self.signed_evidence(
            second, PrincipalKind.EXPERT, 2,
            (brief_ref, recommendation_ref, rubric_ref),
            "envresearch.ExpertScoreSheet",
        )
        self.service.publish_expert_score(
            "case-rct", second, expert_two, slot=2,
        )
        adjudicator = self.human(PrincipalKind.ADJUDICATOR, 1)
        third = expert_sheet(recommendation_ref, adjudicator.principal_id)
        self.signed_evidence(
            third, PrincipalKind.ADJUDICATOR, 1,
            (brief_ref, recommendation_ref, rubric_ref),
            "envresearch.ExpertScoreSheet",
        )
        self.service.publish_third_score(
            "case-rct", third, adjudicator,
        )
        verdict = AdjudicationVerdict(
            score_sheet_ref=self.service.ref("case-rct", "expert_one"),
            verdict="accept",
            rationale="The blind scores support acceptance.",
            adjudicator_principal=adjudicator.principal_id,
        )
        self.signed_evidence(
            verdict, PrincipalKind.ADJUDICATOR, 1,
            (
                recommendation_ref,
                self.service.ref("case-rct", "expert_one"),
                self.service.ref("case-rct", "expert_two"),
                self.service.ref("case-rct", "third_score"),
            ),
            "envresearch.AdjudicationVerdict",
        )
        self.service.publish_adjudication(
            "case-rct", verdict, adjudicator,
        )
        self.service.publish_posthoc(
            "case-rct",
            PosthocComparison(
                recommendation_ref=recommendation_ref,
                realized_method_profile_ref="rct-profile-v1",
                comparison={"classification": "defensible-alternative"},
                analyst_principal=adjudicator.principal_id,
            ),
            adjudicator,
        )
        self._publish_citation_report()

    def _publish_citation_report(self) -> None:
        service = self.service
        paths = service.paths("case-rct")
        payload = service.lifecycle.read_artifact(paths.recommendation).payload
        usages = tuple(
            ClaimUsage(
                claim_id="claim-001",
                statement_sha256=hashlib.sha256(statement.encode()).hexdigest(),
                json_pointer=pointer,
            )
            for pointer, statement in _string_leaves(payload)
            if CitationIntegrityValidator._is_source_dependent(
                pointer, statement, "method-recommendation"
            )
        )
        report = CitationIntegrityValidator().validate(
            source_sheets=(source_sheet(),),
            fact_maps=(
                fact_map(
                    service.ref("case-rct", "source_sheet"),
                    service.ref("case-rct", "blinded_brief"),
                    self.worker(PrincipalKind.MASKER).principal_id,
                ),
            ),
            artifacts=(
                AcceptedArtifactClaims(
                    artifact_ref=service.ref("case-rct", "recommendation"),
                    payload=payload,
                    usages=usages,
                ),
            ),
            source_sheet_refs=(service.ref("case-rct", "source_sheet"),),
            claim_fact_map_refs=(service.ref("case-rct", "claim_fact_map"),),
            blinded_brief_refs=(service.ref("case-rct", "blinded_brief"),),
        )
        assert report.passed
        service.publish_citation_report(
            "case-rct", report, self.worker(PrincipalKind.LEAKAGE_VALIDATOR)
        )


def _string_leaves(value: object, pointer: str = "") -> tuple[tuple[str, str], ...]:
    if isinstance(value, str):
        return ((pointer, value),)
    if isinstance(value, dict):
        return tuple(
            item
            for key in sorted(value)
            for item in _string_leaves(value[key], f"{pointer}/{key}")
        )
    if isinstance(value, list):
        return tuple(
            item
            for index, child in enumerate(value)
            for item in _string_leaves(child, f"{pointer}/{index}")
        )
    return ()


def _freeze_harness_enrollment(
    registry: PrincipalRegistry, participants: tuple[HumanKeyEnrollment, ...]
) -> None:
    authority = Ed25519PrivateKey.generate()
    pin_authority_anchor(
        registry,
        AuthorityTrustAnchor(key_id="harness-authority", public_key=_public_key(authority)),
    )
    payload = BlindEnrollmentPayload(
        evaluation_id="harness-evaluation",
        authority_key_id="harness-authority",
        frozen_at=NOW,
        cases=(EnrolledBlindCase(
            case_id="case-rct",
            method_family="RCT",
            cohort="pilot",
            source_generation=1,
            descriptor_sha256=SHA256,
            source_ref=_fixture_ref("fixture-source"),
            claim_fact_map_ref=_fixture_ref("fixture-map"),
            blinded_brief_ref=_fixture_ref("fixture-brief"),
        ),),
        participants=participants,
        profile_registry_sha256=SHA256,
        rubric_sha256=SHA256,
        policy_sha256=SHA256,
    )
    signed = SignedBlindEnrollment(
        payload=payload,
        signature=encode_binary(authority.sign(enrollment_signing_bytes(payload))),
    )
    store_signed_enrollment(registry, "case-rct", signed)
    freeze_enrollment(
        registry, "case-rct", read_verified_enrollment(registry, "case-rct")
    )


def _public_key(private: Ed25519PrivateKey) -> str:
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return encode_binary(raw)


def _fixture_ref(artifact_id: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id, artifact_version=1, content_hash=SHA256
    )

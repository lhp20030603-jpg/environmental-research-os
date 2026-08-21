"""Adversarial fresh-reopen substitutions for Task 6 recovery authority."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import pytest
from personal_validation_review_fixtures import ReviewCase, make_review_case

from envresearch.personal_validation._strict import (
    AgentDispatchObservation,
    ReviewAssignment,
    canonical_json,
    materialize_id,
)
from envresearch.personal_validation.errors import (
    PersonalValidationAuthorityInvalid,
    PersonalValidationIntegrityInvalid,
)
from envresearch.personal_validation.review_contracts import ExternalAccessRequest


def _snapshot(case: ReviewCase) -> tuple[tuple[str, int, bytes], ...]:
    root = case.store.root.lexical_path
    return tuple(
        (str(path.relative_to(root)), path.stat().st_mode, path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def test_report_rejects_cross_attempt_evaluation_and_publication_substitution(
    tmp_path: Path,
) -> None:
    left = make_review_case(tmp_path / "left")
    right = make_review_case(tmp_path / "right", case_kind="successful-end-to-end")
    try:
        scientific = left.record("scientific", invocation_id="left-science")
        evidence = left.record("evidence", invocation_id="left-evidence")
        synthesis = left.record(
            "synthesis",
            invocation_id="left-synthesis",
            primary_publication_refs=(
                scientific.publication_ref,
                evidence.publication_ref,
            ),
        )
        evaluation = left.service.evaluate_case(left.attempt_ref)
        foreign_evaluation = right.service.evaluate_case(right.attempt_ref)
        foreign_publication = right.record(
            "scientific", invocation_id="right-science"
        ).publication_ref
        substitutions = (
            (foreign_evaluation, scientific.publication_ref),
            (evaluation, foreign_publication),
        )
        for evaluation_ref, scientific_ref in substitutions:
            before = _snapshot(left)
            with pytest.raises(
                (PersonalValidationAuthorityInvalid, PersonalValidationIntegrityInvalid)
            ):
                left.service.finalize_report(
                    left.attempt_ref,
                    evaluation_ref,
                    scientific_ref,
                    evidence.publication_ref,
                    synthesis.publication_ref,
                )
            assert _snapshot(left) == before
    finally:
        right.close()
        left.close()


def test_dispatch_rejects_forged_assignment_policy_without_store_mutation(
    tmp_path: Path,
) -> None:
    case = make_review_case(tmp_path)
    try:
        prepared = case.prepare("scientific")
        assignment_ref = case.service.assign_review(
            case.attempt_ref,
            prepared.bundle_ref,
            role="scientific",
            invocation_id="forged-policy",
        )
        assignment = case.store.load(assignment_ref, ReviewAssignment)
        payload = assignment.model_dump(mode="python", exclude={"assignment_id"})
        payload["policy_sha256"] = "b" * 64
        payload["assignment_id"] = materialize_id(
            "personal-review-assignment-", payload
        )
        forged = ReviewAssignment.model_validate(payload)
        forged_ref = case.store.publish(forged.assignment_id, forged)
        observed = AgentDispatchObservation(
            schema_version="personal.agent-dispatch-observation.v1",
            invocation_id=forged.invocation_id,
            observed_model_id="review-model-v1",
            observed_runtime_id="review-runtime-v1",
            dispatched_at=datetime(2026, 8, 21, tzinfo=UTC),
        )
        before = _snapshot(case)
        with pytest.raises(PersonalValidationAuthorityInvalid, match="policy"):
            case.service.record_dispatch(
                forged_ref, canonical_json(observed.model_dump(mode="json"))
            )
        assert _snapshot(case) == before
    finally:
        case.close()


def test_external_dispatch_rejects_private_root_embedded_in_https_locator(
    tmp_path: Path,
) -> None:
    case = make_review_case(tmp_path)
    try:
        prepared = case.prepare("scientific")
        assignment = case.service.assign_review(
            case.attempt_ref,
            prepared.bundle_ref,
            role="scientific",
            invocation_id="private-root-request",
        )
        private_root = case.store.root.lexical_path.as_posix()
        encoded = quote(private_root, safe="")
        variants = (
            private_root,
            encoded,
            quote(encoded, safe=""),
            encoded.replace("%", "％"),
            private_root.replace("/", "\\"),
        )
        locators = tuple(f"https://example.org/{value}/document" for value in variants)
        query_variants = (*variants, private_root.upper())
        locators += tuple(
            f"https://example.org/document?q={value}" for value in query_variants
        )
        for locator in locators:
            request = ExternalAccessRequest(
                provider="web",
                operation="read-official-documentation",
                source_locator=locator,
                local_finding_keys=(),
            )
            before = _snapshot(case)
            with pytest.raises(
                PersonalValidationAuthorityInvalid, match="oracle|canary"
            ):
                case.service.record_external_access_dispatch(
                    assignment,
                    assignment,
                    canonical_json(request.model_dump(mode="json")),
                )
            assert _snapshot(case) == before
    finally:
        case.close()

"""Blind benchmark artifacts retain exact, authenticated case lineage."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from blind_artifact_helpers import (
    CaseHarness,
    expert_sheet,
    recommendation,
    source_sheet,
)

from envresearch.benchmarks.claim_report import (
    accepted_artifact_binding,
    binding_sha256,
    report_from_payload,
)
from envresearch.models.artifact import seal_artifact
from envresearch.models.benchmark_evaluation import (
    AcceptedArtifactClaims,
    AdjudicationVerdict,
    PosthocComparison,
)
from envresearch.models.principal import PrincipalKind
from envresearch.research.artifact_lifecycle_support import history_path


def test_cross_case_reference_is_rejected(tmp_path: Path) -> None:
    harness = CaseHarness(tmp_path)
    harness.through_recommendation()
    cross_case_ref = harness.service.ref(
        "case-rct", "blinded_brief"
    ).model_copy(
        update={"artifact_id": "blind/case-iv/blinded-brief"}
    )
    forged = recommendation(
        harness.service.ref("case-rct", "blinded_brief"),
        harness.service.ref("case-rct", "leakage_report"),
        harness.worker(PrincipalKind.RECOMMENDER).principal_id,
    ).model_copy(
        update={"blinded_brief_ref": cross_case_ref}
    )

    with pytest.raises(ValueError, match="case lineage mismatch"):
        harness.service.publish_recommendation(
            "case-rct", forged, harness.worker(PrincipalKind.RECOMMENDER)
        )


def test_posthoc_identity_cannot_publish_before_blind_scores_lock(
    tmp_path: Path,
) -> None:
    harness = CaseHarness(tmp_path)
    harness.through_recommendation()
    adjudicator = harness.human(PrincipalKind.ADJUDICATOR, 1)

    with pytest.raises(ValueError, match="blind scores must be locked"):
        harness.service.publish_posthoc(
            "case-rct",
            PosthocComparison(
                recommendation_ref=harness.service.ref("case-rct", "recommendation"),
                realized_method_profile_ref="rct-profile-v1",
                comparison={"classification": "match"},
                analyst_principal=adjudicator.principal_id,
            ),
            adjudicator,
        )


def test_current_chain_authenticates_exact_inputs_and_roles(tmp_path: Path) -> None:
    harness = CaseHarness(tmp_path)
    harness.populated()

    refs = harness.service.require_current_chain("case-rct")

    assert refs[0] == harness.service.ref("case-rct", "source_sheet")
    assert refs[-1] == harness.service.ref("case-rct", "citation_report")


def test_wrong_authenticated_role_cannot_publish_source(tmp_path: Path) -> None:
    harness = CaseHarness(tmp_path)

    with pytest.raises(ValueError, match="principal role mismatch"):
        harness.service.publish_source(
            "case-rct", source_sheet(), harness.worker(PrincipalKind.MASKER)
        )


def test_stale_history_is_rejected_even_when_current_bytes_are_untouched(
    tmp_path: Path,
) -> None:
    harness = CaseHarness(tmp_path)
    harness.populated()
    paths = harness.service.paths("case-rct")
    history = harness.service.lifecycle.read_history(
        paths.recommendation,
        harness.service.lifecycle.current_envelope(
            paths.recommendation
        ).artifact_version,
    )
    forged = history.model_copy(
        update={
            "payload": {**history.payload, "estimand_interpretation": "Replacement."}
        }
    )
    harness.service.lifecycle.store.write_structured(
        history_path(paths.recommendation, history.envelope.artifact_version),
        seal_artifact(forged),
    )

    with pytest.raises((FileExistsError, ValueError), match="history|lineage"):
        harness.service.require_current_chain("case-rct")


def test_self_consistent_current_replacement_cannot_detach_from_history(
    tmp_path: Path,
) -> None:
    harness = CaseHarness(tmp_path)
    harness.populated()
    path = harness.service.paths("case-rct").recommendation
    current = harness.service.lifecycle.read_artifact(path)
    forged = current.model_copy(
        update={
            "payload": {**current.payload, "estimand_interpretation": "Replacement."}
        }
    )
    harness.service.lifecycle.store.write_structured(path, seal_artifact(forged))

    with pytest.raises(ValueError, match="blind benchmark lineage is stale"):
        harness.service.require_current_chain("case-rct")


def test_self_consistent_but_unverified_citation_replacement_is_rejected(
    tmp_path: Path,
) -> None:
    harness = CaseHarness(tmp_path)
    harness.populated()
    paths = harness.service.paths("case-rct")
    current = report_from_payload(
        harness.service.lifecycle.read_artifact(paths.citation_report).payload
    )
    recommendation_payload = harness.service.lifecycle.read_artifact(
        paths.recommendation
    ).payload
    replacement_binding = accepted_artifact_binding(
        AcceptedArtifactClaims(
            artifact_ref=harness.service.ref("case-rct", "recommendation"),
            payload=recommendation_payload,
            usages=(),
        )
    )
    forged = replace(
        current,
        accepted_artifact_bindings=(replacement_binding,),
        binding_sha256=binding_sha256(
            current.source_sheet_refs,
            current.claim_fact_map_refs,
            current.blinded_brief_refs,
            (replacement_binding,),
            current.validator_version,
        ),
    )

    with pytest.raises(ValueError, match="citation integrity report is not current"):
        harness.service.publish_citation_report(
            "case-rct", forged, harness.worker(PrincipalKind.LEAKAGE_VALIDATOR)
        )


def test_legacy_v1_report_cannot_replace_current_blind_semantics(
    tmp_path: Path,
) -> None:
    """Formal blind lineage must recompute under the current fact-only policy."""
    harness = CaseHarness(tmp_path)
    harness.through_recommendation()
    harness._publish_citation_report()
    path = harness.service.paths("case-rct").citation_report
    current = report_from_payload(
        harness.service.lifecycle.read_artifact(path).payload
    )
    legacy = replace(
        current,
        validator_version="claim-integrity-v1",
        binding_sha256=binding_sha256(
            current.source_sheet_refs,
            current.claim_fact_map_refs,
            current.blinded_brief_refs,
            current.accepted_artifact_bindings,
            "claim-integrity-v1",
        ),
    )

    with pytest.raises(ValueError, match="citation integrity report is not current"):
        harness.service.publish_citation_report(
            "case-rct", legacy, harness.worker(PrincipalKind.LEAKAGE_VALIDATOR)
        )


def test_noncanonical_adjudicator_slot_is_rejected_before_persistence(
    tmp_path: Path,
) -> None:
    harness = CaseHarness(tmp_path)
    harness.through_recommendation()
    recommendation_ref = harness.service.ref("case-rct", "recommendation")
    brief_ref = harness.service.ref("case-rct", "blinded_brief")
    rubric_ref = harness.service.lifecycle.artifact_ref(
        harness.service.paths("case-rct").source_sheet.parent / "expert-rubric.json"
    )
    for slot in (1, 2):
        expert = harness.human(PrincipalKind.EXPERT, slot)
        score = expert_sheet(recommendation_ref, expert.principal_id)
        harness.signed_evidence(
            score,
            PrincipalKind.EXPERT,
            slot,
            (brief_ref, recommendation_ref, rubric_ref),
            "envresearch.ExpertScoreSheet",
        )
        harness.service.publish_expert_score(
            "case-rct", score, expert, slot=slot,
        )
    adjudicator = harness.human(PrincipalKind.ADJUDICATOR, 1)

    with pytest.raises(ValueError, match="canonical adjudicator slot is one"):
        harness.service.publish_adjudication(
            "case-rct",
            AdjudicationVerdict(
                score_sheet_ref=harness.service.ref("case-rct", "expert_one"),
                verdict="accept",
                rationale="Attempted noncanonical adjudication.",
                adjudicator_principal=adjudicator.principal_id,
            ),
            adjudicator,
            slot=2,
        )

    assert not (tmp_path / harness.service.paths("case-rct").adjudication).exists()

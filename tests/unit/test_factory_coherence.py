"""Independent typed cross-stage coherence reconstruction."""

from __future__ import annotations

import hashlib

import pytest
from paper_draft_fixtures import evidence
from test_factory_design_contracts import _handoff
from test_paper_audit import _report

from envresearch.factory.coherence import reconstruct_binding_report
from envresearch.factory.errors import FactoryError
from envresearch.models.design import ClaimMode, EstimandSpecPayload, EstimandType
from envresearch.paper.release import PaperReleaseService

LIMITATION = "The value is conditional on the registered response model."


def _design():  # type: ignore[no-untyped-def]
    design = _handoff()
    estimand = EstimandSpecPayload(
        estimand_id="median-wtp",
        estimand_type=EstimandType.CAUSAL,
        population="survey respondent",
        unit="USD",
        exposure_or_treatment="registered hypothetical market",
        outcome="willingness to pay",
        comparison_or_counterfactual="zero willingness to pay",
        time_horizon="annual",
        target_parameter="median-wtp",
        evidence_refs=("registered-analysis",),
        assumption_refs=("registered-response-model",),
    )
    plan = design.plan.model_copy(
        update={
            "estimand": estimand,
            "primary_method_profile_ref": "contingent-valuation@0.3.1",
            "alternative_method_profile_refs": ("hedonic@0.2.0",),
            "data_boundaries": ("price-base:synthetic-2025-USD",),
            "fallback_rules": (LIMITATION,),
        }
    )
    manifest = design.manifest.model_copy(
        update={
            "method_profiles": {
                "contingent-valuation": "0.3.1",
                "hedonic": "0.2.0",
            },
            "method_profile_sha256": {
                "contingent-valuation": "1" * 64,
                "hedonic": "2" * 64,
            },
        }
    )
    return design.model_copy(
        update={
            "plan": plan,
            "manifest": manifest,
            "method_profile_sha256": manifest.method_profile_sha256,
        }
    )


def _release():  # type: ignore[no-untyped-def]
    report = _report(blocked=False)
    transition_ref = evidence()[0].transition_ref
    prior_transition = report.transition_refs[0]
    report = report.model_copy(
        update={
            "transition_refs": (transition_ref,),
            "transitive_refs": tuple(
                transition_ref if item == prior_transition else item
                for item in report.transitive_refs
            ),
        }
    )
    audit_ref = report.draft_ref.model_copy(
        update={
            "artifact_id": report.audit_id,
            "content_hash": hashlib.sha256(report.model_dump_json().encode()).hexdigest(),
        }
    )
    return PaperReleaseService._materialize(audit_ref, report)


@pytest.mark.parametrize(
    ("dimension", "expected_code"),
    [
        ("method", "FACTORY_SUPPORT_INVALID"),
        ("estimand", "FACTORY_SUPPORT_INVALID"),
        ("unit", "FACTORY_SCOPE_EXCEEDED"),
        ("population", "FACTORY_SCOPE_EXCEEDED"),
        ("time", "FACTORY_SCOPE_EXCEEDED"),
        ("price", "FACTORY_SCOPE_EXCEEDED"),
        ("strength", "FACTORY_SCOPE_EXCEEDED"),
        ("limitation", "FACTORY_SUPPORT_INVALID"),
    ],
)
def test_coherence_rejects_typed_mismatch(
    dimension: str, expected_code: str
) -> None:
    """Catch accepting a release after any typed design/evidence dimension drifts."""
    design = _design()
    ledger = evidence()[0]
    claim = ledger.claims[0]
    if dimension == "method":
        claim = claim.model_copy(
            update={
                "claim_id": "travel-cost-median-wtp",
                "method_id": "travel-cost",
            }
        )
    elif dimension == "estimand":
        claim = claim.model_copy(
            update={
                "claim_id": "contingent-valuation-mean-wtp",
                "quantity": "mean-wtp",
            }
        )
    elif dimension == "unit":
        claim = claim.model_copy(update={"unit": "EUR"})
    elif dimension == "population":
        claim = claim.model_copy(update={"population_basis": "all households"})
    elif dimension == "time":
        claim = claim.model_copy(update={"time_basis": "monthly"})
    elif dimension == "price":
        claim = claim.model_copy(update={"price_base": "synthetic-2024-USD"})
    elif dimension == "strength":
        design = design.model_copy(
            update={
                "plan": design.plan.model_copy(
                    update={"claim_mode": ClaimMode.DESCRIPTIVE}
                )
            }
        )
    else:
        claim = claim.model_copy(update={"limitations": ("Different limit.",)})
    mutated = ledger.model_copy(update={"claims": (claim,)})

    with pytest.raises(FactoryError) as caught:
        reconstruct_binding_report(design, _release(), mutated)

    assert caught.value.code == expected_code


def test_coherence_accepts_typed_narrowing_only_if_original_limitation_is_preserved() -> None:
    """Catch legal scope narrowing that silently drops the approved limitation."""
    design = _design()
    estimand = design.plan.estimand
    assert estimand is not None
    narrowed = design.model_copy(
        update={
            "plan": design.plan.model_copy(
                update={
                    "estimand": estimand.model_copy(
                        update={"population": "survey respondent:*"}
                    )
                }
            )
        }
    )

    report = reconstruct_binding_report(narrowed, _release(), evidence()[0])

    assert any(field.relation == "narrower" for field in report.fields)
    assert LIMITATION in report.limitations
    assert report.provenance_claim == "retrospective-coherence"


def test_coherence_uses_typed_ledger_not_draft_renderers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch retrospective coherence delegating to V0.4 prose acceptance."""
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("draft prose machinery must not run")

    monkeypatch.setattr("envresearch.paper.draft_validation.validate_draft", forbidden)
    monkeypatch.setattr(
        "envresearch.paper.draft_validation.render_claim_sentence", forbidden
    )

    report = reconstruct_binding_report(_design(), _release(), evidence()[0])

    assert report.verdict == "coherent"
    assert {item.claim_id for item in report.fields} == {
        "contingent-valuation-median-wtp"
    }


def test_coherence_rejects_unregistered_estimand_on_every_claim_row() -> None:
    """Catch arbitrary secondary quantities being mislabeled as typed narrowing."""
    ledger = evidence()[0]
    primary = ledger.claims[0]
    unrelated = primary.model_copy(
        update={
            "claim_id": "contingent-valuation-unregistered-target",
            "quantity": "unregistered-target",
        }
    )
    multirow = ledger.model_copy(update={"claims": (primary, unrelated)})

    with pytest.raises(FactoryError) as caught:
        reconstruct_binding_report(_design(), _release(), multirow)

    assert caught.value.code == "FACTORY_SUPPORT_INVALID"
    assert caught.value.finding_kind == "estimand-binding-invalid"


def test_coherence_requires_each_claim_row_to_preserve_design_limitation() -> None:
    """Catch one compliant row masking a second row that drops the design limit."""
    ledger = evidence()[0]
    primary = ledger.claims[0]
    unbounded = primary.model_copy(
        update={
            "claim_id": "hedonic-pricing-median-wtp",
            "method_id": "hedonic-pricing",
            "limitations": ("Different limit.",),
        }
    )
    multirow = ledger.model_copy(update={"claims": (primary, unbounded)})

    with pytest.raises(FactoryError) as caught:
        reconstruct_binding_report(_design(), _release(), multirow)

    assert caught.value.code == "FACTORY_SUPPORT_INVALID"
    assert caught.value.finding_kind == "limitation-binding-invalid"


@pytest.mark.parametrize("lineage", ("transition", "output"))
def test_coherence_rejects_exact_ledger_lineage_mutation(lineage: str) -> None:
    """Catch typed ledger lineage drifting from the exact V0.4 release closure."""
    ledger = evidence()[0]
    claim = ledger.claims[0]
    if lineage == "transition":
        transition_ref = ledger.transition_ref.model_copy(
            update={"content_hash": "0" * 64}
        )
        claim = claim.model_copy(update={"transition_ref": transition_ref})
        mutated = ledger.model_copy(
            update={"transition_ref": transition_ref, "claims": (claim,)}
        )
    else:
        output = claim.output_evidence[0].model_copy(update={"sha256": "0" * 64})
        claim = claim.model_copy(update={"output_evidence": (output,)})
        mutated = ledger.model_copy(update={"claims": (claim,)})

    with pytest.raises(FactoryError) as caught:
        reconstruct_binding_report(_design(), _release(), mutated)

    assert caught.value.code == "FACTORY_SUPPORT_INVALID"
    assert caught.value.finding_kind == f"{lineage}-binding-invalid"


@pytest.mark.parametrize(
    ("dimension", "registered", "collision"),
    (
        ("unit", "USD:*", "USDA"),
        ("population", "survey respondent:*", "survey respondent-outside"),
        ("time", "annual:*", "annually"),
        ("price", "synthetic:*", "synthetic-outside"),
    ),
)
def test_scope_narrowing_rejects_lexical_prefix_collisions(
    dimension: str, registered: str, collision: str
) -> None:
    """Catch wildcard scopes accepting values outside their typed namespace."""
    design = _design()
    estimand = design.plan.estimand
    assert estimand is not None
    claim = evidence()[0].claims[0]
    if dimension == "price":
        design = design.model_copy(
            update={
                "plan": design.plan.model_copy(
                    update={"data_boundaries": (f"price-base:{registered}",)}
                )
            }
        )
        claim = claim.model_copy(update={"price_base": collision})
    else:
        design_field = {
            "unit": "unit",
            "population": "population",
            "time": "time_horizon",
        }[dimension]
        design = design.model_copy(
            update={
                "plan": design.plan.model_copy(
                    update={
                        "estimand": estimand.model_copy(
                            update={design_field: registered}
                        )
                    }
                )
            }
        )
        release_field = {
            "unit": "unit",
            "population": "population_basis",
            "time": "time_basis",
        }[dimension]
        claim = claim.model_copy(update={release_field: collision})

    with pytest.raises(FactoryError) as caught:
        reconstruct_binding_report(
            design, _release(), evidence()[0].model_copy(update={"claims": (claim,)})
        )

    assert caught.value.code == "FACTORY_SCOPE_EXCEEDED"
    assert caught.value.finding_kind == f"{dimension}-binding-invalid"


@pytest.mark.parametrize(
    "limitations", (("A | B",), ("A", "B"))
)
def test_limitation_binding_is_unambiguous_for_delimiters_and_multiple_values(
    limitations: tuple[str, ...],
) -> None:
    """Catch one delimiter-bearing value colliding with two distinct values."""
    design = _design().model_copy(
        update={
            "plan": _design().plan.model_copy(
                update={"fallback_rules": limitations}
            )
        }
    )
    ledger = evidence()[0]
    claim = ledger.claims[0].model_copy(update={"limitations": limitations})

    report = reconstruct_binding_report(
        design, _release(), ledger.model_copy(update={"claims": (claim,)})
    )

    assert report.limitations == tuple(sorted(limitations))

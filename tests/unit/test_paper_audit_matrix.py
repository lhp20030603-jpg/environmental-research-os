"""Independent accumulating audit matrix over coherent forged drafts."""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Callable
from hashlib import sha256

import pytest
from paper_audit_fixtures import (
    AuditCase,
    base_case,
    citation_attack,
    cross_section_attack,
    disjoint_results_coverage_attack,
    numeric_attack,
    policy_attack,
    roundtrip_case,
    scope_attack,
    strength_attack,
    unbound_methods_attack,
)
from paper_audit_matrix_cases import (
    ATTACKS,
    ExpectedKinds,
    TargetKey,
    figure_attack,
    population_attack,
    price_attack,
    table_attack,
    time_attack,
    unit_attack,
)
from paper_audit_regression_cases import (
    nested_citation_overlaps_attack,
    nested_claim_overlaps_attack,
)

from envresearch.paper.audit_contracts import (
    DraftBindingTarget,
    OutputBindingTarget,
    TextSpan,
)
from envresearch.paper.auditor import reconstruct_audit_findings


def _target_key(
    target: TextSpan | OutputBindingTarget | DraftBindingTarget,
) -> TargetKey:
    if isinstance(target, TextSpan):
        return (target.target_type, target.paragraph_id, target.start, target.end)
    if isinstance(target, OutputBindingTarget):
        return (target.target_type, target.kind, target.binding_id, target.binding_id)
    return (target.target_type, target.paragraph_id, target.start, target.end)


def _audit(case: AuditCase):  # type: ignore[no-untyped-def]
    return reconstruct_audit_findings(
        draft_ref=case.draft_ref,
        draft=case.draft,
        argument_map=case.argument_map,
        ledger=case.ledger,
        citation_snapshot=case.snapshot,
    )


@pytest.mark.parametrize(
    ("attack", "expected_kind", "expected_code", "expected_targets"), ATTACKS
)
def test_independent_audit_matrix_returns_exact_typed_findings(
    attack: Callable[[AuditCase], AuditCase],
    expected_kind: ExpectedKinds,
    expected_code: str,
    expected_targets: frozenset[TargetKey],
) -> None:
    """Catch omission, misclassification, or inexact targets for every attack."""
    case = roundtrip_case(attack(base_case()))

    findings = _audit(case)

    expected_kinds = (
        frozenset({expected_kind}) if isinstance(expected_kind, str) else expected_kind
    )
    assert {item.finding_kind for item in findings} == expected_kinds
    assert {item.code for item in findings} == {expected_code}
    assert {_target_key(item.target) for item in findings} == expected_targets
    assert all(item.draft_ref in item.upstream_refs for item in findings)
    for finding in findings:
        if isinstance(finding.target, TextSpan):
            paragraph = next(
                item
                for item in case.draft.paragraphs
                if item.paragraph_id == finding.target.paragraph_id
            )
            selected = paragraph.text[finding.target.start : finding.target.end]
            assert sha256(selected.encode()).hexdigest() == finding.target.text_sha256
        elif isinstance(finding.target, OutputBindingTarget):
            assert any(
                item.binding_id == finding.target.binding_id
                for item in (*case.draft.tables, *case.draft.figures)
            )
        else:
            bindings = (
                case.draft.claim_bindings
                if finding.target.target_type == "claim-binding"
                else case.draft.citation_bindings
            )
            binding = next(
                item
                for item in bindings
                if (
                    item.paragraph_id,
                    item.start,
                    item.end,
                )
                == (
                    finding.target.paragraph_id,
                    finding.target.start,
                    finding.target.end,
                )
            )
            assert (
                finding.target.binding_sha256
                == sha256(binding.model_dump_json().encode()).hexdigest()
            )


def test_one_coherent_forgery_returns_all_findings_in_one_audit() -> None:
    """Catch validators that stop at the first independently observable failure."""
    case = cross_section_attack(base_case())
    for attack in (
        citation_attack,
        unbound_methods_attack,
        numeric_attack,
        table_attack,
        figure_attack,
        strength_attack,
        policy_attack,
        unit_attack,
        population_attack,
        time_attack,
        price_attack,
        scope_attack,
    ):
        case = attack(case)

    findings = _audit(case)

    assert {item.finding_kind for item in findings} == {
        "citation-mismatch",
        "numeric-contradiction",
        "output-evidence-mismatch",
        "claim-strength-excess",
        "policy-overclaim",
        "basis-overreach",
        "scope-inconsistency",
        "cross-section-contradiction",
    }


def test_clean_draft_has_no_independent_findings() -> None:
    """Catch an auditor that reports false positives on the deterministic slice."""
    assert _audit(base_case()) == ()


def test_finding_ids_use_full_digest_without_collision_cardinality_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch distinct findings overwriting each other on a shared digest prefix."""
    import envresearch.paper._audit_findings as finding_module

    real_sha256 = hashlib.sha256

    class SharedPrefixDigest:
        def __init__(self, data: bytes = b"") -> None:
            self.data = data

        def hexdigest(self) -> str:
            return "a" * 12 + real_sha256(self.data).hexdigest()[12:]

    monkeypatch.setattr(finding_module.hashlib, "sha256", SharedPrefixDigest)
    findings = _audit(disjoint_results_coverage_attack(base_case()))

    assert len(findings) == 2
    assert len({item.finding_id for item in findings}) == 2
    assert all(
        len(item.finding_id.removeprefix("scope-inconsistency-")) == 64
        for item in findings
    )


def test_nested_overlap_targets_every_inner_binding() -> None:
    """Catch pairwise overlap checks that lose a later span under one outer span."""
    findings = _audit(nested_claim_overlaps_attack(base_case()))

    binding_targets = {
        _target_key(item.target)
        for item in findings
        if isinstance(item.target, DraftBindingTarget)
    }
    assert binding_targets == {
        ("claim-binding", "results-primary", 1, 10),
        ("claim-binding", "results-primary", 20, 30),
    }


def test_nested_citation_overlap_targets_every_inner_binding() -> None:
    """Catch citation overlap checks that lose a later span under one outer span."""
    findings = _audit(nested_citation_overlaps_attack(base_case()))

    binding_targets = {
        _target_key(item.target)
        for item in findings
        if isinstance(item.target, DraftBindingTarget)
    }
    assert binding_targets == {
        ("citation-binding", "methods-source", 1, 10),
        ("citation-binding", "methods-source", 20, 30),
    }


def test_audit_does_not_share_task3_validation_or_rendering_oracles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch an audit whose independent verdict is inherited from Task 3."""
    import envresearch.paper._audit_reconstruction as reconstruction
    import envresearch.paper._draft_prose as task3_prose
    import envresearch.paper.draft_validation as task3

    def forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("Task 3 oracle must not run during independent audit")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(task3, "validate_draft", forbidden)
            patch.setattr(task3, "render_claim_sentence", forbidden)
            patch.setattr(task3, "render_output_caption", forbidden)
            patch.setattr(task3, "_NUMBER", object(), raising=False)
            patch.setattr(task3, "_POLICY_OVERREACH", object())
            patch.setattr(task3, "_CAUSAL_LANGUAGE", object())
            patch.setattr(task3_prose, "render_claim_sentence", forbidden)
            patch.setattr(task3_prose, "render_output_caption", forbidden)
            patch.setattr(task3_prose, "NUMBER", object())
            patch.setattr(task3_prose, "POLICY_OVERREACH", object())
            patch.setattr(task3_prose, "CAUSAL_LANGUAGE", object())
            independent = importlib.reload(reconstruction)
            clean = base_case()
            assert (
                independent.reconstruct_audit_findings(
                    draft_ref=clean.draft_ref,
                    draft=clean.draft,
                    argument_map=clean.argument_map,
                    ledger=clean.ledger,
                    citation_snapshot=clean.snapshot,
                )
                == ()
            )
            attacked = numeric_attack(clean)
            findings = independent.reconstruct_audit_findings(
                draft_ref=attacked.draft_ref,
                draft=attacked.draft,
                argument_map=attacked.argument_map,
                ledger=attacked.ledger,
                citation_snapshot=attacked.snapshot,
            )
            assert {item.finding_kind for item in findings} == {"numeric-contradiction"}
    finally:
        importlib.reload(reconstruction)

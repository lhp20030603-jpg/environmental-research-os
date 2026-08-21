"""Literal attack outcomes for the independent paper-audit matrix."""

from __future__ import annotations

from collections.abc import Callable

from paper_audit_binding_cases import (
    dangling_citation_binding_attack,
    dangling_claim_binding_attack,
    missing_results_binding_attack,
    overlapping_citation_bindings_attack,
    overlapping_claim_bindings_attack,
    oversized_claim_span_attack,
    purpose_section_attack,
    unbound_validation_scope_attack,
)
from paper_audit_fixtures import (
    AuditCase,
    basis_attack,
    citation_attack,
    citation_token_attack,
    cross_section_attack,
    disjoint_results_coverage_attack,
    no_empirical_node_attack,
    numeric_attack,
    output_attack,
    policy_attack,
    question_number_attack,
    scope_attack,
    strength_attack,
    title_number_attack,
    unbound_causal_attack,
    unbound_methods_attack,
    unregistered_validator_attack,
    validation_scope_attack,
)
from paper_audit_regression_cases import (
    design_causal_result_attack,
    duplicate_citation_sources_attack,
    methods_numeric_attack,
    methods_spacing_attack,
    policy_ought_title_attack,
    policy_universal_question_attack,
    policy_warranted_title_attack,
)

TargetKey = tuple[str, str, int | str, int | str]
ExpectedKinds = str | frozenset[str]
AttackCase = tuple[
    Callable[[AuditCase], AuditCase], ExpectedKinds, str, frozenset[TargetKey]
]


def table_attack(case: AuditCase) -> AuditCase:
    return output_attack(case, "table")


def figure_attack(case: AuditCase) -> AuditCase:
    return output_attack(case, "figure")


def unit_attack(case: AuditCase) -> AuditCase:
    return basis_attack(case, "unit", "EUR")


def population_attack(case: AuditCase) -> AuditCase:
    return basis_attack(case, "population_basis", "all households")


def time_attack(case: AuditCase) -> AuditCase:
    return basis_attack(case, "time_basis", "monthly")


def price_attack(case: AuditCase) -> AuditCase:
    return basis_attack(case, "price_base", "nominal-2030-EUR")


def causal_title_attack(case: AuditCase) -> AuditCase:
    return unbound_causal_attack(case, "title")


def causal_question_attack(case: AuditCase) -> AuditCase:
    return unbound_causal_attack(case, "research-question")


ATTACKS: tuple[AttackCase, ...] = (
    (
        citation_attack,
        "citation-mismatch",
        "PAPER_SUPPORT_INVALID",
        frozenset(
            {
                ("text-span", "methods-source", 0, 42),
                ("text-span", "methods-source", 42, 44),
            }
        ),
    ),
    (
        citation_token_attack,
        "citation-mismatch",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "methods-source", 0, 42)}),
    ),
    (
        duplicate_citation_sources_attack,
        "citation-mismatch",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "methods-source", 0, 42)}),
    ),
    (
        unbound_methods_attack,
        "citation-mismatch",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "methods-source", 43, 76)}),
    ),
    (
        methods_spacing_attack,
        "citation-mismatch",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "methods-source", 42, 44)}),
    ),
    (
        methods_numeric_attack,
        "numeric-contradiction",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "methods-source", 22, 24)}),
    ),
    (
        numeric_attack,
        "numeric-contradiction",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "results-primary", 0, 150)}),
    ),
    (
        disjoint_results_coverage_attack,
        "scope-inconsistency",
        "PAPER_SUPPORT_INVALID",
        frozenset(
            {
                ("text-span", "results-primary", 0, 7),
                ("text-span", "results-primary", 159, 166),
            }
        ),
    ),
    (
        table_attack,
        "output-evidence-mismatch",
        "PAPER_INTEGRITY_INVALID",
        frozenset({("output-binding", "table", "results-table", "results-table")}),
    ),
    (
        figure_attack,
        "output-evidence-mismatch",
        "PAPER_INTEGRITY_INVALID",
        frozenset({("output-binding", "figure", "response-curve", "response-curve")}),
    ),
    (
        strength_attack,
        "claim-strength-excess",
        "PAPER_SCOPE_EXCEEDED",
        frozenset(
            {
                ("text-span", "results-primary", 0, 150),
                ("text-span", "limitations-primary", 0, 58),
            }
        ),
    ),
    (
        policy_attack,
        "policy-overclaim",
        "PAPER_SCOPE_EXCEEDED",
        frozenset({("text-span", "paper-title", 0, 17)}),
    ),
    (
        policy_warranted_title_attack,
        "policy-overclaim",
        "PAPER_SCOPE_EXCEEDED",
        frozenset({("text-span", "paper-title", 10, 38)}),
    ),
    (
        policy_ought_title_attack,
        "policy-overclaim",
        "PAPER_SCOPE_EXCEEDED",
        frozenset({("text-span", "paper-title", 0, 54)}),
    ),
    (
        policy_universal_question_attack,
        "policy-overclaim",
        "PAPER_SCOPE_EXCEEDED",
        frozenset({("text-span", "research-question", 0, 37)}),
    ),
    (
        title_number_attack,
        "numeric-contradiction",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "paper-title", 24, 27)}),
    ),
    (
        question_number_attack,
        "numeric-contradiction",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "research-question", 13, 16)}),
    ),
    (
        causal_title_attack,
        "claim-strength-excess",
        "PAPER_SCOPE_EXCEEDED",
        frozenset({("text-span", "paper-title", 12, 18)}),
    ),
    (
        causal_question_attack,
        "claim-strength-excess",
        "PAPER_SCOPE_EXCEEDED",
        frozenset({("text-span", "research-question", 16, 21)}),
    ),
    (
        design_causal_result_attack,
        "claim-strength-excess",
        "PAPER_SCOPE_EXCEEDED",
        frozenset({("text-span", "results-primary", 15, 21)}),
    ),
    (
        unit_attack,
        "basis-overreach",
        "PAPER_SCOPE_EXCEEDED",
        frozenset(
            {
                ("text-span", "results-primary", 0, 150),
                ("text-span", "limitations-primary", 0, 58),
            }
        ),
    ),
    (
        population_attack,
        "basis-overreach",
        "PAPER_SCOPE_EXCEEDED",
        frozenset(
            {
                ("text-span", "results-primary", 0, 150),
                ("text-span", "limitations-primary", 0, 58),
            }
        ),
    ),
    (
        time_attack,
        "basis-overreach",
        "PAPER_SCOPE_EXCEEDED",
        frozenset(
            {
                ("text-span", "results-primary", 0, 150),
                ("text-span", "limitations-primary", 0, 58),
            }
        ),
    ),
    (
        price_attack,
        "basis-overreach",
        "PAPER_SCOPE_EXCEEDED",
        frozenset(
            {
                ("text-span", "results-primary", 0, 150),
                ("text-span", "limitations-primary", 0, 58),
            }
        ),
    ),
    (
        scope_attack,
        "scope-inconsistency",
        "PAPER_SUPPORT_INVALID",
        frozenset(
            {
                ("text-span", "limitations-primary", 0, 58),
                ("text-span", "limitations-primary", 58, 59),
            }
        ),
    ),
    (
        dangling_claim_binding_attack,
        frozenset({"scope-inconsistency", "numeric-contradiction"}),
        "PAPER_SUPPORT_INVALID",
        frozenset(
            {
                ("claim-binding", "missing-results", 0, 150),
                ("text-span", "results-primary", 0, 150),
                ("text-span", "results-primary", 29, 33),
                ("text-span", "results-primary", 91, 95),
                ("text-span", "results-primary", 108, 110),
                ("text-span", "results-primary", 137, 141),
                ("text-span", "results-primary", 145, 149),
            }
        ),
    ),
    (
        dangling_citation_binding_attack,
        "citation-mismatch",
        "PAPER_SUPPORT_INVALID",
        frozenset(
            {
                ("citation-binding", "missing-methods", 0, 42),
                ("text-span", "methods-source", 0, 42),
            }
        ),
    ),
    (
        oversized_claim_span_attack,
        "scope-inconsistency",
        "PAPER_SUPPORT_INVALID",
        frozenset({("claim-binding", "results-primary", 0, 151)}),
    ),
    (
        overlapping_claim_bindings_attack,
        "scope-inconsistency",
        "PAPER_SUPPORT_INVALID",
        frozenset(
            {
                ("claim-binding", "results-primary", 1, 150),
                ("text-span", "results-primary", 1, 150),
            }
        ),
    ),
    (
        overlapping_citation_bindings_attack,
        "citation-mismatch",
        "PAPER_SUPPORT_INVALID",
        frozenset(
            {
                ("citation-binding", "methods-source", 1, 42),
                ("text-span", "methods-source", 1, 42),
            }
        ),
    ),
    (
        missing_results_binding_attack,
        frozenset({"scope-inconsistency", "numeric-contradiction"}),
        "PAPER_SUPPORT_INVALID",
        frozenset(
            {
                ("text-span", "paper-title", 0, 40),
                ("text-span", "paper-title", 0, 1),
                ("text-span", "results-primary", 0, 150),
                ("text-span", "results-primary", 29, 33),
                ("text-span", "results-primary", 91, 95),
                ("text-span", "results-primary", 108, 110),
                ("text-span", "results-primary", 137, 141),
                ("text-span", "results-primary", 145, 149),
            }
        ),
    ),
    (
        purpose_section_attack,
        "scope-inconsistency",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "results-primary", 0, 150)}),
    ),
    (
        no_empirical_node_attack,
        "scope-inconsistency",
        "PAPER_SUPPORT_INVALID",
        frozenset(
            {
                ("text-span", "results-primary", 0, 150),
                ("text-span", "limitations-primary", 0, 58),
                ("output-binding", "table", "results-table", "results-table"),
                ("output-binding", "figure", "response-curve", "response-curve"),
            }
        ),
    ),
    (
        validation_scope_attack,
        "scope-inconsistency",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "validation-boundary", 0, 54)}),
    ),
    (
        unbound_validation_scope_attack,
        "scope-inconsistency",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "validation-boundary", 0, 54)}),
    ),
    (
        unregistered_validator_attack,
        "citation-mismatch",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "methods-source", 0, 42)}),
    ),
    (
        cross_section_attack,
        "cross-section-contradiction",
        "PAPER_SUPPORT_INVALID",
        frozenset({("text-span", "limitations-primary", 0, 58)}),
    ),
)

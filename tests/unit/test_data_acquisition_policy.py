"""Tests for progressive, budget-aware dataset acquisition decisions."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from envresearch.models.evidence import (
    AcquisitionBudget,
    AcquisitionPolicy,
    DatasetCandidate,
)


def dataset(
    *,
    public: bool = True,
    license_clear: bool = True,
    bytes: int = 10,
    suitable: bool = True,
) -> DatasetCandidate:
    """Build a candidate with explicit estimates for policy tests."""
    return DatasetCandidate(
        dataset_id="public-air-quality",
        source="https://example.invalid/public-air-quality",
        public_access=public,
        requires_credentials=False,
        clear_license=license_clear,
        license="CC-BY-4.0" if license_clear else "unknown",
        estimated_download_bytes=bytes,
        estimated_local_storage_bytes=bytes,
        estimated_api_calls=1,
        estimated_external_cost=Decimal(0),
        estimated_elapsed_seconds=10,
        suitable_for_design=suitable,
        suitability_reason="Includes the approved exposure and outcome variables.",
        access_reason="Published by the agency in a public catalogue.",
    )


def budget() -> AcquisitionBudget:
    """Return a small, fully explicit acquisition budget."""
    return AcquisitionBudget(
        max_download_bytes=100,
        max_local_storage_bytes=200,
        max_api_calls=10,
        max_external_cost=Decimal(0),
        max_elapsed_seconds=60,
    )


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (dataset(public=True, license_clear=True, bytes=10), "auto_acquire"),
        (dataset(public=False, license_clear=True, bytes=10), "gate_required"),
        (dataset(public=True, license_clear=False, bytes=10), "gate_required"),
        (dataset(public=True, license_clear=True, bytes=101), "gate_required"),
        (dataset(public=True, license_clear=True, bytes=10, suitable=False), "planning_only"),
    ],
)
def test_acquisition_policy_selects_progressive_action(
    candidate: DatasetCandidate, expected: str
) -> None:
    """Only public, licensed, suitable, in-budget data can be acquired automatically."""
    assert AcquisitionPolicy().evaluate(candidate, budget()).action == expected


def test_policy_requires_gate_for_credentialed_source_even_if_public() -> None:
    """A credential requirement is a human decision, never automatic acquisition."""
    candidate = dataset().model_copy(update={"requires_credentials": True})

    decision = AcquisitionPolicy().evaluate(candidate, budget())

    assert decision.action == "gate_required"
    assert "credentials" in " ".join(decision.reasons)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("estimated_download_bytes", True),
        ("estimated_download_bytes", "10"),
        ("estimated_download_bytes", -1),
        ("estimated_external_cost", Decimal("-0.01")),
        ("estimated_external_cost", Decimal("NaN")),
        ("estimated_external_cost", Decimal("Infinity")),
    ],
)
def test_candidate_rejects_ambiguous_or_invalid_measurements(
    field: str, value: object
) -> None:
    """Policy estimates must not silently coerce unsafe domain values."""
    values = dataset().model_dump()
    values[field] = value

    with pytest.raises(ValidationError):
        DatasetCandidate.model_validate(values)


def test_budget_exposes_pre_estimate_reasons() -> None:
    """Budget checks explain every limit exceeded before connector acquisition."""
    candidate = dataset(bytes=101).model_copy(
        update={"estimated_api_calls": 11, "estimated_elapsed_seconds": 61}
    )

    reasons = budget().estimate_reasons(candidate)

    assert reasons == (
        "estimated download bytes exceed budget",
        "estimated api calls exceed budget",
        "estimated elapsed seconds exceed budget",
    )

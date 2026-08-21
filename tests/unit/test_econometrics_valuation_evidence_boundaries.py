"""Strict valuation evidence and contract boundary coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from test_econometrics_valuation_contracts import hedonic, travel_cost

from envresearch.econometrics._valuation_evidence import (
    BidYesShare,
    CovarianceEvidence,
    ValuationConfiguration,
    ValuationSupport,
    bid_yes_shares_match,
)
from envresearch.econometrics.valuation_contracts import (
    HedonicColumns,
    HedonicSpec,
    TravelCostSpec,
)
from envresearch.models.artifact import ArtifactRef


def test_covariance_rejects_duplicate_dimensions_finiteness_and_diagonal() -> None:
    with pytest.raises(ValidationError, match="terms must be unique"):
        CovarianceEvidence(terms=("a", "a"), values=((1.0, 0.0), (0.0, 1.0)))
    with pytest.raises(ValidationError, match="dimensions"):
        CovarianceEvidence(terms=("a", "b"), values=((1.0,),))
    with pytest.raises(ValidationError, match="must be finite"):
        CovarianceEvidence(terms=("a",), values=((float("nan"),),))
    with pytest.raises(ValidationError, match="diagonal must be nonnegative"):
        CovarianceEvidence(terms=("a",), values=((-1.0,),))
    with pytest.raises(ValidationError, match="positive semidefinite"):
        CovarianceEvidence(terms=("a", "b"), values=((0.0, 1.0), (1.0, 1.0)))


def test_bid_yes_share_matching_allows_r_csv_rounding_only() -> None:
    reconstructed = (
        BidYesShare(
            bid=10.123456789012345,
            yes_count=1,
            observations=3,
            yes_share=1 / 3,
        ),
    )
    serialized_by_r = (
        BidYesShare(
            bid=10.1234567890123,
            yes_count=1,
            observations=3,
            yes_share=0.333333333333333,
        ),
    )
    wrong_counts = (
        BidYesShare(
            bid=10.1234567890123,
            yes_count=2,
            observations=3,
            yes_share=2 / 3,
        ),
    )

    assert bid_yes_shares_match(serialized_by_r, reconstructed)
    assert not bid_yes_shares_match(wrong_counts, reconstructed)


def test_configuration_and_support_reject_duplicate_or_impossible_authority() -> None:
    reference = ArtifactRef(
        artifact_id="package", artifact_version=1, content_hash="0" * 64
    )
    with pytest.raises(ValidationError, match="references must be unique"):
        ValuationConfiguration(
            method_id="dce-clogit",
            r_version="4.4.3",
            confidence_level=0.95,
            cluster_column="id",
            fixed_effects=(),
            functional_form=None,
            family=None,
            link=None,
            package_authorities=(reference, reference),
        )
    with pytest.raises(ValidationError, match="counts do not reconcile"):
        ValuationSupport(observations=1, primary_units=2)


def test_valuation_columns_and_paths_are_unambiguous(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="columns must be unique"):
        HedonicColumns(
            transaction="id",
            price="price",
            environmental_attribute="air",
            controls=("rooms", "rooms"),
        )
    payload = hedonic(tmp_path / "data.csv").model_dump()
    with pytest.raises(ValidationError, match="data path must be canonical"):
        HedonicSpec.model_validate({**payload, "data_path": " bad.csv "})
    with pytest.raises(ValidationError, match="less than 1"):
        HedonicSpec.model_validate({**payload, "confidence_level": float("nan")})
    with pytest.raises(ValidationError, match="thresholds must be finite"):
        HedonicSpec.model_validate({**payload, "max_sensitivity_change": float("inf")})


def test_valuation_specs_reject_undeclared_clusters(tmp_path: Path) -> None:
    hedonic_payload = hedonic(tmp_path / "hedonic.csv").model_dump()
    with pytest.raises(ValidationError, match="declared source role"):
        HedonicSpec.model_validate({**hedonic_payload, "cluster_column": "unknown"})

    travel_payload = travel_cost(tmp_path / "travel.csv").model_dump()
    with pytest.raises(ValidationError, match="declared source role"):
        TravelCostSpec.model_validate({**travel_payload, "cluster_column": "unknown"})

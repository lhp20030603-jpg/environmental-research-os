"""Independent welfare and sensitivity fail-closed branch coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from econometrics_valuation_verifier_fixtures import (
    ValuationVerifierBackend,
    spec_for,
)

from envresearch.econometrics._valuation_welfare import (
    _sensitivity_matches,
    ratio_standard_error,
    require_welfare_uncertainty,
    valuation_evidence_matches,
)
from envresearch.econometrics.service import LocalAnalysisService
from envresearch.storage.research_artifacts import ResearchArtifactStore


def _result(method_id: str, tmp_path: Path):
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / method_id), ValuationVerifierBackend(method_id)
    )
    result = service.status(service.run(spec_for(method_id))).result
    assert result is not None
    return spec_for(method_id), result


def test_welfare_match_and_uncertainty_fail_closed(tmp_path: Path) -> None:
    spec, result = _result("contingent-valuation", tmp_path)
    assert not valuation_evidence_matches(spec, object())
    with pytest.raises(ValueError, match="uncertainty is inconsistent"):
        require_welfare_uncertainty(result.welfare[0], 999.0, spec.confidence_level)

    covariance = result.covariance.model_copy(
        update={"values": ((-1.0, 0.0, 0.0), (0.0, 0.0001, 0.0), (0.0, 0.0, 4e-6))}
    )
    with pytest.raises(ValueError, match="variance is invalid"):
        ratio_standard_error(covariance, "(Intercept)", "bid", 2.0, -0.1)


def test_registered_sensitivity_forms_cannot_drift(tmp_path: Path) -> None:
    for method_id, change in (
        ("hedonic-pricing", {"sensitivity_form": "log-level"}),
        ("travel-cost", {"sensitivity_cost_coefficient": 1.0}),
        ("contingent-valuation", {"sensitivity_bid_coefficient": 1.0}),
        ("dce-clogit", {"sensitivity_cost_coefficient": 0.0}),
    ):
        spec, result = _result(method_id, tmp_path)
        forged = result.model_copy(update=change)
        assert not _sensitivity_matches(spec, forged, result.welfare)


def test_sensitivity_requires_exactly_one_row(tmp_path: Path) -> None:
    spec, result = _result("contingent-valuation", tmp_path)
    forged = result.model_copy(update={"sensitivities": ()})
    assert not _sensitivity_matches(spec, forged, result.welfare)

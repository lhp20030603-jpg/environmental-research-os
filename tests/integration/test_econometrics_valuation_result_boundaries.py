"""Scientific invariant coverage for authenticated valuation results."""

from __future__ import annotations

from pathlib import Path

import pytest
from econometrics_valuation_verifier_fixtures import (
    ValuationVerifierBackend,
    spec_for,
)
from pydantic import ValidationError

from envresearch.econometrics.service import LocalAnalysisService
from envresearch.storage.research_artifacts import ResearchArtifactStore


def _result(method_id: str, tmp_path: Path):
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / method_id), ValuationVerifierBackend(method_id)
    )
    result = service.status(service.run(spec_for(method_id))).result
    assert result is not None
    return result


def _reject(result, payload: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        type(result).model_validate(payload)


def test_shared_result_rejects_digest_covariance_and_sensitivity_forgery(
    tmp_path: Path,
) -> None:
    result = _result("hedonic-pricing", tmp_path)
    payload = result.model_dump()
    _reject(result, {**payload, "figure_sha256": "BAD"}, "digest")

    bad_covariance = result.covariance.model_copy(update={"terms": ("unrelated",)})
    _reject(
        result,
        {**payload, "covariance": bad_covariance},
        "covariance does not match",
    )

    sensitivity = result.sensitivities[0]
    _reject(
        result,
        {**payload, "sensitivities": (sensitivity, sensitivity)},
        "labels must be unique",
    )
    _reject(
        result,
        {**payload, "max_sensitivity_change": 0.01},
        "exceeds its threshold",
    )


def test_hedonic_rejects_wrong_configuration_and_sensitivity_transform(
    tmp_path: Path,
) -> None:
    result = _result("hedonic-pricing", tmp_path)
    payload = result.model_dump()
    wrong_method = result.configuration.model_copy(update={"method_id": "travel-cost"})
    forged = result.model_copy(update={"configuration": wrong_method})
    with pytest.raises(ValueError, match="another method"):
        forged.coherent()

    wrong_label = result.sensitivities[0].model_copy(update={"label": "unregistered"})
    _reject(
        result,
        {**payload, "sensitivities": (wrong_label,)},
        "not registered",
    )

    missing_form = result.configuration.model_copy(update={"functional_form": None})
    forged = result.model_copy(update={"configuration": missing_form})
    with pytest.raises(ValueError, match="functional form is missing"):
        forged.coherent()

    _reject(
        result,
        {**payload, "sensitivity_coefficient": -1.0},
        "sensitivity transformation",
    )


def test_travel_cost_rejects_family_and_sensitivity_inconsistency(
    tmp_path: Path,
) -> None:
    result = _result("travel-cost", tmp_path)
    payload = result.model_dump()
    wrong_method = result.configuration.model_copy(
        update={"method_id": "hedonic-pricing"}
    )
    forged = result.model_copy(update={"configuration": wrong_method})
    with pytest.raises(ValueError, match="another method"):
        forged.coherent()
    _reject(result, {**payload, "theta": None}, "theta does not match")

    wrong_label = result.sensitivities[0].model_copy(update={"label": "unregistered"})
    _reject(
        result,
        {**payload, "sensitivities": (wrong_label,)},
        "not registered",
    )
    _reject(
        result,
        {**payload, "sensitivity_family": "poisson"},
        "sensitivity transformation",
    )


def test_cv_rejects_configuration_probability_and_intercept_forgery(
    tmp_path: Path,
) -> None:
    result = _result("contingent-valuation", tmp_path)
    payload = result.model_dump()
    wrong_method = result.configuration.model_copy(update={"method_id": "dce-clogit"})
    forged = result.model_copy(update={"configuration": wrong_method})
    with pytest.raises(ValueError, match="another method"):
        forged.coherent()
    _reject(
        result,
        {**payload, "probability_min": 0.95},
        "probability range",
    )
    _reject(
        result,
        {**payload, "extreme_probability_share": 0.3},
        "extreme probability share",
    )

    coefficients = tuple(
        item for item in result.coefficients if item.term != result.intercept_term
    )
    covariance = result.covariance.model_copy(
        update={
            "terms": tuple(item.term for item in coefficients),
            "values": ((0.0001, 0.0), (0.0, 4e-6)),
        }
    )
    _reject(
        result,
        {**payload, "coefficients": coefficients, "covariance": covariance},
        "intercept coefficient is missing",
    )

    wrong_label = result.sensitivities[0].model_copy(update={"label": "unregistered"})
    _reject(
        result,
        {**payload, "sensitivities": (wrong_label,)},
        "not registered",
    )


def test_dce_rejects_configuration_attribute_and_welfare_ambiguity(
    tmp_path: Path,
) -> None:
    result = _result("dce-clogit", tmp_path)
    payload = result.model_dump()
    wrong_method = result.configuration.model_copy(
        update={"method_id": "contingent-valuation"}
    )
    forged = result.model_copy(update={"configuration": wrong_method})
    with pytest.raises(ValueError, match="another method"):
        forged.coherent()

    wrong_label = result.sensitivities[0].model_copy(update={"label": "unregistered"})
    _reject(
        result,
        {**payload, "sensitivities": (wrong_label,)},
        "not registered",
    )
    _reject(
        result,
        {**payload, "attribute_terms": ("air_quality", "air_quality")},
        "terms must be unique",
    )
    _reject(
        result,
        {**payload, "attribute_terms": (*result.attribute_terms, "missing")},
        "coefficient is missing",
    )

    swapped = tuple(reversed(result.welfare))
    _reject(
        result,
        {**payload, "attribute_terms": ("air_quality",), "welfare": (swapped[0],)},
        "does not cover each attribute",
    )

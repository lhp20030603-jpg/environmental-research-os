"""Regression coverage for independent CV probability verification."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from econometrics_valuation_verifier_fixtures import (
    ValuationVerifierBackend,
    spec_for,
)

from envresearch.econometrics._valuation_verification import (
    valuation_configuration_matches,
)
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.report import LocalAnalysisReport
from envresearch.econometrics.service import EvidenceTampered, LocalAnalysisService
from envresearch.storage.research_artifacts import ResearchArtifactStore


def test_status_rejects_resealed_cv_probability_summary_forgery(
    tmp_path: Path,
) -> None:
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "store"),
        ValuationVerifierBackend("contingent-valuation"),
    )
    report = service.status(service.run(spec_for("contingent-valuation")))
    forged = _reseal_probability_summary(service, report)

    with pytest.raises(EvidenceTampered, match="CONFIGURATION_MISMATCH"):
        service.status(service.publisher.publish(forged))


def test_cv_report_carries_typed_bid_yes_shares(tmp_path: Path) -> None:
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "store"),
        ValuationVerifierBackend("contingent-valuation"),
    )

    report = service.status(service.run(spec_for("contingent-valuation")))

    assert report.result is not None
    assert tuple(item.model_dump() for item in report.result.bid_yes_shares) == (
        {"bid": 10.0, "yes_count": 7, "observations": 10, "yes_share": 0.7},
        {"bid": 20.0, "yes_count": 6, "observations": 10, "yes_share": 0.6},
        {"bid": 30.0, "yes_count": 4, "observations": 10, "yes_share": 0.4},
        {"bid": 40.0, "yes_count": 3, "observations": 10, "yes_share": 0.3},
    )
    assert "bid_yes_shares.csv" in {item.name for item in report.outputs}


def test_status_rejects_resealed_cv_bid_yes_share_forgery(tmp_path: Path) -> None:
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "store"),
        ValuationVerifierBackend("contingent-valuation"),
    )
    report = service.status(service.run(spec_for("contingent-valuation")))
    data = (
        b"bid,yes_count,observations,yes_share\n"
        b"10,8,10,0.8\n20,6,10,0.6\n30,4,10,0.4\n40,2,10,0.2\n"
    )
    forged = _reseal_output(service, report, "bid_yes_shares.csv", data)

    with pytest.raises(EvidenceTampered, match="CONFIGURATION_MISMATCH"):
        service.status(service.publisher.publish(forged))


@pytest.mark.parametrize(
    ("link", "minimum", "maximum"),
    (
        ("logit", 0.10475248044630801, 0.7585048861053055),
        ("probit", 0.01595644571147925, 0.8737918310544732),
    ),
)
def test_cv_reconstruction_uses_registered_binary_link(
    link: str, minimum: float, maximum: float, tmp_path: Path
) -> None:
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "store"),
        ValuationVerifierBackend("contingent-valuation"),
    )
    report = service.status(service.run(spec_for("contingent-valuation")))
    assert report.snapshot is not None
    assert report.execution is not None
    assert report.result is not None
    spec = report.spec.model_copy(update={"link": link})
    configuration = report.result.configuration.model_copy(update={"link": link})
    result = report.result.model_copy(
        update={
            "configuration": configuration,
            "sensitivity_link": link,
            "probability_min": minimum,
            "probability_max": maximum,
            "extreme_probability_share": 0.0,
        }
    )
    data = service.files.read(report.snapshot.relative_path)

    assert valuation_configuration_matches(
        data, spec, result, report.execution.runtime.version
    )
    forged = result.model_copy(update={"probability_min": 0.2})
    assert not valuation_configuration_matches(
        data, spec, forged, report.execution.runtime.version
    )


def _reseal_probability_summary(
    service: LocalAnalysisService, report: LocalAnalysisReport
) -> LocalAnalysisReport:
    data = b"minimum,maximum,extreme_share,max_extreme_share\n0.2,0.8,0.1,0.2\n"
    return _reseal_output(service, report, "probabilities.csv", data)


def _reseal_output(
    service: LocalAnalysisService,
    report: LocalAnalysisReport,
    name: str,
    data: bytes,
) -> LocalAnalysisReport:
    output = next(item for item in report.outputs if item.name == name)
    target = service.store.root / output.relative_path
    target.chmod(0o600)
    target.write_bytes(data)
    resealed = output.model_copy(
        update={"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
    )
    outputs = tuple(
        resealed if item.name == resealed.name else item for item in report.outputs
    )
    assert report.execution is not None
    assert report.output_root is not None
    result = recipe_for(
        report.spec.method_id, workspace=service.store.root / "reparse"
    ).parse(service.store.root / report.output_root)
    return report.model_copy(update={"outputs": outputs, "result": result})

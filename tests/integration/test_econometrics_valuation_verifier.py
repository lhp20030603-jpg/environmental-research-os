"""Adversarial independent verification for all valuation recipes."""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

import pytest
from econometrics_valuation_verifier_fixtures import (
    ValuationVerifierBackend,
    package_authority,
    spec_for,
)

from envresearch.econometrics.cli import _service_for
from envresearch.econometrics.frozen_r_library import FrozenRLibrary
from envresearch.econometrics.installed_package_authority import (
    InstalledPackageAuthority,
)
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.report import LocalAnalysisReport
from envresearch.econometrics.service import EvidenceTampered, LocalAnalysisService
from envresearch.storage.research_artifacts import ResearchArtifactStore

VALUATION_METHODS = (
    "hedonic-pricing",
    "travel-cost",
    "contingent-valuation",
    "dce-clogit",
)
SUPPORT_OUTPUTS = {
    "hedonic-pricing": "support.csv",
    "travel-cost": "support.csv",
    "contingent-valuation": "bid_support.csv",
    "dce-clogit": "choice_support.csv",
}


class _StatusOnlyBackend:
    def execute(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("read-only status must not execute")


@pytest.mark.parametrize("method_id", VALUATION_METHODS)
def test_status_rejects_resealed_support_forgery(
    method_id: str, tmp_path: Path
) -> None:
    service, report = _green_report(method_id, tmp_path)
    name = SUPPORT_OUTPUTS[method_id]
    target = _output(service, report, name)
    lines = target.read_text(encoding="utf-8").splitlines()
    fields = lines[1].split(",")
    fields[0] = str(int(fields[0]) + 1)
    forged_bytes = ("\n".join((lines[0], ",".join(fields))) + "\n").encode()

    forged = _reseal_output(service, report, name, forged_bytes)

    with pytest.raises(EvidenceTampered, match="CONFIGURATION_MISMATCH"):
        service.status(service.publisher.publish(forged))


@pytest.mark.parametrize("method_id", VALUATION_METHODS)
def test_status_rejects_resealed_coefficient_interval_forgery(
    method_id: str, tmp_path: Path
) -> None:
    service, report = _green_report(method_id, tmp_path)
    target = _output(service, report, "coefficients.csv")
    lines = target.read_text(encoding="utf-8").splitlines()
    fields = lines[1].split(",")
    fields[-2:] = ("-999999", "999999")
    forged_bytes = ("\n".join((lines[0], ",".join(fields), *lines[2:])) + "\n").encode()

    forged = _reseal_output(service, report, "coefficients.csv", forged_bytes)

    with pytest.raises(EvidenceTampered, match="CONFIGURATION_MISMATCH"):
        service.status(service.publisher.publish(forged))


@pytest.mark.parametrize("method_id", VALUATION_METHODS)
def test_status_rejects_resealed_wrong_package_authority(
    method_id: str, tmp_path: Path
) -> None:
    service, report = _green_report(method_id, tmp_path)
    assert report.execution is not None
    assert report.output_root is not None
    forged_execution = report.execution.model_copy(
        update={"package_authorities": (package_authority("unrelated"),)}
    )
    refs = tuple(item.ref() for item in forged_execution.package_authorities)
    forged_result = recipe_for(method_id, workspace=tmp_path / "reparse").parse(
        service.store.root / report.output_root, refs
    )
    forged = report.model_copy(
        update={"execution": forged_execution, "result": forged_result}
    )

    with pytest.raises(EvidenceTampered, match="CONFIGURATION_MISMATCH"):
        service.status(service.publisher.publish(forged))


@pytest.mark.parametrize("method_id", ("hedonic-pricing", "travel-cost", "dce-clogit"))
def test_status_rejects_same_package_with_forged_tree_authority(
    method_id: str, tmp_path: Path
) -> None:
    service, report = _green_report(method_id, tmp_path)
    assert report.execution is not None
    assert report.output_root is not None
    original = report.execution.package_authorities[0]
    changed = original.model_copy(update={"installed_tree_sha256": "f" * 64})
    forged_execution = report.execution.model_copy(
        update={"package_authorities": (changed,)}
    )
    refs = tuple(item.ref() for item in forged_execution.package_authorities)
    forged_result = recipe_for(method_id, workspace=tmp_path / "reparse-tree").parse(
        service.store.root / report.output_root, refs
    )
    forged = report.model_copy(
        update={"execution": forged_execution, "result": forged_result}
    )

    with pytest.raises(EvidenceTampered, match="CONFIGURATION_MISMATCH"):
        service.status(service.publisher.publish(forged))


def test_fresh_status_reauthenticates_exact_frozen_package_tree(
    tmp_path: Path,
) -> None:
    frozen, authorities = _frozen_package(tmp_path, "fixest")
    store = ResearchArtifactStore(tmp_path / "store")
    service = LocalAnalysisService(
        store, ValuationVerifierBackend("hedonic-pricing", authorities)
    )
    report = service.status(service.run(spec_for("hedonic-pricing")))
    assert report.execution is not None
    original = report.execution.package_authorities[0]
    changed = original.model_copy(update={"installed_tree_sha256": "f" * 64})
    forged_execution = report.execution.model_copy(
        update={"package_authorities": (changed,)}
    )
    assert report.output_root is not None
    forged_result = recipe_for(
        report.spec.method_id, workspace=tmp_path / "restart-reparse"
    ).parse(store.root / report.output_root, (changed.ref(),))
    forged = report.model_copy(
        update={"execution": forged_execution, "result": forged_result}
    )
    forged_ref = service.publisher.publish(forged)

    restarted = _service_for(
        store.root,
        frozen_pack_root=frozen.store_root,
        frozen_pack_hash=authorities[0].pack_hash,
    )

    with pytest.raises(EvidenceTampered, match="CONFIGURATION_MISMATCH"):
        restarted.status(forged_ref)


def test_fresh_status_requires_external_authority_for_package_method(
    tmp_path: Path,
) -> None:
    frozen, authorities = _frozen_package(tmp_path, "fixest")
    del frozen
    store = ResearchArtifactStore(tmp_path / "store")
    service = LocalAnalysisService(
        store, ValuationVerifierBackend("hedonic-pricing", authorities)
    )
    reference = service.run(spec_for("hedonic-pricing"))

    with pytest.raises(EvidenceTampered, match="CONFIGURATION_MISMATCH"):
        _service_for(store.root).status(reference)


def test_fresh_status_rejects_injected_authority_for_base_only_method(
    tmp_path: Path,
) -> None:
    store = ResearchArtifactStore(tmp_path / "store")
    service = LocalAnalysisService(
        store, ValuationVerifierBackend("contingent-valuation")
    )
    report = service.status(service.run(spec_for("contingent-valuation")))
    assert report.execution is not None
    assert report.output_root is not None
    injected = package_authority("unrelated")
    execution = report.execution.model_copy(update={"package_authorities": (injected,)})
    result = recipe_for(
        report.spec.method_id, workspace=tmp_path / "cv-authority-reparse"
    ).parse(store.root / report.output_root, (injected.ref(),))
    reference = service.publisher.publish(
        report.model_copy(update={"execution": execution, "result": result})
    )
    restarted = LocalAnalysisService(store, _StatusOnlyBackend())  # type: ignore[arg-type]

    with pytest.raises(EvidenceTampered, match="CONFIGURATION_MISMATCH"):
        restarted.status(reference)


@pytest.mark.parametrize(
    ("name", "selector", "column", "value"),
    (
        ("coefficients.csv", ("term", "income"), "estimate", "0.011"),
        ("wtp.csv", ("name", "median-wtp"), "currency", "EUR"),
        ("wtp.csv", ("name", "median-wtp"), "price_base", "2030"),
    ),
)
def test_status_rejects_parse_valid_resealed_evidence_via_reconstruction(
    name: str,
    selector: tuple[str, ...],
    column: str,
    value: str,
    tmp_path: Path,
) -> None:
    service, report = _green_report("contingent-valuation", tmp_path)
    data = _mutate_csv(
        _output(service, report, name).read_bytes(), selector, column, value
    )
    forged = _reseal_output(service, report, name, data)

    with pytest.raises(EvidenceTampered, match="CONFIGURATION_MISMATCH"):
        service.status(service.publisher.publish(forged))


@pytest.mark.parametrize(
    ("name", "selector", "column", "value"),
    (
        (
            "covariance.csv",
            ("row_term", "income", "column_term", "income"),
            "value",
            "0.000009",
        ),
        (
            "wtp.csv",
            ("name", "median-wtp"),
            "transformation",
            "negative-inverse-cost",
        ),
        ("wtp.csv", ("name", "median-wtp"), "estimate", "21"),
        ("sensitivity.csv", ("label", "exclude-covariates"), "estimate", "20.2"),
        (
            "package_configuration.csv",
            ("method_id", "contingent-valuation"),
            "method_id",
            "dce-clogit",
        ),
    ),
)
def test_status_rejects_parse_invalid_resealed_evidence(
    name: str,
    selector: tuple[str, ...],
    column: str,
    value: str,
    tmp_path: Path,
) -> None:
    service, report = _green_report("contingent-valuation", tmp_path)
    data = _mutate_csv(
        _output(service, report, name).read_bytes(), selector, column, value
    )
    forged = _reseal_bytes_without_result(service, report, name, data)

    with pytest.raises(EvidenceTampered, match="OUTPUT_INVALID"):
        service.status(service.publisher.publish(forged))


def test_status_rejects_resealed_unregistered_script_bytes(tmp_path: Path) -> None:
    service, report = _green_report("contingent-valuation", tmp_path)
    assert report.script_path is not None
    assert report.execution is not None
    data = service.files.read(report.script_path) + b"\n# forged\n"
    service.files.write(report.script_path, data)
    digest = hashlib.sha256(data).hexdigest()
    script = report.execution.script.model_copy(update={"sha256": digest})
    execution = report.execution.model_copy(update={"script": script})
    forged = report.model_copy(update={"script_sha256": digest, "execution": execution})

    with pytest.raises(EvidenceTampered, match="SCRIPT_NOT_REGISTERED"):
        service.status(service.publisher.publish(forged))


def _green_report(
    method_id: str, tmp_path: Path
) -> tuple[LocalAnalysisService, LocalAnalysisReport]:
    service = LocalAnalysisService(
        ResearchArtifactStore(tmp_path / "store"), ValuationVerifierBackend(method_id)
    )
    report = service.status(service.run(spec_for(method_id)))
    assert report.status == "passed"
    return service, report


def _frozen_package(
    tmp_path: Path, package: str
) -> tuple[FrozenRLibrary, tuple[InstalledPackageAuthority, ...]]:
    source = tmp_path / "source" / package
    source.mkdir(parents=True)
    source.joinpath("DESCRIPTION").write_text(
        f"Package: {package}\nVersion: 1.0.0\nLicense: GPL-3\n",
        encoding="utf-8",
    )
    source.joinpath("R").mkdir()
    source.joinpath("R", package).write_text("fixture <- TRUE\n", encoding="utf-8")
    frozen = FrozenRLibrary((tmp_path / "frozen-pack").resolve())
    authorities = frozen.freeze(
        ((tmp_path / "source").resolve(),),
        required_packages=(package,),
        r_version="4.4.3",
    )
    return frozen, authorities


def _output(
    service: LocalAnalysisService, report: LocalAnalysisReport, name: str
) -> Path:
    item = next(value for value in report.outputs if value.name == name)
    return service.store.root / item.relative_path


def _reseal_output(
    service: LocalAnalysisService,
    report: LocalAnalysisReport,
    name: str,
    data: bytes,
) -> LocalAnalysisReport:
    target = _output(service, report, name)
    target.chmod(0o600)
    target.write_bytes(data)
    outputs = tuple(
        item.model_copy(
            update={"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
        )
        if item.name == name
        else item
        for item in report.outputs
    )
    assert report.execution is not None
    assert report.output_root is not None
    refs = tuple(item.ref() for item in report.execution.package_authorities)
    result = recipe_for(
        report.spec.method_id, workspace=service.store.root / "reparse"
    ).parse(service.store.root / report.output_root, refs)
    return report.model_copy(update={"outputs": outputs, "result": result})


def _reseal_bytes_without_result(
    service: LocalAnalysisService,
    report: LocalAnalysisReport,
    name: str,
    data: bytes,
) -> LocalAnalysisReport:
    target = _output(service, report, name)
    target.chmod(0o600)
    target.write_bytes(data)
    outputs = tuple(
        item.model_copy(
            update={"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
        )
        if item.name == name
        else item
        for item in report.outputs
    )
    return report.model_copy(update={"outputs": outputs})


def _mutate_csv(
    data: bytes, selector: tuple[str, ...], column: str, value: str
) -> bytes:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))
    rows = list(reader)
    if reader.fieldnames is None or len(selector) % 2:
        raise AssertionError("fixture selector is invalid")
    selected = [
        row
        for row in rows
        if all(
            row[selector[index]] == selector[index + 1]
            for index in range(0, len(selector), 2)
        )
    ]
    assert len(selected) == 1
    selected[0][column] = value
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=reader.fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")

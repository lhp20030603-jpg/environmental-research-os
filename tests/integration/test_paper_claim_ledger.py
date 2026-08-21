"""Exact-evidence ingestion for the first useful V0.4 paper artifact."""

from __future__ import annotations

import hashlib
import os
from multiprocessing import get_context
from pathlib import Path

import pytest
from paper_claim_fixtures import build_worker, cv_resolver, transition

from envresearch.econometrics.valuation_transition import V031ExitHarness
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.contracts import (
    ClaimEvidenceLedger,
    DescriptiveRangeValue,
    DescriptiveSeriesValue,
    EstimatedClaimValue,
)
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperIntegrityInvalid,
    PaperSupportInvalid,
)
from envresearch.paper.ledger import CLAIM_LEDGER_SUBJECT, ClaimLedgerService


def test_builds_queryable_cv_claims_from_genuine_reconstructed_report(
    tmp_path: Path,
) -> None:
    resolver = cv_resolver(tmp_path)
    service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )

    reference = service.build(resolver.transition_ref)
    ledger = service.status(reference, resolver.transition_ref)
    repeated = service.build(resolver.transition_ref)

    assert repeated == reference
    assert tuple(row.claim_id for row in ledger.claims) == (
        "contingent-valuation-median-wtp",
        "contingent-valuation-probability-range",
        "contingent-valuation-bid-yes-shares",
    )
    welfare, probability, shares = ledger.claims
    assert isinstance(welfare.value, EstimatedClaimValue)
    assert welfare.value.estimate == 20.0
    assert isinstance(probability.value, DescriptiveRangeValue)
    assert probability.value.minimum == pytest.approx(0.10475248044630801)
    assert probability.value.maximum == pytest.approx(0.7585048861053055)
    assert probability.output_evidence[0].result_pointers == (
        "/probability_min",
        "/probability_max",
    )
    assert isinstance(shares.value, DescriptiveSeriesValue)
    assert tuple(point.numerator for point in shares.value.points) == (7, 6, 4, 3)
    assert {item.name for item in welfare.output_evidence} >= {"wtp.csv", "cv_plot.svg"}
    assert all(
        output.analysis_ref == welfare.analysis_ref
        for output in welfare.output_evidence
    )


def test_rejects_forged_or_stale_upstream_without_publishing_current(
    tmp_path: Path,
) -> None:
    resolver = cv_resolver(tmp_path)
    service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )

    with pytest.raises(PaperAuthorityInvalid):
        service.build(transition("b"))
    assert service.registry.current(CLAIM_LEDGER_SUBJECT) is None

    reference = service.build(resolver.transition_ref)
    resolver.current = False
    with pytest.raises(PaperAuthorityInvalid):
        service.status(reference, resolver.transition_ref)


def test_build_rejects_conflicting_existing_current_ledger(tmp_path: Path) -> None:
    resolver = cv_resolver(tmp_path)
    service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )
    reference = service.build(resolver.transition_ref)
    ledger = service.registry.load(reference, ClaimEvidenceLedger)
    other = ClaimEvidenceLedger(
        schema_version=ledger.schema_version,
        ledger_id="valuation-core-bbbbbbbbbbbb",
        producer=ledger.producer,
        transition_ref=ledger.transition_ref,
        claims=tuple(
            item.model_copy(update={"limitations": (*item.limitations, "Conflict.")})
            for item in ledger.claims
        ),
    )
    forged = service.registry.publish(other.ledger_id, other)
    service.registry.set_current(CLAIM_LEDGER_SUBJECT, forged)

    with pytest.raises(PaperAuthorityInvalid, match="different claim ledger"):
        service.build(resolver.transition_ref)
    assert service.registry.current(CLAIM_LEDGER_SUBJECT) == forged


def test_status_rejects_current_payload_published_under_forged_identity(
    tmp_path: Path,
) -> None:
    resolver = cv_resolver(tmp_path)
    service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )
    reference = service.build(resolver.transition_ref)
    ledger = service.registry.load(reference, ClaimEvidenceLedger)
    forged = service.registry.publish("attacker-ledger", ledger)
    service.registry.set_current(CLAIM_LEDGER_SUBJECT, forged)

    with pytest.raises(PaperIntegrityInvalid, match="identity"):
        service.status(forged, resolver.transition_ref)


def test_status_rejects_noncanonical_nested_exact_reference_bytes(
    tmp_path: Path,
) -> None:
    resolver = cv_resolver(tmp_path)
    service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )
    reference = service.build(resolver.transition_ref)
    ledger = service.registry.load(reference, ClaimEvidenceLedger)
    raw = (
        ledger.model_dump_json()
        .replace('"artifact_version":1', '"artifact_version":"1"')
        .encode()
    )
    digest = hashlib.sha256(raw).hexdigest()
    forged = ArtifactRef(
        artifact_id=ledger.ledger_id, artifact_version=1, content_hash=digest
    )
    service.registry.files.persist_exact(
        Path("exit/objects") / ledger.ledger_id / f"v1-{digest}.json", raw
    )
    service.registry.set_current(CLAIM_LEDGER_SUBJECT, forged)

    with pytest.raises(PaperIntegrityInvalid, match="canonical"):
        service.status(forged, resolver.transition_ref)


def test_ledger_load_reads_and_authenticates_object_bytes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = cv_resolver(tmp_path)
    service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )
    reference = service.build(resolver.transition_ref)
    original = service.registry.files.read
    object_reads = 0

    def count_object_reads(relative: Path) -> bytes:
        nonlocal object_reads
        if str(relative).endswith(f"{reference.content_hash}.json"):
            object_reads += 1
        return original(relative)

    monkeypatch.setattr(service.registry.files, "read", count_object_reads)
    assert service._load(reference).claims
    assert object_reads == 1


def test_final_authority_failure_rolls_back_new_current_pointer(tmp_path: Path) -> None:
    resolver = cv_resolver(tmp_path)
    resolver.fail_current_check = 4
    service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )

    with pytest.raises(PaperAuthorityInvalid):
        service.build(resolver.transition_ref)
    assert service.registry.current(CLAIM_LEDGER_SUBJECT) is None


def test_output_change_in_final_window_rolls_back_new_current_pointer(
    tmp_path: Path,
) -> None:
    resolver = cv_resolver(tmp_path)
    analysis_ref, report = resolver.reports[0]
    changed = report.outputs[0].model_copy(update={"sha256": "f" * 64})
    resolver.mutate_reports_on_current_check = 3
    resolver.replacement_reports = (
        (
            analysis_ref,
            report.model_copy(update={"outputs": (changed, *report.outputs[1:])}),
        ),
    )
    service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )

    with pytest.raises(PaperIntegrityInvalid, match="changed during publication"):
        service.build(resolver.transition_ref)
    assert service.registry.current(CLAIM_LEDGER_SUBJECT) is None


def test_current_publication_failure_is_typed_and_retry_recovers_exact_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = cv_resolver(tmp_path)
    service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )
    original = service.registry.set_current

    def fail_once(_subject: str, _reference: ArtifactRef) -> None:
        raise OSError("injected current publication failure")

    monkeypatch.setattr(service.registry, "set_current", fail_once)
    with pytest.raises(PaperIntegrityInvalid, match="publication"):
        service.build(resolver.transition_ref)
    assert service.registry.current(CLAIM_LEDGER_SUBJECT) is None

    monkeypatch.setattr(service.registry, "set_current", original)
    recovered = service.build(resolver.transition_ref)
    assert service.status(recovered, resolver.transition_ref).claims


def test_two_processes_publish_one_identical_current_ledger(tmp_path: Path) -> None:
    resolver = cv_resolver(tmp_path)
    analysis_ref, report = resolver.reports[0]
    context = get_context("spawn")
    start = context.Event()
    results = context.Queue()
    arguments = (
        str(tmp_path / "paper"),
        resolver.transition_ref.model_dump_json(),
        analysis_ref.model_dump_json(),
        report.model_dump_json(),
        start,
        results,
    )
    processes = tuple(
        context.Process(target=build_worker, args=arguments) for _ in range(2)
    )
    for process in processes:
        process.start()
    start.set()
    received = tuple(results.get(timeout=30) for _ in processes)
    for process in processes:
        process.join(timeout=30)

    assert all(process.exitcode == 0 for process in processes)
    assert all(status == "ok" for status, _ in received), received
    references = tuple(ArtifactRef.model_validate_json(item) for _, item in received)
    assert references[0] == references[1]
    reopened = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )
    assert reopened.registry.current(CLAIM_LEDGER_SUBJECT) == references[0]


@pytest.mark.parametrize("relation", ("same", "ancestor", "descendant", "alias"))
def test_v031_composition_rejects_overlapping_authority_roots(
    tmp_path: Path, relation: str
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    if relation == "same":
        paper_root = run_root
    elif relation == "ancestor":
        paper_root = tmp_path
    elif relation == "descendant":
        paper_root = run_root / "paper"
    else:
        paper_root = tmp_path / "alias"
        paper_root.symlink_to(run_root, target_is_directory=True)

    with pytest.raises(PaperAuthorityInvalid, match="roots"):
        ClaimLedgerService.from_v031(run_root=run_root, paper_root=paper_root)


def test_formal_v031_transition_materializes_all_green_welfare_claims(
    tmp_path: Path,
) -> None:
    value = os.environ.get("ENVRESEARCH_V031_ACCEPTANCE_ROOT")
    if value is None:
        pytest.skip("sealed V0.3.1 acceptance root is not configured")
    run_root = Path(value).resolve(strict=True)
    transition_ref = V031ExitHarness(run_root).marker_ref
    service = ClaimLedgerService.from_v031(
        run_root=run_root, paper_root=tmp_path / "paper"
    )

    reference = service.build(transition_ref)
    ledger = service.status(reference, transition_ref)

    assert len(ledger.claims) == 7
    assert {row.method_id for row in ledger.claims} == {
        "contingent-valuation",
        "dce-clogit",
        "hedonic-pricing",
        "travel-cost",
    }


def test_non_green_report_cannot_be_promoted_to_claim_evidence(
    tmp_path: Path,
) -> None:
    resolver = cv_resolver(tmp_path)
    reference, report = resolver.reports[0]
    resolver.reports = ((reference, report.model_copy(update={"status": "exception"})),)
    service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )

    with pytest.raises(PaperSupportInvalid):
        service.build(resolver.transition_ref)

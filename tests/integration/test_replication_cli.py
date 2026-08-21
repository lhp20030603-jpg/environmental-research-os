"""Operator CLI contracts for the reference-based Tier-2 replay service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import pytest
import yaml  # type: ignore[import-untyped]
from replication_service_fixtures import ServiceCase, admission
from typer.testing import CliRunner

import envresearch.replication.cli as replication_cli
from envresearch.cli import app
from envresearch.models.artifact import ArtifactRef

CLI = CliRunner()
ARTICLE_URL = "https://www.aeaweb.org/articles?id=10.1257/jel.20251650"
PACKAGE_URL = "https://example.org/tiny-did.tar.gz"


class _CliResult(Protocol):
    stdout: str


def _dry_payload() -> dict[str, object]:
    return {
        "schema_version": "tier2-dry-proposal-v1",
        "proposal_kind": "dry",
        "package_id": "jel-did-2026",
        "admission_status": "proposed",
        "target_work": {
            "title": "Difference-in-Differences Designs: A Practitioner's Guide",
            "authors": [
                "Andrew Baker",
                "Brantly Callaway",
                "Scott Cunningham",
                "Andrew Goodman-Bacon",
                "Pedro H. C. Sant'Anna",
            ],
            "journal": "Journal of Economic Literature",
            "publication_year": 2026,
            "volume": 64,
            "issue": 2,
            "pages": "498-557",
            "doi": "10.1257/jel.20251650",
            "article_url": ARTICLE_URL,
            "package_landing_url": "https://github.com/pedrohcgs/JEL-DiD",
        },
        "runtime_requirement": {
            "language": "R",
            "profile_id": "r-did-v1",
        },
        "metadata_verified_on": "2026-08-10",
        "metadata_source_urls": [ARTICLE_URL],
        "unresolved_blockers": [
            {
                "code": "archive-direct-locator-unapproved",
                "detail": "No exact acquisition URL has been approved.",
            },
            {
                "code": "archive-sha256-unobserved",
                "detail": "No archive has been acquired or hashed.",
            },
            {
                "code": "license-scope-unverified",
                "detail": "Code and data license scope requires review.",
            },
            {
                "code": "self-contained-status-unverified",
                "detail": "External data access described by the landing page requires review.",
            },
            {
                "code": "pinned-runtime-image-unapproved",
                "detail": "No executable image digest has been approved.",
            },
        ],
    }


def _write_yaml(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _write_ref(path: Path, reference: ArtifactRef) -> Path:
    path.write_text(reference.model_dump_json(), encoding="utf-8")
    return path


def _body(result: _CliResult) -> dict[str, object]:
    stdout = result.stdout
    assert isinstance(stdout, str) and stdout
    value = json.loads(stdout)
    assert isinstance(value, dict)
    return value


def _use_service(monkeypatch: pytest.MonkeyPatch, case: ServiceCase) -> None:
    monkeypatch.setattr(
        replication_cli, "_service_for_root", lambda run_root: case.service
    )


def test_replication_validate_dry_proposal_is_read_only(tmp_path: Path) -> None:
    proposal = _write_yaml(tmp_path / "dry.yaml", _dry_payload())
    before = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))

    result = CLI.invoke(app, ["replication", "validate", str(proposal), "--json"])

    assert result.exit_code == 0, result.output
    assert _body(result) == {
        "admission_status": "proposed",
        "executable": False,
        "package_id": "jel-did-2026",
        "proposal": str(proposal),
        "schema_version": "tier2-dry-proposal-v1",
        "unresolved_blockers": [
            "archive-direct-locator-unapproved",
            "archive-sha256-unobserved",
            "license-scope-unverified",
            "self-contained-status-unverified",
            "pinned-runtime-image-unapproved",
        ],
        "valid": True,
    }
    after = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))
    assert after == before


def test_replication_validate_executable_proposal_is_read_only(
    service_case: ServiceCase, tmp_path: Path
) -> None:
    proposal = _write_yaml(
        tmp_path / "intake.yaml", service_case.proposal.model_dump(mode="json")
    )

    result = CLI.invoke(app, ["replication", "validate", str(proposal), "--json"])

    assert result.exit_code == 0, result.output
    assert _body(result) == {
        "admission_status": "ready_for_external_admission",
        "executable": True,
        "package_id": "tiny-service-did",
        "proposal": str(proposal),
        "schema_version": "tier2-intake-v1",
        "unresolved_blockers": [],
        "valid": True,
    }
    assert not (tmp_path / "artifacts").exists()


def test_approve_external_rejects_dry_proposal_without_writes(
    service_case: ServiceCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_service(monkeypatch, service_case)
    proposal = _write_yaml(tmp_path / "dry.yaml", _dry_payload())

    result = CLI.invoke(
        app,
        [
            "replication",
            "approve-external",
            str(proposal),
            "--run-root",
            str(tmp_path),
            "--approver-id",
            "research-owner",
            "--rationale",
            "Reviewed public metadata.",
            "--approved-locator",
            "https://github.com/pedrohcgs/JEL-DiD",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert _body(result)["error"]["code"] == "DRY_PROPOSAL_NOT_EXECUTABLE"  # type: ignore[index]
    assert not (tmp_path / "artifacts").exists()


def test_approve_external_rejects_locator_mismatch_atomically(
    service_case: ServiceCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_service(monkeypatch, service_case)
    proposal = _write_yaml(
        tmp_path / "intake.yaml", service_case.proposal.model_dump(mode="json")
    )

    result = CLI.invoke(
        app,
        [
            "replication",
            "approve-external",
            str(proposal),
            "--run-root",
            str(tmp_path),
            "--approver-id",
            "research-owner",
            "--rationale",
            "Reviewed exact package authority.",
            "--approved-locator",
            "https://other.example/package.tar.gz",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert _body(result)["error"]["code"] == "APPROVED_LOCATOR_MISMATCH"  # type: ignore[index]
    assert not (tmp_path / "artifacts").exists()


def test_approve_external_persists_and_returns_exact_references(
    service_case: ServiceCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_service(monkeypatch, service_case)
    proposal = _write_yaml(
        tmp_path / "intake.yaml", service_case.proposal.model_dump(mode="json")
    )

    result = CLI.invoke(
        app,
        [
            "replication",
            "approve-external",
            str(proposal),
            "--run-root",
            str(tmp_path),
            "--approver-id",
            "research-owner",
            "--rationale",
            "Reviewed exact package authority.",
            "--approved-locator",
            PACKAGE_URL,
            "--json",
        ],
    )

    body = _body(result)
    assert result.exit_code == 0, result.output
    proposal_ref = ArtifactRef.model_validate(body["proposal_ref"])
    approved_ref = ArtifactRef.model_validate(body["approved_ref"])
    assert proposal_ref.artifact_id == "tier2-intake-proposal"
    assert approved_ref.artifact_id == "approved-tier2-intake"
    assert (
        tmp_path / f"artifacts/replication/proposals/{proposal_ref.content_hash}.json"
    ).is_file()
    assert (
        tmp_path / f"artifacts/replication/approved/{approved_ref.content_hash}.json"
    ).is_file()


def test_replication_run_rejects_missing_exact_external_admission(
    tmp_path: Path,
) -> None:
    result = CLI.invoke(
        app,
        [
            "replication",
            "run",
            "--run-root",
            str(tmp_path),
            "--approved-ref",
            str(tmp_path / "missing.json"),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert _body(result)["error"]["code"] == "EXTERNAL_ADMISSION_REQUIRED"  # type: ignore[index]
    assert not (tmp_path / "artifacts").exists()


def test_approve_output_is_the_explicit_authority_consumed_by_run(
    service_case: ServiceCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_service(monkeypatch, service_case)
    proposal = _write_yaml(
        tmp_path / "intake.yaml", service_case.proposal.model_dump(mode="json")
    )
    approved = CLI.invoke(
        app,
        [
            "replication",
            "approve-external",
            str(proposal),
            "--run-root",
            str(tmp_path),
            "--approver-id",
            "research-owner",
            "--rationale",
            "Reviewed exact package authority.",
            "--approved-locator",
            PACKAGE_URL,
            "--json",
        ],
    )
    authority = tmp_path / "external-admission.json"
    authority.write_text(approved.stdout, encoding="utf-8")

    result = CLI.invoke(
        app,
        [
            "replication",
            "run",
            "--run-root",
            str(tmp_path),
            "--approved-ref",
            str(authority),
            "--json",
        ],
    )

    body = _body(result)
    assert approved.exit_code == 0
    assert result.exit_code == 0, result.output
    assert body["state"] == "passed"
    assert body["exception"] is None
    assert ArtifactRef.model_validate(body["status_ref"]).artifact_id == (
        "replication-ledger"
    )


@pytest.mark.parametrize("service_case", ["no-engine"], indirect=True)
def test_run_and_status_serialize_durable_exception_with_distinct_exit_codes(
    service_case: ServiceCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_service(monkeypatch, service_case)
    proposal_ref = service_case.service.intake.record_proposal(service_case.proposal)
    approved_ref = service_case.service.approve_external_admission(
        proposal_ref, admission()
    )
    approved_path = _write_ref(tmp_path / "approved-ref.json", approved_ref)

    executed = CLI.invoke(
        app,
        [
            "replication",
            "run",
            "--run-root",
            str(tmp_path),
            "--approved-ref",
            str(approved_path),
            "--json",
        ],
    )

    execution = _body(executed)
    assert executed.exit_code == 1
    assert execution["state"] == "exception"
    assert execution["exception"]["code"] == "NO_CONTAINER_ENGINE"  # type: ignore[index]
    assert ArtifactRef.model_validate(execution["status_ref"]) == approved_ref
    status_ref = _write_ref(tmp_path / "status-ref.json", approved_ref)

    status = CLI.invoke(
        app,
        [
            "replication",
            "status",
            "--run-root",
            str(tmp_path),
            "--ref",
            str(status_ref),
            "--json",
        ],
    )

    assert status.exit_code == 0, status.output
    assert _body(status) == execution


def test_replication_resume_rejects_nonledger_reference_before_service_call(
    service_case: ServiceCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_service(monkeypatch, service_case)
    proposal_ref = service_case.service.intake.record_proposal(service_case.proposal)
    reference = _write_ref(tmp_path / "proposal-ref.json", proposal_ref)

    result = CLI.invoke(
        app,
        [
            "replication",
            "resume",
            "--run-root",
            str(tmp_path),
            "--run-ref",
            str(reference),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert _body(result)["error"]["code"] == "RUN_REFERENCE_INVALID"  # type: ignore[index]

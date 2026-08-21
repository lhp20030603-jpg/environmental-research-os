"""Exact-reference deterministic JSON CLI for governed factory runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_factory_promotion import _capability
from test_factory_run import connected_factory
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.kernel.gates import GateDecision
from envresearch.models.enums import GateStatus


def _write_ref(path: Path, reference: object) -> Path:
    path.write_text(reference.model_dump_json(), encoding="utf-8")  # type: ignore[attr-defined]
    return path


def _roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    roots = tuple(
        (tmp_path / name).resolve() for name in ("research", "v031", "paper", "factory")
    )
    for root in roots:
        root.mkdir(parents=True)
    research = roots[0]
    for derived in (
        research / "design",
        research / ".design.worker-queue-control",
        research / "citation/research",
        research / "citation/.research.worker-queue-control",
    ):
        derived.mkdir(parents=True)
    return roots  # type: ignore[return-value]


def _root_args(roots: tuple[Path, Path, Path, Path]) -> list[str]:
    research, v031, paper, factory = roots
    return [
        "--research-root",
        str(research),
        "--v031-root",
        str(v031),
        "--paper-root",
        str(paper),
        "--factory-root",
        str(factory),
    ]


def _decision(path: Path, status: GateStatus = GateStatus.APPROVED) -> Path:
    decision = GateDecision(
        status=status,
        decided_by="human-reviewer",
        rationale="Reviewed the exact governed run and its limitations.",
        decided_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    path.write_text(decision.model_dump_json(), encoding="utf-8")
    return path


def test_factory_command_group_is_registered() -> None:
    """Catch the governed factory facade being absent from the public CLI."""
    result = CliRunner().invoke(app, ["factory", "--help"])

    assert result.exit_code == 0, result.output
    assert "assemble" in result.output


def test_assemble_emits_exact_reference_payload_and_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch path handoffs, implicit discovery, or nondeterministic JSON output."""
    fixture = connected_factory(tmp_path / "fixture")
    try:
        import envresearch.factory.cli as factory_cli

        monkeypatch.setattr(
            factory_cli, "service_for_roots", lambda *args, **kwargs: fixture.service
        )
        design = _write_ref(tmp_path / "design.json", fixture.design_ref)
        release = _write_ref(tmp_path / "release.json", fixture.release_ref)
        roots = _roots(tmp_path / "roots")
        arguments = [
            "factory",
            "assemble",
            str(design),
            str(release),
            *_root_args(roots),
        ]

        first = CliRunner().invoke(app, arguments)
        second = CliRunner().invoke(app, arguments)

        assert first.exit_code == second.exit_code == 0, first.output
        assert first.stdout == second.stdout
        payload = json.loads(first.stdout)
        assert set(payload) == {"payload", "reference", "status"}
        assert payload["status"]["state"] == "promotion-required"
        assert payload["reference"] == payload["status"]["run_ref"]
        assert set(payload["reference"]) == {
            "artifact_id",
            "artifact_version",
            "content_hash",
        }
    finally:
        fixture.close()


def test_cli_drives_exact_promotion_and_reports_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch promotion commands bypassing the sole public workflow facade."""
    fixture = connected_factory(tmp_path / "fixture")
    try:
        import envresearch.factory.cli as factory_cli

        monkeypatch.setattr(
            factory_cli, "service_for_roots", lambda *args, **kwargs: fixture.service
        )
        run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        run_path = _write_ref(tmp_path / "run.json", run_ref)
        roots = _roots(tmp_path / "roots")
        root_args = _root_args(roots)
        runner = CliRunner()

        pending = runner.invoke(app, ["factory", "status", str(run_path), *root_args])
        assert pending.exit_code == 1
        assert json.loads(pending.stdout)["error"]["code"] == (
            "FACTORY_PROMOTION_REQUIRED"
        )

        requested = runner.invoke(
            app,
            [
                "factory",
                "request-promotion",
                str(run_path),
                "--requested-by",
                "factory-agent",
                *root_args,
            ],
        )
        assert requested.exit_code == 0, requested.output
        requested_payload = json.loads(requested.stdout)
        context_path = tmp_path / "context.json"
        context_path.write_text(json.dumps(requested_payload["reference"]))
        capability = tmp_path / "capability.txt"
        capability.write_text(_capability(fixture), encoding="utf-8")

        recorded = runner.invoke(
            app,
            [
                "factory",
                "record-promotion",
                str(context_path),
                str(run_path),
                str(_decision(tmp_path / "decision.json")),
                "--principal-capability-file",
                str(capability),
                *root_args,
            ],
        )
        assert recorded.exit_code == 0, recorded.output
        recorded_payload = json.loads(recorded.stdout)
        assert recorded_payload["status"]["state"] == "promoted"
        promotion_path = tmp_path / "promotion.json"
        promotion_path.write_text(json.dumps(recorded_payload["reference"]))

        reopened = runner.invoke(
            app,
            [
                "factory",
                "promotion-status",
                str(promotion_path),
                str(run_path),
                *root_args,
            ],
        )
        assert reopened.exit_code == 0
        assert reopened.stdout == recorded.stdout
    finally:
        fixture.close()


def test_rejected_decision_is_stable_terminal_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a rejection being mislabeled success or broad product promotion."""
    fixture = connected_factory(tmp_path / "fixture")
    try:
        import envresearch.factory.cli as factory_cli

        monkeypatch.setattr(
            factory_cli, "service_for_roots", lambda *args, **kwargs: fixture.service
        )
        run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = fixture.service.request_promotion(
            run_ref, requested_by="factory-agent"
        )
        context = _write_ref(tmp_path / "context.json", context_ref)
        run = _write_ref(tmp_path / "run.json", run_ref)
        capability = tmp_path / "capability.txt"
        capability.write_text(_capability(fixture), encoding="utf-8")
        result = CliRunner().invoke(
            app,
            [
                "factory",
                "record-promotion",
                str(context),
                str(run),
                str(_decision(tmp_path / "decision.json", GateStatus.REJECTED)),
                "--principal-capability-file",
                str(capability),
                *_root_args(_roots(tmp_path / "roots")),
            ],
        )

        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"] == {
            "code": "FACTORY_PROMOTION_REJECTED",
            "finding_kind": "promotion-rejected",
            "message": "the independent human decision rejected this exact run",
        }
    finally:
        fixture.close()


def test_noncanonical_requester_repeats_one_typed_json_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch public request validation leaking ValueError with empty stdout."""
    fixture = connected_factory(tmp_path / "fixture")
    try:
        import envresearch.factory.cli as factory_cli

        monkeypatch.setattr(
            factory_cli, "service_for_roots", lambda *args, **kwargs: fixture.service
        )
        run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        arguments = [
            "factory",
            "request-promotion",
            str(_write_ref(tmp_path / "run.json", run_ref)),
            "--requested-by",
            " Factory-Agent ",
            *_root_args(_roots(tmp_path / "roots")),
        ]

        first = CliRunner().invoke(app, arguments)
        second = CliRunner().invoke(app, arguments)

        assert first.exit_code == second.exit_code == 2
        assert first.stdout == second.stdout
        assert json.loads(first.stdout)["error"] == {
            "code": "FACTORY_AUTHORITY_INVALID",
            "finding_kind": "promotion-requester-invalid",
            "message": "promotion requester must be one canonical principal",
        }
    finally:
        fixture.close()


@pytest.mark.parametrize(
    "conditions,finding_kind",
    (
        ({"additional_limitations": []}, "promotion-conditions-invalid"),
        ({"product_release": "approved"}, "promotion-scope"),
    ),
)
def test_invalid_decision_conditions_repeat_one_typed_json_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    conditions: dict[str, object],
    finding_kind: str,
) -> None:
    """Catch condition validation leaking raw ValidationError at the CLI boundary."""
    fixture = connected_factory(tmp_path / "fixture")
    try:
        import envresearch.factory.cli as factory_cli

        monkeypatch.setattr(
            factory_cli, "service_for_roots", lambda *args, **kwargs: fixture.service
        )
        run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = fixture.service.request_promotion(run_ref, "factory-agent")
        decision = GateDecision(
            status=GateStatus.APPROVED,
            decided_by="human-reviewer",
            rationale="Reviewed the exact governed run and its limitations.",
            conditions=conditions,
            decided_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        decision_path = tmp_path / "decision.json"
        decision_path.write_text(decision.model_dump_json(), encoding="utf-8")
        capability = tmp_path / "capability.txt"
        capability.write_text(_capability(fixture), encoding="utf-8")
        arguments = [
            "factory",
            "record-promotion",
            str(_write_ref(tmp_path / "context.json", context_ref)),
            str(_write_ref(tmp_path / "run.json", run_ref)),
            str(decision_path),
            "--principal-capability-file",
            str(capability),
            *_root_args(_roots(tmp_path / "roots")),
        ]

        first = CliRunner().invoke(app, arguments)
        second = CliRunner().invoke(app, arguments)

        assert first.exit_code == second.exit_code == 2
        assert first.stdout == second.stdout
        assert json.loads(first.stdout)["error"]["finding_kind"] == finding_kind
    finally:
        fixture.close()


@pytest.mark.parametrize("contents", ("{}", "not-json"))
def test_malformed_reference_is_stable_json(tmp_path: Path, contents: str) -> None:
    """Catch malformed references reaching discovery or Rich-only diagnostics."""
    invalid = tmp_path / "invalid.json"
    invalid.write_text(contents, encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "factory",
            "status",
            str(invalid),
            *_root_args(_roots(tmp_path / "roots")),
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"] == {
        "code": "FACTORY_AUTHORITY_INVALID",
        "finding_kind": "reference-input-invalid",
        "message": "explicit ArtifactRef JSON is invalid",
    }


@pytest.mark.parametrize(
    "arguments",
    (
        ["factory", "status"],
        ["factory", "status", "missing.json", "--unknown-option"],
    ),
)
def test_missing_inputs_and_parser_failures_are_deterministic_json(
    arguments: list[str],
) -> None:
    """Catch framework parser errors escaping as human-only Rich stderr."""
    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert set(payload["error"]) == {"code", "finding_kind", "message"}

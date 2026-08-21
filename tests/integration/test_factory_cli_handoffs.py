"""Operator-visible promotion handoffs for the governed factory CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_factory_cli import _decision, _root_args, _roots, _write_ref
from test_factory_promotion import _capability
from test_factory_run import connected_factory
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import GateStatus


def test_promotion_commands_emit_the_exact_reopened_context_and_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a promotion command handing off the run instead of its own artifact."""
    fixture = connected_factory(tmp_path / "fixture")
    try:
        import envresearch.factory.cli as factory_cli

        monkeypatch.setattr(
            factory_cli, "service_for_roots", lambda *args, **kwargs: fixture.service
        )
        run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        run_path = _write_ref(tmp_path / "run.json", run_ref)
        root_args = _root_args(_roots(tmp_path / "roots"))
        runner = CliRunner()

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
        requested_json = json.loads(requested.stdout)
        context_ref = ArtifactRef.model_validate(requested_json["reference"])
        context = fixture.service._promotions.store.load_context(context_ref)
        assert requested_json["payload"] == context.model_dump(mode="json")
        assert requested_json["payload"]["schema_version"] == (
            "factory.promotion-context.v1"
        )
        assert requested_json["payload"]["run_ref"] == run_ref.model_dump(mode="json")

        context_path = _write_ref(tmp_path / "context.json", context_ref)
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
        recorded_json = json.loads(recorded.stdout)
        promotion_ref = ArtifactRef.model_validate(recorded_json["reference"])
        promotion = fixture.service._promotions.store.load_promotion(promotion_ref)
        assert recorded_json["payload"] == promotion.model_dump(mode="json")
        assert recorded_json["payload"]["schema_version"] == (
            "factory.run-promotion.v1"
        )
        assert recorded_json["payload"]["context_ref"] == context_ref.model_dump(
            mode="json"
        )
    finally:
        fixture.close()


def test_rejected_record_and_status_preserve_the_exact_promotion_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a terminal rejection discarding the reference needed to reopen it."""
    fixture = connected_factory(tmp_path / "fixture")
    try:
        import envresearch.factory.cli as factory_cli

        monkeypatch.setattr(
            factory_cli, "service_for_roots", lambda *args, **kwargs: fixture.service
        )
        run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = fixture.service.request_promotion(run_ref, "factory-agent")
        run_path = _write_ref(tmp_path / "run.json", run_ref)
        context_path = _write_ref(tmp_path / "context.json", context_ref)
        capability = tmp_path / "capability.txt"
        capability.write_text(_capability(fixture), encoding="utf-8")
        root_args = _root_args(_roots(tmp_path / "roots"))
        runner = CliRunner()

        recorded = runner.invoke(
            app,
            [
                "factory",
                "record-promotion",
                str(context_path),
                str(run_path),
                str(_decision(tmp_path / "decision.json", GateStatus.REJECTED)),
                "--principal-capability-file",
                str(capability),
                *root_args,
            ],
        )

        assert recorded.exit_code == 1
        recorded_json = json.loads(recorded.stdout)
        assert set(recorded_json) == {"error", "payload", "reference", "status"}
        assert recorded_json["error"] == {
            "code": "FACTORY_PROMOTION_REJECTED",
            "finding_kind": "promotion-rejected",
            "message": "the independent human decision rejected this exact run",
        }
        assert recorded_json["status"]["state"] == "promotion-rejected"
        promotion_ref = ArtifactRef.model_validate(recorded_json["reference"])
        promotion = fixture.service._promotions.store.load_promotion(promotion_ref)
        assert recorded_json["payload"] == promotion.model_dump(mode="json")
        promotion_path = _write_ref(tmp_path / "promotion.json", promotion_ref)

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

        assert reopened.exit_code == 1
        assert reopened.stdout == recorded.stdout
    finally:
        fixture.close()


def test_decision_json_with_an_unknown_field_is_stable_typed_input_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch the CLI silently discarding an unexpected decision field."""
    fixture = connected_factory(tmp_path / "fixture")
    try:
        import envresearch.factory.cli as factory_cli

        monkeypatch.setattr(
            factory_cli, "service_for_roots", lambda *args, **kwargs: fixture.service
        )
        run_ref = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        context_ref = fixture.service.request_promotion(run_ref, "factory-agent")
        decision_path = _decision(tmp_path / "decision.json")
        decision_json = json.loads(decision_path.read_text(encoding="utf-8"))
        decision_json["unexpected"] = "must-not-be-ignored"
        decision_path.write_text(json.dumps(decision_json), encoding="utf-8")
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
        runner = CliRunner()

        first = runner.invoke(app, arguments)
        second = runner.invoke(app, arguments)

        assert first.exit_code == second.exit_code == 2
        assert first.stdout == second.stdout
        assert json.loads(first.stdout) == {
            "error": {
                "code": "FACTORY_AUTHORITY_INVALID",
                "finding_kind": "decision-input-invalid",
                "message": "explicit GateDecision JSON is invalid",
            }
        }
    finally:
        fixture.close()

"""CLI regressions for non-finite benchmark tolerance validation."""

import json
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from typer.testing import CliRunner

from envresearch.cli import app

CLI = CliRunner()


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "neg-inf"]
)
def test_validate_json_rejects_nonfinite_tolerance(
    tmp_path: Path, value: float
) -> None:
    """CLI validation must reject every YAML spelling of a non-finite float."""
    manifest = tmp_path / "benchmark.yaml"
    payload = {
        "id": "tolerance-case",
        "title": "Tolerance case",
        "method_family": "fixture",
        "topic": "validation",
        "public": False,
        "source_url": "https://example.org/source",
        "commands": [],
        "expected_outputs": [
            {
                "path": "result.json",
                "comparator": "json_numeric",
                "expected_path": "expected.json",
                "absolute_tolerance": value,
            }
        ],
    }
    manifest.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    result = CLI.invoke(app, ["benchmark", "validate", str(manifest), "--json"])
    body = json.loads(result.stdout)

    assert result.exit_code == 2
    assert body["valid"] is False
    assert [finding["code"] for finding in body["findings"]] == ["SCHEMA_INVALID"]

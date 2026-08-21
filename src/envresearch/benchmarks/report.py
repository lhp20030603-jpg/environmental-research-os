"""Durable serialization and retryable publication of benchmark reports."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from envresearch.models.run import RunReport
from envresearch.storage.atomic import atomic_write_bytes

FINALIZATION_PATH = Path("benchmark-finalization.json")


class FinalizationJournal(BaseModel):
    """Canonical final report retained across interrupted publication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    state: Literal["pending", "published"]
    report: RunReport


def write_run_report(report: RunReport, path: Path) -> None:
    """Atomically persist one canonical, JSON-encoded run report."""
    payload = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    atomic_write_bytes(path, payload)


def stage_finalization(report: RunReport, workspace: Path) -> None:
    """Durably stage the canonical report before attempting publication."""
    _write_journal(
        FinalizationJournal(state="pending", report=report),
        workspace / FINALIZATION_PATH,
    )


def read_finalization(workspace: Path) -> FinalizationJournal | None:
    """Read and validate an existing finalization journal, if present."""
    path = workspace / FINALIZATION_PATH
    if not path.exists():
        return None
    return FinalizationJournal.model_validate_json(path.read_text(encoding="utf-8"))


def publish_pending_finalization(
    workspace: Path, benchmark_id: str
) -> RunReport | None:
    """Publish a matching pending report and mark its journal published."""
    journal = read_finalization(workspace)
    if journal is None or journal.state != "pending":
        return None
    if journal.report.benchmark_id != benchmark_id:
        raise ValueError("pending finalization belongs to another benchmark")
    write_run_report(journal.report, workspace / "run-report.json")
    _write_journal(
        journal.model_copy(update={"state": "published"}),
        workspace / FINALIZATION_PATH,
    )
    return journal.report


def _write_journal(journal: FinalizationJournal, path: Path) -> None:
    payload = json.dumps(
        journal.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    atomic_write_bytes(path, payload)

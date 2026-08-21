"""Small recoverable publication store for local analysis reports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from envresearch.econometrics._store_files import StoreFiles
from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport


class ReportPublisher:
    """Publish immutable history before one recoverable current pointer."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files = StoreFiles(root)
        self.fail_after_history = False

    def publish(self, report: LocalAnalysisReport) -> LocalAnalysisReference:
        """Seal pending, history, and current in a deterministic order."""
        data = _report_bytes(report)
        report = LocalAnalysisReport.model_validate_json(data)
        data = _report_bytes(report)
        digest = hashlib.sha256(data).hexdigest()
        directory = Path("analyses") / report.analysis_id
        history = (
            directory / "history" / f"generation-{report.generation}-{digest}.json"
        )
        pending = directory / "pending.json"
        reference = LocalAnalysisReference(
            analysis_id=report.analysis_id,
            generation=report.generation,
            relative_path=history,
            sha256=digest,
        )
        self.files.write(pending, data)
        try:
            existing = self.files.read(history)
        except FileNotFoundError:
            existing = None
        if existing is not None and existing != data:
            raise ValueError("local analysis history collision")
        if existing is None:
            self.files.write(history, data)
        if self.fail_after_history:
            raise OSError("injected current publication failure")
        self.files.write(directory / "current.json", _reference_bytes(reference))
        self.files.unlink(pending)
        return reference

    def recover(self, analysis_id: str) -> LocalAnalysisReference | None:
        """Finish one sealed pending publication without rerunning analysis."""
        pending = Path("analyses") / analysis_id / "pending.json"
        if not self.files.exists(pending):
            return None
        report = LocalAnalysisReport.model_validate_json(self.files.read(pending))
        if report.analysis_id != analysis_id:
            raise ValueError("pending local analysis identity changed")
        return self.publish(report)

    def current(self, analysis_id: str) -> LocalAnalysisReference | None:
        """Return the exact current report reference when present."""
        path = Path("analyses") / analysis_id / "current.json"
        if not self.files.exists(path):
            return None
        reference = LocalAnalysisReference.model_validate_json(self.files.read(path))
        if reference.analysis_id != analysis_id:
            raise ValueError("current local analysis identity changed")
        return reference

    def load(self, reference: LocalAnalysisReference) -> LocalAnalysisReport:
        """Reopen an exact immutable report and verify its content hash."""
        data = self.files.read(reference.relative_path)
        if hashlib.sha256(data).hexdigest() != reference.sha256:
            raise ValueError("local analysis report identity changed")
        report = LocalAnalysisReport.model_validate_json(data)
        if (report.analysis_id, report.generation) != (
            reference.analysis_id,
            reference.generation,
        ):
            raise ValueError("local analysis reference does not match report")
        return report


def _report_bytes(report: LocalAnalysisReport) -> bytes:
    """Return canonical serialized report bytes."""
    payload = report.model_dump(mode="json")
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _reference_bytes(reference: LocalAnalysisReference) -> bytes:
    """Return canonical serialized current-reference bytes."""
    payload = reference.model_dump(mode="json")
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()

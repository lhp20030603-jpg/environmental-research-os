"""Manifest/report identity invariants for durable workflow records."""

from envresearch.models.run import RunManifest, RunReport


def report_identity_error(manifest: RunManifest, report: RunReport) -> str | None:
    """Describe the first manifest/report identity mismatch, if any."""
    if report.run_id != manifest.run_id:
        return f"run id mismatch: expected {manifest.run_id}, found {report.run_id}"
    if report.benchmark_id != manifest.benchmark_id:
        return (
            "benchmark id mismatch: expected "
            f"{manifest.benchmark_id}, found {report.benchmark_id}"
        )
    return None


def bind_report_identity(manifest: RunManifest, report: RunReport) -> RunReport:
    """Return a report identity aligned with its owning manifest."""
    return report.model_copy(
        update={"run_id": manifest.run_id, "benchmark_id": manifest.benchmark_id}
    )

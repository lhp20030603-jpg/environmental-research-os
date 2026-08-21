"""Private controller values re-exported by the replication service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from envresearch.models.artifact import ArtifactRef
from envresearch.replication._ledger_models import OutputResult
from envresearch.replication._service_support import require_safe_relative_paths
from envresearch.replication.contracts import (
    ReplicationException,
    ReplicationRunState,
)


@dataclass(frozen=True, slots=True)
class DidReplayConfiguration:
    """Immutable mapping from an approved package to the bounded DiD replay."""

    author_script: Path
    data_path: Path
    unit_column: str
    time_column: str
    treatment_column: str
    cohort_column: str
    outcome_column: str
    reference_period: int

    def __post_init__(self) -> None:
        require_safe_relative_paths(self.author_script, self.data_path)


class ReplicationFault(RuntimeError):
    """Known orchestration boundary failure with stable code and evidence."""

    def __init__(
        self, code: str, message: str, evidence: tuple[ArtifactRef, ...] = ()
    ) -> None:
        if not code.strip() or not message.strip():
            raise ValueError("replication fault code and message must be nonblank")
        super().__init__(message)
        self.code, self.evidence = code, tuple(evidence)

    def record(self) -> ReplicationException:
        return ReplicationException(
            code=self.code, message=str(self), evidence_refs=self.evidence
        )


@dataclass(frozen=True, slots=True)
class ReplicationReport:
    """Authenticated current read model returned by the public controller."""

    run_ref: ArtifactRef
    state: ReplicationRunState
    exception: ReplicationException | None = None
    author_outputs: tuple[OutputResult, ...] = ()
    derived_ref: ArtifactRef | None = None
    verification_ref: ArtifactRef | None = None

"""Offline fixtures shared by replication service integration tests."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import HttpUrl, TypeAdapter

from envresearch.models.artifact import ResearchArtifact
from envresearch.replication._runtime_identity import mount_sha256
from envresearch.replication._runtime_owner import (
    RuntimeLaunchIdentity,
    RuntimeOwnership,
)
from envresearch.replication._workspace_checkpoint import persist_workspace_checkpoint
from envresearch.replication.container import (
    ContainerPlan,
    ContainerResult,
    RuntimeObservation,
)
from envresearch.replication.contracts import (
    ExternalAdmission,
    Tier2IntakeProposal,
)
from envresearch.replication.intake import Tier2IntakeService
from envresearch.replication.ledger import ResourceObservation
from envresearch.replication.service import (
    DidReplayConfiguration,
    ReplicationFault,
    ReplicationReport,
    ReplicationService,
)
from envresearch.storage.research_artifacts import ResearchArtifactStore

URL = TypeAdapter(HttpUrl).validate_python
SHA256 = "a" * 64


def runtime_identities(
    plan: ContainerPlan,
) -> tuple[RuntimeLaunchIdentity, RuntimeOwnership]:
    """Build strict fake launch/owner values without touching a real runtime."""
    nonce = hashlib.sha256(
        f"fixture\0{plan.output_root}\0{plan.output_namespace}".encode()
    ).hexdigest()
    now = datetime.now(UTC)
    launch = RuntimeLaunchIdentity(
        engine="fake-container",
        attempt_nonce=nonce,
        container_name=f"envresearch-{nonce[:24]}",
        cidfile_path=str(plan.output_root.parent / ".runtime" / f"{nonce}.cid"),
        image_digest=plan.image_digest,
        input_mount_sha256=mount_sha256(plan.input_root),
        output_mount_sha256=mount_sha256(plan.output_root),
        prepared_at=now,
    )
    owner = RuntimeOwnership(
        engine=launch.engine,
        pid=4242,
        pgid=4242,
        process_birth_sha256="b" * 64,
        attempt_nonce=launch.attempt_nonce,
        container_name=launch.container_name,
        container_id=hashlib.sha256(nonce.encode()).hexdigest(),
        image_digest=launch.image_digest,
        input_mount_sha256=launch.input_mount_sha256,
        output_mount_sha256=launch.output_mount_sha256,
        started_at=now,
    )
    return launch, owner


def publish_runtime_owner(plan: ContainerPlan, callback: object) -> None:
    if callback is None:
        return
    launch, owner = runtime_identities(plan)
    callback(launch)  # type: ignore[operator]
    callback(owner)  # type: ignore[operator]


class FixtureFetcher:
    def __init__(self, archive: Path) -> None:
        self.archive = archive
        self.calls = 0

    def fetch(self, url: HttpUrl, destination: Path, max_bytes: int) -> None:
        del url, max_bytes
        self.calls += 1
        destination.write_bytes(self.archive.read_bytes())


class FakeEngine:
    identity = "fake-container"
    executable_sha256 = "e" * 64
    endpoint = "unix:///tmp/envresearch-fake-container.sock"

    def __init__(self, outcome: str = "green") -> None:
        self.outcome = outcome
        self.plans: list[ContainerPlan] = []
        self.contain_calls: list[tuple[object, tuple[str, ...]]] = []

    def preflight(self, profile: object) -> RuntimeObservation:
        del profile
        if self.outcome == "no-engine":
            raise RuntimeError("container engine is unavailable")
        now = datetime.now(UTC)
        return RuntimeObservation(
            engine="fake-container",
            executable_sha256=self.executable_sha256,
            endpoint=self.endpoint,
            version="1.0",
            started_at=now,
            finished_at=now,
            stdout_sha256="1" * 64,
            stderr_sha256="2" * 64,
            stdout_truncated=False,
            stderr_truncated=False,
            peak_memory_bytes=0,
            storage_bytes=0,
        )

    def run(  # type: ignore[no-untyped-def]
        self,
        plan: ContainerPlan,
        *,
        on_progress=None,
        on_started=None,
        on_stopped=None,
    ) -> ContainerResult:
        del on_progress
        self.plans.append(plan)
        publish_runtime_owner(plan, on_started)
        try:
            if self.outcome == "resource-error":
                raise RuntimeError("container exceeded approved memory budget")
            if (
                self.outcome == "network"
                and plan.output_namespace == "author-reproduction"
            ):
                raise ReplicationFault(
                    "UNDECLARED_EXTERNAL_ACCESS", "container attempted network access"
                )
            now = datetime.now(UTC)
            if plan.output_namespace == "author-reproduction":
                output = plan.output_root / "output/results.csv"
                output.parent.mkdir(parents=True, exist_ok=True)
                value = (
                    "estimate\n0.9\n"
                    if self.outcome == "mismatch"
                    else "estimate\n0.1\n"
                )
                output.write_text(value, encoding="utf-8")
            else:
                output = (
                    plan.output_root
                    / "derived-did-event-study"
                    / "derived-did-event-study-v1.json"
                )
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(derived_payload()), encoding="utf-8")
            peak = plan.budget.max_memory_bytes + 1 if self.outcome == "resource" else 1
            return ContainerResult(
                engine="fake-container",
                image_digest=plan.image_digest,
                exit_status=0,
                started_at=now,
                finished_at=now,
                stdout_sha256="3" * 64,
                stderr_sha256="4" * 64,
                stdout_truncated=False,
                stderr_truncated=False,
                peak_memory_bytes=peak,
                storage_bytes=1,
            )
        finally:
            if on_stopped is not None:
                on_stopped()

    def contain(self, owner, names):  # type: ignore[no-untyped-def]
        self.contain_calls.append((owner, names))


@dataclass(frozen=True)
class ServiceCase:
    service: ReplicationService
    store: ResearchArtifactStore
    fetcher: FixtureFetcher
    proposal: Tier2IntakeProposal
    archive: Path


@pytest.fixture
def service_case(
    tmp_path: Path, request: pytest.FixtureRequest
) -> Iterator[ServiceCase]:
    outcome = getattr(request, "param", "green")
    archive = build_archive(tmp_path)
    fetcher = FixtureFetcher(archive)
    store = ResearchArtifactStore(tmp_path)
    service = ReplicationService(
        store,
        Tier2IntakeService(store, fetcher=fetcher),
        FakeEngine(outcome),
        replay_configuration(),
    )
    proposal = proposal_payload()
    yield ServiceCase(service, store, fetcher, proposal, archive)


def approve(case: ServiceCase):  # type: ignore[no-untyped-def]
    proposal_ref = case.service.intake.record_proposal(case.proposal)
    return case.service.approve_external_admission(proposal_ref, admission())


def pause_with_checkpoint(case: ServiceCase, started):  # type: ignore[no-untyped-def]
    """Publish a fixture emergency pause with its exact empty workspace snapshot."""
    _, run = case.service.ledger.read_current(started)
    root = case.store.root / run.output_root
    checkpoint = persist_workspace_checkpoint(
        case.store,
        started,
        run,
        root,
        max_bytes=case.proposal.budget.max_storage_bytes,
    )
    return case.service.ledger.pause(
        started, reason="emergency-stop", evidence_refs=(checkpoint,)
    )


def admission() -> ExternalAdmission:
    return ExternalAdmission(
        approver_id="researcher-17",
        rationale="Public fixture package is approved for offline replay.",
        approved_locator=URL("https://example.org/tiny-did.tar.gz"),
    )


def replay_configuration() -> DidReplayConfiguration:
    return DidReplayConfiguration(
        author_script=Path("code/run.R"),
        data_path=Path("data/analysis.csv"),
        unit_column="unit",
        time_column="time",
        treatment_column="treated",
        cohort_column="cohort",
        outcome_column="outcome",
        reference_period=-1,
    )


def proposal_payload() -> Tier2IntakeProposal:
    return Tier2IntakeProposal.model_validate(
        {
            "schema_version": "tier2-intake-v1",
            "package_id": "tiny-service-did",
            "canonical_url": "https://example.org/tiny-did.tar.gz",
            "declared_version": "1",
            "doi": None,
            "license_name": "MIT",
            "license_url": "https://example.org/license",
            "declared_inputs": (
                {
                    "path": Path("code/run.R"),
                    "purpose": "author-code",
                    "required": True,
                },
                {
                    "path": Path("data/analysis.csv"),
                    "purpose": "author-data",
                    "required": True,
                },
                {
                    "path": Path("expected/results.csv"),
                    "purpose": "author-output-target",
                    "required": True,
                },
            ),
            "expected_outputs": (
                {
                    "path": "output/results.csv",
                    "comparator": "csv_numeric",
                    "expected_path": "expected/results.csv",
                    "absolute_tolerance": 0.001,
                    "relative_tolerance": 0.0,
                },
            ),
            "runtime": {
                "profile_id": "r-did-v1",
                "image_digest": f"example/r@sha256:{SHA256}",
                "nonroot_uid_gid": "1000:1000",
            },
            "budget": {
                "max_download_bytes": 10_000,
                "max_storage_bytes": 10_000,
                "max_memory_bytes": 10_000,
                "inactivity_seconds": 10,
            },
            "self_contained": True,
        }
    )


def build_archive(tmp_path: Path) -> Path:
    archive_path = tmp_path / "package.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        add_file(archive, "code/run.R", b"print('offline fixture')\n")
        add_file(
            archive,
            "data/analysis.csv",
            b"unit,time,treated,cohort,outcome\n1,2012,1,2012,0.1\n",
        )
        add_file(archive, "expected/results.csv", b"estimate\n0.1\n")
    return archive_path


def add_file(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    entry = tarfile.TarInfo(name)
    entry.size = len(data)
    archive.addfile(entry, io.BytesIO(data))


def derived_payload() -> dict[str, object]:
    return {
        "schema_version": "derived-did-event-study-v1",
        "treatment_timing": {"first_treated_period": 2012},
        "support": {"group_time_supported": False},
        "balance": {"units": 1},
        "event_time": {"reference_period": -1},
        "twfe_event_study": {"estimates": []},
        "callaway_santanna": {
            "status": "unsupported",
            "reason": "fixture has only one cohort",
        },
        "configuration": {"outcome_column": "outcome"},
    }


def observation() -> ResourceObservation:
    return ResourceObservation(
        elapsed_seconds=1,
        storage_bytes=1,
        memory_bytes=1,
        heartbeat_at=datetime.now(UTC),
    )


def read_verification(
    store: ResearchArtifactStore, report: ReplicationReport
) -> ResearchArtifact[object]:
    assert report.verification_ref is not None
    return store.read_structured(
        Path(
            "artifacts/replication/verifications/"
            f"{report.verification_ref.content_hash}.json"
        ),
        TypeAdapter(ResearchArtifact[object]),
    )

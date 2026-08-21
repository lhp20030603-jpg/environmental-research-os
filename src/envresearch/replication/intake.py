"""Approved, bounded acquisition of immutable Tier-2 replication archives."""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Protocol

from pydantic import HttpUrl, TypeAdapter

from envresearch.models.artifact import (
    ArtifactEnvelope,
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
    verify_artifact,
)
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ApprovedTier2Intake,
    DeclaredInput,
    ExternalAdmission,
    InventoryFile,
    Tier2IntakeProposal,
)
from envresearch.storage.atomic import _sync_parent_directory, atomic_write_bytes
from envresearch.storage.research_artifacts import ResearchArtifactStore


class ArchiveFetcher(Protocol):
    """Fetch exactly one approved archive into a private staging path."""

    def fetch(self, url: HttpUrl, destination: Path, max_bytes: int) -> None:
        """Write at most ``max_bytes`` to ``destination`` or raise."""


class HttpArchiveFetcher:
    """One-request HTTP fetcher that refuses redirects and oversized responses."""

    def fetch(self, url: HttpUrl, destination: Path, max_bytes: int) -> None:
        request = urllib.request.Request(str(url), method="GET")
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            response = opener.open(request)
            if response is None:
                raise ValueError("archive fetch failed")
            with response, destination.open("xb") as output:
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        declared_bytes = int(length)
                    except ValueError as error:
                        raise ValueError("invalid Content-Length header") from error
                    if declared_bytes < 0:
                        raise ValueError("invalid Content-Length header")
                    if declared_bytes > max_bytes:
                        raise ValueError("download exceeds approved byte budget")
                total = 0
                while chunk := response.read(min(1024 * 1024, max_bytes + 1)):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("download exceeds approved byte budget")
                    output.write(chunk)
        except urllib.error.HTTPError as error:
            raise ValueError("archive fetch failed") from error


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Treat every redirect as a rejected acquisition instead of following it."""

    def redirect_request(
        self, *args: object, **kwargs: object
    ) -> urllib.request.Request | None:
        del args, kwargs
        return None


class Tier2IntakeService:
    """Persist approved intake decisions and safely inventory raw archives."""

    def __init__(
        self, store: ResearchArtifactStore, fetcher: ArchiveFetcher | None = None
    ) -> None:
        self._store = store
        self._fetcher = fetcher or HttpArchiveFetcher()

    def record_proposal(self, proposal: Tier2IntakeProposal) -> ArtifactRef:
        """Seal an independently prepared pre-acquisition proposal."""
        artifact = _sealed_artifact(
            artifact_id="tier2-intake-proposal",
            payload=proposal,
            inputs=(),
        )
        reference = _reference(artifact)
        self._store.write_structured(_proposal_path(reference), artifact)
        return reference

    def approve(
        self, proposal_ref: ArtifactRef, approval: ExternalAdmission
    ) -> ArtifactRef:
        """Seal a human admission decision only for the proposal's exact URL."""
        proposal = self._read_proposal(proposal_ref)
        if str(approval.approved_locator) != str(proposal.canonical_url):
            raise PermissionError("approval locator is not the proposal canonical URL")
        artifact = _sealed_artifact(
            artifact_id="approved-tier2-intake",
            payload=ApprovedTier2Intake(
                proposal_ref=proposal_ref,
                approval=approval,
                approved_at=datetime.now(UTC),
            ),
            inputs=(proposal_ref,),
        )
        reference = _reference(artifact)
        self._store.write_structured(_approved_path(reference), artifact)
        return reference

    def acquire(self, approved_ref: ArtifactRef, url: HttpUrl) -> ArtifactRef:
        """Fetch, inspect, then atomically publish the exact approved archive."""
        approved = self._read_approved(approved_ref)
        proposal = self._read_proposal(approved.proposal_ref)
        if str(url) != str(approved.approval.approved_locator) or str(url) != str(
            proposal.canonical_url
        ):
            raise PermissionError("acquisition URL is not the approved locator")

        with TemporaryDirectory(prefix="tier2-acquire-") as temporary:
            archive = Path(temporary) / "archive.tar.gz"
            self._fetch_to_stage(url, archive, proposal.budget.max_download_bytes)
            inventory = inspect_safe_archive(
                archive,
                proposal.declared_inputs,
                proposal.budget.max_storage_bytes,
                approved_ref,
            )
            return self._persist_acquired(approved_ref, archive, inventory)

    def _read_proposal(self, reference: ArtifactRef) -> Tier2IntakeProposal:
        artifact = self._store.read_structured(
            _proposal_path(reference), TypeAdapter(ResearchArtifact[object])
        )
        if _reference(artifact) != reference:
            raise ValueError("proposal artifact reference mismatch")
        if not isinstance(artifact.payload, dict):
            raise TypeError("proposal artifact payload must be an object")
        payload = dict(artifact.payload)
        declared_inputs = payload.get("declared_inputs")
        if not isinstance(declared_inputs, list):
            raise TypeError("proposal declared inputs must be an array")
        restored_inputs: list[dict[str, object]] = []
        for item in declared_inputs:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise TypeError("proposal declared input must have a path")
            restored = dict(item)
            restored["path"] = Path(item["path"])
            restored_inputs.append(restored)
        payload["declared_inputs"] = tuple(restored_inputs)
        expected_outputs = payload.get("expected_outputs")
        if not isinstance(expected_outputs, list):
            raise TypeError("proposal expected outputs must be an array")
        payload["expected_outputs"] = tuple(expected_outputs)
        return Tier2IntakeProposal.model_validate(payload)

    def _read_approved(self, reference: ArtifactRef) -> ApprovedTier2Intake:
        artifact = self._store.read_structured(
            _approved_path(reference), TypeAdapter(ResearchArtifact[object])
        )
        if _reference(artifact) != reference:
            raise ValueError("approved intake artifact reference mismatch")
        if not isinstance(artifact.payload, dict):
            raise TypeError("approved intake artifact payload must be an object")
        payload = dict(artifact.payload)
        approved_at = payload.get("approved_at")
        if not isinstance(approved_at, str):
            raise TypeError("approved intake timestamp must be a string")
        payload["approved_at"] = datetime.fromisoformat(approved_at)
        return ApprovedTier2Intake.model_validate(payload)

    def _fetch_to_stage(self, url: HttpUrl, archive: Path, max_bytes: int) -> None:
        self._fetcher.fetch(url, archive, max_bytes)
        if not archive.is_file():
            raise ValueError("fetcher did not produce an archive")
        if archive.stat().st_size > max_bytes:
            raise ValueError("download exceeds approved byte budget")

    def _persist_acquired(
        self,
        approved_ref: ArtifactRef,
        archive: Path,
        inventory: AcquiredPackageInventory,
    ) -> ArtifactRef:
        artifact = _sealed_artifact(
            artifact_id="acquired-tier2-package-inventory",
            payload=inventory,
            inputs=(approved_ref,),
        )
        reference = _reference(artifact)
        archive_destination = self._store.root / _raw_archive_path(
            inventory.archive_sha256
        )
        inventory_destination = self._store.root / _inventory_path(reference)
        _write_archive_and_inventory(
            archive, archive_destination, inventory_destination, artifact
        )
        return reference


def inspect_safe_archive(
    archive_path: Path,
    declared_inputs: tuple[DeclaredInput, ...],
    max_storage_bytes: int,
    approved_intake_ref: ArtifactRef,
) -> AcquiredPackageInventory:
    """Return an inventory only when every archive member is reviewed and safe."""
    declared = {item.path.as_posix() for item in declared_inputs}
    seen: set[str] = set()
    files: list[InventoryFile] = []
    total = 0
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            for member in archive:
                if member.issym() or member.islnk():
                    raise ValueError("symlink archive members are not allowed")
                if not member.isreg():
                    raise ValueError("non-regular archive member is not allowed")
                normalized = _safe_member_path(member.name)
                if normalized in seen:
                    raise ValueError("duplicate normalized archive path")
                seen.add(normalized)
                if normalized not in declared:
                    raise ValueError("archive contains an undeclared file")
                total += member.size
                if total > max_storage_bytes:
                    raise ValueError("archive exceeds approved storage budget")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError("archive member could not be read")
                digest = hashlib.sha256()
                observed = 0
                with extracted:
                    while chunk := extracted.read(1024 * 1024):
                        observed += len(chunk)
                        if observed > member.size:
                            raise ValueError("archive member exceeds declared size")
                        digest.update(chunk)
                if observed != member.size:
                    raise ValueError("archive member size does not match header")
                files.append(
                    InventoryFile(
                        path=Path(normalized), bytes=observed, sha256=digest.hexdigest()
                    )
                )
    except tarfile.TarError as error:
        raise ValueError("archive is not a readable tar package") from error

    observed_paths = {item.path.as_posix() for item in files}
    if observed_paths != declared:
        raise ValueError("archive does not contain every declared input")
    return AcquiredPackageInventory(
        approved_intake_ref=approved_intake_ref,
        archive_sha256=_sha256_file(archive_path),
        archive_bytes=archive_path.stat().st_size,
        files=tuple(sorted(files, key=lambda item: item.path.as_posix())),
    )


def _safe_member_path(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or (len(name) >= 2 and name[0].isalpha() and name[1] == ":")
        or name != path.as_posix()
        or any(part in {".", ".."} for part in name.split("/"))
    ):
        raise ValueError("archive member must use a safe relative path")
    return name


def _sealed_artifact(
    artifact_id: str,
    payload: object,
    inputs: tuple[ArtifactRef, ...],
) -> ResearchArtifact[object]:
    return seal_artifact(
        ResearchArtifact(
            envelope=ArtifactEnvelope(
                artifact_id=artifact_id,
                artifact_version=1,
                run_id="tier2-replication-intake",
                created_at=datetime.now(UTC),
                producer=ProducerIdentity(component="tier2-intake", version="0.3.0"),
                input_artifacts=inputs,
            ),
            payload=payload,
        )
    )


def _reference(artifact: ResearchArtifact[object]) -> ArtifactRef:
    content_hash = artifact.envelope.content_hash
    if content_hash is None:
        raise ValueError("artifact must be sealed")
    return ArtifactRef(
        artifact_id=artifact.envelope.artifact_id,
        artifact_version=artifact.envelope.artifact_version,
        content_hash=content_hash,
    )


def _proposal_path(reference: ArtifactRef) -> Path:
    return Path(f"artifacts/replication/proposals/{reference.content_hash}.json")


def _approved_path(reference: ArtifactRef) -> Path:
    return Path(f"artifacts/replication/approved/{reference.content_hash}.json")


def _inventory_path(reference: ArtifactRef) -> Path:
    return Path(f"artifacts/replication/inventories/{reference.content_hash}.json")


def _raw_archive_path(archive_sha256: str) -> Path:
    return Path(f"artifacts/replication/raw/{archive_sha256}.tar.gz")


def _write_archive_and_inventory(
    source_archive: Path,
    archive_destination: Path,
    inventory_destination: Path,
    inventory: ResearchArtifact[object],
) -> None:
    """Publish the raw bytes and sealed inventory together, or restore both."""
    verify_artifact(inventory)
    archive_data = source_archive.read_bytes()
    inventory_data = json.dumps(
        inventory.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    previous: dict[Path, bytes | None] = {
        archive_destination: archive_destination.read_bytes()
        if archive_destination.exists()
        else None,
        inventory_destination: inventory_destination.read_bytes()
        if inventory_destination.exists()
        else None,
    }
    staged: list[tuple[Path, Path]] = []
    try:
        for destination, data in (
            (archive_destination, archive_data),
            (inventory_destination, inventory_data),
        ):
            stage = destination.with_name(f".{destination.name}.stage")
            atomic_write_bytes(stage, data)
            staged.append((stage, destination))
        for stage, destination in staged:
            os.replace(stage, destination)
            _sync_parent_directory(destination.parent)
    except OSError:
        for destination, previous_data in previous.items():
            if previous_data is None:
                if destination.exists():
                    destination.unlink()
                    _sync_parent_directory(destination.parent)
            else:
                atomic_write_bytes(destination, previous_data)
        raise
    finally:
        for stage, _ in staged:
            stage.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

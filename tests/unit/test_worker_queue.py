"""Tests for isolated, content-addressed worker file exchange."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef, ProducerIdentity
from envresearch.workers import (
    FilesystemWorkerQueue,
    WorkerRole,
    WorkerSubmission,
    WorkOrder,
)


def work_order(
    node_id: str = "map-literature",
    *,
    order_id: str | None = None,
    output_schema: str = "LiteratureMapPayload",
    output_filenames: tuple[str, ...] = ("candidate.json",),
) -> WorkOrder:
    """Build one deterministic synthetic order with no external inputs."""
    return WorkOrder(
        order_id=order_id or f"order-{node_id}",
        node_id=node_id,
        node_version="1.0",
        role=WorkerRole.LITERATURE_CARTOGRAPHER,
        input_artifacts=(
            ArtifactRef(
                artifact_id="research-charter",
                artifact_version=1,
                content_hash="a" * 64,
            ),
        ),
        expected_output_schema=output_schema,
        expected_output_filenames=output_filenames,
        policy_constraints=("offline-only", "no-authoritative-writes"),
        evidence_requirements=("cite-source-ids",),
    )


def _tamper_json(path: Path, field: str, value: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def _write_canonical_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _same_inode(descriptor: int, path: Path) -> bool:
    opened = os.fstat(descriptor)
    current = os.stat(path, follow_symlinks=False)
    return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)


def _leave_receipt_temp_by_process_death(
    queue: FilesystemWorkerQueue,
    control: Path,
    order: WorkOrder,
    source: Path,
) -> None:
    """Exit a real child immediately before its receipt-anchor rename."""
    from envresearch.workers import filesystem as worker_fs

    child = os.fork()
    if child == 0:
        original_rename = worker_fs._rename_directory_noreplace

        def exit_before_receipt_rename(
            source_fd: int,
            source_name: str,
            destination_fd: int,
            destination: str,
        ) -> None:
            receipt_directory = control / "receipts" / order.order_id
            if (
                destination == "candidate.json.json"
                and receipt_directory.exists()
                and _same_inode(destination_fd, receipt_directory)
            ):
                os._exit(91)
            original_rename(source_fd, source_name, destination_fd, destination)

        worker_fs._rename_directory_noreplace = exit_before_receipt_rename
        queue.submit(order.order_id, source)
        os._exit(92)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 91


def test_work_order_hash_binds_all_order_content() -> None:
    """Omitting any order field from the digest would allow silent task changes."""
    order = work_order()
    canonical = order.model_dump(mode="json", exclude={"order_hash"})
    expected = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    assert order.order_hash == expected
    assert work_order(output_schema="OtherPayload").order_hash != order.order_hash


def test_work_order_accepts_benchmark_curator_role() -> None:
    """Benchmark-specific responsibilities must be valid durable queue roles."""
    order = WorkOrder(
        order_id="benchmark-curation",
        node_id="benchmark-curation",
        node_version="1.0",
        role=WorkerRole.BENCHMARK_CURATOR,
        input_artifacts=(
            ArtifactRef(
                artifact_id="research-charter",
                artifact_version=1,
                content_hash="a" * 64,
            ),
        ),
        expected_output_schema="BenchmarkCuratorPayload",
        expected_output_filenames=("candidate.json",),
        policy_constraints=("offline-only",),
        evidence_requirements=("cite-source-ids",),
    )

    assert order.role is WorkerRole.BENCHMARK_CURATOR


def test_work_order_rejects_supplied_hash_mismatch() -> None:
    """A caller-provided digest must not replace the digest of actual order content."""
    payload = work_order().model_dump(mode="python")
    payload["order_hash"] = "0" * 64

    with pytest.raises(ValidationError, match="work order hash mismatch"):
        WorkOrder.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("order_id", "../escape"),
        ("node_id", "node/name"),
        ("node_version", " "),
        ("expected_output_schema", "not a schema"),
        ("expected_output_schema", "Schema..Payload"),
        ("expected_output_filenames", ("../artifact.json",)),
        ("expected_output_filenames", ("/artifact.json",)),
        ("expected_output_filenames", ("artifact.json", "artifact.json")),
        ("expected_output_filenames", ("artifact.json", "Artifact.json")),
        ("expected_output_filenames", ("artifacts",)),
        ("policy_constraints", ("",)),
        ("evidence_requirements", ("cite", "cite")),
    ],
)
def test_work_order_rejects_noncanonical_content(field: str, value: object) -> None:
    """Unsafe or ambiguous order content cannot acquire a durable identity."""
    payload = work_order().model_dump(mode="python", exclude={"order_hash"})
    payload[field] = value

    with pytest.raises(ValidationError):
        WorkOrder.model_validate(payload)


def test_work_order_is_strict_frozen_and_forbids_extra_fields() -> None:
    """Coercion, mutation, and ignored fields would weaken the hash contract."""
    payload = work_order().model_dump(mode="python", exclude={"order_hash"})
    payload["node_version"] = 1
    with pytest.raises(ValidationError):
        WorkOrder.model_validate(payload)

    payload = work_order().model_dump(mode="python", exclude={"order_hash"})
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        WorkOrder.model_validate(payload)

    order = work_order()
    with pytest.raises(ValidationError):
        order.node_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize("artifact_version", ["1", True, 1.0])
def test_work_order_rejects_coerced_nested_artifact_versions(
    artifact_version: object,
) -> None:
    """Nested provenance scalars must not be normalized before order hashing."""
    payload = work_order().model_dump(mode="python", exclude={"order_hash"})
    payload["input_artifacts"] = (
        {
            "artifact_id": "research-charter",
            "artifact_version": artifact_version,
            "content_hash": "a" * 64,
        },
    )

    with pytest.raises(ValidationError):
        WorkOrder.model_validate(payload)


def test_work_order_rejects_forged_nested_artifact_ref() -> None:
    """A forged nested model must not bypass strict scalar validation."""
    forged = ArtifactRef(
        artifact_id="research-charter",
        artifact_version=1,
        content_hash="a" * 64,
    ).model_copy(update={"artifact_version": "1"})
    payload = work_order().model_dump(mode="python", exclude={"order_hash"})
    payload["input_artifacts"] = (forged,)

    with pytest.raises(ValidationError):
        WorkOrder.model_validate(payload)


@pytest.mark.parametrize(
    "filename",
    ["CON", "con.json", "NUL.txt", "COM1", "lpt9.csv", "candidate."],
)
def test_work_order_rejects_nonportable_output_filenames(filename: str) -> None:
    """Portable queue identities must not alias Windows devices or trim dots."""
    payload = work_order().model_dump(mode="python", exclude={"order_hash"})
    payload["expected_output_filenames"] = (filename,)

    with pytest.raises(ValidationError):
        WorkOrder.model_validate(payload)


@pytest.mark.parametrize(
    "filename",
    ["receipt.json", "queue.key", "locks", "orders", "receipts", "transactions"],
)
def test_work_order_reserves_internal_queue_filenames(filename: str) -> None:
    """Candidate names cannot collide with fixed transaction or control entries."""
    payload = work_order().model_dump(mode="python", exclude={"order_hash"})
    payload["expected_output_filenames"] = (filename,)

    with pytest.raises(ValidationError, match="reserved"):
        WorkOrder.model_validate(payload)


@pytest.mark.parametrize("order_id", ["CON", "nul.json", "LPT1", "order."])
def test_work_order_rejects_nonportable_order_ids(order_id: str) -> None:
    """Order identities must remain unambiguous filesystem components."""
    payload = work_order().model_dump(mode="python", exclude={"order_hash"})
    payload["order_id"] = order_id

    with pytest.raises(ValidationError, match="portable"):
        WorkOrder.model_validate(payload)


def test_derived_queue_names_enforce_exact_name_max_boundary(tmp_path: Path) -> None:
    """Every derived entry must fit NAME_MAX before protected intent is created."""
    name_max = os.pathconf(tmp_path, "PC_NAME_MAX")
    candidate = "a" * (name_max - len(".submission"))
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order(output_filenames=(candidate,))
    queue.issue(order)
    (tmp_path / candidate).write_text("{}", encoding="utf-8")

    record = queue.submit(order.order_id, Path(candidate))

    assert record.relative_path.name == candidate
    overlong = candidate + "a"
    with pytest.raises(ValidationError, match="filesystem byte limit"):
        work_order(output_filenames=(overlong,))


def test_derived_order_names_enforce_exact_name_max_boundary(tmp_path: Path) -> None:
    """Order directories, anchors, and locks must all fit the real filesystem."""
    name_max = os.pathconf(tmp_path, "PC_NAME_MAX")
    order_id = "o" * (name_max - len(".filelock"))
    queue = FilesystemWorkerQueue(tmp_path)

    queue.issue(work_order(order_id=order_id))

    assert (tmp_path / "work-orders" / f"{order_id}.json").is_file()
    with pytest.raises(ValidationError, match="filesystem byte limit"):
        work_order(order_id=order_id + "o")


def test_issue_is_idempotent_but_rejects_identity_reuse(tmp_path: Path) -> None:
    """Concurrent retries may repeat an order, but must never replace its meaning."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()

    first = queue.issue(order)
    second = queue.issue(order)

    assert first.relative_path == Path("work-orders/order-map-literature.json")
    assert second.sha256 == first.sha256
    with pytest.raises(RuntimeError, match="work order identity collision"):
        queue.issue(work_order(output_schema="OtherPayload"))


def test_issue_parent_swap_cannot_redirect_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Order publication must remain bound to its opened work-orders directory."""
    from envresearch.workers import filesystem as worker_fs

    exchange = tmp_path / "exchange"
    queue = FilesystemWorkerQueue(exchange)
    artifacts = exchange / "artifacts"
    artifacts.mkdir()
    original_rename = worker_fs._rename_directory_noreplace
    swapped = False

    def swap_then_rename(
        source_fd: int, source: str, destination_fd: int, destination: str
    ) -> None:
        nonlocal swapped
        work_orders = exchange / "work-orders"
        if (
            not swapped
            and work_orders.exists()
            and _same_inode(destination_fd, work_orders)
        ):
            work_orders.rename(exchange / "opened-work-orders")
            work_orders.symlink_to(artifacts, target_is_directory=True)
            swapped = True
        original_rename(source_fd, source, destination_fd, destination)

    monkeypatch.setattr(
        worker_fs, "_rename_directory_noreplace", swap_then_rename
    )

    queue.issue(work_order())

    assert swapped
    assert not (artifacts / "order-map-literature.json").exists()
    assert (exchange / "opened-work-orders" / "order-map-literature.json").is_file()


def test_issue_revalidates_forged_model_copy(tmp_path: Path) -> None:
    """Pydantic model_copy must not bypass order-hash verification at publication."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order().model_copy(update={"node_id": "other"})

    with pytest.raises(ValueError, match="work order hash mismatch"):
        queue.issue(order)

    assert not (tmp_path / "work-orders" / "order-map-literature.json").exists()


def test_issue_rejects_forged_copy_that_clears_order_hash(tmp_path: Path) -> None:
    """Clearing a digest through model_copy must not request a new trusted seal."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order().model_copy(update={"order_hash": None})

    with pytest.raises(ValueError, match="work order hash"):
        queue.issue(order)

    assert not (tmp_path / "work-orders" / "order-map-literature.json").exists()


def test_submission_cannot_write_authoritative_artifact(tmp_path: Path) -> None:
    """Untrusted worker bytes must remain outside kernel-owned namespaces."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"sources": []}', encoding="utf-8")

    receipt = queue.submit(order.order_id, candidate)

    assert receipt.relative_path.parts[0] == "worker-submissions"
    assert (tmp_path / receipt.relative_path).read_bytes() == candidate.read_bytes()
    assert not (tmp_path / "artifacts" / "literature-map.json").exists()
    assert not (tmp_path / "decisions").exists()
    assert not (tmp_path / "node-checkpoints").exists()


def test_submit_records_authoritative_byte_hash_and_identity(tmp_path: Path) -> None:
    """The receipt must describe the queue copy, not a worker-claimed digest."""
    producer = ProducerIdentity(
        component="worker-adapter", version="2.1", model="local-test"
    )
    queue = FilesystemWorkerQueue(tmp_path, producer=producer)
    order = work_order(output_schema="EstimandSpecPayload")
    queue.issue(order)
    candidate = tmp_path / "candidate.json"
    candidate.write_bytes(b"\x00candidate\r\nbytes\xff")

    record = queue.submit(order.order_id, candidate)
    submissions = queue.collect(order.order_id)

    assert record.sha256 == hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert len(submissions) == 1
    submission = submissions[0]
    assert submission.order_id == order.order_id
    assert submission.order_hash == order.order_hash
    assert submission.producer == producer
    assert submission.candidate_relative_paths == (record.relative_path,)
    assert submission.candidate_sha256 == (record.sha256,)
    assert submission.claimed_schema == "EstimandSpecPayload"
    assert submission.submitted_at.utcoffset() == timedelta(0)
    assert submission.submitted_at.tzname() == "UTC"


def test_submit_rejects_unknown_or_changed_order(tmp_path: Path) -> None:
    """No bytes may enter the queue without an intact issued order."""
    queue = FilesystemWorkerQueue(tmp_path)
    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="unknown work order"):
        queue.submit("order-unknown", candidate)

    order = work_order()
    queue.issue(order)
    _tamper_json(
        tmp_path / "work-orders" / f"{order.order_id}.json", "node_id", "other"
    )
    with pytest.raises(ValueError, match="work order hash mismatch"):
        queue.submit(order.order_id, candidate)


def test_submit_rejects_schema_and_filename_mismatch(tmp_path: Path) -> None:
    """A candidate cannot be relabeled for a different promised output contract."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    wrong_name = tmp_path / "other.json"
    wrong_name.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected output filename"):
        queue.submit(order.order_id, wrong_name)

    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="claimed schema mismatch"):
        queue.submit(order.order_id, candidate, claimed_schema="OtherPayload")

    for invalid in ("", " ", "Schema..Payload"):
        with pytest.raises(ValueError, match="schema"):
            queue.submit(order.order_id, candidate, claimed_schema=invalid)


def test_submit_rejects_authoritative_source_path(tmp_path: Path) -> None:
    """Copying kernel output back through the inbox must not launder authority."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    source = tmp_path / "artifacts" / "candidate.json"
    source.parent.mkdir()
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(PermissionError, match="authoritative namespace"):
        queue.submit(order.order_id, source)


def test_submit_rejects_absolute_source_outside_queue_root(tmp_path: Path) -> None:
    """An exchange order cannot read arbitrary process-accessible files."""
    queue = FilesystemWorkerQueue(tmp_path / "exchange")
    order = work_order()
    queue.issue(order)
    source = tmp_path / "candidate.json"
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the queue root"):
        queue.submit(order.order_id, source)


def test_submit_accepts_absolute_source_through_root_alias(tmp_path: Path) -> None:
    """The caller's real system root alias must map to the pinned exchange inode."""
    private_var = Path("/private/var")
    var = Path("/var")
    resolved_tmp = tmp_path.resolve()
    if var.resolve() != private_var or not resolved_tmp.is_relative_to(private_var):
        pytest.skip("platform has no /var to /private/var filesystem alias")
    lexical_exchange = var / resolved_tmp.relative_to(private_var) / "exchange"
    queue = FilesystemWorkerQueue(lexical_exchange)
    order = work_order()
    queue.issue(order)
    source = lexical_exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")

    record = queue.submit(order.order_id, source)

    assert (queue.root / record.relative_path).read_text(encoding="utf-8") == "{}"


def test_submit_rejects_source_traversal(tmp_path: Path) -> None:
    """Relative intake paths cannot escape the pinned queue root."""
    exchange = tmp_path / "exchange"
    queue = FilesystemWorkerQueue(exchange)
    order = work_order(output_filenames=("outside.json",))
    queue.issue(order)
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="source path"):
        queue.submit(order.order_id, Path("../outside.json"))


def test_submit_rejects_absolute_source_traversal(tmp_path: Path) -> None:
    """Absolute aliases cannot normalize traversal before queue validation."""
    exchange = tmp_path / "exchange"
    queue = FilesystemWorkerQueue(exchange)
    order = work_order()
    queue.issue(order)
    (exchange / "candidate.json").write_text("{}", encoding="utf-8")
    traversing = Path(f"{exchange}/intake/../candidate.json")

    with pytest.raises(ValueError, match="source path"):
        queue.submit(order.order_id, traversing)


def test_submit_rejects_source_parent_symlink(tmp_path: Path) -> None:
    """Every source parent must be opened without following symlinks."""
    exchange = tmp_path / "exchange"
    queue = FilesystemWorkerQueue(exchange)
    order = work_order()
    queue.issue(order)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "candidate.json").write_text("{}", encoding="utf-8")
    (exchange / "intake").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink|source path"):
        queue.submit(order.order_id, Path("intake/candidate.json"))


def test_submit_rejects_hardlink_alias_of_authoritative_file(tmp_path: Path) -> None:
    """A benign-looking pathname cannot launder an authoritative inode."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    authoritative = tmp_path / "artifacts" / "authoritative.json"
    authoritative.parent.mkdir()
    authoritative.write_text("{}", encoding="utf-8")
    alias = tmp_path / "candidate.json"
    os.link(authoritative, alias)

    with pytest.raises(ValueError, match="hardlink|link count"):
        queue.submit(order.order_id, alias)


def test_source_parent_swap_reads_from_pinned_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source-parent replacement cannot redirect the already-open intake path."""
    from envresearch.workers import filesystem as worker_fs

    exchange = tmp_path / "exchange"
    queue = FilesystemWorkerQueue(exchange)
    order = work_order()
    queue.issue(order)
    intake = exchange / "intake"
    intake.mkdir()
    (intake / "candidate.json").write_bytes(b"original")
    attacker = exchange / "attacker"
    attacker.mkdir()
    (attacker / "candidate.json").write_bytes(b"redirected")
    original_open = worker_fs.os.open
    swapped = False

    def swap_then_open(
        path: str | bytes,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if (
            not swapped
            and path == "candidate.json"
            and dir_fd is not None
            and _same_inode(dir_fd, intake)
        ):
            intake.rename(exchange / "opened-intake")
            attacker.rename(intake)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(worker_fs.os, "open", swap_then_open)

    record = queue.submit(order.order_id, Path("intake/candidate.json"))

    assert swapped
    assert (exchange / record.relative_path).read_bytes() == b"original"


def test_submit_parent_swap_cannot_redirect_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transaction rename must target the pinned submissions directory."""
    from envresearch.workers import filesystem as worker_fs

    exchange = tmp_path / "exchange"
    queue = FilesystemWorkerQueue(exchange)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    artifacts = exchange / "artifacts"
    artifacts.mkdir()
    original_rename = worker_fs._rename_directory_noreplace
    swapped = False

    def swap_then_rename(
        source_fd: int, source_name: str, destination_fd: int, destination: str
    ) -> None:
        nonlocal swapped
        transactions = (
            exchange / "worker-submissions" / order.order_id / "transactions"
        )
        if (
            not swapped
            and transactions.exists()
            and _same_inode(destination_fd, transactions)
        ):
            transactions.rename(transactions.parent / "opened-transactions")
            transactions.symlink_to(artifacts, target_is_directory=True)
            swapped = True
        original_rename(source_fd, source_name, destination_fd, destination)

    monkeypatch.setattr(worker_fs, "_rename_directory_noreplace", swap_then_rename)

    queue.submit(order.order_id, source)

    assert swapped
    assert not (artifacts / "candidate.json.submission").exists()
    assert (
        exchange
        / "worker-submissions"
        / order.order_id
        / "opened-transactions"
        / "candidate.json.submission"
        / "candidate.json"
    ).is_file()


def test_collect_parent_swap_reads_pinned_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Collection must not follow a transaction path swapped after descriptor open."""
    from envresearch.workers import filesystem as worker_fs

    exchange = tmp_path / "exchange"
    queue = FilesystemWorkerQueue(exchange)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_bytes(b"original")
    record = queue.submit(order.order_id, source)
    transaction = (exchange / record.relative_path).parent
    artifacts = exchange / "artifacts"
    artifacts.mkdir()
    (artifacts / "candidate.json").write_bytes(b"redirected")
    original_open = worker_fs.os.open
    swapped = False

    def swap_then_open(
        path: str | bytes,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if (
            not swapped
            and path == "candidate.json"
            and dir_fd is not None
            and _same_inode(dir_fd, transaction)
        ):
            transaction.rename(transaction.parent / "opened-transaction")
            transaction.symlink_to(artifacts, target_is_directory=True)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(worker_fs.os, "open", swap_then_open)

    submissions = queue.collect(order.order_id)

    assert swapped
    assert submissions[0].candidate_sha256 == (
        hashlib.sha256(b"original").hexdigest(),
    )


def test_submission_publication_is_fail_if_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A direct writer winning the destination race must never be overwritten."""
    from envresearch.workers import filesystem as worker_fs

    exchange = tmp_path / "exchange"
    queue = FilesystemWorkerQueue(exchange)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    original_rename = worker_fs._rename_directory_noreplace
    raced = False

    def create_conflict_then_rename(
        source_fd: int, source_name: str, destination_fd: int, destination: str
    ) -> None:
        nonlocal raced
        if not raced and source_name.startswith("txn-"):
            os.mkdir(destination, dir_fd=destination_fd)
            conflict_fd = os.open(
                destination,
                os.O_RDONLY | os.O_DIRECTORY,
                dir_fd=destination_fd,
            )
            try:
                marker_fd = os.open(
                    "attacker-marker",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=conflict_fd,
                )
                os.close(marker_fd)
            finally:
                os.close(conflict_fd)
            raced = True
        original_rename(source_fd, source_name, destination_fd, destination)

    monkeypatch.setattr(
        worker_fs, "_rename_directory_noreplace", create_conflict_then_rename
    )

    with pytest.raises(RuntimeError, match="submission conflict"):
        queue.submit(order.order_id, source)

    assert raced
    assert (
        exchange
        / "worker-submissions"
        / order.order_id
        / "transactions"
        / "candidate.json.submission"
        / "attacker-marker"
    ).exists()


def test_interrupted_transaction_recovers_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after protected prepare cannot strand the same-content retry."""
    from envresearch.workers import filesystem as worker_fs

    class SimulatedCrash(BaseException):
        pass

    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_bytes(b"candidate")
    original_rename = worker_fs._rename_directory_noreplace

    def crash_before_publish(
        source_fd: int, source_name: str, destination_fd: int, destination: str
    ) -> None:
        raise SimulatedCrash

    monkeypatch.setattr(
        worker_fs, "_rename_directory_noreplace", crash_before_publish
    )
    with pytest.raises(SimulatedCrash):
        queue.submit(order.order_id, source)

    monkeypatch.setattr(worker_fs, "_rename_directory_noreplace", original_rename)
    record = queue.submit(order.order_id, source)

    assert (exchange / record.relative_path).read_bytes() == b"candidate"
    assert len(queue.collect(order.order_id)) == 1


@pytest.mark.parametrize("phase", ["key", "lock", "anchor", "order"])
def test_interrupted_immutable_file_publication_recovers_single_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Native no-replace publication cannot strand a two-link authority file."""
    from envresearch.workers import filesystem as worker_fs

    class SimulatedCrash(BaseException):
        pass

    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    order = work_order()
    original_rename = worker_fs._rename_directory_noreplace
    interrupted = False

    def crash_after_publish(
        source_fd: int, source: str, destination_fd: int, destination: str
    ) -> None:
        nonlocal interrupted
        original_rename(source_fd, source, destination_fd, destination)
        destination_is_target = (
            (phase == "key" and destination == "queue.key")
            or (phase == "lock" and destination == f"{order.order_id}.filelock")
            or (
                phase == "anchor"
                and destination == f"{order.order_id}.json"
                and (control / "orders").exists()
                and _same_inode(destination_fd, control / "orders")
            )
            or (
                phase == "order"
                and destination == f"{order.order_id}.json"
                and (exchange / "work-orders").exists()
                and _same_inode(destination_fd, exchange / "work-orders")
            )
        )
        if destination_is_target and not interrupted:
            interrupted = True
            raise SimulatedCrash

    if phase == "key":
        monkeypatch.setattr(
            worker_fs, "_rename_directory_noreplace", crash_after_publish
        )
        with pytest.raises(SimulatedCrash):
            FilesystemWorkerQueue(exchange, control_root=control)
        target = control / "queue.key"
        monkeypatch.setattr(
            worker_fs, "_rename_directory_noreplace", original_rename
        )
        queue = FilesystemWorkerQueue(exchange, control_root=control)
        queue.issue(order)
    else:
        queue = FilesystemWorkerQueue(exchange, control_root=control)
        monkeypatch.setattr(
            worker_fs, "_rename_directory_noreplace", crash_after_publish
        )
        with pytest.raises(SimulatedCrash):
            queue.issue(order)
        target = {
            "lock": control / "locks" / f"{order.order_id}.filelock",
            "anchor": control / "orders" / f"{order.order_id}.json",
            "order": exchange / "work-orders" / f"{order.order_id}.json",
        }[phase]
        monkeypatch.setattr(
            worker_fs, "_rename_directory_noreplace", original_rename
        )
        queue.issue(order)

    assert interrupted
    assert target.stat().st_nlink == 1
    assert not list(target.parent.glob(".tmp-*"))
    assert queue.collect(order.order_id) == ()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX process death")
def test_pre_rename_process_death_recovers_receipt_temp(tmp_path: Path) -> None:
    """A real process death cannot poison same-content retry and collection."""
    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    _leave_receipt_temp_by_process_death(queue, control, order, source)
    receipt_directory = control / "receipts" / order.order_id
    assert len(list(receipt_directory.glob(".tmp-*"))) == 1

    record = queue.submit(order.order_id, source)
    submissions = queue.collect(order.order_id)

    assert len(submissions) == 1
    assert submissions[0].candidate_relative_paths == (record.relative_path,)
    assert not list(receipt_directory.glob(".tmp-*"))


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX process death")
def test_receipt_recovery_never_deletes_another_orders_active_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Target-scoped recovery cannot remove a different order's active write."""
    from envresearch.workers import filesystem as worker_fs

    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    stale_order = work_order(order_id="order-stale")
    active_order = work_order(order_id="order-active")
    queue.issue(stale_order)
    queue.issue(active_order)
    stale_source = exchange / "stale" / "candidate.json"
    active_source = exchange / "active" / "candidate.json"
    stale_source.parent.mkdir()
    active_source.parent.mkdir()
    stale_source.write_text("stale", encoding="utf-8")
    active_source.write_text("active", encoding="utf-8")
    _leave_receipt_temp_by_process_death(
        queue, control, stale_order, stale_source
    )
    original_rename = worker_fs._rename_directory_noreplace
    active_ready = threading.Event()
    release_active = threading.Event()
    errors: list[Exception] = []
    paused = False

    def pause_active_receipt(
        source_fd: int,
        source_name: str,
        destination_fd: int,
        destination: str,
    ) -> None:
        nonlocal paused
        active_receipts = control / "receipts" / active_order.order_id
        if (
            not paused
            and destination == "candidate.json.json"
            and active_receipts.exists()
            and _same_inode(destination_fd, active_receipts)
        ):
            paused = True
            active_ready.set()
            if not release_active.wait(timeout=5):
                raise TimeoutError("test did not release active receipt")
        original_rename(source_fd, source_name, destination_fd, destination)

    def publish_active_order() -> None:
        try:
            queue.submit(active_order.order_id, active_source)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            errors.append(error)

    monkeypatch.setattr(
        worker_fs, "_rename_directory_noreplace", pause_active_receipt
    )
    publisher = threading.Thread(target=publish_active_order)
    publisher.start()
    assert active_ready.wait(timeout=5)
    active_receipts = control / "receipts" / active_order.order_id
    active_temps = tuple(active_receipts.glob(".tmp-*"))
    assert len(active_temps) == 1
    try:
        assert queue.collect(stale_order.order_id) == ()
        assert active_temps[0].is_file()
    finally:
        release_active.set()
        publisher.join(timeout=5)

    assert not publisher.is_alive()
    assert errors == []
    assert len(queue.collect(active_order.order_id)) == 1


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX process death")
@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("mode", "permissions"),
        ("hardlink", "link count"),
        ("directory", "regular non-symlink"),
        ("bytes", "authentication"),
    ],
)
def test_receipt_recovery_rejects_corrupt_internal_temp(
    tmp_path: Path, corruption: str, message: str
) -> None:
    """Recovery removes only authentic single-link controller-owned files."""
    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    _leave_receipt_temp_by_process_death(queue, control, order, source)
    temporary = next((control / "receipts" / order.order_id).glob(".tmp-*"))
    if corruption == "mode":
        temporary.chmod(0o644)
    elif corruption == "hardlink":
        os.link(temporary, tmp_path / "receipt-alias")
    elif corruption == "directory":
        temporary.unlink()
        temporary.mkdir()
    else:
        temporary.write_bytes(b"not-an-authenticated-anchor")

    with pytest.raises(ValueError, match=message):
        queue.submit(order.order_id, source)

    assert temporary.exists()


def test_receipt_recovery_rejects_unrecognized_or_unbounded_temps(
    tmp_path: Path,
) -> None:
    """Arbitrary temp-looking files are corruption, never cleanup candidates."""
    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    receipt_directory = control / "receipts" / order.order_id
    receipt_directory.mkdir()
    arbitrary = receipt_directory / ".tmp-attacker"
    arbitrary.write_text("attacker", encoding="utf-8")
    arbitrary.chmod(0o600)

    with pytest.raises(ValueError, match="invalid grammar"):
        queue.submit(order.order_id, source)
    assert arbitrary.exists()

    arbitrary.unlink()
    for index in range(65):
        path = receipt_directory / f".tmp-attacker-{index}"
        path.write_text("attacker", encoding="utf-8")
        path.chmod(0o600)
    with pytest.raises(ValueError, match="too many"):
        queue.submit(order.order_id, source)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX process death")
def test_receipt_recovery_preflights_namespace_before_cleanup(
    tmp_path: Path,
) -> None:
    """A bad sibling entry prevents every cleanup side effect."""
    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    _leave_receipt_temp_by_process_death(queue, control, order, source)
    receipt_directory = control / "receipts" / order.order_id
    temporary = next(receipt_directory.glob(".tmp-*"))
    (receipt_directory / "unexpected").write_text("corrupt", encoding="utf-8")

    with pytest.raises(ValueError, match="submission anchor path mismatch"):
        queue.collect(order.order_id)

    assert temporary.exists()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX process death")
def test_receipt_recovery_rejects_multiple_temps_for_one_target(
    tmp_path: Path,
) -> None:
    """Ambiguous stale writers fail before either authentic temp is removed."""
    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    _leave_receipt_temp_by_process_death(queue, control, order, source)
    receipt_directory = control / "receipts" / order.order_id
    first = next(receipt_directory.glob(".tmp-*"))
    second = receipt_directory / f"{first.name.rsplit('-', 1)[0]}-{'0' * 32}"
    second.write_bytes(first.read_bytes())
    second.chmod(0o600)

    with pytest.raises(ValueError, match="multiple protected temporaries"):
        queue.collect(order.order_id)

    assert first.exists()
    assert second.exists()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX process death")
def test_receipt_recovery_does_not_hide_disappearing_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a missing receipt directory is empty; a vanished listed temp is corruption."""
    from envresearch.workers import recovery as worker_recovery

    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    _leave_receipt_temp_by_process_death(queue, control, order, source)
    original_read = worker_recovery.read_regular_with_identity_at

    def remove_before_read(
        parent_fd: int,
        name: str,
        *,
        description: str,
        required_mode: int | None = None,
        required_owner: int | None = None,
    ) -> tuple[bytes, tuple[int, int]]:
        os.unlink(name, dir_fd=parent_fd)
        return original_read(
            parent_fd,
            name,
            description=description,
            required_mode=required_mode,
            required_owner=required_owner,
        )

    monkeypatch.setattr(
        worker_recovery, "read_regular_with_identity_at", remove_before_read
    )

    with pytest.raises(FileNotFoundError):
        queue.collect(order.order_id)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX process death")
def test_receipt_recovery_rechecks_metadata_before_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A temp mutated after its first read is not silently deleted."""
    from envresearch.workers import recovery as worker_recovery

    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    _leave_receipt_temp_by_process_death(queue, control, order, source)
    temporary = next((control / "receipts" / order.order_id).glob(".tmp-*"))
    original_read = worker_recovery.read_regular_with_identity_at

    def mutate_after_read(
        parent_fd: int,
        name: str,
        *,
        description: str,
        required_mode: int | None = None,
        required_owner: int | None = None,
    ) -> tuple[bytes, tuple[int, int]]:
        result = original_read(
            parent_fd,
            name,
            description=description,
            required_mode=required_mode,
            required_owner=required_owner,
        )
        temporary.chmod(0o644)
        return result

    monkeypatch.setattr(
        worker_recovery, "read_regular_with_identity_at", mutate_after_read
    )

    with pytest.raises(ValueError, match="permissions"):
        queue.collect(order.order_id)
    assert temporary.exists()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX process death")
def test_receipt_recovery_rejects_content_change_at_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Content changed after final authentication is never unlinked."""
    from envresearch.workers import recovery as worker_recovery

    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    _leave_receipt_temp_by_process_death(queue, control, order, source)
    temporary = next((control / "receipts" / order.order_id).glob(".tmp-*"))
    original_unlink = worker_recovery._unlink_validated_at

    def mutate_then_unlink(
        parent_fd: int,
        validated: worker_recovery._ValidatedTemporary,
        owner: int,
    ) -> None:
        data = temporary.read_bytes()
        temporary.write_bytes(b"x" + data[1:])
        original_unlink(parent_fd, validated, owner)

    monkeypatch.setattr(
        worker_recovery, "_unlink_validated_at", mutate_then_unlink
    )

    with pytest.raises(ValueError, match="changed during recovery"):
        queue.collect(order.order_id)
    assert temporary.exists()


def test_receipt_recovery_bounds_the_entire_namespace(tmp_path: Path) -> None:
    """The receipt scan stops before materializing an unbounded namespace."""
    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order = work_order()
    queue.issue(order)
    receipt_directory = control / "receipts" / order.order_id
    receipt_directory.mkdir()
    for index in range(257):
        (receipt_directory / f"fake-{index}.json").write_text(
            "{}", encoding="utf-8"
        )

    with pytest.raises(ValueError, match="too many protected namespace entries"):
        queue.collect(order.order_id)


def test_failed_cleanup_does_not_follow_swapped_staging_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure cleanup must never delete through a replaced staging pathname."""
    from envresearch.workers import filesystem as worker_fs

    exchange = tmp_path / "exchange"
    queue = FilesystemWorkerQueue(exchange)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    artifacts = exchange / "artifacts"
    artifacts.mkdir()
    sentinel = artifacts / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    original_rename = worker_fs._rename_directory_noreplace
    swapped = False

    def swap_then_fail(
        source_fd: int, source_name: str, destination_fd: int, destination: str
    ) -> None:
        nonlocal swapped
        if not source_name.startswith("txn-"):
            original_rename(source_fd, source_name, destination_fd, destination)
            return
        staging = (
            exchange / "worker-submissions" / order.order_id / ".staging"
        )
        staging.rename(staging.parent / "opened-staging")
        staging.symlink_to(artifacts, target_is_directory=True)
        swapped = True
        raise OSError("injected publication failure")

    monkeypatch.setattr(worker_fs, "_rename_directory_noreplace", swap_then_fail)

    with pytest.raises(OSError, match="injected publication failure"):
        queue.submit(order.order_id, source)

    assert swapped
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list(
        (
            exchange
            / "worker-submissions"
            / order.order_id
            / "opened-staging"
        ).iterdir()
    ) == []


def test_control_secret_and_anchors_are_not_public(tmp_path: Path) -> None:
    """Signing material stays mode-restricted and outside exchange records."""
    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order = work_order()
    queue.issue(order)
    source = exchange / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    queue.submit(order.order_id, source)

    key_path = control / "queue.key"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    for public_json in exchange.rglob("*.json"):
        payload = public_json.read_text(encoding="utf-8")
        assert "queue.key" not in payload
        assert '"mac"' not in payload


def test_control_root_cannot_contain_exchange_root(tmp_path: Path) -> None:
    """Opened control and exchange directories must be strictly disjoint."""
    with pytest.raises(ValueError, match="control root must be separate"):
        FilesystemWorkerQueue(tmp_path / "exchange", control_root=tmp_path)
    equal = tmp_path / "equal"
    with pytest.raises(ValueError, match="control root must be separate"):
        FilesystemWorkerQueue(equal, control_root=equal)
    exchange = tmp_path / "outer-exchange"
    with pytest.raises(ValueError, match="control root must be separate"):
        FilesystemWorkerQueue(exchange, control_root=exchange / "control")


def test_control_root_swap_cannot_expose_queue_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A final-entry symlink race must fail before protected state is initialized."""
    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    original_resolve = Path.resolve
    armed = True

    def swap_after_preflight(path: Path, strict: bool = False) -> Path:
        nonlocal armed
        resolved = original_resolve(path, strict=strict)
        if armed and Path(os.path.abspath(path)) == control:
            control.symlink_to(exchange, target_is_directory=True)
            armed = False
        return resolved

    monkeypatch.setattr(Path, "resolve", swap_after_preflight)

    with pytest.raises(ValueError, match="symlink|separate"):
        FilesystemWorkerQueue(exchange, control_root=control)

    assert not (exchange / "queue.key").exists()
    assert not (exchange / "orders").exists()


def test_control_root_rejects_preexisting_final_symlink(tmp_path: Path) -> None:
    """The control boundary itself must be a real directory entry, not an alias."""
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    control = tmp_path / "control"
    control.symlink_to(exchange, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        FilesystemWorkerQueue(exchange, control_root=control)

    assert not (exchange / "queue.key").exists()


def test_control_root_replacement_cannot_split_descriptor_lock(
    tmp_path: Path,
) -> None:
    """All locks from one pinned controller must target the original lock inode."""
    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order_id = "order-map-literature"

    with queue.control.order_lock(order_id):
        control.rename(tmp_path / "opened-control")
        control.mkdir()
        with (
            pytest.raises(TimeoutError, match="lock"),
            queue.control.order_lock(order_id, timeout=0),
        ):
            pytest.fail("replacement control path split the lock")


def test_descriptor_lock_fails_closed_on_unsupported_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unavailable native locking must report ENOTSUP before opening a path."""
    from envresearch.workers import native as worker_native

    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    monkeypatch.setattr(worker_native.sys, "platform", "win32")
    try:
        with (
            pytest.raises(OSError, match="unsupported") as error,
            worker_native.locked_regular_at(descriptor, "lock.file"),
        ):
            pytest.fail("unsupported platform acquired a lock")
    finally:
        os.close(descriptor)

    assert error.value.errno == errno.ENOTSUP


def test_submit_rejects_symlink_and_nonregular_sources(tmp_path: Path) -> None:
    """Symlinks and special files can redirect, block, or fabricate copied bytes."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    target = tmp_path / "real.json"
    target.write_text("{}", encoding="utf-8")
    symlink = tmp_path / "candidate.json"
    symlink.symlink_to(target)

    with pytest.raises(ValueError, match="regular non-symlink"):
        queue.submit(order.order_id, symlink)

    symlink.unlink()
    symlink.mkdir()
    with pytest.raises(ValueError, match="regular non-symlink"):
        queue.submit(order.order_id, symlink)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_submit_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    """Opening an attacker-controlled FIFO must fail promptly instead of hanging."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    fifo = tmp_path / "candidate.json"
    os.mkfifo(fifo)

    with pytest.raises(ValueError, match="regular non-symlink"):
        queue.submit(order.order_id, fifo)


def test_duplicate_submission_is_idempotent_and_conflict_preserves_bytes(
    tmp_path: Path,
) -> None:
    """Retries may repeat exact bytes; changed bytes must not replace a receipt."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    candidate = tmp_path / "candidate.json"
    candidate.write_bytes(b"first")

    first = queue.submit(order.order_id, candidate)
    second = queue.submit(order.order_id, candidate)
    candidate.write_bytes(b"changed")

    assert second == first
    with pytest.raises(RuntimeError, match="submission conflict"):
        queue.submit(order.order_id, candidate)
    assert (tmp_path / first.relative_path).read_bytes() == b"first"


def test_collect_returns_deterministic_filename_order(tmp_path: Path) -> None:
    """Filesystem enumeration order must not change orchestration decisions."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order(output_filenames=("z.json", "a.json"))
    queue.issue(order)
    for name in ("z.json", "a.json"):
        candidate = tmp_path / name
        candidate.write_text(name, encoding="utf-8")
        queue.submit(order.order_id, candidate)

    submissions = queue.collect(order.order_id)

    assert [item.candidate_relative_paths[0].name for item in submissions] == [
        "a.json",
        "z.json",
    ]


def test_collect_rejects_changed_work_order_hash(tmp_path: Path) -> None:
    """Collection must bind submissions to the exact immutable issued task."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order("define-estimand", output_schema="EstimandSpecPayload")
    queue.issue(order)
    _tamper_json(
        tmp_path / "work-orders" / f"{order.order_id}.json", "node_id", "other"
    )

    with pytest.raises(ValueError, match="work order hash mismatch"):
        queue.collect(order.order_id)


def test_collect_rejects_coherently_rehashed_public_order(tmp_path: Path) -> None:
    """A self-consistent public replacement cannot supersede the issued anchor."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    forged_payload = order.model_dump(mode="python", exclude={"order_hash"})
    forged_payload["node_id"] = "forged-node"
    forged = WorkOrder.model_validate(forged_payload)
    _write_canonical_json(
        tmp_path / "work-orders" / f"{order.order_id}.json",
        forged.model_dump(mode="json"),
    )

    with pytest.raises(ValueError, match="anchor|authentic"):
        queue.collect(order.order_id)


def test_collect_rejects_coherently_forged_public_receipt(tmp_path: Path) -> None:
    """Worker-controlled candidate and receipt hashes cannot forge queue authorship."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    source = tmp_path / "candidate.json"
    source.write_text("original", encoding="utf-8")
    record = queue.submit(order.order_id, source)
    stored = tmp_path / record.relative_path
    forged_bytes = b"worker-forged"
    stored.write_bytes(forged_bytes)
    receipt_paths = [
        path
        for path in (tmp_path / "worker-submissions" / order.order_id).rglob("*.json")
        if path != stored
    ]
    assert len(receipt_paths) == 1
    receipt_payload = json.loads(receipt_paths[0].read_text(encoding="utf-8"))
    receipt_payload["candidate_sha256"] = [
        hashlib.sha256(forged_bytes).hexdigest()
    ]
    _write_canonical_json(receipt_paths[0], receipt_payload)

    with pytest.raises(ValueError, match="anchor|authentic"):
        queue.collect(order.order_id)


def test_collect_rejects_directly_forged_public_transaction(tmp_path: Path) -> None:
    """A canonical public transaction is not authoritative without queue intent."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    candidate = b"worker-authored"
    relative = (
        Path("worker-submissions")
        / order.order_id
        / "transactions"
        / "candidate.json.submission"
        / "candidate.json"
    )
    submission = WorkerSubmission(
        order_id=order.order_id,
        order_hash=order.order_hash,
        producer=ProducerIdentity(component="worker", version="1.0"),
        candidate_relative_paths=(relative,),
        candidate_sha256=(hashlib.sha256(candidate).hexdigest(),),
        claimed_schema=order.expected_output_schema,
        submitted_at=datetime.now(UTC),
    )
    transaction = (tmp_path / relative).parent
    transaction.mkdir(parents=True)
    (transaction / relative.name).write_bytes(candidate)
    _write_canonical_json(
        transaction / "receipt.json", submission.model_dump(mode="json")
    )

    with pytest.raises(ValueError, match="anchor authentication missing"):
        queue.collect(order.order_id)


@pytest.mark.parametrize("deleted_entry", ["candidate", "receipt", "transaction"])
def test_collect_rejects_deleted_published_transaction_state(
    tmp_path: Path, deleted_entry: str
) -> None:
    """Protected submission intent makes every published transaction mandatory."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    source = tmp_path / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    record = queue.submit(order.order_id, source)
    candidate = tmp_path / record.relative_path
    receipt = candidate.parent / "receipt.json"
    if deleted_entry == "candidate":
        candidate.unlink()
    elif deleted_entry == "receipt":
        receipt.unlink()
    else:
        candidate.unlink()
        receipt.unlink()
        candidate.parent.rmdir()

    with pytest.raises(ValueError, match="submission transaction is incomplete"):
        queue.collect(order.order_id)


def test_collect_revalidates_authenticated_control_anchor(tmp_path: Path) -> None:
    """Even protected state must fail closed if its authenticated bytes change."""
    exchange = tmp_path / "exchange"
    control = tmp_path / "control"
    queue = FilesystemWorkerQueue(exchange, control_root=control)
    order = work_order()
    queue.issue(order)
    anchors = list(control.rglob("*.json"))
    assert len(anchors) == 1
    _tamper_json(anchors[0], "record_sha256", "0" * 64)

    with pytest.raises(ValueError, match="control anchor authentication"):
        queue.collect(order.order_id)


@pytest.mark.parametrize("tamper_target", ["manifest-hash", "file-bytes", "schema"])
def test_collect_revalidates_every_submission_byte(
    tmp_path: Path, tamper_target: str
) -> None:
    """Changing a manifest, schema claim, or candidate after submit is corruption."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}", encoding="utf-8")
    record = queue.submit(order.order_id, candidate)
    receipt_path = (tmp_path / record.relative_path).parent / "receipt.json"

    if tamper_target == "manifest-hash":
        _tamper_json(receipt_path, "candidate_sha256", ["0" * 64])
    elif tamper_target == "file-bytes":
        (tmp_path / record.relative_path).write_text("changed", encoding="utf-8")
    else:
        _tamper_json(receipt_path, "claimed_schema", "OtherPayload")

    with pytest.raises(ValueError, match="submission (hash|schema) mismatch"):
        queue.collect(order.order_id)


def test_collect_rejects_symlinked_candidate(tmp_path: Path) -> None:
    """A post-submit symlink swap cannot redirect collection to other bytes."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}", encoding="utf-8")
    record = queue.submit(order.order_id, candidate)
    stored = tmp_path / record.relative_path
    stored.unlink()
    stored.symlink_to(candidate)

    with pytest.raises(ValueError, match="regular non-symlink"):
        queue.collect(order.order_id)


def test_collect_rejects_unreceipted_candidate_file(tmp_path: Path) -> None:
    """Workers cannot bypass queue receipts by placing bytes in the inbox directly."""
    queue = FilesystemWorkerQueue(tmp_path)
    order = work_order()
    queue.issue(order)
    injected = (
        tmp_path
        / "worker-submissions"
        / order.order_id
        / "candidates"
        / "candidate.json"
    )
    injected.parent.mkdir(parents=True)
    injected.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="submission path mismatch"):
        queue.collect(order.order_id)


def test_worker_submission_contract_rejects_non_utc_and_forged_paths() -> None:
    """Receipts must remain portable and confined even outside queue construction."""
    order = work_order()
    payload = {
        "order_id": order.order_id,
        "order_hash": order.order_hash,
        "producer": ProducerIdentity(component="worker", version="1.0"),
        "candidate_relative_paths": (
            Path("worker-submissions")
            / order.order_id
            / "transactions"
            / "candidate.json.submission"
            / "candidate.json",
        ),
        "candidate_sha256": ("b" * 64,),
        "claimed_schema": order.expected_output_schema,
        "submitted_at": datetime(2026, 8, 5, tzinfo=UTC).replace(tzinfo=None),
    }

    with pytest.raises(ValidationError, match="UTC-aware"):
        WorkerSubmission.model_validate(payload)

    payload["submitted_at"] = datetime(2026, 8, 5, tzinfo=UTC)
    payload["candidate_relative_paths"] = (Path("../candidate.json"),)
    with pytest.raises(ValidationError, match="candidate path"):
        WorkerSubmission.model_validate(payload)

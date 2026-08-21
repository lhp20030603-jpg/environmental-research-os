"""Private author and derived evidence persistence for replication service."""

from __future__ import annotations

import json
from pathlib import Path

from envresearch.benchmarks.compare import ComparisonStatus, compare_output
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark import ExpectedOutput
from envresearch.replication._evidence_support import persist_execution_log
from envresearch.replication._raw_evidence import persist_raw_output
from envresearch.replication._service_models import ReplicationFault
from envresearch.replication._service_support import (
    execution_evidence,
    persist_output_result,
    persist_payload,
)
from envresearch.replication.container import ContainerPlan, ContainerResult
from envresearch.replication.contracts import Tier2IntakeProposal
from envresearch.replication.did_r import parse_derived_report
from envresearch.replication.ledger import OutputResult, ReplicationRun
from envresearch.storage.hashing import sha256_file
from envresearch.storage.research_artifacts import ResearchArtifactStore


def persist_author_outputs(
    store: ResearchArtifactStore,
    run: ReplicationRun,
    proposal: Tier2IntakeProposal,
    plan: ContainerPlan,
    result: ContainerResult,
) -> tuple[OutputResult, ...]:
    inputs = (run.approved_intake_ref, run.acquired_inventory_ref, run.runtime_ref)
    log_ref = persist_execution_log(store, "author-reproduction", result, inputs)
    records: list[OutputResult] = []
    for output in proposal.expected_outputs:
        spec = ExpectedOutput(
            path=Path(output.path),
            comparator=output.comparator,
            expected_path=Path(output.expected_path),
            absolute_tolerance=output.absolute_tolerance,
            relative_tolerance=output.relative_tolerance,
        )
        comparison = compare_output(plan.output_root, plan.input_root, spec)
        if comparison.status is not ComparisonStatus.MATCHED:
            raise ReplicationFault(
                "OUTPUT_MISMATCH",
                f"{output.path} comparison {comparison.status.value}",
                inputs,
            )
        raw_ref = persist_raw_output(
            store,
            output.path,
            plan.output_root / output.path,
            inputs,
            log_ref,
            max_bytes=proposal.budget.max_storage_bytes,
        )
        records.append(
            persist_output_result(
                store,
                output.path,
                sha256_file(plan.output_root / output.path),
                output.comparator,
                inputs,
                log_ref,
                raw_ref,
                execution_evidence(plan, result),
            )
        )
    return tuple(records)


def persist_derived_output(
    store: ResearchArtifactStore,
    run: ReplicationRun,
    outputs: tuple[OutputResult, ...],
    plan: ContainerPlan,
    result: ContainerResult,
) -> tuple[ArtifactRef, ArtifactRef]:
    path = plan.output_root / (
        "derived-did-event-study/derived-did-event-study-v1.json"
    )
    try:
        parsed = parse_derived_report(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError) as error:
        raise ReplicationFault("DERIVED_OUTPUT_INVALID", str(error)) from error
    inputs = (
        run.approved_intake_ref,
        run.acquired_inventory_ref,
        run.runtime_ref,
        *(item.artifact_ref for item in outputs),
    )
    log_ref = persist_execution_log(
        store, "derived-did-event-study", result, inputs[:3]
    )
    reference = persist_payload(
        store,
        "tier2-derived-output",
        "derived",
        parsed.model_dump(mode="json", exclude={"reproduction_result"}),
        (*inputs, log_ref),
        "tier2-replication",
        provenance=execution_evidence(plan, result),
    )
    return reference, log_ref

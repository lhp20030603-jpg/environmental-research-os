"""Construct blind orders only after authenticating frozen enrollment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from envresearch.benchmarks.blind_enrollment_marker import require_frozen_enrollment
from envresearch.models.artifact import ArtifactRef
from envresearch.models.principal import PrincipalAssignment
from envresearch.research.order_policy import blind_order_constraints
from envresearch.workers.contracts import WorkerRole, WorkOrder

if TYPE_CHECKING:
    from envresearch.research.order_issuance import BlindControllerInfrastructure


def build_blind_order(
    infrastructure: BlindControllerInfrastructure,
    order_id: str,
    role: WorkerRole,
    inputs: tuple[ArtifactRef, ...],
    schema: str,
    filename: str,
    assignment: PrincipalAssignment,
) -> WorkOrder:
    require_frozen_enrollment(infrastructure.registry, infrastructure.case_id)
    return WorkOrder(
        order_id=order_id,
        node_id=order_id,
        node_version=f"generation-{infrastructure._source_generation()}",
        role=role,
        input_artifacts=inputs,
        expected_output_schema=schema,
        expected_output_filenames=(filename,),
        policy_constraints=blind_order_constraints(role),
        evidence_requirements=("Retain opaque fact references",),
        principal_assignment=assignment,
    )

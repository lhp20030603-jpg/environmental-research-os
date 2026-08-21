"""Small support-artifact and public-projection helpers for blind controllers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from envresearch.benchmarks.blind_public_projection import public_brief_payload
from envresearch.models.artifact import ProducerIdentity
from envresearch.models.benchmark_blinding import BlindedBrief, LeakageReport
from envresearch.research.order_policy import blind_expert_rubric, blind_profile_payload

if TYPE_CHECKING:
    from envresearch.research.order_issuance import BlindControllerInfrastructure


def ensure_support(infra: BlindControllerInfrastructure, method_root: Path) -> None:
    from envresearch.benchmarks.blind_enrollment_marker import require_frozen_enrollment

    require_frozen_enrollment(infra.registry, infra.case_id)
    infra.artifacts.lifecycle.persist_structured(
        infra._profile_path(),
        blind_profile_payload(method_root),
        ProducerIdentity(component="method-profile-registry", version="0.2.0"),
        (),
    )
    infra.artifacts.lifecycle.persist_structured(
        infra._rubric_path(),
        blind_expert_rubric(),
        ProducerIdentity(component="blind-expert-rubric", version="1.0"),
        (),
    )


def profile_payload(infra: BlindControllerInfrastructure) -> dict[str, object]:
    payload = infra.artifacts.lifecycle.read_artifact(infra._profile_path()).payload
    if not isinstance(payload, dict):
        raise TypeError("method profile registry artifact is invalid")
    return cast(dict[str, object], payload)


def public_brief(infra: BlindControllerInfrastructure) -> dict[str, object]:
    brief = infra.artifacts.lifecycle.read_payload(
        infra.artifacts.paths(infra.case_id).blinded_brief, BlindedBrief
    )
    return public_brief_payload(brief)


def public_leakage(infra: BlindControllerInfrastructure) -> dict[str, object]:
    report = infra.artifacts.lifecycle.read_payload(
        infra.artifacts.paths(infra.case_id).leakage_report, LeakageReport
    )
    return cast(
        dict[str, object],
        report.model_dump(
            mode="json", exclude={"source_sheet_ref", "validator_principal"}
        ),
    )

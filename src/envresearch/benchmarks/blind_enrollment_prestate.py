"""Fail-closed prestate scan for enrollment as the first run transition."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from envresearch.research.order_issuance import BlindControllerInfrastructure


def require_enrollment_prestate(
    infrastructure: BlindControllerInfrastructure, *, allow_partial: bool
) -> None:
    case_id = infrastructure.case_id
    case_digest = hashlib.sha256(case_id.encode()).hexdigest()
    control = Path("control/queues/recommender") / case_id
    exchange = Path("exchanges/recommender") / case_id
    allowed_directories = {
        Path("control"),
        Path("control/queues"),
        Path("control/queues/recommender"),
        control,
        control / "locks",
        control / "orders",
        control / "receipts",
        control / "principals",
        control / "principals/gates",
        Path("exchanges"),
        Path("exchanges/recommender"),
        exchange,
    }
    allowed_files = {
        control / "queue.key",
        control / "principals/gate.capability",
        control / "principals/revision.capability",
        control / "principals/blind-authority-trust-anchor.json",
        control
        / "locks"
        / f"research-blind-enrollment-{case_digest}.filelock",
        control / "locks" / f"research-blind-case-{case_digest}.filelock",
    }
    if allow_partial:
        benchmark = control / "principals/benchmark"
        enrolled = benchmark / case_id
        allowed_directories.update((benchmark, enrolled))
        allowed_files.update(
            (
                enrolled / "signed-enrollment.json",
                enrolled / "human-keys.json",
            )
        )
    root = infrastructure.run_root
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError("enrollment first transition found aliased run state")
        if path.is_dir():
            allowed = relative in allowed_directories
        else:
            allowed = relative in allowed_files
        if not allowed:
            raise ValueError("enrollment must be the first authenticated run transition")

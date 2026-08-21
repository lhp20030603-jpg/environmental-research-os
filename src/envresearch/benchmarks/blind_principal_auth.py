"""Protected principal authentication for blind artifact persistence."""

from __future__ import annotations

from pathlib import Path

from envresearch.models.artifact import ProducerIdentity
from envresearch.models.principal import PrincipalAssignment, PrincipalKind
from envresearch.research.principal_registry import PrincipalRegistry


class PrincipalAuthenticator:
    """Reuse protected registry records without creating a second authority."""

    def __init__(self, registry: PrincipalRegistry) -> None:
        self.registry = registry

    def require_assignment(
        self,
        case_id: str,
        supplied: PrincipalAssignment,
        kind: PrincipalKind,
        slot: int | None,
    ) -> PrincipalAssignment:
        if supplied.kind is not kind:
            raise ValueError("principal role mismatch")
        if kind in {PrincipalKind.EXPERT, PrincipalKind.ADJUDICATOR}:
            if slot is None:
                raise ValueError("benchmark human slot is required")
            path = self._directory(case_id) / f"{kind.value}-{slot}.json"
            durable = self.registry._read_benchmark_assignment(path)
            if durable != supplied:
                raise ValueError("principal assignment authentication failed")
        else:
            durable = self.registry.require_benchmark_role(supplied, kind)
        if kind in {PrincipalKind.EXPERT, PrincipalKind.ADJUDICATOR}:
            expected = self.registry.enrolled_human_key(
                case_id, kind, slot or 0
            ).principal_id
        else:
            expected = f"principal-{case_id}-{kind.value}"
        if durable.principal_id != expected:
            raise ValueError("case lineage mismatch")
        return durable

    def require_producer(
        self,
        case_id: str,
        producer: ProducerIdentity,
        kind: PrincipalKind,
        slot: int | None,
    ) -> PrincipalAssignment:
        directory = self._directory(case_id)
        if kind in {PrincipalKind.EXPERT, PrincipalKind.ADJUDICATOR}:
            if slot is None:
                raise ValueError("benchmark human slot is required")
            names: tuple[str, ...] = (f"{kind.value}-{slot}.json",)
        else:
            names = tuple(
                name
                for name in self.registry.control.storage.list_directory(directory)
                if name.startswith(f"{kind.value}-g") and name.endswith(".json")
            )
        for name in names:
            assignment = self.registry._read_benchmark_assignment(directory / name)
            if assignment.producer == producer:
                return self.require_assignment(case_id, assignment, kind, slot)
        raise ValueError("artifact producer role is not authenticated")

    @staticmethod
    def _directory(case_id: str) -> Path:
        return Path("principals/benchmark") / case_id

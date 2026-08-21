"""Repository-owned connectors used only by deterministic design fixtures."""

from __future__ import annotations

from envresearch.connectors.contracts import (
    ConnectorUnavailable,
    LiteratureQuery,
)
from envresearch.models.evidence import SourceRecord


class RepositoryUnavailableLiteratureConnector:
    """Deterministically model one missing local literature export."""

    connector_id = "repository-local-literature"
    connector_version = "1.0"

    def search(self, query: LiteratureQuery) -> tuple[SourceRecord, ...]:
        """Raise the approved outage condition without external I/O."""
        del query
        raise ConnectorUnavailable(
            connector_id=self.connector_id,
            reason_code="EXPORT_MISSING",
            diagnostic="repository literature export is intentionally unavailable",
        )

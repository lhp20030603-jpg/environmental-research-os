"""Normalized literature coverage at the connector boundary."""

from __future__ import annotations

from envresearch.connectors.contracts import (
    ConnectorCoverage,
    ConnectorUnavailable,
    LiteratureConnector,
    LiteratureQuery,
)


class LiteratureGateway:
    """Normalize a literature connector result into an explicit coverage state."""

    def literature_search(
        self, connector: LiteratureConnector, query: LiteratureQuery
    ) -> ConnectorCoverage:
        """Return records or a deterministic degraded state for known outages only."""
        try:
            records = connector.search(query)
        except ConnectorUnavailable as error:
            if error.connector_id != connector.connector_id or not error.is_degradable:
                raise
            return ConnectorCoverage(
                connector_id=connector.connector_id,
                connector_version=connector.connector_version,
                status="degraded",
                records=(),
                reason_code="CONNECTOR_UNAVAILABLE",
                connector_reason_code=error.reason_code,
                diagnostic=error.diagnostic,
            )
        return ConnectorCoverage(
            connector_id=connector.connector_id,
            connector_version=connector.connector_version,
            status="complete",
            records=records,
        )


def literature_gateway() -> LiteratureGateway:
    """Construct the stateless gateway used by planning-only literature calls."""
    return LiteratureGateway()

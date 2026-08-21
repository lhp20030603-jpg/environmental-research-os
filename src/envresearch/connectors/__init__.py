"""Connector contracts and their progressive acquisition gateway."""

from envresearch.connectors.acquisition_store import AcquisitionStore
from envresearch.connectors.contracts import (
    AcquisitionAuditRecord,
    ConnectorCoverage,
    ConnectorReceipt,
    ConnectorUnavailable,
    DataConnector,
    DataRisk,
    LiteratureConnector,
    LiteratureQuery,
    MeasuredAcquisition,
)
from envresearch.connectors.gateway import (
    ConditionalDataGateRequired,
    ConnectorGateway,
    LiteratureGateway,
    literature_gateway,
)
from envresearch.connectors.zotero_export import ZoteroExportConnector

__all__ = [
    "AcquisitionAuditRecord",
    "AcquisitionStore",
    "ConditionalDataGateRequired",
    "ConnectorCoverage",
    "ConnectorGateway",
    "ConnectorReceipt",
    "ConnectorUnavailable",
    "DataConnector",
    "DataRisk",
    "LiteratureConnector",
    "LiteratureGateway",
    "LiteratureQuery",
    "MeasuredAcquisition",
    "ZoteroExportConnector",
    "literature_gateway",
]

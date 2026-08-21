"""Conditional human-gate signal for connector acquisitions."""

from __future__ import annotations

from envresearch.connectors.contracts import ConnectorReceipt


class ConditionalDataGateRequired(RuntimeError):
    """Signal that a human must approve a conditional data acquisition path."""

    def __init__(
        self, reasons: tuple[str, ...], receipt: ConnectorReceipt | None = None
    ) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons
        self.receipt = receipt

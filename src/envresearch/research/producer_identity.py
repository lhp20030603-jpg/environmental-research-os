"""Authenticated worker identity rules for promoted research artifacts."""

from __future__ import annotations

from envresearch.models.artifact import ProducerIdentity
from envresearch.workers.contracts import revalidate_producer_identity


def authenticated_producer(identity: ProducerIdentity) -> ProducerIdentity:
    """Revalidate the queue-authenticated receipt identity before promotion."""
    durable = revalidate_producer_identity(identity)
    if durable.context_id is None:
        raise ValueError("queue producer requires an authenticated context_id")
    return durable


def require_independent_critic(
    critic: ProducerIdentity,
    reviewed: tuple[ProducerIdentity, ...],
) -> None:
    """Require a named critic context distinct from every reviewed worker context."""
    durable = authenticated_producer(critic)
    if durable.context_id is None:
        raise ValueError("design critic requires an authenticated context_id")
    unknown = tuple(
        producer.component for producer in reviewed if producer.context_id is None
    )
    if unknown:
        raise ValueError("reviewed upstream worker context is unknown")
    upstream_contexts = {producer.context_id for producer in reviewed}
    if durable.context_id in upstream_contexts:
        raise ValueError("design critic context must differ from reviewed producers")

"""Private raw-byte checks for the independent replication verifier."""

from __future__ import annotations

import tarfile

from envresearch.models.artifact import ResearchArtifact
from envresearch.replication._raw_evidence import (
    require_acquired_bytes,
    require_raw_output,
)
from envresearch.replication._service_support import restore_proposal
from envresearch.replication._verification_models import VerificationFinding, restore
from envresearch.replication.contracts import AcquiredPackageInventory
from envresearch.replication.ledger import ReplicationRun
from envresearch.storage.research_artifacts import ResearchArtifactStore


def raw_evidence_finding(
    store: ResearchArtifactStore,
    ledger: ResearchArtifact[ReplicationRun],
    proposal: ResearchArtifact[object] | None,
    inventory: ResearchArtifact[object] | None,
) -> VerificationFinding | None:
    if proposal is None or inventory is None:
        return None
    try:
        admitted = restore_proposal(proposal.payload)
        acquired = restore(AcquiredPackageInventory, inventory.payload)
        input_root = require_acquired_bytes(store, acquired)
        declarations = {item.path: item for item in admitted.expected_outputs}
        for result in ledger.payload.author_outputs:
            declaration = declarations.get(result.path)
            if declaration is None:
                raise ValueError("raw output lacks an approved declaration")
            require_raw_output(store, ledger.payload, result, declaration, input_root)
    except (OSError, tarfile.TarError, TypeError, ValueError) as error:
        return VerificationFinding(code="RAW_EVIDENCE_INVALID", message=str(error))
    return None

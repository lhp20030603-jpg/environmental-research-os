"""Current-pointer authentication for shared exit artifacts."""

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from envresearch.econometrics.exit_registry import ExitRegistry


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    value: str


def test_current_authenticates_referenced_object_hash(tmp_path: Path) -> None:
    registry = ExitRegistry((tmp_path / "registry").resolve())
    reference = registry.publish("payload", _Payload(value="accepted"))
    registry.set_current("payload", reference)
    relative = (
        Path("exit/objects")
        / reference.artifact_id
        / f"v1-{reference.content_hash}.json"
    )
    path = registry.root / relative
    path.chmod(0o600)
    path.write_bytes(b'{"value":"mutated"}')

    with pytest.raises(ValueError, match="content hash mismatch"):
        registry.current("payload")

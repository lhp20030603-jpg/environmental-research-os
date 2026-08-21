"""JSON artifact storage with integrity metadata."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from envresearch.storage.atomic import atomic_write_bytes
from envresearch.storage.hashing import sha256_file
from envresearch.storage.paths import safe_join


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """Metadata describing one persisted artifact."""

    relative_path: Path
    sha256: str
    size_bytes: int
    written_at: datetime


class ArtifactStore:
    """Persist derived JSON artifacts inside a single workspace root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def write_json(
        self, relative: Path, payload: Mapping[str, object]
    ) -> ArtifactRecord:
        """Serialize and atomically write a derived JSON artifact."""
        if _targets_raw(relative):
            raise PermissionError("immutable raw directory")
        path = safe_join(self.root, relative)
        normalized_relative = path.relative_to(self.root)
        if _targets_raw(normalized_relative):
            raise PermissionError("immutable raw directory")

        data = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        atomic_write_bytes(path, data)
        return ArtifactRecord(
            relative_path=normalized_relative,
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            written_at=datetime.now(UTC),
        )

    def read_json(self, relative: Path) -> dict[str, object]:
        """Read a JSON object that remains confined to the workspace."""
        path = safe_join(self.root, relative)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError("artifact JSON must contain an object")
        return value


def _targets_raw(relative: Path) -> bool:
    """Reserve the immutable raw namespace across filesystem case rules."""
    return bool(relative.parts) and relative.parts[0].casefold() == "raw"

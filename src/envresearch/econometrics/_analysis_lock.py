"""Process-safe serialization for one content-addressed local analysis."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import ClassVar

from envresearch.econometrics._store_files import StoreFiles


class AnalysisCoordinator:
    """Serialize recovery, execution, verification, and publication by ID."""

    _threads: ClassVar[dict[tuple[Path, str], Lock]] = {}

    def __init__(self, root: Path, analysis_id: str) -> None:
        self.root = root
        self.analysis_id = analysis_id

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold both the in-process and cooperating-process analysis lock."""
        path = Path("analyses") / ".locks" / f"{self.analysis_id}.lock"
        thread = self._threads.setdefault((self.root, self.analysis_id), Lock())
        with thread:
            descriptor = StoreFiles(self.root).open_lock(path)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

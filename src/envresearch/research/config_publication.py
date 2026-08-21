"""Serialized no-follow publication for one explicit research run config."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from envresearch.research.run_binding import serialize_run_config
from envresearch.research.run_config import verify_bound_config_data
from envresearch.research.workflow import ResearchRunConfig
from envresearch.workers.filesystem import PinnedRoot
from envresearch.workers.native import locked_regular_at

CONFIG_COPY = Path("research-run-config.yaml")
INTERNAL_CONFIG = Path("research-run-config.json")
_LOCK_DIRECTORY = Path(".locks")
_LOCK_NAME = "research-init.filelock"


class RunConfigPublication:
    """Pin one run root and serialize fail-if-exists config publication."""

    def __init__(self, workspace: Path) -> None:
        self.storage = PinnedRoot(workspace)
        try:
            self.storage.ensure_directory(_LOCK_DIRECTORY)
            lock_path = _LOCK_DIRECTORY / _LOCK_NAME
            if not self.storage.exists(lock_path):
                try:
                    self.storage.write_file_noreplace(lock_path, b"", mode=0o600)
                except FileExistsError:
                    pass
        except BaseException:
            self.storage.close()
            raise

    def close(self) -> None:
        self.storage.close()

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the verified workspace initialization lock."""
        with (
            self.storage.directory(_LOCK_DIRECTORY) as directory_fd,
            locked_regular_at(directory_fd, _LOCK_NAME, timeout=30),
        ):
            yield

    def read_optional(self, relative: Path, *, description: str) -> bytes | None:
        """Read an existing regular single-link file without following aliases."""
        if not self.storage.exists(relative):
            return None
        return self.storage.read_file(relative, description=description)

    def publish(self, relative: Path, data: bytes) -> None:
        """Publish exact bytes once without replacing a concurrent entry."""
        self.storage.write_file_noreplace(relative, data, mode=0o600)


@contextmanager
def initialization_transaction(
    config: ResearchRunConfig, explicit_config: bytes | None
) -> Iterator[None]:
    """Bind and finally verify all run identity inside one pinned lock."""
    _validate_explicit_identity(config, explicit_config)
    publication = RunConfigPublication(config.workspace)
    internal = serialize_run_config(config)
    try:
        with publication.locked():
            created: list[Path] = []
            try:
                if _bind_exact(
                    publication, INTERNAL_CONFIG, internal, "internal run config"
                ):
                    created.append(INTERNAL_CONFIG)
                if explicit_config is not None and _bind_exact(
                    publication,
                    CONFIG_COPY,
                    explicit_config,
                    "research config copy",
                ):
                    created.append(CONFIG_COPY)
                yield
                _require_exact(
                    publication, INTERNAL_CONFIG, internal, "internal run config"
                )
                if explicit_config is not None:
                    _require_exact(
                        publication,
                        CONFIG_COPY,
                        explicit_config,
                        "research config copy",
                    )
            except BaseException as error:
                _retain_partial_bindings(error, tuple(created))
                raise
    finally:
        publication.close()


def _validate_explicit_identity(
    config: ResearchRunConfig, explicit_config: bytes | None
) -> None:
    if config.config_sha256 is not None and explicit_config is None:
        raise ValueError("explicit config bytes are required for the bound digest")
    if explicit_config is not None:
        verify_bound_config_data(explicit_config, config)


def _retain_partial_bindings(error: BaseException, created: tuple[Path, ...]) -> None:
    """Leave transaction-created paths untouched and explain the safe retry policy."""
    if not created:
        return
    retained = ", ".join(path.as_posix() for path in created)
    error.add_note(
        f"partial config binding retained without deletion: {retained}; "
        "inspect both config identities, explicitly correct the conflicting entry, "
        "then retry only the same run identity; conflicting identities remain blocked"
    )


def _bind_exact(
    publication: RunConfigPublication,
    relative: Path,
    expected: bytes,
    description: str,
) -> bool:
    existing = publication.read_optional(relative, description=description)
    if existing is None:
        publication.publish(relative, expected)
        return True
    elif existing != expected:
        raise ValueError(f"{description} does not match this research run")
    return False


def _require_exact(
    publication: RunConfigPublication,
    relative: Path,
    expected: bytes,
    description: str,
) -> bytes:
    actual = publication.read_optional(relative, description=description)
    if actual is None or actual != expected:
        raise ValueError(f"{description} changed during initialization")
    return actual

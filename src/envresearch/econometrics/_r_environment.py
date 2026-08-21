"""Minimal explicit environment for trusted local R execution."""

from pathlib import Path

from envresearch.econometrics._r_owned_files import (
    RRuntimeInvalid,
    ensure_owned_directory,
)


def minimal_r_environment(
    executable: Path, workspace: Path, *, managed_library: Path | None = None
) -> dict[str, str]:
    """Return the complete environment without inherited operator variables."""
    home = ensure_owned_directory(workspace, "home")
    library = (
        _managed_library(managed_library)
        if managed_library is not None
        else ensure_owned_directory(workspace, "library")
    )
    temporary = ensure_owned_directory(workspace, "tmp")
    environment = {
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": str(executable.parent),
        "R_LIBS_USER": str(library),
        "TMPDIR": str(temporary),
    }
    if managed_library is not None:
        environment["R_LIBS_SITE"] = str(library)
    return environment


def _managed_library(root: Path) -> Path:
    """Require one absolute, existing, non-symlink managed package root."""
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise RRuntimeInvalid("managed R library is not an authenticated directory")
    return root.resolve()

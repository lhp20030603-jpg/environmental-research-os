"""Workspace-confined path handling."""

from pathlib import Path


def require_safe_workspace_root(path: Path) -> Path:
    """Resolve a workspace target and reject every filesystem root pre-write."""
    resolved = path.expanduser().resolve(strict=False)
    if resolved == Path(resolved.anchor):
        raise ValueError("workspace must not be the filesystem root")
    return resolved


def safe_join(root: Path, relative: Path) -> Path:
    """Resolve a workspace-relative path without allowing it to escape *root*."""
    if relative.is_absolute():
        raise ValueError("path must be relative to workspace")
    resolved_root = root.resolve()
    target = (resolved_root / relative).resolve()
    if not target.is_relative_to(resolved_root):
        raise ValueError("path escapes workspace")
    return target

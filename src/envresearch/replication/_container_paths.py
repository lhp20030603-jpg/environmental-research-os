"""Private operator-authority recovery for container workspace roots."""

from pathlib import Path


def workspace_authority(path: Path, kind: str, name: str) -> Path:
    """Recover authority from an exact artifacts/replication workspace shape."""
    for base in path.parents:
        if (
            base.name == kind
            and base.parent.name == "replication"
            and base.parent.parent.name == "artifacts"
        ):
            relative = path.relative_to(base)
            if len(relative.parts) < 2:
                break
            authority = base.parent.parent.parent
            if authority != Path(authority.anchor):
                return authority
            break
    suffix = "acquired input base" if kind == "acquired" else "run output base"
    raise ValueError(f"{name} must be under the trusted {suffix}")

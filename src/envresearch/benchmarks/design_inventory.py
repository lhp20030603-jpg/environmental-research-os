"""Exact authoritative inventory with stable transaction placeholders."""

from __future__ import annotations

from pathlib import Path

_AUTHORITATIVE_ROOT_FILES = frozenset(
    {
        "decision-log.jsonl",
        "events.jsonl",
        "node-checkpoint-events.jsonl",
        "research-run-config.json",
        "research-run-config.yaml",
        "research-run-manifest.json",
    }
)
_AUTHORITATIVE_NAMESPACES = frozenset(
    {
        "artifacts",
        "connector-receipts",
        "gate-contexts",
        "gates",
        "node-checkpoints",
        "revisions",
        "work-orders",
    }
)


def authoritative_inventory(run_root: Path) -> tuple[Path, ...]:
    """Return exact authority shape while normalizing dynamic ID segments."""
    files: list[Path] = []
    for path in run_root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(run_root)
        if ".locks" in relative.parts or path.name.endswith(".filelock"):
            continue
        if (
            len(relative.parts) == 1 and relative.name in _AUTHORITATIVE_ROOT_FILES
        ) or relative.parts[0] in _AUTHORITATIVE_NAMESPACES:
            files.append(relative)
    invalidations, revisions = _inventory_id_maps(files)
    return tuple(
        sorted(
            _canonical_inventory_path(
                relative,
                invalidations=invalidations,
                revisions=revisions,
            )
            for relative in files
        )
    )


def _inventory_id_maps(
    files: list[Path],
) -> tuple[dict[str, str], dict[str, str]]:
    invalidation_nodes: dict[str, set[str]] = {}
    revision_nodes: dict[str, str] = {}
    for relative in files:
        parts = relative.parts
        if (
            len(parts) >= 4
            and parts[:2] == ("node-checkpoints", "superseded")
            and parts[2].startswith("research.node.invalidated.")
        ):
            invalidation_nodes.setdefault(parts[2], set()).add(Path(parts[-1]).stem)
        if (
            len(parts) == 5
            and parts[0] == "revisions"
            and parts[1].startswith("rev-")
            and parts[2:4] == ("worker", "work-orders")
        ):
            revision_nodes[parts[1]] = Path(parts[4]).stem
    grouped_invalidations: dict[str, list[str]] = {}
    for raw_id, nodes in invalidation_nodes.items():
        label = "+".join(sorted(nodes))
        grouped_invalidations.setdefault(label, []).append(raw_id)
    invalidations = {
        raw_id: f"{{invalidation-{label}-{ordinal}}}"
        for label, raw_ids in grouped_invalidations.items()
        for ordinal, raw_id in enumerate(sorted(raw_ids), start=1)
    }
    grouped_revisions: dict[str, list[str]] = {}
    for raw_id, node in revision_nodes.items():
        grouped_revisions.setdefault(node, []).append(raw_id)
    revisions = {
        raw_id: f"{{revision-{node}-{ordinal}}}"
        for node, raw_ids in grouped_revisions.items()
        for ordinal, raw_id in enumerate(sorted(raw_ids), start=1)
    }
    return invalidations, revisions


def _canonical_inventory_path(
    relative: Path,
    *,
    invalidations: dict[str, str],
    revisions: dict[str, str],
) -> Path:
    parts = relative.parts
    if (
        len(parts) >= 4
        and parts[:2] == ("node-checkpoints", "superseded")
        and parts[2].startswith("research.node.invalidated.")
    ):
        placeholder = invalidations[parts[2]]
        return Path(*parts[:2], placeholder, *parts[3:])
    if len(parts) >= 2 and parts[0] == "revisions" and parts[1].startswith("rev-"):
        revision_placeholder = revisions.get(parts[1])
        if revision_placeholder is None:
            raise ValueError("revision inventory is missing its archived work order")
        return Path("revisions", revision_placeholder, *parts[2:])
    return relative

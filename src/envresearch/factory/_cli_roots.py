"""Complete explicit and derived root validation for factory CLI composition."""

from __future__ import annotations

from pathlib import Path

from envresearch.econometrics.exit_registry import validate_separate_roots
from envresearch.factory.errors import FactoryAuthorityInvalid


def validated_roots(
    research_root: Path | None,
    v031_root: Path | None,
    paper_root: Path | None,
    factory_root: Path | None,
) -> tuple[Path, Path, Path, Path]:
    """Validate a four-root envelope plus fixed design/citation authorities."""
    try:
        supplied = (research_root, v031_root, paper_root, factory_root)
        if any(root is None for root in supplied):
            raise ValueError("explicit roots are required")
        lexical = tuple(root.expanduser().absolute() for root in supplied if root)
        for index, left in enumerate(lexical):
            if left.is_symlink():
                raise ValueError("lexical roots must not be symlinks")
            for right in lexical[index + 1 :]:
                validate_separate_roots(left, right)
        research, v031, paper, factory = tuple(
            root.resolve(strict=True) for root in lexical
        )
        design = research / "design"
        citation = research / "citation/research"
        if design.is_symlink() or citation.is_symlink():
            raise ValueError("derived research roots must not be symlinks")
        design = design.resolve(strict=True)
        citation = citation.resolve(strict=True)
        controls = (
            design.parent / f".{design.name}.worker-queue-control",
            citation.parent / f".{citation.name}.worker-queue-control",
        )
        protected = (design, citation, *controls, v031, paper, factory)
        for index, left in enumerate(protected):
            if left.is_symlink() or not left.is_dir():
                raise ValueError("derived authority root is invalid")
            for right in protected[index + 1 :]:
                validate_separate_roots(left, right)
        return research, v031, paper, factory
    except (OSError, ValueError) as exc:
        raise FactoryAuthorityInvalid(
            "factory roots are invalid or overlap",
            finding_kind="root-authority-overlap",
        ) from exc


__all__ = ["validated_roots"]

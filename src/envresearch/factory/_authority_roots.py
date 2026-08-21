"""Pinned physical root identities for composite factory authority."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from envresearch.econometrics.exit_registry import validate_separate_roots
from envresearch.factory.design_resolver import V02ApprovedDesignResolver
from envresearch.factory.errors import FactoryAuthorityInvalid
from envresearch.paper.release import PaperReleaseService


@dataclass(frozen=True, slots=True)
class AuthorityRootManifest:
    """Complete named physical roots participating in factory authority."""

    roots: tuple[tuple[str, Path], ...]
    identities: tuple[tuple[str, tuple[int, int]], ...]

    @classmethod
    def derive(
        cls,
        *,
        design_resolver: V02ApprovedDesignResolver,
        release_service: PaperReleaseService,
    ) -> AuthorityRootManifest:
        """Derive every injected authority root or fail closed before locking."""
        try:
            orchestrator = design_resolver.orchestrator
            audit = release_service.audit_service
            ledger_resolver = audit.ledger_service.resolver
            accepted_root = getattr(ledger_resolver, "authority_root", None)
            if accepted_root is None:
                accepted_root = getattr(ledger_resolver, "run_root", None)
            citations = audit.citation_authority
            lifecycle = getattr(citations, "lifecycle", None)
            attestations = getattr(citations, "attestations", None)
            catalog_roots = getattr(attestations, "authorized_catalog_roots", None)
            if not catalog_roots:
                raise ValueError("citation source authority roots are missing")
            required: list[tuple[str, object]] = [
                ("research", design_resolver.workspace),
                ("research-control", orchestrator.queue.control.path),
                ("accepted-evidence", accepted_root),
                ("paper", release_service.registry.root),
                ("citation", getattr(lifecycle, "workspace", None)),
                (
                    "citation-control",
                    getattr(
                        getattr(getattr(attestations, "queue", None), "control", None),
                        "path",
                        None,
                    ),
                ),
                ("factory", design_resolver.factory_root),
            ]
            required.extend(
                (f"citation-source-{index}", root)
                for index, root in enumerate(catalog_roots)
            )
            if any(value is None for _, value in required):
                raise ValueError("authority root identity is missing")
            roots = tuple((name, Path(value)) for name, value in required)  # type: ignore[arg-type]
            manifest = cls(roots=roots, identities=_identities(roots))
            manifest.require_pairwise_separate()
            return manifest
        except FactoryAuthorityInvalid:
            raise
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            raise FactoryAuthorityInvalid(
                "factory authority root manifest is missing or invalid",
                finding_kind="root-authority-invalid",
            ) from exc

    def require_pairwise_separate(self) -> None:
        """Reject same, nested, missing, non-directory, or symlink-alias roots."""
        for name, root in self.roots:
            if not root.is_absolute() or root.is_symlink() or not root.is_dir():
                raise ValueError(f"{name} authority root is invalid")
        for index, (_, left) in enumerate(self.roots):
            for _, right in self.roots[index + 1 :]:
                validate_separate_roots(left, right)

    def require_current(self) -> None:
        """Require every lexical root to retain its pinned physical identity."""
        self.require_pairwise_separate()
        if _identities(self.roots) != self.identities:
            raise FactoryAuthorityInvalid(
                "factory authority root identity changed",
                finding_kind="root-authority-invalid",
            )


def _identities(
    roots: tuple[tuple[str, Path], ...],
) -> tuple[tuple[str, tuple[int, int]], ...]:
    return tuple(
        (name, (metadata.st_dev, metadata.st_ino))
        for name, root in roots
        for metadata in (os.stat(root, follow_symlinks=False),)
    )


__all__ = ["AuthorityRootManifest"]

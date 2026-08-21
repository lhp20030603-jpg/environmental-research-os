"""Stable public errors for governed research-factory adapters."""

from __future__ import annotations

from typing import ClassVar


class FactoryError(ValueError):
    """Base error with a stable machine-readable code and finding kind."""

    code: ClassVar[str]

    def __init__(self, message: str, *, finding_kind: str) -> None:
        super().__init__(message)
        self.finding_kind = finding_kind


class FactoryAuthorityInvalid(FactoryError):
    """Raised when a required factory authority cannot be held."""

    code = "FACTORY_AUTHORITY_INVALID"


class FactoryIntegrityInvalid(FactoryError):
    """Raised when immutable factory state cannot be authenticated."""

    code = "FACTORY_INTEGRITY_INVALID"


class FactorySupportInvalid(FactoryError):
    """Raised when required source evidence cannot support a handoff."""

    code = "FACTORY_SUPPORT_INVALID"


class FactoryScopeExceeded(FactoryError):
    """Raised when a request exceeds the V0.2 approved-design boundary."""

    code = "FACTORY_SCOPE_EXCEEDED"

"""Stable public Paper Builder failure categories."""

from typing import ClassVar


class PaperBuilderError(ValueError):
    """Base class for one fail-closed paper construction finding."""

    code: ClassVar[str]

    def __init__(self, message: str, *, finding_kind: str) -> None:
        super().__init__(message)
        self.finding_kind = finding_kind


class PaperAuthorityInvalid(PaperBuilderError):
    """An exact upstream or current authority no longer matches."""

    code = "PAPER_AUTHORITY_INVALID"


class PaperIntegrityInvalid(PaperBuilderError):
    """Authenticated bytes or reconstruction evidence changed."""

    code = "PAPER_INTEGRITY_INVALID"


class PaperSupportInvalid(PaperBuilderError):
    """A proposed claim lacks coherent accepted evidence."""

    code = "PAPER_SUPPORT_INVALID"


class PaperScopeExceeded(PaperBuilderError):
    """A claim or policy statement exceeds its evidence boundary."""

    code = "PAPER_SCOPE_EXCEEDED"

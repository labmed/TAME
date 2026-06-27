from __future__ import annotations


class TameError(Exception):
    """Base class for tametools domain errors."""


class TameFormatError(TameError, ValueError):
    """Raised when a TAME file or path has an unsupported or invalid format."""


class TameValidationError(TameError, ValueError):
    """Raised when validation is configured to fail on detected issues."""


class TameTagError(TameError, ValueError):
    """Raised for invalid tag expressions or tag metadata."""


class TameImportError(TameError, ValueError):
    """Raised when an external file cannot be imported into a TAME template."""

"""Versioned migration adapters kept outside the generic core."""

from .worldcup_brazil import (
    CompatibilityConversion,
    CompatibilityReport,
    generic_to_legacy,
    legacy_to_generic,
)

__all__ = [
    "CompatibilityConversion",
    "CompatibilityReport",
    "generic_to_legacy",
    "legacy_to_generic",
]

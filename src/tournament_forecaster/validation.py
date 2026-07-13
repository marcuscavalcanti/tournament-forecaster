"""Shared runtime validation for schema-bounded numeric values."""

from __future__ import annotations

import math
import sys

from .errors import TournamentValidationError


MIN_FINITE_NUMBER = -sys.float_info.max
MAX_FINITE_NUMBER = sys.float_info.max


def bounded_finite_number(value: object, label: str) -> float:
    """Normalize one schema-bounded finite number without lossy pre-validation."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TournamentValidationError(f"{label} must be within finite numeric bounds")
    if value < MIN_FINITE_NUMBER or value > MAX_FINITE_NUMBER:
        raise TournamentValidationError(f"{label} must be within finite numeric bounds")
    if isinstance(value, float) and not math.isfinite(value):
        raise TournamentValidationError(f"{label} must be within finite numeric bounds")
    try:
        return float(value)
    except OverflowError as error:
        raise TournamentValidationError(
            f"{label} must be within finite numeric bounds"
        ) from error

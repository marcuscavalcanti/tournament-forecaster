"""Errors raised by the generic tournament forecaster."""


class TournamentForecasterError(Exception):
    """Base error for generic tournament forecaster failures."""


class TournamentValidationError(TournamentForecasterError, ValueError):
    """Raised when a tournament document violates its domain contract."""

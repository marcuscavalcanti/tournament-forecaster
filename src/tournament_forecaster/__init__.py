"""Generic, configuration-driven tournament forecasting domain."""

from .config import load_tournament, load_tournament_document
from .domain import (
    CompletedMatch,
    Forecast,
    MatchupProbability,
    Score,
    SimulationOptions,
    Team,
    Tournament,
    validate_tournament,
)
from .simulation import simulate_tournament

__all__ = [
    "CompletedMatch",
    "Forecast",
    "MatchupProbability",
    "Score",
    "SimulationOptions",
    "Team",
    "Tournament",
    "load_tournament",
    "load_tournament_document",
    "simulate_tournament",
    "validate_tournament",
]

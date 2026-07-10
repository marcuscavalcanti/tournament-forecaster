"""Immutable domain objects and semantic validation for tournaments."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar

from .errors import TournamentValidationError


_STABLE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_OUTPUT_KEY = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*\Z")


def _stable_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise TournamentValidationError(f"{label} must be a stable ASCII identifier")
    return value


def _output_key(value: object, label: str) -> str:
    if not isinstance(value, str) or not _OUTPUT_KEY.fullmatch(value):
        raise TournamentValidationError(f"{label} must be an ASCII output key")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TournamentValidationError(f"{label} must be non-empty text")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TournamentValidationError(f"{label} must be an integer greater than or equal to {minimum}")
    return value


def _probability(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TournamentValidationError(f"{label} must be a probability between 0 and 1")
    probability = float(value)
    if not 0.0 <= probability <= 1.0:
        raise TournamentValidationError(f"{label} must be a probability between 0 and 1")
    return probability


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class Team:
    """A tournament entrant identified independently of its display name."""

    id: str
    display_name: str
    aliases: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _stable_id(self.id, "team id")
        _text(self.display_name, "team display name")
        aliases = tuple(self.aliases)
        for alias in aliases:
            _text(alias, "team alias")
        if not isinstance(self.metadata, Mapping):
            raise TournamentValidationError("team metadata must be a mapping")
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class Score:
    """A completed match score in home-away order."""

    home: int
    away: int

    def __post_init__(self) -> None:
        _integer(self.home, "home score")
        _integer(self.away, "away score")


@dataclass(frozen=True, slots=True)
class CompletedMatch:
    """An immutable, observed result keyed by match ID and leg."""

    match_id: str
    stage_id: str
    home_team_id: str
    away_team_id: str
    score: Score
    leg: int = 1
    winner_team_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _stable_id(self.match_id, "completed match id")
        _stable_id(self.stage_id, "completed match stage id")
        _stable_id(self.home_team_id, "completed match home team id")
        _stable_id(self.away_team_id, "completed match away team id")
        if self.home_team_id == self.away_team_id:
            raise TournamentValidationError("completed match teams must be distinct")
        if not isinstance(self.score, Score):
            raise TournamentValidationError("completed match score must be a Score")
        _integer(self.leg, "completed match leg", minimum=1)
        if self.winner_team_id is not None:
            _stable_id(self.winner_team_id, "completed match winner team id")
            if self.winner_team_id not in {self.home_team_id, self.away_team_id}:
                raise TournamentValidationError("completed match winner must be one of its teams")
        if not isinstance(self.metadata, Mapping):
            raise TournamentValidationError("completed match metadata must be a mapping")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class Tournament:
    """A normalized tournament configuration prepared for simulation."""

    id: str
    display_name: str
    focus_team_id: str
    teams: tuple[Team, ...]
    stages: tuple[Mapping[str, object], ...]
    ratings: Mapping[str, float]
    completed_matches: tuple[CompletedMatch, ...]
    season: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    schema_version: int = 2

    def __post_init__(self) -> None:
        if not isinstance(self.teams, tuple):
            object.__setattr__(self, "teams", tuple(self.teams))
        if not isinstance(self.stages, tuple):
            object.__setattr__(self, "stages", tuple(self.stages))
        if not isinstance(self.completed_matches, tuple):
            object.__setattr__(self, "completed_matches", tuple(self.completed_matches))
        if not isinstance(self.ratings, Mapping):
            raise TournamentValidationError("ratings must be a mapping")
        if not isinstance(self.metadata, Mapping):
            raise TournamentValidationError("tournament metadata must be a mapping")
        frozen_stages: list[Mapping[str, object]] = []
        for stage in self.stages:
            if not isinstance(stage, Mapping):
                raise TournamentValidationError("stages must be mappings")
            frozen_stages.append(_freeze_mapping(stage))
        object.__setattr__(self, "stages", tuple(frozen_stages))
        object.__setattr__(self, "ratings", MappingProxyType(dict(self.ratings)))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))
        validate_tournament(self)


@dataclass(frozen=True, slots=True)
class SimulationOptions:
    """Deterministic simulation settings used by later engine layers."""

    seed: int = 0
    iterations: int = 10_000
    confidence_level: float = 0.95

    def __post_init__(self) -> None:
        _integer(self.seed, "simulation seed")
        _integer(self.iterations, "simulation iterations", minimum=1)
        if isinstance(self.confidence_level, bool) or not isinstance(self.confidence_level, (int, float)):
            raise TournamentValidationError("simulation confidence level must be between 0 and 1")
        if not 0.0 < float(self.confidence_level) < 1.0:
            raise TournamentValidationError("simulation confidence level must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class MatchupProbability:
    """The probability of a focus-team matchup at one tournament stage."""

    stage_id: str
    opponent_team_id: str
    probability: float

    def __post_init__(self) -> None:
        _stable_id(self.stage_id, "matchup stage id")
        _stable_id(self.opponent_team_id, "matchup opponent team id")
        object.__setattr__(self, "probability", _probability(self.probability, "matchup probability"))

    def to_dict(self) -> dict[str, object]:
        return {
            "stage_id": self.stage_id,
            "opponent_team_id": self.opponent_team_id,
            "probability": self.probability,
        }


@dataclass(frozen=True, slots=True)
class Forecast:
    """Versioned, generic output from a complete tournament simulation."""

    SCHEMA_VERSION: ClassVar[int] = 2

    run_id: str
    generated_at: str
    tournament_id: str
    focus_team_id: str
    stage_probabilities: Mapping[str, float]
    matchup_probabilities: tuple[MatchupProbability, ...]
    championship_probability: float
    confidence_intervals: Mapping[str, Sequence[float]]
    input_provenance: tuple[Mapping[str, object], ...]
    warnings: tuple[str, ...]
    council: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _stable_id(self.run_id, "forecast run id")
        _text(self.generated_at, "forecast generated at")
        _stable_id(self.tournament_id, "forecast tournament id")
        _stable_id(self.focus_team_id, "forecast focus team id")
        if not isinstance(self.stage_probabilities, Mapping):
            raise TournamentValidationError("forecast stage probabilities must be a mapping")
        normalized_stage_probabilities: dict[str, float] = {}
        for stage_id, value in self.stage_probabilities.items():
            normalized_stage_probabilities[_stable_id(stage_id, "forecast stage id")] = _probability(
                value, "forecast stage probability"
            )
        matchups = tuple(self.matchup_probabilities)
        if not all(isinstance(matchup, MatchupProbability) for matchup in matchups):
            raise TournamentValidationError("forecast matchup probabilities must be MatchupProbability values")
        object.__setattr__(self, "matchup_probabilities", matchups)
        object.__setattr__(self, "stage_probabilities", MappingProxyType(normalized_stage_probabilities))
        object.__setattr__(
            self,
            "championship_probability",
            _probability(self.championship_probability, "championship probability"),
        )
        if not isinstance(self.confidence_intervals, Mapping):
            raise TournamentValidationError("forecast confidence intervals must be a mapping")
        normalized_intervals: dict[str, tuple[float, float]] = {}
        for label, bounds in self.confidence_intervals.items():
            _output_key(label, "confidence interval id")
            if isinstance(bounds, (str, bytes)) or not isinstance(bounds, Sequence) or len(bounds) != 2:
                raise TournamentValidationError("confidence intervals must contain lower and upper bounds")
            lower = _probability(bounds[0], "confidence interval lower bound")
            upper = _probability(bounds[1], "confidence interval upper bound")
            if lower > upper:
                raise TournamentValidationError("confidence interval lower bound cannot exceed upper bound")
            normalized_intervals[label] = (lower, upper)
        provenance = tuple(self.input_provenance)
        if not all(isinstance(record, Mapping) for record in provenance):
            raise TournamentValidationError("forecast input provenance must contain mappings")
        warnings = tuple(self.warnings)
        for warning in warnings:
            _text(warning, "forecast warning")
        if self.council is not None and not isinstance(self.council, Mapping):
            raise TournamentValidationError("forecast council metadata must be a mapping")
        object.__setattr__(self, "confidence_intervals", MappingProxyType(normalized_intervals))
        object.__setattr__(self, "input_provenance", tuple(_freeze_mapping(record) for record in provenance))
        object.__setattr__(self, "warnings", warnings)
        if self.council is not None:
            object.__setattr__(self, "council", _freeze_mapping(self.council))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "tournament_id": self.tournament_id,
            "focus_team_id": self.focus_team_id,
            "stage_probabilities": dict(self.stage_probabilities),
            "matchup_probabilities": [matchup.to_dict() for matchup in self.matchup_probabilities],
            "championship_probability": self.championship_probability,
            "confidence_intervals": {
                label: list(bounds) for label, bounds in self.confidence_intervals.items()
            },
            "input_provenance": [_thaw(record) for record in self.input_provenance],
            "warnings": list(self.warnings),
            "council": _thaw(self.council) if self.council is not None else None,
        }


def validate_tournament(tournament: Tournament) -> None:
    """Validate cross-object invariants after a tournament has been normalized."""

    if not isinstance(tournament, Tournament):
        raise TournamentValidationError("tournament must be a Tournament")
    if tournament.schema_version != 2:
        raise TournamentValidationError("tournament schema version must be 2")
    _stable_id(tournament.id, "tournament id")
    _text(tournament.display_name, "tournament display name")
    _stable_id(tournament.focus_team_id, "focus team id")
    if tournament.season is not None:
        _text(tournament.season, "tournament season")
    if not tournament.teams:
        raise TournamentValidationError("tournament must define at least one team")
    if not all(isinstance(team, Team) for team in tournament.teams):
        raise TournamentValidationError("tournament teams must be Team values")
    team_ids = [team.id for team in tournament.teams]
    if len(team_ids) != len(set(team_ids)):
        raise TournamentValidationError("tournament team ids must be unique")
    if tournament.focus_team_id not in set(team_ids):
        raise TournamentValidationError("focus team id must reference a configured team")
    if not tournament.stages:
        raise TournamentValidationError("tournament must define at least one stage")
    stage_ids: list[str] = []
    for stage in tournament.stages:
        stage_id = stage.get("id")
        stage_type = stage.get("type")
        stage_ids.append(_stable_id(stage_id, "stage id"))
        _text(stage_type, "stage type")
    if len(stage_ids) != len(set(stage_ids)):
        raise TournamentValidationError("tournament stage ids must be unique")
    for team_id, rating in tournament.ratings.items():
        _stable_id(team_id, "rating team id")
        if team_id not in set(team_ids):
            raise TournamentValidationError("ratings must reference configured teams")
        if isinstance(rating, bool) or not isinstance(rating, (int, float)):
            raise TournamentValidationError("ratings must be numeric")
    completed_keys: set[tuple[str, int]] = set()
    for match in tournament.completed_matches:
        if not isinstance(match, CompletedMatch):
            raise TournamentValidationError("completed matches must be CompletedMatch values")
        if match.stage_id not in set(stage_ids):
            raise TournamentValidationError("completed matches must reference configured stages")
        if match.home_team_id not in set(team_ids) or match.away_team_id not in set(team_ids):
            raise TournamentValidationError("completed matches must reference configured teams")
        key = (match.match_id, match.leg)
        if key in completed_keys:
            raise TournamentValidationError("duplicate completed result for match id and leg")
        completed_keys.add(key)

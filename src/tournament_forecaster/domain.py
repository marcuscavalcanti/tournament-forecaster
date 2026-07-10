"""Immutable domain objects and semantic validation for tournaments."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar

from .errors import TournamentValidationError


_STABLE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_GROUP_LABEL = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*\Z")
_OUTPUT_KEY = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*\Z")
_STAGE_TYPES = frozenset({"round_robin_groups", "league_table", "knockout"})
_PAIRING_MODES = frozenset({"fixed", "seeded_draw", "open_draw"})
_ENTRANT_TYPES = frozenset(
    {"group_rank", "best_additional", "league_rank", "match_winner"}
)
_AGGREGATE_TIEBREAKS = frozenset({"extra_time_then_penalties", "penalties"})
_TIEBREAKERS = frozenset(
    {"points", "goal_difference", "goals_for", "wins", "rating", "team_id"}
)
_GROUP_STAGE_PROPERTIES = frozenset(
    {
        "id",
        "type",
        "groups",
        "rounds_per_pair",
        "points",
        "tiebreakers",
        "qualification",
        "metadata",
    }
)
_LEAGUE_STAGE_PROPERTIES = frozenset(
    {
        "id",
        "type",
        "fixtures",
        "points",
        "tiebreakers",
        "qualification_bands",
        "metadata",
    }
)
_KNOCKOUT_STAGE_PROPERTIES = frozenset(
    {
        "id",
        "type",
        "pairing",
        "legs",
        "home_away_order",
        "aggregate_tiebreak",
        "away_goals_rule",
        "metadata",
    }
)
_PROVENANCE_PROPERTIES = frozenset(
    {"kind", "name", "source", "source_id", "uri", "retrieved_at", "metadata"}
)


def _stable_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise TournamentValidationError(f"{label} must be a stable ASCII identifier")
    return value


def _output_key(value: object, label: str) -> str:
    if not isinstance(value, str) or not _OUTPUT_KEY.fullmatch(value):
        raise TournamentValidationError(f"{label} must be an ASCII output key")
    return value


def _group_label(value: object) -> str:
    if not isinstance(value, str) or not _GROUP_LABEL.fullmatch(value):
        raise TournamentValidationError(
            "group label must use ASCII letters or numbers with internal - or _ separators"
        )
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


def _sequence(value: object, label: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TournamentValidationError(f"{label} must be a sequence")
    return tuple(value)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TournamentValidationError(f"{label} must be a mapping")
    return value


def _reject_unknown_properties(
    value: Mapping[str, object],
    allowed: frozenset[str],
    label: str,
) -> None:
    if not all(isinstance(key, str) for key in value):
        raise TournamentValidationError(f"{label} must use string property names")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise TournamentValidationError(
            f"{label} contains unknown properties: {', '.join(unknown)}"
        )


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise TournamentValidationError(f"{label} must be a boolean")
    return value


def _validate_points(value: object, label: str) -> None:
    points = _mapping(value, label)
    _reject_unknown_properties(points, frozenset({"win", "draw", "loss"}), label)
    for result in ("win", "draw", "loss"):
        _integer(points.get(result), f"{label} {result}")


def _validate_tiebreakers(value: object, label: str) -> None:
    tiebreakers = _sequence(value, label)
    if not tiebreakers:
        raise TournamentValidationError(f"{label} must not be empty")
    normalized: list[str] = []
    for value_item in tiebreakers:
        item = _text(value_item, f"{label} item")
        if item not in _TIEBREAKERS:
            raise TournamentValidationError(f"{label} contains an unsupported rule")
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise TournamentValidationError(f"{label} must contain unique rules")


def _validate_stage_metadata(stage: Mapping[str, object], label: str) -> None:
    if "metadata" in stage:
        _mapping(stage["metadata"], f"{label} metadata")


def _freeze(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        raise TournamentValidationError("nested metadata must contain only finite numbers")
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
        aliases = _sequence(self.aliases, "team aliases")
        for alias in aliases:
            _text(alias, "team alias")
        if len(aliases) != len(set(aliases)):
            raise TournamentValidationError("team aliases must be unique")
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
        object.__setattr__(self, "teams", _sequence(self.teams, "teams"))
        object.__setattr__(self, "stages", _sequence(self.stages, "stages"))
        object.__setattr__(
            self,
            "completed_matches",
            _sequence(self.completed_matches, "completed matches"),
        )
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
        matchups = _sequence(self.matchup_probabilities, "forecast matchup probabilities")
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
        provenance = _sequence(self.input_provenance, "forecast input provenance")
        if not all(isinstance(record, Mapping) for record in provenance):
            raise TournamentValidationError("forecast input provenance must contain mappings")
        normalized_provenance: list[Mapping[str, object]] = []
        for index, record in enumerate(provenance):
            assert isinstance(record, Mapping)
            label = f"forecast input provenance[{index}]"
            _reject_unknown_properties(record, _PROVENANCE_PROPERTIES, label)
            _text(record.get("kind"), f"{label} kind")
            for key in ("name", "source", "uri", "retrieved_at"):
                if key in record:
                    _text(record[key], f"{label} {key}")
            if "source_id" in record:
                _stable_id(record["source_id"], f"{label} source id")
            if "metadata" in record:
                _mapping(record["metadata"], f"{label} metadata")
            normalized_provenance.append(_freeze_mapping(record))
        warnings = _sequence(self.warnings, "forecast warnings")
        for warning in warnings:
            _text(warning, "forecast warning")
        if self.council is not None and not isinstance(self.council, Mapping):
            raise TournamentValidationError("forecast council metadata must be a mapping")
        object.__setattr__(self, "confidence_intervals", MappingProxyType(normalized_intervals))
        object.__setattr__(self, "input_provenance", tuple(normalized_provenance))
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


def _validate_group_stage(
    stage: Mapping[str, object],
    team_ids: set[str],
) -> dict[str, str]:
    stage_id = str(stage["id"])
    label = f"group stage {stage_id}"
    _reject_unknown_properties(stage, _GROUP_STAGE_PROPERTIES, label)
    _validate_stage_metadata(stage, label)
    groups = _mapping(stage.get("groups"), f"group stage {stage_id} groups")
    if not groups:
        raise TournamentValidationError("round-robin group stage must define groups")
    memberships: dict[str, str] = {}
    for group_id_value, roster_value in groups.items():
        group_id = _group_label(group_id_value)
        roster = _sequence(roster_value, f"group {group_id} roster")
        if len(roster) < 2:
            raise TournamentValidationError("group roster must contain at least two teams")
        local_ids: set[str] = set()
        for team_id_value in roster:
            team_id = _stable_id(team_id_value, "group team id")
            if team_id not in team_ids:
                raise TournamentValidationError("group rosters must reference configured teams")
            if team_id in local_ids:
                raise TournamentValidationError("group roster contains a duplicate team")
            if team_id in memberships:
                raise TournamentValidationError("a team cannot appear in multiple groups")
            local_ids.add(team_id)
            memberships[team_id] = group_id
    if "rounds_per_pair" in stage:
        _integer(stage["rounds_per_pair"], f"{label} rounds per pair", minimum=1)
    if "points" in stage:
        _validate_points(stage["points"], f"{label} points")
    if "tiebreakers" in stage:
        _validate_tiebreakers(stage["tiebreakers"], f"{label} tiebreakers")
    if "qualification" in stage:
        qualification = _mapping(stage["qualification"], f"{label} qualification")
        _reject_unknown_properties(
            qualification,
            frozenset({"direct_per_group", "best_additional"}),
            f"{label} qualification",
        )
        _integer(
            qualification.get("direct_per_group"),
            f"{label} direct qualification count",
        )
        _integer(
            qualification.get("best_additional"),
            f"{label} additional qualification count",
        )
    return memberships


def _validate_league_stage(
    stage: Mapping[str, object],
    team_ids: set[str],
) -> dict[str, frozenset[str]]:
    stage_id = str(stage["id"])
    label = f"league stage {stage_id}"
    _reject_unknown_properties(stage, _LEAGUE_STAGE_PROPERTIES, label)
    _validate_stage_metadata(stage, label)
    fixtures = _sequence(stage.get("fixtures"), f"league stage {stage_id} fixtures")
    fixture_teams: dict[str, frozenset[str]] = {}
    for fixture_value in fixtures:
        fixture = _mapping(fixture_value, "league fixture")
        _reject_unknown_properties(
            fixture,
            frozenset({"match_id", "home_team_id", "away_team_id", "metadata"}),
            "league fixture",
        )
        if "metadata" in fixture:
            _mapping(fixture["metadata"], "league fixture metadata")
        match_id = _stable_id(fixture.get("match_id"), "league fixture match id")
        home_team_id = _stable_id(fixture.get("home_team_id"), "league fixture home team id")
        away_team_id = _stable_id(fixture.get("away_team_id"), "league fixture away team id")
        if home_team_id == away_team_id:
            raise TournamentValidationError("league fixture teams must be distinct")
        if home_team_id not in team_ids or away_team_id not in team_ids:
            raise TournamentValidationError("league fixtures must reference configured teams")
        if match_id in fixture_teams:
            raise TournamentValidationError("league fixture match ids must be unique")
        fixture_teams[match_id] = frozenset({home_team_id, away_team_id})
    if "points" in stage:
        _validate_points(stage["points"], f"{label} points")
    if "tiebreakers" in stage:
        _validate_tiebreakers(stage["tiebreakers"], f"{label} tiebreakers")
    if "qualification_bands" in stage:
        bands = _sequence(stage["qualification_bands"], f"{label} qualification bands")
        for band_value in bands:
            band = _mapping(band_value, f"{label} qualification band")
            _reject_unknown_properties(
                band,
                frozenset({"ranks", "destination"}),
                f"{label} qualification band",
            )
            ranks = _sequence(band.get("ranks"), f"{label} qualification band ranks")
            if len(ranks) != 2:
                raise TournamentValidationError(
                    f"{label} qualification band ranks must contain two values"
                )
            _integer(ranks[0], f"{label} qualification band first rank", minimum=1)
            _integer(ranks[1], f"{label} qualification band last rank", minimum=1)
            _stable_id(
                band.get("destination"),
                f"{label} qualification band destination",
            )
    return fixture_teams


def _validate_entrant(source_value: object) -> Mapping[str, object]:
    source = _mapping(source_value, "knockout entrant")
    source_type = source.get("type")
    if source_type not in _ENTRANT_TYPES:
        raise TournamentValidationError("knockout entrant type is unsupported")
    if source_type == "group_rank":
        allowed = frozenset({"type", "stage_id", "group", "rank"})
        _reject_unknown_properties(source, allowed, "group_rank entrant")
        _stable_id(source.get("stage_id"), "group_rank entrant stage id")
        _group_label(source.get("group"))
        _integer(source.get("rank"), "group_rank entrant rank", minimum=1)
    elif source_type == "best_additional":
        allowed = frozenset({"type", "stage_id", "rank"})
        _reject_unknown_properties(source, allowed, "best_additional entrant")
        _stable_id(source.get("stage_id"), "best_additional entrant stage id")
        _integer(source.get("rank"), "best_additional entrant rank", minimum=1)
    elif source_type == "league_rank":
        allowed = frozenset({"type", "stage_id", "rank"})
        _reject_unknown_properties(source, allowed, "league_rank entrant")
        _stable_id(source.get("stage_id"), "league_rank entrant stage id")
        _integer(source.get("rank"), "league_rank entrant rank", minimum=1)
    else:
        allowed = frozenset({"type", "match_id"})
        _reject_unknown_properties(source, allowed, "match_winner entrant")
        _stable_id(source.get("match_id"), "match_winner entrant match id")
    return source


def _validate_knockout_stage(
    stage: Mapping[str, object],
) -> tuple[int, set[str], tuple[Mapping[str, object], ...]]:
    stage_id = str(stage["id"])
    label = f"knockout stage {stage_id}"
    _reject_unknown_properties(stage, _KNOCKOUT_STAGE_PROPERTIES, label)
    _validate_stage_metadata(stage, label)
    pairing = _mapping(stage.get("pairing"), f"knockout stage {stage_id} pairing")
    _reject_unknown_properties(
        pairing,
        frozenset({"mode", "ties"}),
        f"{label} pairing",
    )
    mode = pairing.get("mode")
    if mode not in _PAIRING_MODES:
        raise TournamentValidationError("knockout pairing mode must be fixed, seeded_draw, or open_draw")
    ties = _sequence(pairing.get("ties"), "knockout pairing ties")
    tie_ids: set[str] = set()
    entrant_sources: list[Mapping[str, object]] = []
    for tie_value in ties:
        tie = _mapping(tie_value, "knockout tie")
        _reject_unknown_properties(
            tie,
            frozenset({"id", "entrants"}),
            "knockout tie",
        )
        tie_id = _stable_id(tie.get("id"), "knockout tie id")
        if tie_id in tie_ids:
            raise TournamentValidationError("knockout tie ids must be unique")
        tie_ids.add(tie_id)
        entrants = _sequence(tie.get("entrants"), "knockout tie entrants")
        if len(entrants) != 2:
            raise TournamentValidationError("knockout tie must contain exactly two entrants")
        entrant_sources.extend(_validate_entrant(entrant) for entrant in entrants)
    legs = stage.get("legs")
    if isinstance(legs, bool) or legs not in {1, 2}:
        raise TournamentValidationError("knockout stage must use one or two legs")
    if "home_away_order" in stage:
        _text(stage["home_away_order"], f"{label} home away order")
    if "aggregate_tiebreak" in stage:
        aggregate_tiebreak = _text(
            stage["aggregate_tiebreak"],
            f"{label} aggregate tiebreak",
        )
        if aggregate_tiebreak not in _AGGREGATE_TIEBREAKS:
            raise TournamentValidationError(
                "knockout aggregate tiebreak must be extra_time_then_penalties or penalties"
            )
    if "away_goals_rule" in stage:
        _boolean(stage["away_goals_rule"], f"{label} away goals rule")
    return int(legs), tie_ids, tuple(entrant_sources)


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
    team_id_list = [team.id for team in tournament.teams]
    team_ids = set(team_id_list)
    if len(team_id_list) != len(team_ids):
        raise TournamentValidationError("tournament team ids must be unique")
    if tournament.focus_team_id not in team_ids:
        raise TournamentValidationError("focus team id must reference a configured team")
    if not tournament.stages:
        raise TournamentValidationError("tournament must define at least one stage")
    stage_ids: list[str] = []
    stages_by_id: dict[str, Mapping[str, object]] = {}
    group_memberships: dict[str, dict[str, str]] = {}
    league_fixtures: dict[str, dict[str, frozenset[str]]] = {}
    stage_leg_limits: dict[str, int] = {}
    knockout_sources: dict[str, tuple[Mapping[str, object], ...]] = {}
    tie_owners: dict[str, str] = {}
    for stage in tournament.stages:
        stage_id = stage.get("id")
        stable_stage_id = _stable_id(stage_id, "stage id")
        stage_type = _text(stage.get("type"), "stage type")
        if stage_type not in _STAGE_TYPES:
            raise TournamentValidationError("stage type must be a recognized stage type")
        stage_ids.append(stable_stage_id)
        stages_by_id[stable_stage_id] = stage
        if stage_type == "round_robin_groups":
            group_memberships[stable_stage_id] = _validate_group_stage(stage, team_ids)
            stage_leg_limits[stable_stage_id] = 1
        elif stage_type == "league_table":
            league_fixtures[stable_stage_id] = _validate_league_stage(stage, team_ids)
            stage_leg_limits[stable_stage_id] = 1
        else:
            legs, tie_ids, sources = _validate_knockout_stage(stage)
            stage_leg_limits[stable_stage_id] = legs
            knockout_sources[stable_stage_id] = sources
            for tie_id in tie_ids:
                if tie_id in tie_owners:
                    raise TournamentValidationError("knockout tie ids must be globally unique")
                tie_owners[tie_id] = stable_stage_id
    if len(stage_ids) != len(set(stage_ids)):
        raise TournamentValidationError("tournament stage ids must be unique")
    dependencies: dict[str, set[str]] = {stage_id: set() for stage_id in stage_ids}
    for target_stage_id, sources in knockout_sources.items():
        for source in sources:
            source_type = source["type"]
            if source_type in {"group_rank", "best_additional", "league_rank"}:
                source_stage_id = str(source["stage_id"])
                source_stage = stages_by_id.get(source_stage_id)
                if source_stage is None:
                    raise TournamentValidationError("knockout entrant references an unknown stage")
                dependencies[target_stage_id].add(source_stage_id)
                if source_type in {"group_rank", "best_additional"}:
                    if source_stage.get("type") != "round_robin_groups":
                        raise TournamentValidationError("group entrant must reference a group stage")
                    if source_type == "group_rank":
                        groups = source_stage["groups"]
                        assert isinstance(groups, Mapping)
                        group_id = str(source["group"])
                        source_rank = _integer(source["rank"], "group_rank entrant rank", minimum=1)
                        if group_id not in groups or source_rank > len(groups[group_id]):  # type: ignore[arg-type]
                            raise TournamentValidationError("group_rank entrant does not resolve")
                    else:
                        qualification = source_stage.get("qualification")
                        source_rank = _integer(
                            source["rank"],
                            "best_additional entrant rank",
                            minimum=1,
                        )
                        if not isinstance(qualification, Mapping) or source_rank > _integer(
                            qualification.get("best_additional", 0),
                            "group stage additional qualification count",
                        ):
                            raise TournamentValidationError("best_additional entrant does not resolve")
                else:
                    if source_stage.get("type") != "league_table":
                        raise TournamentValidationError("league_rank entrant must reference a league stage")
                    fixture_teams = {
                        team_id
                        for teams in league_fixtures[source_stage_id].values()
                        for team_id in teams
                    }
                    source_rank = _integer(source["rank"], "league_rank entrant rank", minimum=1)
                    if source_rank > len(fixture_teams):
                        raise TournamentValidationError("league_rank entrant does not resolve")
            else:
                match_id = str(source["match_id"])
                owner_stage_id = tie_owners.get(match_id)
                if owner_stage_id is None:
                    raise TournamentValidationError("match_winner entrant references an unknown tie")
                dependencies[target_stage_id].add(owner_stage_id)
    completed_stage_ids: set[str] = set()
    while len(completed_stage_ids) < len(stage_ids):
        ready = {
            stage_id
            for stage_id, required in dependencies.items()
            if stage_id not in completed_stage_ids and required <= completed_stage_ids
        }
        if not ready:
            raise TournamentValidationError("tournament stage dependencies must form a directed acyclic graph")
        completed_stage_ids.update(ready)
    for team_id, rating in tournament.ratings.items():
        _stable_id(team_id, "rating team id")
        if team_id not in team_ids:
            raise TournamentValidationError("ratings must reference configured teams")
        if (
            isinstance(rating, bool)
            or not isinstance(rating, (int, float))
            or not math.isfinite(float(rating))
        ):
            raise TournamentValidationError("ratings must be finite numeric values")
    completed_keys: set[tuple[str, int]] = set()
    completed_identities: dict[str, tuple[str, frozenset[str]]] = {}
    for match in tournament.completed_matches:
        if not isinstance(match, CompletedMatch):
            raise TournamentValidationError("completed matches must be CompletedMatch values")
        if match.stage_id not in stages_by_id:
            raise TournamentValidationError("completed matches must reference configured stages")
        if match.home_team_id not in team_ids or match.away_team_id not in team_ids:
            raise TournamentValidationError("completed matches must reference configured teams")
        key = (match.match_id, match.leg)
        if key in completed_keys:
            raise TournamentValidationError("duplicate completed result for match id and leg")
        completed_keys.add(key)
        identity = (match.stage_id, frozenset({match.home_team_id, match.away_team_id}))
        previous_identity = completed_identities.get(match.match_id)
        if previous_identity is not None:
            if previous_identity[0] != identity[0]:
                raise TournamentValidationError("completed match legs must use the same stage")
            if previous_identity[1] != identity[1]:
                raise TournamentValidationError("completed match legs must use the same team pair")
        else:
            completed_identities[match.match_id] = identity
        if match.leg > stage_leg_limits[match.stage_id]:
            raise TournamentValidationError("completed match leg exceeds stage contract")
        if match.winner_team_id is not None and match.score.home != match.score.away:
            score_winner = (
                match.home_team_id
                if match.score.home > match.score.away
                else match.away_team_id
            )
            stage = stages_by_id[match.stage_id]
            is_two_leg_tie_decider = (
                stage.get("type") == "knockout"
                and stage_leg_limits[match.stage_id] == 2
                and match.leg == 2
            )
            if match.winner_team_id != score_winner and not is_two_leg_tie_decider:
                raise TournamentValidationError("completed match winner contradicts score")
        if match.stage_id in group_memberships:
            membership = group_memberships[match.stage_id]
            if (
                match.home_team_id not in membership
                or match.away_team_id not in membership
                or membership[match.home_team_id] != membership[match.away_team_id]
            ):
                raise TournamentValidationError("completed group match teams must share the same configured group")
        if match.stage_id in league_fixtures:
            configured_pair = league_fixtures[match.stage_id].get(match.match_id)
            if configured_pair != identity[1]:
                raise TournamentValidationError("completed league match must reference a configured league fixture")

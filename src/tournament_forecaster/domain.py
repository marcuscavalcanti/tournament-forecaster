"""Immutable domain objects and semantic validation for tournaments."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar

from .errors import TournamentValidationError
from .group_fixtures import group_fixture_contract
from .pairing import resolve_ties, validate_locked_pairs
from .qualification import QualificationState
from .standings import TableMatch, calculate_group_tables, calculate_league_table


_STABLE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_GROUP_LABEL = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*\Z")
_OUTPUT_KEY = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*\Z")
_STAGE_TYPES = frozenset({"round_robin_groups", "league_table", "knockout"})
_PAIRING_MODES = frozenset({"fixed", "seeded_draw", "open_draw"})
_ENTRANT_TYPES = frozenset(
    {"team", "group_rank", "best_additional", "league_rank", "match_winner"}
)
_AGGREGATE_TIEBREAKS = frozenset({"extra_time_then_penalties", "penalties"})
_HOME_AWAY_ORDERS = frozenset(
    {"listed_team_first_leg_home", "seeded_team_second_leg_home"}
)
_KNOCKOUT_TERMINALS = frozenset({"championship", "placement"})
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
        "terminal",
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
    if any(not _is_xml_1_0_character(character) for character in value):
        raise TournamentValidationError(
            f"{label} must contain only XML 1.0-safe text"
        )
    return value


def _is_xml_1_0_character(character: str) -> bool:
    codepoint = ord(character)
    return (
        codepoint in {0x09, 0x0A, 0x0D}
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


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


def _entrant_is_resolved(
    source: Mapping[str, object], state: QualificationState
) -> bool:
    source_type = source.get("type")
    if source_type == "team":
        return True
    if source_type == "group_rank":
        stage_id = source.get("stage_id")
        group = source.get("group")
        return (
            isinstance(stage_id, str)
            and isinstance(group, str)
            and group in state.group_rankings.get(stage_id, {})
        )
    if source_type == "best_additional":
        return source.get("stage_id") in state.best_additional
    if source_type == "league_rank":
        return source.get("stage_id") in state.league_rankings
    if source_type == "match_winner":
        return source.get("match_id") in state.match_winners
    return False


def _tie_is_resolved(
    tie: Mapping[str, object], state: QualificationState
) -> bool:
    entrants = tie.get("entrants")
    return (
        isinstance(entrants, Sequence)
        and not isinstance(entrants, (str, bytes))
        and all(
            isinstance(source, Mapping) and _entrant_is_resolved(source, state)
            for source in entrants
        )
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
        metadata = _mapping(stage["metadata"], f"{label} metadata")
        home_advantage = metadata.get("home_advantage_rating_points", 0.0)
        if (
            isinstance(home_advantage, bool)
            or not isinstance(home_advantage, (int, float))
            or not math.isfinite(float(home_advantage))
        ):
            raise TournamentValidationError(
                f"{label} metadata home_advantage_rating_points must be finite numeric"
            )


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
    stage_order: tuple[str, ...]
    matchup_probabilities: tuple[MatchupProbability, ...]
    championship_probability: float
    confidence_intervals: Mapping[str, Sequence[float]]
    input_provenance: tuple[Mapping[str, object], ...]
    warnings: tuple[str, ...]
    council: Mapping[str, object] | None = None
    tournament_display_name: str | None = None
    team_display_names: Mapping[str, str] = field(default_factory=dict)
    simulation: Mapping[str, object] = field(default_factory=dict)

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
        stage_order = tuple(
            _stable_id(stage_id, "forecast stage order id")
            for stage_id in _sequence(self.stage_order, "forecast stage order")
        )
        if len(stage_order) != len(set(stage_order)):
            raise TournamentValidationError(
                "forecast stage order must not contain duplicates"
            )
        if set(stage_order) != set(normalized_stage_probabilities):
            raise TournamentValidationError(
                "forecast stage order must be a permutation of stage probability keys"
            )
        matchups = _sequence(self.matchup_probabilities, "forecast matchup probabilities")
        if not all(isinstance(matchup, MatchupProbability) for matchup in matchups):
            raise TournamentValidationError("forecast matchup probabilities must be MatchupProbability values")
        object.__setattr__(self, "matchup_probabilities", matchups)
        object.__setattr__(self, "stage_probabilities", MappingProxyType(normalized_stage_probabilities))
        object.__setattr__(self, "stage_order", stage_order)
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
        if self.tournament_display_name is not None:
            _text(self.tournament_display_name, "forecast tournament display name")
        if not isinstance(self.team_display_names, Mapping):
            raise TournamentValidationError("forecast team display names must be a mapping")
        normalized_names: dict[str, str] = {}
        for team_id, display_name in self.team_display_names.items():
            normalized_names[_stable_id(team_id, "forecast display-name team id")] = _text(
                display_name,
                "forecast team display name",
            )
        if normalized_names and self.focus_team_id not in normalized_names:
            raise TournamentValidationError(
                "forecast team display names must include the focus team"
            )
        if not isinstance(self.simulation, Mapping):
            raise TournamentValidationError("forecast simulation metadata must be a mapping")
        normalized_simulation: dict[str, object] = {}
        if self.simulation:
            _reject_unknown_properties(
                self.simulation,
                frozenset({"seed", "iterations", "confidence_level"}),
                "forecast simulation metadata",
            )
            normalized_simulation = {
                "seed": _integer(
                    self.simulation.get("seed"),
                    "forecast simulation seed",
                ),
                "iterations": _integer(
                    self.simulation.get("iterations"),
                    "forecast simulation iterations",
                    minimum=1,
                ),
            }
            confidence_level = self.simulation.get("confidence_level")
            if (
                isinstance(confidence_level, bool)
                or not isinstance(confidence_level, (int, float))
                or not math.isfinite(float(confidence_level))
                or not 0.0 < float(confidence_level) < 1.0
            ):
                raise TournamentValidationError(
                    "forecast simulation confidence level must be between 0 and 1"
                )
            normalized_simulation["confidence_level"] = float(confidence_level)
        object.__setattr__(self, "confidence_intervals", MappingProxyType(normalized_intervals))
        object.__setattr__(self, "input_provenance", tuple(normalized_provenance))
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "team_display_names", MappingProxyType(normalized_names))
        object.__setattr__(self, "simulation", MappingProxyType(normalized_simulation))
        if self.council is not None:
            object.__setattr__(self, "council", _freeze_mapping(self.council))

    def to_dict(self) -> dict[str, object]:
        document: dict[str, object] = {
            "schema_version": self.SCHEMA_VERSION,
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "tournament_id": self.tournament_id,
            "focus_team_id": self.focus_team_id,
            "stage_probabilities": dict(self.stage_probabilities),
            "stage_order": list(self.stage_order),
            "matchup_probabilities": [matchup.to_dict() for matchup in self.matchup_probabilities],
            "championship_probability": self.championship_probability,
            "confidence_intervals": {
                label: list(bounds) for label, bounds in self.confidence_intervals.items()
            },
            "input_provenance": [_thaw(record) for record in self.input_provenance],
            "warnings": list(self.warnings),
            "council": _thaw(self.council) if self.council is not None else None,
        }
        if self.tournament_display_name is not None:
            document["tournament_display_name"] = self.tournament_display_name
        if self.team_display_names:
            document["team_display_names"] = dict(self.team_display_names)
        if self.simulation:
            document["simulation"] = dict(self.simulation)
        return document


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
    roster_sizes: list[int] = []
    for group_id_value, roster_value in groups.items():
        group_id = _group_label(group_id_value)
        roster = _sequence(roster_value, f"group {group_id} roster")
        if len(roster) < 2:
            raise TournamentValidationError("group roster must contain at least two teams")
        roster_sizes.append(len(roster))
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
            frozenset({"direct_per_group", "best_additional", "additional_rank"}),
            f"{label} qualification",
        )
        direct_per_group = _integer(
            qualification.get("direct_per_group"),
            f"{label} direct qualification count",
        )
        best_additional = _integer(
            qualification.get("best_additional"),
            f"{label} additional qualification count",
        )
        if any(direct_per_group > roster_size for roster_size in roster_sizes):
            raise TournamentValidationError(
                f"{label} qualification counts must be attainable from every group roster"
            )
        if best_additional > len(roster_sizes):
            raise TournamentValidationError(
                f"{label} additional qualification count cannot exceed the number of groups"
            )
        additional_rank_value = qualification.get("additional_rank")
        if best_additional > 0 or additional_rank_value is not None:
            additional_rank = _integer(
                additional_rank_value,
                f"{label} additional rank",
                minimum=1,
            )
            if additional_rank <= direct_per_group:
                raise TournamentValidationError(
                    f"{label} additional rank cannot overlap direct qualification ranks"
                )
            if any(additional_rank > roster_size for roster_size in roster_sizes):
                raise TournamentValidationError(
                    f"{label} additional rank must exist in every group roster"
                )
    return memberships


def _validate_league_stage(
    stage: Mapping[str, object],
    team_ids: set[str],
) -> tuple[dict[str, tuple[str, str]], dict[str, frozenset[int]]]:
    stage_id = str(stage["id"])
    label = f"league stage {stage_id}"
    _reject_unknown_properties(stage, _LEAGUE_STAGE_PROPERTIES, label)
    _validate_stage_metadata(stage, label)
    fixtures = _sequence(stage.get("fixtures"), f"league stage {stage_id} fixtures")
    fixture_teams: dict[str, tuple[str, str]] = {}
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
        fixture_teams[match_id] = (home_team_id, away_team_id)
    if "points" in stage:
        _validate_points(stage["points"], f"{label} points")
    if "tiebreakers" in stage:
        _validate_tiebreakers(stage["tiebreakers"], f"{label} tiebreakers")
    band_ranks: dict[str, set[int]] = {}
    used_ranks: set[int] = set()
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
            first_rank = _integer(
                ranks[0],
                f"{label} qualification band first rank",
                minimum=1,
            )
            last_rank = _integer(
                ranks[1],
                f"{label} qualification band last rank",
                minimum=1,
            )
            if first_rank > last_rank:
                raise TournamentValidationError(
                    f"{label} qualification band ranks must be ordered"
                )
            fixture_team_ids = {
                team_id for teams in fixture_teams.values() for team_id in teams
            }
            if last_rank > len(fixture_team_ids):
                raise TournamentValidationError(
                    f"{label} qualification band ranks must be attainable"
                )
            destination = _stable_id(
                band.get("destination"),
                f"{label} qualification band destination",
            )
            covered_ranks = set(range(first_rank, last_rank + 1))
            if covered_ranks & used_ranks:
                raise TournamentValidationError(
                    f"{label} qualification bands cannot overlap"
                )
            used_ranks.update(covered_ranks)
            band_ranks.setdefault(destination, set()).update(covered_ranks)
    return fixture_teams, {
        destination: frozenset(ranks)
        for destination, ranks in band_ranks.items()
    }


def _validate_entrant(source_value: object) -> Mapping[str, object]:
    source = _mapping(source_value, "knockout entrant")
    source_type = source.get("type")
    if source_type not in _ENTRANT_TYPES:
        raise TournamentValidationError("knockout entrant type is unsupported")
    if source_type == "team":
        allowed = frozenset({"type", "team_id"})
        _reject_unknown_properties(source, allowed, "team entrant")
        _stable_id(source.get("team_id"), "team entrant team id")
    elif source_type == "group_rank":
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
    home_away_order = _text(
        stage.get("home_away_order"),
        f"{label} home away order",
    )
    if home_away_order not in _HOME_AWAY_ORDERS:
        raise TournamentValidationError(
            "knockout home away order must be listed_team_first_leg_home or seeded_team_second_leg_home"
        )
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
        away_goals_rule = _boolean(stage["away_goals_rule"], f"{label} away goals rule")
        if away_goals_rule and legs == 1:
            raise TournamentValidationError("knockout away goals rule requires two legs")
    if "terminal" in stage:
        terminal = _text(stage["terminal"], f"{label} terminal")
        if terminal not in _KNOCKOUT_TERMINALS:
            raise TournamentValidationError(
                "knockout terminal must be championship or placement"
            )
        if terminal == "championship" and len(ties) != 1:
            raise TournamentValidationError(
                "championship terminal must contain exactly one tie"
            )
    return int(legs), tie_ids, tuple(entrant_sources)


def _completed_knockout_winner(
    stage: Mapping[str, object],
    matches: Sequence[CompletedMatch],
) -> str | None:
    """Validate and resolve a fully completed knockout tie without randomness."""

    legs = stage.get("legs")
    assert isinstance(legs, int) and not isinstance(legs, bool)
    canonical_pairs = {_knockout_fact_pair(stage, match) for match in matches}
    if len(canonical_pairs) > 1:
        raise TournamentValidationError(
            "completed knockout leg home-away order contradicts its tie"
        )
    facts_by_leg = {match.leg: match for match in matches}
    all_legs_present = all(leg in facts_by_leg for leg in range(1, legs + 1))
    if any(match.winner_team_id is not None for match in matches) and not all_legs_present:
        raise TournamentValidationError(
            "explicit winner requires every configured leg to be completed"
        )
    if any(match.winner_team_id is not None and match.leg != legs for match in matches):
        raise TournamentValidationError(
            "completed two-leg winner must be declared on the final leg"
        )
    if not all_legs_present:
        return None

    first = facts_by_leg[1]
    team_ids = {first.home_team_id, first.away_team_id}
    totals = {team_id: 0 for team_id in team_ids}
    away_goals = {team_id: 0 for team_id in team_ids}
    for leg in range(1, legs + 1):
        match = facts_by_leg[leg]
        totals[match.home_team_id] += match.score.home
        totals[match.away_team_id] += match.score.away
        away_goals[match.away_team_id] += match.score.away

    final_fact = facts_by_leg[legs]
    declared_winner = final_fact.winner_team_id
    if legs == 1:
        if final_fact.score.home != final_fact.score.away:
            inferred = (
                final_fact.home_team_id
                if final_fact.score.home > final_fact.score.away
                else final_fact.away_team_id
            )
            if declared_winner is not None and declared_winner != inferred:
                raise TournamentValidationError(
                    "completed winner contradicts decisive score"
                )
            return inferred
        if declared_winner is None:
            raise TournamentValidationError(
                "completed draw requires explicit winner"
            )
        return declared_winner

    ordered_teams = sorted(team_ids)
    first_team, second_team = ordered_teams
    if totals[first_team] != totals[second_team]:
        inferred = first_team if totals[first_team] > totals[second_team] else second_team
        if declared_winner is not None and declared_winner != inferred:
            raise TournamentValidationError(
                "completed winner contradicts decisive aggregate"
            )
        return inferred
    if bool(stage.get("away_goals_rule", False)) and away_goals[first_team] != away_goals[second_team]:
        inferred = (
            first_team
            if away_goals[first_team] > away_goals[second_team]
            else second_team
        )
        if declared_winner is not None and declared_winner != inferred:
            raise TournamentValidationError(
                "completed winner contradicts decisive away-goals result"
            )
        return inferred
    if declared_winner is None:
        raise TournamentValidationError(
            "completed aggregate draw requires explicit winner"
        )
    return declared_winner


def _knockout_fact_pair(
    stage: Mapping[str, object],
    match: CompletedMatch,
) -> tuple[str, str]:
    """Normalize one knockout leg to configured first/second entrant order."""

    legs = stage.get("legs")
    assert isinstance(legs, int) and not isinstance(legs, bool)
    if legs == 1:
        return match.home_team_id, match.away_team_id
    home_away_order = str(stage.get("home_away_order"))
    first_team_is_home = (
        home_away_order == "listed_team_first_leg_home" and match.leg == 1
    ) or (
        home_away_order == "seeded_team_second_leg_home" and match.leg == 2
    )
    if first_team_is_home:
        return match.home_team_id, match.away_team_id
    return match.away_team_id, match.home_team_id


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
    group_fixture_contracts: dict[
        str,
        dict[str, tuple[str, str, int]],
    ] = {}
    league_fixtures: dict[str, dict[str, tuple[str, str]]] = {}
    league_qualification_bands: dict[str, dict[str, frozenset[int]]] = {}
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
            group_fixture_contracts[stable_stage_id] = group_fixture_contract(stage)
            stage_leg_limits[stable_stage_id] = 1
        elif stage_type == "league_table":
            fixtures, bands = _validate_league_stage(stage, team_ids)
            league_fixtures[stable_stage_id] = fixtures
            league_qualification_bands[stable_stage_id] = bands
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
    championship_terminals = [
        stage_id
        for stage_id, stage in stages_by_id.items()
        if stage.get("type") == "knockout" and stage.get("terminal") == "championship"
    ]
    if len(championship_terminals) != 1:
        raise TournamentValidationError(
            "tournament must define exactly one knockout championship terminal"
        )
    championship_stage_id = championship_terminals[0]
    for bands in league_qualification_bands.values():
        for destination in bands:
            if destination == "eliminated":
                continue
            destination_stage = stages_by_id.get(destination)
            if destination_stage is None:
                raise TournamentValidationError(
                    "league qualification band destination references an unknown stage"
                )
            if destination_stage.get("type") != "knockout":
                raise TournamentValidationError(
                    "league qualification band destination must reference a knockout stage"
                )
    dependencies: dict[str, set[str]] = {stage_id: set() for stage_id in stage_ids}
    league_source_ranks: dict[tuple[str, str], set[int]] = {}
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
                        group_teams = groups.get(group_id)
                        if not isinstance(group_teams, Sequence) or source_rank > len(group_teams):
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
                    league_source_ranks.setdefault(
                        (source_stage_id, target_stage_id),
                        set(),
                    ).add(source_rank)
            elif source_type == "match_winner":
                match_id = str(source["match_id"])
                owner_stage_id = tie_owners.get(match_id)
                if owner_stage_id is None:
                    raise TournamentValidationError("match_winner entrant references an unknown tie")
                dependencies[target_stage_id].add(owner_stage_id)
            else:
                if source["team_id"] not in team_ids:
                    raise TournamentValidationError("team entrant references an unknown team")
    band_contracts = {
        (league_stage_id, destination): set(ranks)
        for league_stage_id, bands in league_qualification_bands.items()
        for destination, ranks in bands.items()
        if destination != "eliminated"
    }
    if set(band_contracts) != set(league_source_ranks) or any(
        band_contracts[key] != league_source_ranks[key]
        for key in band_contracts.keys() & league_source_ranks.keys()
    ):
        raise TournamentValidationError(
            "league qualification bands do not align with league_rank pairing sources"
        )
    if any(
        championship_stage_id in required_stages
        for required_stages in dependencies.values()
    ):
        raise TournamentValidationError(
            "championship terminal must be a graph sink"
        )
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
    completed_knockout_ties: dict[tuple[str, str], list[CompletedMatch]] = {}
    completed_by_stage: dict[str, list[CompletedMatch]] = {}
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
        completed_by_stage.setdefault(match.stage_id, []).append(match)
        if match.leg > stage_leg_limits[match.stage_id]:
            raise TournamentValidationError("completed match leg exceeds stage contract")
        stage = stages_by_id[match.stage_id]
        if stage.get("type") == "knockout":
            if tie_owners.get(match.match_id) != match.stage_id:
                raise TournamentValidationError(
                    "completed knockout match is not a configured tie"
                )
            completed_knockout_ties.setdefault(
                (match.stage_id, match.match_id),
                [],
            ).append(match)
        elif match.winner_team_id is not None:
            if match.score.home == match.score.away:
                raise TournamentValidationError(
                    "completed group or league draw cannot declare a winner"
                )
            score_winner = (
                match.home_team_id
                if match.score.home > match.score.away
                else match.away_team_id
            )
            if match.winner_team_id != score_winner:
                raise TournamentValidationError("completed match winner contradicts score")
        if match.stage_id in group_memberships:
            membership = group_memberships[match.stage_id]
            if (
                match.home_team_id not in membership
                or match.away_team_id not in membership
                or membership[match.home_team_id] != membership[match.away_team_id]
            ):
                raise TournamentValidationError("completed group match teams must share the same configured group")
            expected_identity = group_fixture_contracts[match.stage_id].get(match.match_id)
            if expected_identity != (
                match.home_team_id,
                match.away_team_id,
                match.leg,
            ):
                raise TournamentValidationError(
                    "completed group match does not match the generated group fixture contract"
                )
        if match.stage_id in league_fixtures:
            configured_pair = league_fixtures[match.stage_id].get(match.match_id)
            if configured_pair != (match.home_team_id, match.away_team_id):
                raise TournamentValidationError("completed league match must reference a configured league fixture")
    completed_winners: dict[str, str] = {}
    locked_entrants_by_stage: dict[str, set[str]] = {}
    locked_pairs_by_stage: dict[str, dict[str, tuple[str, str]]] = {}
    for (stage_id, match_id), matches in sorted(completed_knockout_ties.items()):
        locked_entrants = locked_entrants_by_stage.setdefault(stage_id, set())
        team_pair = {matches[0].home_team_id, matches[0].away_team_id}
        if locked_entrants & team_pair:
            raise TournamentValidationError(
                "a locked entrant cannot be reserved in more than one tie"
            )
        locked_entrants.update(team_pair)
        locked_pairs_by_stage.setdefault(stage_id, {})[match_id] = (
            _knockout_fact_pair(stages_by_id[stage_id], matches[0])
        )
        winner = _completed_knockout_winner(stages_by_id[stage_id], matches)
        if winner is not None:
            completed_winners[match_id] = winner

    tie_sources: dict[str, tuple[Mapping[str, object], ...]] = {}
    for stage in tournament.stages:
        if stage.get("type") != "knockout":
            continue
        pairing = stage["pairing"]
        assert isinstance(pairing, Mapping)
        ties = pairing["ties"]
        assert isinstance(ties, Sequence)
        for tie in ties:
            assert isinstance(tie, Mapping)
            entrants = tie["entrants"]
            assert isinstance(entrants, Sequence)
            tie_sources[str(tie["id"])] = tuple(
                entrant for entrant in entrants if isinstance(entrant, Mapping)
            )

    for (stage_id, match_id), _matches in sorted(completed_knockout_ties.items()):
        stage = stages_by_id[stage_id]
        pairing = stage["pairing"]
        assert isinstance(pairing, Mapping)
        sources = (
            tie_sources[match_id]
            if pairing["mode"] == "fixed"
            else knockout_sources[stage_id]
        )
        rank_stages = {
            (
                "league" if source.get("type") == "league_rank" else "group",
                str(source["stage_id"]),
            )
            for source in sources
            if source.get("type") in {"group_rank", "best_additional", "league_rank"}
        }
        for rank_stage_type, rank_stage_id in sorted(rank_stages):
            source_facts = completed_by_stage.get(rank_stage_id, [])
            if rank_stage_type == "group":
                completed_contract = {
                    match.match_id: (
                        match.home_team_id,
                        match.away_team_id,
                        match.leg,
                    )
                    for match in source_facts
                }
                if completed_contract != group_fixture_contracts[rank_stage_id]:
                    raise TournamentValidationError(
                        "completed rank-fed tie requires every generated group fixture"
                    )
            elif {match.match_id for match in source_facts} != set(
                league_fixtures[rank_stage_id]
            ):
                raise TournamentValidationError(
                    "completed rank-fed tie requires every configured league fixture"
                )

    for (_, match_id), matches in sorted(completed_knockout_ties.items()):
        required_winners: list[str] = []
        for source in tie_sources[match_id]:
            if source.get("type") != "match_winner":
                continue
            ancestor_match_id = str(source["match_id"])
            ancestor_winner = completed_winners.get(ancestor_match_id)
            if ancestor_winner is None:
                raise TournamentValidationError(
                    "completed match_winner ancestor must also be fully completed"
                )
            required_winners.append(ancestor_winner)
        if required_winners:
            locked_pair = {matches[0].home_team_id, matches[0].away_team_id}
            required_set = set(required_winners)
            is_consistent = (
                required_set == locked_pair
                if len(required_winners) == 2
                else required_set <= locked_pair
            )
            if not is_consistent:
                raise TournamentValidationError(
                    "completed downstream tie contradicts completed ancestor winners"
                )

    resolved_state = QualificationState(match_winners=dict(completed_winners))
    for stage_id, expected_contract in group_fixture_contracts.items():
        source_facts = completed_by_stage.get(stage_id, [])
        completed_contract = {
            match.match_id: (
                match.home_team_id,
                match.away_team_id,
                match.leg,
            )
            for match in source_facts
        }
        if completed_contract != expected_contract:
            continue
        table_matches = tuple(
            TableMatch(
                match.match_id,
                match.home_team_id,
                match.away_team_id,
                match.score,
                match.leg,
            )
            for match in source_facts
        )
        group_rankings, best_additional, _qualified = calculate_group_tables(
            stages_by_id[stage_id],
            table_matches,
            ratings=tournament.ratings,
        )
        resolved_state.group_rankings[stage_id] = {
            group_id: tuple(row.team_id for row in rows)
            for group_id, rows in group_rankings.items()
        }
        resolved_state.best_additional[stage_id] = best_additional

    for stage_id, configured_fixtures in league_fixtures.items():
        source_facts = completed_by_stage.get(stage_id, [])
        if {match.match_id for match in source_facts} != set(configured_fixtures):
            continue
        table_matches = tuple(
            TableMatch(
                match.match_id,
                match.home_team_id,
                match.away_team_id,
                match.score,
                match.leg,
            )
            for match in source_facts
        )
        league_rankings = calculate_league_table(
            stages_by_id[stage_id],
            table_matches,
            ratings=tournament.ratings,
        )
        resolved_state.league_rankings[stage_id] = tuple(
            row.team_id for row in league_rankings
        )

    for stage_id, stage in sorted(stages_by_id.items()):
        if stage.get("type") != "knockout":
            continue
        pairing = stage["pairing"]
        assert isinstance(pairing, Mapping)
        ties = pairing["ties"]
        assert isinstance(ties, Sequence)
        mode = str(pairing["mode"])
        locked_pairs = locked_pairs_by_stage.get(stage_id, {})
        selected_ties = tuple(
            tie
            for tie in ties
            if isinstance(tie, Mapping)
            and _tie_is_resolved(tie, resolved_state)
        )
        if not selected_ties and not locked_pairs:
            continue
        resolved_ties = resolve_ties(selected_ties, resolved_state)
        validate_locked_pairs(
            mode,
            resolved_ties,
            locked_pairs,
            configured_tie_ids=tuple(
                str(tie["id"])
                for tie in ties
                if isinstance(tie, Mapping)
            ),
        )

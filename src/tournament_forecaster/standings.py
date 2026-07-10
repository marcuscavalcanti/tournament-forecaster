"""Competition-neutral league-table calculation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import TournamentValidationError

if TYPE_CHECKING:
    from .domain import Score


DEFAULT_RATING = 1500.0
DEFAULT_POINTS: Mapping[str, int] = {"win": 3, "draw": 1, "loss": 0}
DEFAULT_TIEBREAKERS = (
    "points",
    "goal_difference",
    "goals_for",
    "wins",
    "rating",
)


@dataclass(frozen=True, slots=True)
class Fixture:
    match_id: str
    home_team_id: str
    away_team_id: str


@dataclass(frozen=True, slots=True)
class TableMatch:
    match_id: str
    home_team_id: str
    away_team_id: str
    score: Score
    leg: int = 1


@dataclass(frozen=True, slots=True)
class StandingRow:
    team_id: str
    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    points: int
    rating: float

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


def rank_standing_rows(
    rows: Sequence[StandingRow],
    tiebreakers: Sequence[str] = DEFAULT_TIEBREAKERS,
) -> tuple[StandingRow, ...]:
    """Rank rows by ordered rules with team ID as the stable final fallback."""

    ranked = sorted(rows, key=lambda row: row.team_id)
    for rule in reversed(tuple(tiebreakers)):
        if rule == "team_id":
            ranked.sort(key=lambda row: row.team_id)
        elif rule in {"points", "goal_difference", "goals_for", "wins", "rating"}:
            ranked.sort(key=lambda row: _descending_value(row, rule), reverse=True)
        else:
            raise TournamentValidationError(f"unsupported standings tiebreaker: {rule}")
    return tuple(ranked)


def _descending_value(row: StandingRow, rule: str) -> float:
    if rule == "points":
        return float(row.points)
    if rule == "goal_difference":
        return float(row.goal_difference)
    if rule == "goals_for":
        return float(row.goals_for)
    if rule == "wins":
        return float(row.wins)
    return row.rating


def calculate_standings(
    team_ids: Sequence[str],
    matches: Sequence[TableMatch],
    *,
    ratings: Mapping[str, float],
    points: Mapping[str, int] = DEFAULT_POINTS,
    tiebreakers: Sequence[str] = DEFAULT_TIEBREAKERS,
) -> tuple[StandingRow, ...]:
    """Calculate and rank a table from completed or simulated matches."""

    ordered_team_ids = tuple(sorted(set(team_ids)))
    statistics = {
        team_id: {
            "played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": 0,
            "goals_against": 0,
            "points": 0,
        }
        for team_id in ordered_team_ids
    }
    point_values = {result: int(points[result]) for result in ("win", "draw", "loss")}

    for match in matches:
        if match.home_team_id not in statistics or match.away_team_id not in statistics:
            raise TournamentValidationError("table match references a team outside the table")
        home = statistics[match.home_team_id]
        away = statistics[match.away_team_id]
        home["played"] += 1
        away["played"] += 1
        home["goals_for"] += match.score.home
        home["goals_against"] += match.score.away
        away["goals_for"] += match.score.away
        away["goals_against"] += match.score.home
        if match.score.home > match.score.away:
            home["wins"] += 1
            away["losses"] += 1
            home["points"] += point_values["win"]
            away["points"] += point_values["loss"]
        elif match.score.home < match.score.away:
            away["wins"] += 1
            home["losses"] += 1
            away["points"] += point_values["win"]
            home["points"] += point_values["loss"]
        else:
            home["draws"] += 1
            away["draws"] += 1
            home["points"] += point_values["draw"]
            away["points"] += point_values["draw"]

    rows = tuple(
        StandingRow(
            team_id=team_id,
            rating=float(ratings.get(team_id, DEFAULT_RATING)),
            **statistics[team_id],
        )
        for team_id in ordered_team_ids
    )
    return rank_standing_rows(rows, tiebreakers)


def calculate_group_tables(
    stage: Mapping[str, object],
    matches: Sequence[TableMatch],
    *,
    ratings: Mapping[str, float],
) -> tuple[
    dict[str, tuple[StandingRow, ...]],
    tuple[str, ...],
    tuple[str, ...],
]:
    """Resolve group tables and configured direct/additional qualification."""

    groups = stage["groups"]
    assert isinstance(groups, Mapping)
    points = stage.get("points", DEFAULT_POINTS)
    tiebreakers = stage.get("tiebreakers", DEFAULT_TIEBREAKERS)
    assert isinstance(points, Mapping)
    assert isinstance(tiebreakers, Sequence)
    rankings: dict[str, tuple[StandingRow, ...]] = {}
    for group_id in sorted(groups):
        roster = tuple(str(team_id) for team_id in groups[group_id])  # type: ignore[arg-type]
        roster_set = set(roster)
        group_matches = tuple(
            match
            for match in matches
            if match.home_team_id in roster_set and match.away_team_id in roster_set
        )
        rankings[str(group_id)] = calculate_standings(
            roster,
            group_matches,
            ratings=ratings,
            points=points,  # type: ignore[arg-type]
            tiebreakers=tiebreakers,  # type: ignore[arg-type]
        )

    qualification = stage.get("qualification", {})
    assert isinstance(qualification, Mapping)
    direct_per_group = int(qualification.get("direct_per_group", 0))
    additional_count = int(qualification.get("best_additional", 0))
    direct = tuple(
        row.team_id
        for group_id in sorted(rankings)
        for row in rankings[group_id][:direct_per_group]
    )
    additional_candidates = tuple(
        row
        for group_id in sorted(rankings)
        for row in rankings[group_id][direct_per_group:]
    )
    best_additional = tuple(
        row.team_id
        for row in rank_standing_rows(additional_candidates, tiebreakers)[:additional_count]
    )
    return rankings, best_additional, direct + best_additional


def calculate_league_table(
    stage: Mapping[str, object],
    matches: Sequence[TableMatch],
    *,
    ratings: Mapping[str, float],
) -> tuple[StandingRow, ...]:
    """Resolve one league table from configured fixtures and completed matches."""

    fixture_values = stage["fixtures"]
    assert isinstance(fixture_values, Sequence)
    team_ids = sorted(
        {
            str(team_id)
            for fixture in fixture_values
            if isinstance(fixture, Mapping)
            for team_id in (fixture["home_team_id"], fixture["away_team_id"])
        }
    )
    points = stage.get("points", DEFAULT_POINTS)
    tiebreakers = stage.get("tiebreakers", DEFAULT_TIEBREAKERS)
    assert isinstance(points, Mapping)
    assert isinstance(tiebreakers, Sequence)
    return calculate_standings(
        team_ids,
        matches,
        ratings=ratings,
        points=points,  # type: ignore[arg-type]
        tiebreakers=tiebreakers,  # type: ignore[arg-type]
    )

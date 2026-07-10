"""Explicit-fixture league-stage simulation."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ..domain import CompletedMatch, Score
from ..errors import TournamentValidationError
from ..probabilities import DEFAULT_RATING, simulate_score
from ..standings import (
    Fixture,
    StandingRow,
    TableMatch,
    calculate_league_table,
)


ScoreSimulator = Callable[[float, float, random.Random], Score]


@dataclass(frozen=True, slots=True)
class LeagueStageResult:
    stage_id: str
    fixtures: tuple[Fixture, ...]
    matches: tuple[TableMatch, ...]
    rankings: tuple[StandingRow, ...]
    qualification_bands: Mapping[str, tuple[str, ...]]


def _fixtures(stage: Mapping[str, object]) -> tuple[Fixture, ...]:
    fixture_values = stage.get("fixtures")
    if not isinstance(fixture_values, Sequence) or isinstance(fixture_values, (str, bytes)):
        raise TournamentValidationError("league fixtures must be a sequence")
    fixtures = [
        Fixture(
            match_id=str(fixture["match_id"]),
            home_team_id=str(fixture["home_team_id"]),
            away_team_id=str(fixture["away_team_id"]),
        )
        for fixture in fixture_values
        if isinstance(fixture, Mapping)
    ]
    if len(fixtures) != len(fixture_values):
        raise TournamentValidationError("league fixture must be a mapping")
    return tuple(sorted(fixtures, key=lambda fixture: fixture.match_id))


def simulate_league_stage(
    stage: Mapping[str, object],
    *,
    ratings: Mapping[str, float],
    completed_matches: Sequence[CompletedMatch],
    rng: random.Random,
    score_simulator: ScoreSimulator = simulate_score,
) -> LeagueStageResult:
    """Simulate missing explicit fixtures and return one ranked league table."""

    stage_id = str(stage["id"])
    fixtures = _fixtures(stage)
    configured = {fixture.match_id: fixture for fixture in fixtures}
    completed: dict[str, CompletedMatch] = {}
    for match in sorted(completed_matches, key=lambda item: (item.match_id, item.leg)):
        if match.stage_id != stage_id:
            continue
        fixture = configured.get(match.match_id)
        if fixture is None:
            raise TournamentValidationError("completed league match is not a configured fixture")
        if frozenset((fixture.home_team_id, fixture.away_team_id)) != frozenset(
            (match.home_team_id, match.away_team_id)
        ):
            raise TournamentValidationError("completed league match teams contradict its fixture")
        completed[match.match_id] = match

    matches: list[TableMatch] = []
    for fixture in fixtures:
        fact = completed.get(fixture.match_id)
        if fact is not None:
            matches.append(TableMatch(fact.match_id, fact.home_team_id, fact.away_team_id, fact.score))
        else:
            matches.append(
                TableMatch(
                    fixture.match_id,
                    fixture.home_team_id,
                    fixture.away_team_id,
                    score_simulator(
                        float(ratings.get(fixture.home_team_id, DEFAULT_RATING)),
                        float(ratings.get(fixture.away_team_id, DEFAULT_RATING)),
                        rng,
                    ),
                )
            )

    rankings = calculate_league_table(
        stage,
        matches,
        ratings=ratings,
    )
    bands: dict[str, tuple[str, ...]] = {}
    band_values = stage.get("qualification_bands", ())
    assert isinstance(band_values, Sequence)
    for band in band_values:
        assert isinstance(band, Mapping)
        ranks = band["ranks"]
        assert isinstance(ranks, Sequence)
        first, last = int(ranks[0]), int(ranks[1])
        bands[str(band["destination"])] = tuple(
            row.team_id for row in rankings[first - 1 : last]
        )
    return LeagueStageResult(stage_id, fixtures, tuple(matches), rankings, bands)

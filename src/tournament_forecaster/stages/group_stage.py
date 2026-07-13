"""Round-robin group fixture generation and simulation."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ..domain import CompletedMatch, Score
from ..errors import TournamentValidationError
from ..group_fixtures import (
    generate_group_fixture_specs,
    group_fixture_match_id as group_fixture_match_id,
)
from ..probabilities import (
    DEFAULT_RATING,
    compose_rating,
    simulate_score,
    stage_home_advantage_points,
)
from ..standings import (
    Fixture,
    StandingRow,
    TableMatch,
    calculate_group_tables,
)


ScoreSimulator = Callable[[float, float, random.Random], Score]


@dataclass(frozen=True, slots=True)
class GroupStageResult:
    stage_id: str
    fixtures: tuple[Fixture, ...]
    matches: tuple[TableMatch, ...]
    rankings: Mapping[str, tuple[StandingRow, ...]]
    best_additional_team_ids: tuple[str, ...]
    qualified_team_ids: tuple[str, ...]


def generate_group_fixtures(stage: Mapping[str, object]) -> tuple[Fixture, ...]:
    """Generate a stable round-robin schedule from normalized group data."""

    return tuple(
        Fixture(
            match_id=fixture.match_id,
            home_team_id=fixture.home_team_id,
            away_team_id=fixture.away_team_id,
        )
        for fixture in generate_group_fixture_specs(stage)
    )


def _completed_results(
    stage_id: str,
    fixtures: Sequence[Fixture],
    completed_matches: Sequence[CompletedMatch],
) -> dict[str, CompletedMatch]:
    configured = {fixture.match_id: fixture for fixture in fixtures}
    completed: dict[str, CompletedMatch] = {}
    for match in sorted(completed_matches, key=lambda item: (item.match_id, item.leg)):
        if match.stage_id != stage_id:
            continue
        fixture = configured.get(match.match_id)
        if fixture is None:
            raise TournamentValidationError("completed group match does not match a generated fixture id")
        if frozenset((fixture.home_team_id, fixture.away_team_id)) != frozenset(
            (match.home_team_id, match.away_team_id)
        ):
            raise TournamentValidationError("completed group match teams contradict its generated fixture")
        completed[match.match_id] = match
    return completed


def simulate_group_stage(
    stage: Mapping[str, object],
    *,
    ratings: Mapping[str, float],
    completed_matches: Sequence[CompletedMatch],
    rng: random.Random,
    score_simulator: ScoreSimulator = simulate_score,
) -> GroupStageResult:
    """Simulate every missing group fixture and calculate qualification."""

    stage_id = str(stage["id"])
    home_advantage = stage_home_advantage_points(stage)
    fixtures = generate_group_fixtures(stage)
    completed = _completed_results(stage_id, fixtures, completed_matches)
    matches: list[TableMatch] = []
    for fixture in fixtures:
        fact = completed.get(fixture.match_id)
        if fact is not None:
            matches.append(
                TableMatch(
                    fact.match_id,
                    fact.home_team_id,
                    fact.away_team_id,
                    fact.score,
                )
            )
            continue
        matches.append(
            TableMatch(
                fixture.match_id,
                fixture.home_team_id,
                fixture.away_team_id,
                score_simulator(
                    compose_rating(
                        float(ratings.get(fixture.home_team_id, DEFAULT_RATING)),
                        home_advantage,
                    ),
                    float(ratings.get(fixture.away_team_id, DEFAULT_RATING)),
                    rng,
                ),
            )
        )

    rankings, best_additional, qualified = calculate_group_tables(
        stage,
        matches,
        ratings=ratings,
    )
    return GroupStageResult(
        stage_id=stage_id,
        fixtures=fixtures,
        matches=tuple(matches),
        rankings=rankings,
        best_additional_team_ids=best_additional,
        qualified_team_ids=qualified,
    )

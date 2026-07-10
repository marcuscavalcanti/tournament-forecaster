"""Round-robin group fixture generation and simulation."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

from ..domain import CompletedMatch, Score
from ..errors import TournamentValidationError
from ..probabilities import DEFAULT_RATING, simulate_score
from ..standings import (
    DEFAULT_POINTS,
    DEFAULT_TIEBREAKERS,
    Fixture,
    StandingRow,
    TableMatch,
    calculate_standings,
    rank_standing_rows,
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

    stage_id = str(stage["id"])
    groups = stage.get("groups")
    if not isinstance(groups, Mapping):
        raise TournamentValidationError("group stage groups must be a mapping")
    rounds_value = stage.get("rounds_per_pair", 1)
    if isinstance(rounds_value, bool) or not isinstance(rounds_value, int):
        raise TournamentValidationError("group rounds per pair must be an integer")
    rounds = rounds_value
    fixtures: list[Fixture] = []
    for group_id in sorted(groups):
        roster = groups[group_id]
        if not isinstance(roster, Sequence) or isinstance(roster, (str, bytes)):
            raise TournamentValidationError("group roster must be a sequence")
        for first, second in combinations(sorted(str(team_id) for team_id in roster), 2):
            for round_number in range(1, rounds + 1):
                home, away = (first, second) if round_number % 2 else (second, first)
                fixtures.append(
                    Fixture(
                        match_id=f"{stage_id}-{first}-vs-{second}-{round_number}",
                        home_team_id=home,
                        away_team_id=away,
                    )
                )
    return tuple(fixtures)


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
                    float(ratings.get(fixture.home_team_id, DEFAULT_RATING)),
                    float(ratings.get(fixture.away_team_id, DEFAULT_RATING)),
                    rng,
                ),
            )
        )

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
    return GroupStageResult(
        stage_id=stage_id,
        fixtures=fixtures,
        matches=tuple(matches),
        rankings=rankings,
        best_additional_team_ids=best_additional,
        qualified_team_ids=direct + best_additional,
    )

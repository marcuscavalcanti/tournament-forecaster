from __future__ import annotations

import pytest

from tournament_forecaster.domain import Score
from tournament_forecaster.probabilities import wilson_interval
from tournament_forecaster.standings import TableMatch, calculate_standings


def _cyclic_results() -> tuple[TableMatch, ...]:
    return (
        TableMatch("match-1", "alpha", "beta", Score(5, 4)),
        TableMatch("match-2", "beta", "gamma", Score(4, 0)),
        TableMatch("match-3", "gamma", "alpha", Score(5, 0)),
    )


def test_standings_apply_configured_points_and_ordered_tiebreakers() -> None:
    ratings = {"alpha": 1500.0, "beta": 1500.0, "gamma": 1500.0}

    by_goal_difference = calculate_standings(
        ("gamma", "beta", "alpha"),
        _cyclic_results(),
        ratings=ratings,
        points={"win": 2, "draw": 1, "loss": 1},
        tiebreakers=("points", "goal_difference", "team_id"),
    )
    by_goals_for = calculate_standings(
        ("gamma", "beta", "alpha"),
        _cyclic_results(),
        ratings=ratings,
        points={"win": 2, "draw": 1, "loss": 1},
        tiebreakers=("points", "goals_for", "team_id"),
    )

    assert [row.team_id for row in by_goal_difference] == ["beta", "gamma", "alpha"]
    assert [row.team_id for row in by_goals_for] == ["beta", "alpha", "gamma"]
    assert {row.points for row in by_goal_difference} == {3}


def test_standings_support_rating_and_stable_team_id_fallbacks() -> None:
    by_rating = calculate_standings(
        ("charlie", "alpha", "bravo"),
        (),
        ratings={"alpha": 1400.0, "bravo": 1700.0, "charlie": 1500.0},
        tiebreakers=("points", "rating"),
    )
    by_stable_id = calculate_standings(
        ("charlie", "alpha", "bravo"),
        (),
        ratings={},
        tiebreakers=("points",),
    )

    assert [row.team_id for row in by_rating] == ["bravo", "charlie", "alpha"]
    assert [row.team_id for row in by_stable_id] == ["alpha", "bravo", "charlie"]


@pytest.mark.parametrize(
    ("successes", "iterations", "expected"),
    [
        (50, 100, (0.4038, 0.5962)),
        (0, 100, (0.0, 0.0370)),
        (100, 100, (0.9630, 1.0)),
    ],
)
def test_wilson_interval_is_deterministic_and_clipped(
    successes: int,
    iterations: int,
    expected: tuple[float, float],
) -> None:
    first = wilson_interval(successes, iterations, 0.95)
    second = wilson_interval(successes, iterations, 0.95)

    assert first == second
    assert first == pytest.approx(expected, abs=0.0001)

import math
import random

import pytest

from tournament_forecaster.probabilities import predict_match_outcomes, simulate_score


def test_exact_outcomes_normalize_and_neutral_equal_ratings_are_symmetric() -> None:
    probabilities = predict_match_outcomes(1500.0, 1500.0)

    assert probabilities.home_win == pytest.approx(probabilities.away_win, abs=1e-15)
    assert probabilities.home_win + probabilities.draw + probabilities.away_win == pytest.approx(1.0)
    assert all(
        math.isfinite(value) and 0.0 <= value <= 1.0
        for value in (probabilities.home_win, probabilities.draw, probabilities.away_win)
    )


def test_exact_outcomes_apply_only_explicit_home_advantage() -> None:
    neutral = predict_match_outcomes(1500.0, 1500.0)
    advantaged = predict_match_outcomes(
        1500.0,
        1500.0,
        home_advantage_points=65.0,
    )

    assert neutral.home_win == pytest.approx(neutral.away_win)
    assert advantaged.home_win > advantaged.away_win


def test_generic_score_simulation_has_no_hidden_home_advantage() -> None:
    home_goals = 0
    away_goals = 0
    for seed in range(20_000):
        score = simulate_score(1500.0, 1500.0, random.Random(seed))
        home_goals += score.home
        away_goals += score.away

    assert abs(home_goals - away_goals) < 600

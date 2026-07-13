from __future__ import annotations

import random

from tournament_forecaster.domain import CompletedMatch, Score
from tournament_forecaster.stages.league_stage import simulate_league_stage


def test_league_stage_consumes_explicit_fixtures_locks_results_and_builds_bands() -> None:
    stage = {
        "id": "league",
        "type": "league_table",
        "fixtures": [
            {"match_id": "league-3", "home_team_id": "bravo", "away_team_id": "charlie"},
            {"match_id": "league-1", "home_team_id": "alpha", "away_team_id": "bravo"},
            {"match_id": "league-2", "home_team_id": "alpha", "away_team_id": "charlie"},
        ],
        "points": {"win": 3, "draw": 1, "loss": 0},
        "tiebreakers": ["points", "goal_difference", "goals_for", "team_id"],
        "qualification_bands": [
            {"ranks": [1, 2], "destination": "next-stage"},
            {"ranks": [3, 3], "destination": "eliminated"},
        ],
    }
    completed = CompletedMatch(
        match_id="league-1",
        stage_id="league",
        home_team_id="alpha",
        away_team_id="bravo",
        score=Score(0, 5),
    )
    simulated_pairs: list[tuple[float, float]] = []

    def home_wins(home_rating: float, away_rating: float, rng: random.Random) -> Score:
        del rng
        simulated_pairs.append((home_rating, away_rating))
        return Score(1, 0)

    result = simulate_league_stage(
        stage,
        ratings={"alpha": 1500.0, "bravo": 1600.0, "charlie": 1400.0},
        completed_matches=(completed,),
        rng=random.Random(3),
        score_simulator=home_wins,
    )

    assert [match.match_id for match in result.matches] == ["league-1", "league-2", "league-3"]
    assert result.matches[0].score == Score(0, 5)
    assert len(simulated_pairs) == 2
    assert [row.team_id for row in result.rankings] == ["bravo", "alpha", "charlie"]
    assert result.qualification_bands == {
        "next-stage": ("bravo", "alpha"),
        "eliminated": ("charlie",),
    }


def test_league_stage_composes_extreme_finite_home_advantage_without_overflow() -> None:
    result = simulate_league_stage(
        {
            "id": "league",
            "type": "league_table",
            "fixtures": [
                {
                    "match_id": "league-1",
                    "home_team_id": "alpha",
                    "away_team_id": "bravo",
                }
            ],
            "metadata": {"home_advantage_rating_points": 1e308},
        },
        ratings={"alpha": 1e308, "bravo": 0.0},
        completed_matches=(),
        rng=random.Random(0),
    )

    assert len(result.matches) == 1

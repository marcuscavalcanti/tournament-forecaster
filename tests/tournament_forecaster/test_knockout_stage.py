from __future__ import annotations

import random

from tournament_forecaster.domain import CompletedMatch, Score
from tournament_forecaster.qualification import QualificationState
from tournament_forecaster.stages.knockout_stage import simulate_knockout_stage


def _state() -> QualificationState:
    return QualificationState(
        group_rankings={"groups": {"A": ("alpha", "bravo")}},
    )


def _stage(*, legs: int = 1, away_goals_rule: bool = False) -> dict[str, object]:
    return {
        "id": "final",
        "type": "knockout",
        "pairing": {
            "mode": "fixed",
            "ties": [
                {
                    "id": "final-1",
                    "entrants": [
                        {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 1},
                        {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 2},
                    ],
                }
            ],
        },
        "legs": legs,
        "home_away_order": "seeded_team_second_leg_home",
        "aggregate_tiebreak": "extra_time_then_penalties",
        "away_goals_rule": away_goals_rule,
    }


def test_locked_one_leg_draw_uses_completed_winner_without_resimulation() -> None:
    completed = CompletedMatch(
        match_id="final-1",
        stage_id="final",
        home_team_id="alpha",
        away_team_id="bravo",
        score=Score(1, 1),
        winner_team_id="bravo",
    )

    def forbidden_score(*args: object) -> Score:
        raise AssertionError("a completed knockout match was resimulated")

    result = simulate_knockout_stage(
        _stage(),
        state=_state(),
        ratings={"alpha": 2000.0, "bravo": 1000.0},
        completed_matches=(completed,),
        rng=random.Random(4),
        score_simulator=forbidden_score,
    )

    assert result.winners == {"final-1": "bravo"}
    assert result.matches[0].score == Score(1, 1)


def test_partially_completed_two_leg_tie_simulates_only_the_missing_leg() -> None:
    completed = CompletedMatch(
        match_id="final-1",
        stage_id="final",
        home_team_id="bravo",
        away_team_id="alpha",
        score=Score(0, 3),
        leg=1,
    )
    calls: list[tuple[float, float]] = []

    def missing_leg(home_rating: float, away_rating: float, rng: random.Random) -> Score:
        del rng
        calls.append((home_rating, away_rating))
        return Score(0, 1)

    result = simulate_knockout_stage(
        _stage(legs=2),
        state=_state(),
        ratings={"alpha": 1500.0, "bravo": 1500.0},
        completed_matches=(completed,),
        rng=random.Random(5),
        score_simulator=missing_leg,
    )

    assert calls == [(1500.0, 1500.0)]
    assert [(match.leg, match.home_team_id, match.away_team_id, match.score) for match in result.matches] == [
        (1, "bravo", "alpha", Score(0, 3)),
        (2, "alpha", "bravo", Score(0, 1)),
    ]
    assert result.winners == {"final-1": "alpha"}


def test_two_leg_aggregate_tie_applies_configured_away_goals_rule() -> None:
    completed = (
        CompletedMatch(
            match_id="final-1",
            stage_id="final",
            home_team_id="bravo",
            away_team_id="alpha",
            score=Score(1, 0),
            leg=1,
        ),
        CompletedMatch(
            match_id="final-1",
            stage_id="final",
            home_team_id="alpha",
            away_team_id="bravo",
            score=Score(2, 1),
            leg=2,
        ),
    )

    def forbidden_score(*args: object) -> Score:
        raise AssertionError("a completed knockout leg was resimulated")

    result = simulate_knockout_stage(
        _stage(legs=2, away_goals_rule=True),
        state=_state(),
        ratings={"alpha": 1500.0, "bravo": 1500.0},
        completed_matches=completed,
        rng=random.Random(6),
        score_simulator=forbidden_score,
    )

    assert result.winners == {"final-1": "bravo"}


def test_locked_two_leg_winner_resolves_the_tie_even_when_second_leg_score_differs() -> None:
    completed = (
        CompletedMatch(
            match_id="final-1",
            stage_id="final",
            home_team_id="bravo",
            away_team_id="alpha",
            score=Score(1, 0),
            leg=1,
        ),
        CompletedMatch(
            match_id="final-1",
            stage_id="final",
            home_team_id="alpha",
            away_team_id="bravo",
            score=Score(1, 0),
            leg=2,
            winner_team_id="bravo",
        ),
    )

    def forbidden_score(*args: object) -> Score:
        raise AssertionError("a completed knockout leg was resimulated")

    result = simulate_knockout_stage(
        _stage(legs=2),
        state=_state(),
        ratings={"alpha": 2200.0, "bravo": 800.0},
        completed_matches=completed,
        rng=random.Random(19),
        score_simulator=forbidden_score,
    )

    assert result.winners == {"final-1": "bravo"}


def test_simulated_one_leg_draw_always_resolves_to_one_entrant() -> None:
    def draw(home_rating: float, away_rating: float, rng: random.Random) -> Score:
        del home_rating, away_rating, rng
        return Score(0, 0)

    result = simulate_knockout_stage(
        _stage(),
        state=_state(),
        ratings={"alpha": 1500.0, "bravo": 1500.0},
        completed_matches=(),
        rng=random.Random(7),
        score_simulator=draw,
    )

    assert result.winners["final-1"] in {"alpha", "bravo"}


def test_penalties_tiebreak_uses_a_neutral_shootout_after_a_draw() -> None:
    stage = _stage()
    stage["aggregate_tiebreak"] = "penalties"

    def draw(home_rating: float, away_rating: float, rng: random.Random) -> Score:
        del home_rating, away_rating, rng
        return Score(0, 0)

    result = simulate_knockout_stage(
        stage,
        state=_state(),
        ratings={"alpha": 2400.0, "bravo": 600.0},
        completed_matches=(),
        rng=random.Random(2),
        score_simulator=draw,
    )

    assert result.winners == {"final-1": "bravo"}

from __future__ import annotations

import random

import pytest

from tournament_forecaster.domain import CompletedMatch, Score
from tournament_forecaster.errors import TournamentValidationError
from tournament_forecaster.qualification import QualificationState
from tournament_forecaster.stages.knockout_stage import simulate_knockout_stage


def _state() -> QualificationState:
    return QualificationState(
        group_rankings={"groups": {"A": ("alpha", "bravo")}},
    )


def _four_team_state() -> QualificationState:
    return QualificationState(
        group_rankings={
            "groups": {"A": ("alpha", "bravo", "charlie", "delta")}
        },
    )


def _draw_stage(mode: str) -> dict[str, object]:
    return {
        "id": "semi-finals",
        "type": "knockout",
        "pairing": {
            "mode": mode,
            "ties": [
                {
                    "id": "semi-1",
                    "entrants": [
                        {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 1},
                        {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 2},
                    ],
                },
                {
                    "id": "semi-2",
                    "entrants": [
                        {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 3},
                        {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 4},
                    ],
                },
            ],
        },
        "legs": 1,
        "home_away_order": "listed_team_first_leg_home",
        "aggregate_tiebreak": "extra_time_then_penalties",
        "away_goals_rule": False,
    }


@pytest.mark.parametrize(
    ("mode", "locked_pair", "remaining_pair"),
    [
        ("fixed", ("alpha", "bravo"), {"charlie", "delta"}),
        ("seeded_draw", ("alpha", "delta"), {"bravo", "charlie"}),
        ("open_draw", ("alpha", "delta"), {"bravo", "charlie"}),
    ],
)
def test_partial_lock_reserves_entrants_before_every_pairing_mode(
    mode: str,
    locked_pair: tuple[str, str],
    remaining_pair: set[str],
) -> None:
    completed = CompletedMatch(
        match_id="semi-1",
        stage_id="semi-finals",
        home_team_id=locked_pair[0],
        away_team_id=locked_pair[1],
        score=Score(1, 0),
    )

    result = simulate_knockout_stage(
        _draw_stage(mode),
        state=_four_team_state(),
        ratings={},
        completed_matches=(completed,),
        rng=random.Random(0),
        score_simulator=lambda *_: Score(1, 0),
    )

    pairings = {pairing.match_id: pairing for pairing in result.pairings}
    assert {
        pairings["semi-1"].first_team_id,
        pairings["semi-1"].second_team_id,
    } == set(locked_pair)
    assert {
        pairings["semi-2"].first_team_id,
        pairings["semi-2"].second_team_id,
    } == remaining_pair
    assert len(result.entrant_team_ids) == 4


def test_fixed_partial_lock_must_match_its_declared_sources() -> None:
    completed = CompletedMatch(
        match_id="semi-1",
        stage_id="semi-finals",
        home_team_id="alpha",
        away_team_id="charlie",
        score=Score(1, 0),
    )

    with pytest.raises(TournamentValidationError, match="contradicts fixed pairing"):
        simulate_knockout_stage(
            _draw_stage("fixed"),
            state=_four_team_state(),
            ratings={},
            completed_matches=(completed,),
            rng=random.Random(0),
        )


def test_locked_entrant_cannot_be_reserved_in_two_ties() -> None:
    completed = (
        CompletedMatch(
            match_id="semi-1",
            stage_id="semi-finals",
            home_team_id="alpha",
            away_team_id="bravo",
            score=Score(1, 0),
        ),
        CompletedMatch(
            match_id="semi-2",
            stage_id="semi-finals",
            home_team_id="alpha",
            away_team_id="delta",
            score=Score(0, 1),
        ),
    )

    with pytest.raises(TournamentValidationError, match="locked entrant"):
        simulate_knockout_stage(
            _draw_stage("open_draw"),
            state=_four_team_state(),
            ratings={},
            completed_matches=completed,
            rng=random.Random(0),
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


def test_completed_one_leg_decisive_score_infers_winner_without_rng() -> None:
    class ForbiddenRandom(random.Random):
        def random(self) -> float:
            raise AssertionError("a fully completed decisive tie used randomness")

    completed = CompletedMatch(
        match_id="final-1",
        stage_id="final",
        home_team_id="alpha",
        away_team_id="bravo",
        score=Score(2, 0),
    )

    result = simulate_knockout_stage(
        _stage(),
        state=_state(),
        ratings={},
        completed_matches=(completed,),
        rng=ForbiddenRandom(),
        score_simulator=lambda *_: (_ for _ in ()).throw(
            AssertionError("a completed match was resimulated")
        ),
    )

    assert result.winners == {"final-1": "alpha"}


def test_completed_one_leg_draw_requires_explicit_winner() -> None:
    completed = CompletedMatch(
        match_id="final-1",
        stage_id="final",
        home_team_id="alpha",
        away_team_id="bravo",
        score=Score(1, 1),
    )

    with pytest.raises(TournamentValidationError, match="completed draw requires explicit winner"):
        simulate_knockout_stage(
            _stage(),
            state=_state(),
            ratings={},
            completed_matches=(completed,),
            rng=random.Random(4),
        )


def test_completed_one_leg_winner_cannot_contradict_decisive_score() -> None:
    completed = CompletedMatch(
        match_id="final-1",
        stage_id="final",
        home_team_id="alpha",
        away_team_id="bravo",
        score=Score(2, 0),
        winner_team_id="bravo",
    )

    with pytest.raises(TournamentValidationError, match="contradicts decisive score"):
        simulate_knockout_stage(
            _stage(),
            state=_state(),
            ratings={},
            completed_matches=(completed,),
            rng=random.Random(4),
        )


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


def test_two_leg_stage_applies_home_advantage_to_each_actual_home_side() -> None:
    stage = _stage(legs=2)
    stage["metadata"] = {"home_advantage_rating_points": 25}
    calls: list[tuple[float, float]] = []

    def record_ratings(home_rating: float, away_rating: float, rng: random.Random) -> Score:
        del rng
        calls.append((home_rating, away_rating))
        return Score(1, 0)

    simulate_knockout_stage(
        stage,
        state=_state(),
        ratings={"alpha": 1500.0, "bravo": 1500.0},
        completed_matches=(),
        rng=random.Random(0),
        score_simulator=record_ratings,
    )

    assert calls == [(1525.0, 1500.0), (1525.0, 1500.0)]


def test_knockout_stage_composes_extreme_finite_home_advantage_without_overflow() -> None:
    stage = _stage()
    stage["metadata"] = {"home_advantage_rating_points": 1e308}

    result = simulate_knockout_stage(
        stage,
        state=_state(),
        ratings={"alpha": 1e308, "bravo": 0.0},
        completed_matches=(),
        rng=random.Random(0),
    )

    assert len(result.matches) == 1


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


def test_completed_two_leg_aggregate_draw_requires_explicit_winner() -> None:
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
        ),
    )

    with pytest.raises(TournamentValidationError, match="aggregate draw requires explicit winner"):
        simulate_knockout_stage(
            _stage(legs=2),
            state=_state(),
            ratings={},
            completed_matches=completed,
            rng=random.Random(19),
        )


def test_completed_two_leg_winner_cannot_contradict_decisive_aggregate() -> None:
    completed = (
        CompletedMatch(
            match_id="final-1",
            stage_id="final",
            home_team_id="bravo",
            away_team_id="alpha",
            score=Score(0, 2),
            leg=1,
        ),
        CompletedMatch(
            match_id="final-1",
            stage_id="final",
            home_team_id="alpha",
            away_team_id="bravo",
            score=Score(0, 0),
            leg=2,
            winner_team_id="bravo",
        ),
    )

    with pytest.raises(TournamentValidationError, match="contradicts decisive aggregate"):
        simulate_knockout_stage(
            _stage(legs=2),
            state=_state(),
            ratings={},
            completed_matches=completed,
            rng=random.Random(19),
        )


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


@pytest.mark.parametrize("aggregate_tiebreak", ["extra_time_then_penalties", "penalties"])
@pytest.mark.parametrize(
    ("legs", "home_away_order", "expected_home_team"),
    [
        (1, "listed_team_first_leg_home", "alpha"),
        (2, "listed_team_first_leg_home", "bravo"),
    ],
)
def test_draw_resolution_applies_home_advantage_at_the_deciding_venue(
    aggregate_tiebreak: str,
    legs: int,
    home_away_order: str,
    expected_home_team: str,
) -> None:
    stage = _stage(legs=legs)
    stage["home_away_order"] = home_away_order
    stage["aggregate_tiebreak"] = aggregate_tiebreak
    stage["metadata"] = {"home_advantage_rating_points": 2000}

    winners = [
        simulate_knockout_stage(
            stage,
            state=_state(),
            ratings={"alpha": 1500.0, "bravo": 1500.0},
            completed_matches=(),
            rng=random.Random(seed),
            score_simulator=lambda *_: Score(0, 0),
        ).winners["final-1"]
        for seed in range(50)
    ]

    assert winners.count(expected_home_team) >= 49

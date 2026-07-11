from __future__ import annotations

import random

from tournament_forecaster.domain import CompletedMatch, Score
from tournament_forecaster.stages.group_stage import (
    generate_group_fixtures,
    simulate_group_stage,
)


def test_group_fixture_generation_is_stable_and_alternates_home_teams() -> None:
    stage = {
        "id": "groups",
        "type": "round_robin_groups",
        "groups": {"B": ["charlie", "alpha", "bravo"]},
        "rounds_per_pair": 2,
    }

    fixtures = generate_group_fixtures(stage)

    assert [fixture.match_id for fixture in fixtures] == [
        "groups-group-42-round-1-match-616c706861-627261766f",
        "groups-group-42-round-2-match-616c706861-627261766f",
        "groups-group-42-round-1-match-616c706861-636861726c6965",
        "groups-group-42-round-2-match-616c706861-636861726c6965",
        "groups-group-42-round-1-match-627261766f-636861726c6965",
        "groups-group-42-round-2-match-627261766f-636861726c6965",
    ]
    assert (fixtures[0].home_team_id, fixtures[0].away_team_id) == ("alpha", "bravo")
    assert (fixtures[1].home_team_id, fixtures[1].away_team_id) == ("bravo", "alpha")


def test_group_fixture_ids_are_collision_free_for_delimiter_like_team_ids() -> None:
    stage = {
        "id": "groups",
        "type": "round_robin_groups",
        "groups": {"A": ["a", "a-vs-b", "b-vs-c", "c"]},
        "rounds_per_pair": 1,
    }

    fixtures = generate_group_fixtures(stage)
    ids_by_pair = {
        frozenset((fixture.home_team_id, fixture.away_team_id)): fixture.match_id
        for fixture in fixtures
    }

    assert len(fixtures) == len({fixture.match_id for fixture in fixtures}) == 6
    assert ids_by_pair[frozenset(("a", "b-vs-c"))] != ids_by_pair[
        frozenset(("a-vs-b", "c"))
    ]
    assert all("-group-41-round-1-match-" in fixture.match_id for fixture in fixtures)


def test_completed_group_result_is_locked_and_bypasses_score_simulation() -> None:
    stage = {
        "id": "groups",
        "type": "round_robin_groups",
        "groups": {"A": ["alpha", "bravo"]},
        "qualification": {"direct_per_group": 1, "best_additional": 0},
    }
    completed = CompletedMatch(
        match_id=generate_group_fixtures(stage)[0].match_id,
        stage_id="groups",
        home_team_id="alpha",
        away_team_id="bravo",
        score=Score(0, 7),
    )

    def forbidden_score(*args: object) -> Score:
        raise AssertionError("a completed group match was resimulated")

    result = simulate_group_stage(
        stage,
        ratings={"alpha": 2200.0, "bravo": 800.0},
        completed_matches=(completed,),
        rng=random.Random(1),
        score_simulator=forbidden_score,
    )

    assert result.matches[0].score == Score(0, 7)
    assert [row.team_id for row in result.rankings["A"]] == ["bravo", "alpha"]
    assert result.qualified_team_ids == ("bravo",)


def test_group_qualification_exposes_direct_and_best_additional_entrants() -> None:
    stage = {
        "id": "groups",
        "type": "round_robin_groups",
        "groups": {
            "B": ["b3", "b1", "b2"],
            "A": ["a3", "a1", "a2"],
        },
        "points": {"win": 3, "draw": 1, "loss": 0},
        "tiebreakers": ["points", "goal_difference", "goals_for", "rating"],
        "qualification": {
            "direct_per_group": 1,
            "best_additional": 1,
            "additional_rank": 2,
        },
    }
    ratings = {
        "a1": 1900.0,
        "a2": 1800.0,
        "a3": 1200.0,
        "b1": 1700.0,
        "b2": 1600.0,
        "b3": 1100.0,
    }

    def stronger_team_wins(home_rating: float, away_rating: float, rng: random.Random) -> Score:
        del rng
        return Score(1, 0) if home_rating > away_rating else Score(0, 1)

    result = simulate_group_stage(
        stage,
        ratings=ratings,
        completed_matches=(),
        rng=random.Random(2),
        score_simulator=stronger_team_wins,
    )

    assert [row.team_id for row in result.rankings["A"]] == ["a1", "a2", "a3"]
    assert [row.team_id for row in result.rankings["B"]] == ["b1", "b2", "b3"]
    assert result.best_additional_team_ids == ("a2",)
    assert result.qualified_team_ids == ("a1", "b1", "a2")


def test_additional_rank_uses_one_locked_candidate_per_group_at_seed_11() -> None:
    stage = {
        "id": "groups",
        "type": "round_robin_groups",
        "groups": {
            "A": ["a1", "a2", "a3", "a4"],
            "B": ["b1", "b2", "b3", "b4"],
        },
        "points": {"win": 3, "draw": 1, "loss": 0},
        "tiebreakers": ["points", "goal_difference", "goals_for", "wins", "rating"],
        "qualification": {
            "direct_per_group": 2,
            "best_additional": 2,
            "additional_rank": 3,
        },
    }
    winners = {
        frozenset(("a1", "a2")): "a1",
        frozenset(("a1", "a3")): "a1",
        frozenset(("a1", "a4")): "a1",
        frozenset(("a2", "a3")): "a2",
        frozenset(("a2", "a4")): "a4",
        frozenset(("a3", "a4")): "a3",
        frozenset(("b1", "b2")): "b1",
        frozenset(("b1", "b3")): "b1",
        frozenset(("b1", "b4")): "b1",
        frozenset(("b2", "b3")): "b2",
        frozenset(("b2", "b4")): "b2",
        frozenset(("b3", "b4")): None,
    }
    completed = []
    for fixture in generate_group_fixtures(stage):
        winner = winners[frozenset((fixture.home_team_id, fixture.away_team_id))]
        score = (
            Score(0, 0)
            if winner is None
            else Score(1, 0)
            if winner == fixture.home_team_id
            else Score(0, 1)
        )
        completed.append(
            CompletedMatch(
                match_id=fixture.match_id,
                stage_id="groups",
                home_team_id=fixture.home_team_id,
                away_team_id=fixture.away_team_id,
                score=score,
            )
        )

    def forbidden_score(*args: object) -> Score:
        raise AssertionError("a locked group match was resimulated")

    result = simulate_group_stage(
        stage,
        ratings={
            "a1": 1800.0,
            "a2": 1700.0,
            "a3": 1600.0,
            "a4": 1500.0,
            "b1": 1400.0,
            "b2": 1300.0,
            "b3": 1200.0,
            "b4": 1100.0,
        },
        completed_matches=tuple(completed),
        rng=random.Random(11),
        score_simulator=forbidden_score,
    )

    assert [row.team_id for row in result.rankings["A"]] == ["a1", "a2", "a3", "a4"]
    assert [row.team_id for row in result.rankings["B"]] == ["b1", "b2", "b3", "b4"]
    assert result.best_additional_team_ids == ("a3", "b3")
    assert result.qualified_team_ids == ("a1", "a2", "b1", "b2", "a3", "b3")
    assert "a4" not in result.qualified_team_ids
    assert len(result.qualified_team_ids) == len(set(result.qualified_team_ids)) == 6
    assert len({team_id[0] for team_id in result.best_additional_team_ids}) == 2

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

from tournament_forecaster import simulate_tournament
from tournament_forecaster.config import load_tournament_document
from tournament_forecaster.domain import SimulationOptions
from tournament_forecaster.errors import TournamentValidationError
from tournament_forecaster.stages.group_stage import generate_group_fixtures


def _document() -> dict[str, object]:
    group_stage = {
        "id": "groups",
        "type": "round_robin_groups",
        "groups": {"B": ["gamma", "delta"], "A": ["beta", "alpha"]},
        "rounds_per_pair": 1,
        "points": {"win": 3, "draw": 1, "loss": 0},
        "tiebreakers": ["points", "goal_difference", "goals_for", "rating"],
        "qualification": {"direct_per_group": 2, "best_additional": 0},
    }
    semi_finals = {
        "id": "semi-finals",
        "type": "knockout",
        "pairing": {
            "mode": "fixed",
            "ties": [
                {
                    "id": "semi-2",
                    "entrants": [
                        {"type": "group_rank", "stage_id": "groups", "group": "B", "rank": 1},
                        {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 2},
                    ],
                },
                {
                    "id": "semi-1",
                    "entrants": [
                        {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 1},
                        {"type": "group_rank", "stage_id": "groups", "group": "B", "rank": 2},
                    ],
                },
            ],
        },
        "legs": 1,
        "home_away_order": "listed_team_first_leg_home",
        "aggregate_tiebreak": "extra_time_then_penalties",
        "away_goals_rule": False,
    }
    final = {
        "id": "final",
        "type": "knockout",
        "pairing": {
            "mode": "fixed",
            "ties": [
                {
                    "id": "final-1",
                    "entrants": [
                        {"type": "match_winner", "match_id": "semi-1"},
                        {"type": "match_winner", "match_id": "semi-2"},
                    ],
                }
            ],
        },
        "legs": 1,
        "home_away_order": "listed_team_first_leg_home",
        "aggregate_tiebreak": "extra_time_then_penalties",
        "away_goals_rule": False,
        "terminal": "championship",
    }
    group_match_ids = {
        frozenset((fixture.home_team_id, fixture.away_team_id)): fixture.match_id
        for fixture in generate_group_fixtures(group_stage)
    }
    return {
        "schema_version": 2,
        "tournament": {"id": "synthetic-cup", "display_name": "Synthetic Cup"},
        "focus_team_id": "alpha",
        "teams": [
            {"id": "delta", "display_name": "Delta"},
            {"id": "alpha", "display_name": "Alpha"},
            {"id": "gamma", "display_name": "Gamma"},
            {"id": "beta", "display_name": "Beta"},
        ],
        # Deliberately not topological: dependencies, then stable IDs, drive traversal.
        "stages": [final, group_stage, semi_finals],
        "ratings": {"alpha": 1600, "beta": 1500, "gamma": 1550, "delta": 1450},
        "completed_matches": [
            {
                "match_id": group_match_ids[frozenset(("alpha", "beta"))],
                "stage_id": "groups",
                "home_team_id": "alpha",
                "away_team_id": "beta",
                "score": {"home": 2, "away": 0},
            },
            {
                "match_id": group_match_ids[frozenset(("delta", "gamma"))],
                "stage_id": "groups",
                "home_team_id": "delta",
                "away_team_id": "gamma",
                "score": {"home": 0, "away": 2},
            },
        ],
    }


def test_complete_tournament_probability_fields_replay_deterministically() -> None:
    tournament = load_tournament_document(_document())
    options = SimulationOptions(seed=29, iterations=600, confidence_level=0.95)

    first = simulate_tournament(tournament, options=options)
    replay = simulate_tournament(tournament, options=options)

    assert first.run_id == replay.run_id
    assert first.stage_probabilities == replay.stage_probabilities
    assert first.stage_order == ("groups", "semi-finals", "final")
    assert first.matchup_probabilities == replay.matchup_probabilities
    assert first.championship_probability == replay.championship_probability
    assert first.confidence_intervals == replay.confidence_intervals
    assert datetime.fromisoformat(first.generated_at).tzinfo is not None
    assert first.generated_at != "1970-01-01T00:00:00+00:00"


def test_run_id_uses_the_same_canonical_order_as_simulation() -> None:
    document = _document()
    stages = document["stages"]
    assert isinstance(stages, list)
    stages.append(
        {
            "id": "league",
            "type": "league_table",
            "fixtures": [
                {"match_id": "league-2", "home_team_id": "gamma", "away_team_id": "delta"},
                {"match_id": "league-1", "home_team_id": "alpha", "away_team_id": "beta"},
            ],
        }
    )
    reordered = deepcopy(document)
    reordered["teams"] = list(reversed(reordered["teams"]))  # type: ignore[arg-type]
    reordered["stages"] = list(reversed(reordered["stages"]))  # type: ignore[arg-type]
    reordered["ratings"] = dict(reversed(list(reordered["ratings"].items())))  # type: ignore[union-attr]
    reordered["completed_matches"] = list(reversed(reordered["completed_matches"]))  # type: ignore[arg-type]
    for stage in reordered["stages"]:  # type: ignore[union-attr]
        assert isinstance(stage, dict)
        if stage["type"] == "round_robin_groups":
            groups = stage["groups"]
            assert isinstance(groups, dict)
            stage["groups"] = {
                group_id: list(reversed(roster))
                for group_id, roster in reversed(list(groups.items()))
            }
        elif stage["type"] == "league_table":
            stage["fixtures"] = list(reversed(stage["fixtures"]))  # type: ignore[arg-type]
        else:
            pairing = stage["pairing"]
            assert isinstance(pairing, dict)
            pairing["ties"] = list(reversed(pairing["ties"]))  # type: ignore[arg-type]

    options = SimulationOptions(seed=53, iterations=300)
    first = simulate_tournament(load_tournament_document(document), options=options)
    second = simulate_tournament(load_tournament_document(reordered), options=options)

    assert first.run_id == second.run_id
    assert first.stage_probabilities == second.stage_probabilities
    assert first.matchup_probabilities == second.matchup_probabilities
    assert first.championship_probability == second.championship_probability


def test_focus_reach_matchups_and_title_probability_share_complete_iteration_counts() -> None:
    tournament = load_tournament_document(_document())
    forecast = simulate_tournament(
        tournament,
        options=SimulationOptions(seed=31, iterations=1_500),
    )

    assert forecast.stage_probabilities["groups"] == 1.0
    assert forecast.stage_probabilities["semi-finals"] == 1.0
    assert 0.0 < forecast.stage_probabilities["final"] < 1.0
    assert 0.0 < forecast.championship_probability <= forecast.stage_probabilities["final"]
    assert forecast.stage_probabilities["groups"] >= forecast.stage_probabilities["semi-finals"]
    assert forecast.stage_probabilities["semi-finals"] >= forecast.stage_probabilities["final"]
    final_matchups = [
        matchup for matchup in forecast.matchup_probabilities if matchup.stage_id == "final"
    ]
    assert {matchup.opponent_team_id for matchup in final_matchups} == {"beta", "gamma"}
    assert sum(matchup.probability for matchup in final_matchups) == pytest.approx(
        forecast.stage_probabilities["final"]
    )
    assert set(forecast.confidence_intervals) == {
        "groups",
        "semi-finals",
        "final",
        "championship_probability",
    }


def test_partial_open_draw_lock_keeps_reach_and_matchup_sums_coherent() -> None:
    document = _document()
    stages = document["stages"]
    completed = document["completed_matches"]
    assert isinstance(stages, list) and isinstance(completed, list)
    semi_finals = next(
        stage
        for stage in stages
        if isinstance(stage, dict) and stage["id"] == "semi-finals"
    )
    semi_finals["pairing"]["mode"] = "open_draw"  # type: ignore[index]
    completed.append(
        {
            "match_id": "semi-1",
            "stage_id": "semi-finals",
            "home_team_id": "alpha",
            "away_team_id": "delta",
            "score": {"home": 1, "away": 0},
        }
    )

    forecast = simulate_tournament(
        load_tournament_document(document),
        options=SimulationOptions(seed=59, iterations=600),
    )

    assert forecast.stage_probabilities["groups"] == 1.0
    assert forecast.stage_probabilities["semi-finals"] == 1.0
    assert forecast.stage_probabilities["final"] == 1.0
    assert forecast.championship_probability <= forecast.stage_probabilities["final"]
    semi_matchups = [
        matchup
        for matchup in forecast.matchup_probabilities
        if matchup.stage_id == "semi-finals"
    ]
    final_matchups = [
        matchup
        for matchup in forecast.matchup_probabilities
        if matchup.stage_id == "final"
    ]
    assert [(matchup.opponent_team_id, matchup.probability) for matchup in semi_matchups] == [
        ("delta", 1.0)
    ]
    assert {matchup.opponent_team_id for matchup in final_matchups} == {"beta", "gamma"}
    assert sum(matchup.probability for matchup in final_matchups) == pytest.approx(1.0)


def test_locked_knockout_path_sets_already_reached_stages_and_title_exactly() -> None:
    document = _document()
    completed = document["completed_matches"]
    assert isinstance(completed, list)
    completed.extend(
        [
            {
                "match_id": "semi-1",
                "stage_id": "semi-finals",
                "home_team_id": "alpha",
                "away_team_id": "delta",
                "score": {"home": 1, "away": 1},
                "winner_team_id": "alpha",
            },
            {
                "match_id": "semi-2",
                "stage_id": "semi-finals",
                "home_team_id": "gamma",
                "away_team_id": "beta",
                "score": {"home": 0, "away": 0},
                "winner_team_id": "gamma",
            },
            {
                "match_id": "final-1",
                "stage_id": "final",
                "home_team_id": "alpha",
                "away_team_id": "gamma",
                "score": {"home": 0, "away": 0},
                "winner_team_id": "alpha",
            },
        ]
    )

    forecast = simulate_tournament(
        load_tournament_document(document),
        options=SimulationOptions(seed=37, iterations=200),
    )

    assert forecast.stage_probabilities == {
        "final": 1.0,
        "groups": 1.0,
        "semi-finals": 1.0,
    }
    assert forecast.championship_probability == 1.0
    assert [
        matchup.to_dict() for matchup in forecast.matchup_probabilities
    ] == [
        {"stage_id": "final", "opponent_team_id": "gamma", "probability": 1.0},
        {"stage_id": "semi-finals", "opponent_team_id": "delta", "probability": 1.0},
    ]


def test_completed_downstream_tie_requires_completed_match_winner_ancestors() -> None:
    document = _document()
    completed = document["completed_matches"]
    assert isinstance(completed, list)
    completed.append(
        {
            "match_id": "final-1",
            "stage_id": "final",
            "home_team_id": "alpha",
            "away_team_id": "gamma",
            "score": {"home": 2, "away": 0},
        }
    )

    with pytest.raises(
        TournamentValidationError,
        match="completed match_winner ancestor",
    ):
        load_tournament_document(document)


def test_completed_downstream_tie_must_match_completed_ancestor_winners() -> None:
    document = _document()
    completed = document["completed_matches"]
    assert isinstance(completed, list)
    completed.extend(
        [
            {
                "match_id": "semi-1",
                "stage_id": "semi-finals",
                "home_team_id": "alpha",
                "away_team_id": "delta",
                "score": {"home": 1, "away": 0},
            },
            {
                "match_id": "semi-2",
                "stage_id": "semi-finals",
                "home_team_id": "gamma",
                "away_team_id": "beta",
                "score": {"home": 0, "away": 1},
            },
            {
                "match_id": "final-1",
                "stage_id": "final",
                "home_team_id": "alpha",
                "away_team_id": "gamma",
                "score": {"home": 2, "away": 0},
            },
        ]
    )

    with pytest.raises(
        TournamentValidationError,
        match="contradicts completed ancestor winners",
    ):
        load_tournament_document(document)


def test_completed_non_focus_draw_is_validated_during_full_traversal() -> None:
    document = _document()
    completed = document["completed_matches"]
    assert isinstance(completed, list)
    completed.append(
        {
            "match_id": "semi-2",
            "stage_id": "semi-finals",
            "home_team_id": "gamma",
            "away_team_id": "beta",
            "score": {"home": 0, "away": 0},
        }
    )

    with pytest.raises(
        TournamentValidationError,
        match="completed draw requires explicit winner",
    ):
        load_tournament_document(document)


def test_placement_terminal_never_determines_the_champion() -> None:
    document = _document()
    stages = document["stages"]
    completed = document["completed_matches"]
    assert isinstance(stages, list) and isinstance(completed, list)
    final = next(stage for stage in stages if isinstance(stage, dict) and stage["id"] == "final")
    final["id"] = "a-championship"
    final["terminal"] = "championship"
    final["pairing"]["ties"][0]["id"] = "championship-1"  # type: ignore[index]
    placement = {
        "id": "z-placement",
        "type": "knockout",
        "pairing": {
            "mode": "fixed",
            "ties": [
                {
                    "id": "placement-1",
                    "entrants": [
                        {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 2},
                        {"type": "group_rank", "stage_id": "groups", "group": "B", "rank": 2},
                    ],
                }
            ],
        },
        "legs": 1,
        "home_away_order": "listed_team_first_leg_home",
        "aggregate_tiebreak": "extra_time_then_penalties",
        "away_goals_rule": False,
        "terminal": "placement",
    }
    stages.append(placement)
    completed.extend(
        [
            {
                "match_id": "semi-1",
                "stage_id": "semi-finals",
                "home_team_id": "alpha",
                "away_team_id": "delta",
                "score": {"home": 1, "away": 0},
            },
            {
                "match_id": "semi-2",
                "stage_id": "semi-finals",
                "home_team_id": "gamma",
                "away_team_id": "beta",
                "score": {"home": 1, "away": 0},
            },
            {
                "match_id": "championship-1",
                "stage_id": "a-championship",
                "home_team_id": "alpha",
                "away_team_id": "gamma",
                "score": {"home": 2, "away": 0},
            },
            {
                "match_id": "placement-1",
                "stage_id": "z-placement",
                "home_team_id": "beta",
                "away_team_id": "delta",
                "score": {"home": 0, "away": 1},
            },
        ]
    )

    forecast = simulate_tournament(
        load_tournament_document(document),
        options=SimulationOptions(seed=47, iterations=50),
    )

    assert forecast.championship_probability == 1.0


def test_simulation_rejects_corrupted_championship_without_exactly_one_tie() -> None:
    document = _document()
    tournament = load_tournament_document(document)
    corrupted_stages = deepcopy(document["stages"])
    championship = next(
        stage
        for stage in corrupted_stages
        if isinstance(stage, dict) and stage.get("terminal") == "championship"
    )
    championship["pairing"]["ties"] = []  # type: ignore[index]
    object.__setattr__(tournament, "stages", tuple(corrupted_stages))

    with pytest.raises(
        TournamentValidationError,
        match="championship terminal must contain exactly one tie",
    ):
        simulate_tournament(
            tournament,
            options=SimulationOptions(seed=59, iterations=1),
        )


def test_simulation_rejects_championship_stage_without_exactly_one_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tournament_forecaster.simulation as simulation_module
    from tournament_forecaster.stages.knockout_stage import KnockoutStageResult

    tournament = load_tournament_document(_document())
    original = simulation_module.simulate_knockout_stage

    def corrupted_terminal_result(*args: object, **kwargs: object) -> KnockoutStageResult:
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        stage = args[0]
        assert isinstance(stage, dict) or hasattr(stage, "get")
        if stage.get("terminal") == "championship":  # type: ignore[union-attr]
            return KnockoutStageResult(
                stage_id=result.stage_id,
                pairings=result.pairings,
                matches=result.matches,
                winners={},
                entrant_team_ids=result.entrant_team_ids,
            )
        return result

    monkeypatch.setattr(
        simulation_module,
        "simulate_knockout_stage",
        corrupted_terminal_result,
    )

    with pytest.raises(
        TournamentValidationError,
        match="championship terminal must resolve exactly one winner",
    ):
        simulate_tournament(
            tournament,
            options=SimulationOptions(seed=61, iterations=1),
        )


def test_higher_focus_rating_increases_title_probability() -> None:
    high_document = _document()
    low_document = deepcopy(high_document)
    high_ratings = high_document["ratings"]
    low_ratings = low_document["ratings"]
    assert isinstance(high_ratings, dict) and isinstance(low_ratings, dict)
    high_ratings["alpha"] = 2200
    low_ratings["alpha"] = 800
    options = SimulationOptions(seed=41, iterations=2_000)

    high = simulate_tournament(load_tournament_document(high_document), options=options)
    low = simulate_tournament(load_tournament_document(low_document), options=options)

    assert high.championship_probability > low.championship_probability


def test_focus_team_override_uses_the_requested_configured_team() -> None:
    forecast = simulate_tournament(
        load_tournament_document(_document()),
        focus_team_id="beta",
        options=SimulationOptions(seed=43, iterations=300),
    )

    assert forecast.focus_team_id == "beta"
    assert forecast.stage_probabilities["groups"] == 1.0
    assert forecast.stage_probabilities["semi-finals"] == 1.0


def test_generic_engine_sources_are_self_contained() -> None:
    package_root = Path(__file__).parents[2] / "src" / "tournament_forecaster"
    task_files = (
        "group_fixtures.py",
        "probabilities.py",
        "standings.py",
        "qualification.py",
        "pairing.py",
        "simulation.py",
        "stages/group_stage.py",
        "stages/league_stage.py",
        "stages/knockout_stage.py",
    )

    for relative_path in task_files:
        assert "tournament_forecaster.compatibility" not in (
            package_root / relative_path
        ).read_text(encoding="utf-8")

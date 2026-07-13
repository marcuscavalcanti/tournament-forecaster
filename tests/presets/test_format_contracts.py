from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import json
from pathlib import Path
import random
from typing import Any, cast

import pytest
import tournament_forecaster

from tournament_forecaster.config import load_tournament
from tournament_forecaster.domain import CompletedMatch, Score, SimulationOptions, Tournament
from tournament_forecaster.qualification import QualificationState
from tournament_forecaster.resources import copy_template, load_bundled_preset, resource_path
from tournament_forecaster.simulation import simulate_tournament
from tournament_forecaster.stages.group_stage import simulate_group_stage
from tournament_forecaster.stages.knockout_stage import simulate_knockout_stage
from tournament_forecaster.stages.league_stage import simulate_league_stage


PRESET_NAMES = (
    "synthetic-cup",
    "world-cup-style",
    "champions-league-style",
    "libertadores-style",
)
TEMPLATE_NAMES = (
    "group-knockout",
    "league-knockout",
    "group-two-leg-knockout",
)
GROUP_PRESET_QUALIFIERS = {
    "synthetic-cup": (
        ("north-city", "river-town", "east-city", "valley-rovers"),
        (),
    ),
    "world-cup-style": (
        (
            "amber-town",
            "blue-harbor",
            "elm-city",
            "flint-rovers",
            "iron-bay",
            "jasper-town",
            "maple-city",
            "noble-rovers",
            "quartz-bay",
            "ridge-town",
            "umber-city",
            "verdant-rovers",
            "cedar-fc",
            "glen-athletic",
            "kelp-fc",
            "orchard-athletic",
        ),
        ("cedar-fc", "glen-athletic", "kelp-fc", "orchard-athletic"),
    ),
    "libertadores-style": (
        (
            "acorn-city",
            "boulder-town",
            "evergreen-club",
            "frontier-rovers",
            "indigo-city",
            "jetty-fc",
            "mesa-rovers",
            "nimbus-athletic",
        ),
        (),
    ),
}


def _stage(tournament: Tournament, stage_id: str) -> Mapping[str, Any]:
    return cast(
        Mapping[str, Any],
        next(stage for stage in tournament.stages if stage["id"] == stage_id),
    )


def _knockout_stages(tournament: Tournament) -> list[Mapping[str, Any]]:
    return [
        cast(Mapping[str, Any], stage)
        for stage in tournament.stages
        if stage["type"] == "knockout"
    ]


def _locked_group_result(tournament: Tournament) -> Any:
    stage = _stage(tournament, "group-stage")
    completed = []
    for fixture in tournament_forecaster.list_group_fixtures(tournament, "group-stage"):
        home_rating = tournament.ratings[fixture.home_team_id]
        away_rating = tournament.ratings[fixture.away_team_id]
        completed.append(
            CompletedMatch(
                match_id=fixture.match_id,
                stage_id="group-stage",
                home_team_id=fixture.home_team_id,
                away_team_id=fixture.away_team_id,
                score=Score(1, 0) if home_rating > away_rating else Score(0, 1),
            )
        )

    def forbidden_score(*_args: object) -> Score:
        raise AssertionError("a fully locked group table simulated a score")

    return simulate_group_stage(
        stage,
        ratings=tournament.ratings,
        completed_matches=tuple(completed),
        rng=random.Random(11),
        score_simulator=forbidden_score,
    )


def _simulate_stage_contracts(tournament: Tournament) -> tuple[dict[str, Any], QualificationState]:
    rng = random.Random(11)
    state = QualificationState()
    results: dict[str, Any] = {}
    for stage_value in tournament.stages:
        stage = cast(Mapping[str, Any], stage_value)
        stage_id = str(stage["id"])
        if stage["type"] == "round_robin_groups":
            group_result = simulate_group_stage(
                stage,
                ratings=tournament.ratings,
                completed_matches=tournament.completed_matches,
                rng=rng,
            )
            state.group_rankings[stage_id] = {
                group_id: tuple(row.team_id for row in rows)
                for group_id, rows in group_result.rankings.items()
            }
            state.best_additional[stage_id] = group_result.best_additional_team_ids
            results[stage_id] = group_result
        elif stage["type"] == "league_table":
            league_result = simulate_league_stage(
                stage,
                ratings=tournament.ratings,
                completed_matches=tournament.completed_matches,
                rng=rng,
            )
            state.league_rankings[stage_id] = tuple(
                row.team_id for row in league_result.rankings
            )
            results[stage_id] = league_result
        else:
            knockout_result = simulate_knockout_stage(
                stage,
                state=state,
                ratings=tournament.ratings,
                completed_matches=tournament.completed_matches,
                rng=rng,
            )
            state.match_winners.update(knockout_result.winners)
            results[stage_id] = knockout_result
    return results, state


@pytest.mark.parametrize("preset_name", PRESET_NAMES)
def test_root_preset_copy_is_byte_identical_to_the_canonical_package_resource(
    preset_name: str,
) -> None:
    repository_root = Path(__file__).parents[2]
    root_copy = repository_root / "presets" / preset_name / "tournament.json"

    with resource_path("data", "presets", preset_name, "tournament.json") as canonical:
        assert root_copy.read_bytes() == canonical.read_bytes()


@pytest.mark.parametrize("preset_name", PRESET_NAMES)
def test_root_preset_provenance_declares_project_authored_redistributable_data(
    preset_name: str,
) -> None:
    repository_root = Path(__file__).parents[2]
    provenance = (repository_root / "presets" / preset_name / "DATA_SOURCES.md").read_text(
        encoding="utf-8"
    )

    assert "project-authored" in provenance.lower()
    assert "synthetic" in provenance.lower()
    assert "redistributable" in provenance.lower()


def test_synthetic_cup_is_an_eight_team_one_leg_quickstart_contract() -> None:
    tournament = load_bundled_preset("synthetic-cup")
    group_stage = _stage(tournament, "group-stage")
    semi_finals = _stage(tournament, "semi-finals")
    final = _stage(tournament, "final")

    assert len(tournament.teams) == 8
    assert group_stage["type"] == "round_robin_groups"
    assert {group_id: list(roster) for group_id, roster in group_stage["groups"].items()} == {
        "A": ["north-city", "river-town", "harbor-united", "summit-athletic"],
        "B": ["east-city", "valley-rovers", "lakeside-fc", "meadow-club"],
    }
    assert semi_finals["pairing"]["mode"] == "fixed"
    assert semi_finals["legs"] == 1
    assert final["legs"] == 1
    assert final["terminal"] == "championship"


def test_world_cup_style_contract_has_direct_and_best_additional_qualifiers_in_a_fixed_one_leg_bracket() -> None:
    tournament = load_bundled_preset("world-cup-style")
    group_stage = _stage(tournament, "group-stage")
    round_of_16 = _stage(tournament, "round-of-16")
    final = _stage(tournament, "final")

    assert group_stage["qualification"] == {
        "direct_per_group": 2,
        "best_additional": 4,
        "additional_rank": 3,
    }
    assert round_of_16["pairing"]["mode"] == "fixed"
    assert round_of_16["legs"] == 1
    assert any(
        entrant["type"] == "best_additional"
        for tie in round_of_16["pairing"]["ties"]
        for entrant in tie["entrants"]
    )
    assert all(stage["legs"] == 1 for stage in _knockout_stages(tournament))
    assert final["terminal"] == "championship"


def test_champions_league_style_contract_has_explicit_league_fixtures_seeded_two_leg_rounds_and_one_leg_final() -> None:
    tournament = load_bundled_preset("champions-league-style")
    league_stage = _stage(tournament, "league-stage")
    quarter_finals = _stage(tournament, "quarter-finals")
    final = _stage(tournament, "final")

    assert league_stage["type"] == "league_table"
    assert league_stage["fixtures"]
    assert league_stage["qualification_bands"]
    assert quarter_finals["pairing"]["mode"] == "seeded_draw"
    assert quarter_finals["legs"] == 2
    assert quarter_finals["home_away_order"] == "seeded_team_second_leg_home"
    assert all(
        stage["legs"] == 2
        for stage in _knockout_stages(tournament)
        if stage.get("terminal") != "championship"
    )
    assert final["legs"] == 1
    assert final["terminal"] == "championship"


def test_libertadores_style_contract_has_groups_fixed_two_leg_ties_and_one_leg_final() -> None:
    tournament = load_bundled_preset("libertadores-style")
    group_stage = _stage(tournament, "group-stage")
    quarter_finals = _stage(tournament, "quarter-finals")
    final = _stage(tournament, "final")

    assert group_stage["type"] == "round_robin_groups"
    assert quarter_finals["pairing"]["mode"] == "fixed"
    assert quarter_finals["legs"] == 2
    assert quarter_finals["home_away_order"] == "listed_team_first_leg_home"
    assert all(
        stage["pairing"]["mode"] == "fixed" and stage["legs"] == 2
        for stage in _knockout_stages(tournament)
        if stage.get("terminal") != "championship"
    )
    assert final["legs"] == 1
    assert final["terminal"] == "championship"


@pytest.mark.parametrize("preset_name", PRESET_NAMES)
def test_packaged_presets_validate_and_exercise_the_real_engine_deterministically(
    preset_name: str,
) -> None:
    tournament = load_bundled_preset(preset_name)
    options = SimulationOptions(seed=20260710, iterations=240)

    first = simulate_tournament(tournament, options=options)
    replay = simulate_tournament(tournament, options=options)

    championship_stages = [
        stage for stage in _knockout_stages(tournament) if stage.get("terminal") == "championship"
    ]
    assert len(championship_stages) == 1
    championship_stage_id = str(championship_stages[0]["id"])
    assert first.stage_probabilities == replay.stage_probabilities
    assert first.matchup_probabilities == replay.matchup_probabilities
    assert first.championship_probability == replay.championship_probability
    assert first.stage_probabilities
    assert first.input_provenance
    assert all(0.0 <= probability <= 1.0 for probability in first.stage_probabilities.values())
    assert 0.0 <= first.championship_probability <= first.stage_probabilities[championship_stage_id]

    matchup_sums: dict[str, float] = defaultdict(float)
    for matchup in first.matchup_probabilities:
        matchup_sums[matchup.stage_id] += matchup.probability
    assert matchup_sums
    for stage_id, probability in matchup_sums.items():
        assert probability == pytest.approx(first.stage_probabilities[stage_id])


@pytest.mark.parametrize("preset_name", PRESET_NAMES)
def test_all_team_championship_probabilities_sum_to_one(preset_name: str) -> None:
    tournament = load_bundled_preset(preset_name)
    options = SimulationOptions(seed=11, iterations=120)

    probabilities = [
        simulate_tournament(tournament, focus_team_id=team.id, options=options).championship_probability
        for team in tournament.teams
    ]

    assert sum(probabilities) == pytest.approx(1.0)


@pytest.mark.parametrize("preset_name", tuple(GROUP_PRESET_QUALIFIERS))
def test_group_presets_have_locked_exact_standings_and_qualifiers(preset_name: str) -> None:
    tournament = load_bundled_preset(preset_name)
    group_stage = _stage(tournament, "group-stage")
    expected_qualified, expected_additional = GROUP_PRESET_QUALIFIERS[preset_name]

    result = _locked_group_result(tournament)

    assert {
        group_id: tuple(row.team_id for row in result.rankings[group_id])
        for group_id in sorted(result.rankings)
    } == {
        group_id: tuple(roster)
        for group_id, roster in sorted(group_stage["groups"].items())
    }
    assert result.best_additional_team_ids == expected_additional
    assert result.qualified_team_ids == expected_qualified


def test_champions_preset_has_fictional_identity_locked_table_and_exact_band() -> None:
    tournament = load_bundled_preset("champions-league-style")
    league_stage = _stage(tournament, "league-stage")
    completed = []
    for fixture in league_stage["fixtures"]:
        home = str(fixture["home_team_id"])
        away = str(fixture["away_team_id"])
        completed.append(
            CompletedMatch(
                match_id=str(fixture["match_id"]),
                stage_id="league-stage",
                home_team_id=home,
                away_team_id=away,
                score=Score(1, 0) if tournament.ratings[home] > tournament.ratings[away] else Score(0, 1),
            )
        )

    def forbidden_score(*_args: object) -> Score:
        raise AssertionError("a fully locked league table simulated a score")

    result = simulate_league_stage(
        league_stage,
        ratings=tournament.ratings,
        completed_matches=tuple(completed),
        rng=random.Random(11),
        score_simulator=forbidden_score,
    )
    expected_ranking = (
        "aster-vale-orbitals",
        "ember-athletic",
        "beacon-town",
        "forge-club",
        "comet-city",
        "grove-rangers",
        "drift-united",
        "horizon-fc",
    )

    assert tournament.focus_team_id == "aster-vale-orbitals"
    assert next(team.display_name for team in tournament.teams if team.id == tournament.focus_team_id) == (
        "Aster Vale Orbitals"
    )
    assert all(team.id != "atlas-fc" and team.display_name != "Atlas FC" for team in tournament.teams)
    assert tuple(row.team_id for row in result.rankings) == expected_ranking
    assert result.qualification_bands == {"quarter-finals": expected_ranking}


@pytest.mark.parametrize("preset_name", PRESET_NAMES)
def test_preset_stage_replay_exercises_seed_pots_and_configured_leg_counts(
    preset_name: str,
) -> None:
    tournament = load_bundled_preset(preset_name)

    first_results, first_state = _simulate_stage_contracts(tournament)
    replay_results, replay_state = _simulate_stage_contracts(tournament)

    assert first_results == replay_results
    assert first_state.league_rankings == replay_state.league_rankings
    for stage in _knockout_stages(tournament):
        stage_id = str(stage["id"])
        result = first_results[stage_id]
        ties = stage["pairing"]["ties"]
        assert len(result.matches) == len(ties) * stage["legs"]
        assert all(
            {match.leg for match in result.matches if match.match_id == tie["id"]}
            == set(range(1, stage["legs"] + 1))
            for tie in ties
        )
        if stage.get("terminal") == "championship":
            assert stage["legs"] == 1
            assert len(result.matches) == 1

    if preset_name == "champions-league-style":
        league_ranking = first_state.league_rankings["league-stage"]
        quarter_final_pairings = first_results["quarter-finals"].pairings
        assert {pairing.first_team_id for pairing in quarter_final_pairings} == set(
            league_ranking[:4]
        )
        assert {pairing.second_team_id for pairing in quarter_final_pairings} == set(
            league_ranking[4:]
        )


@pytest.mark.parametrize("template_name", TEMPLATE_NAMES)
def test_templates_copy_complete_strict_json_and_english_usage_guidance(
    template_name: str,
    tmp_path: Path,
) -> None:
    destination = tmp_path / template_name

    copied = copy_template(template_name, destination)

    assert copied == destination / "tournament.json"
    assert copied.is_file()
    readme = destination / "README.md"
    assert readme.is_file()
    guidance = readme.read_text(encoding="utf-8")
    assert "tournament-forecast validate --config tournament.json" in guidance
    assert "tournament-forecast simulate --config tournament.json" in guidance
    assert "terminal" in guidance.lower()
    assert "completed" in guidance.lower()
    assert "match_id" in guidance

    tournament = load_tournament(copied)
    forecast = simulate_tournament(
        tournament,
        options=SimulationOptions(seed=20260710, iterations=120),
    )
    assert forecast.input_provenance
    assert 0.0 <= forecast.championship_probability <= 1.0


def test_templates_cover_each_documented_stage_family() -> None:
    expected_stage_types = {
        "group-knockout": {"round_robin_groups", "knockout"},
        "league-knockout": {"league_table", "knockout"},
        "group-two-leg-knockout": {"round_robin_groups", "knockout"},
    }
    expected_non_final_legs = {
        "group-knockout": 1,
        "league-knockout": 2,
        "group-two-leg-knockout": 2,
    }

    for template_name in TEMPLATE_NAMES:
        with resource_path("data", "templates", template_name, "tournament.json") as path:
            tournament = load_tournament(path)
        stages = [cast(Mapping[str, Any], stage) for stage in tournament.stages]
        championship = [stage for stage in stages if stage.get("terminal") == "championship"]

        assert {stage["type"] for stage in stages} == expected_stage_types[template_name]
        assert len(championship) == 1
        assert championship[0]["legs"] == 1
        assert any(
            stage["type"] == "knockout"
            and stage.get("terminal") != "championship"
            and stage["legs"] == expected_non_final_legs[template_name]
            for stage in stages
        )


@pytest.mark.parametrize(
    "template_name",
    ("group-knockout", "group-two-leg-knockout"),
)
def test_copied_group_template_lists_real_fixture_ids_and_locks_a_completed_result(
    template_name: str,
    tmp_path: Path,
) -> None:
    copied = copy_template(template_name, tmp_path / template_name)
    tournament = load_tournament(copied)

    fixtures = tournament_forecaster.list_group_fixtures(tournament, "group-stage")

    assert fixtures
    assert len(fixtures) == len({fixture.match_id for fixture in fixtures})
    locked_fixture = fixtures[0]
    document = json.loads(copied.read_text(encoding="utf-8"))
    document["completed_matches"] = [
        {
            "match_id": locked_fixture.match_id,
            "stage_id": "group-stage",
            "home_team_id": locked_fixture.home_team_id,
            "away_team_id": locked_fixture.away_team_id,
            "score": {"home": 7, "away": 0},
        }
    ]
    copied.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    locked_tournament = load_tournament(copied)
    group_stage = _stage(locked_tournament, "group-stage")

    result = simulate_group_stage(
        group_stage,
        ratings=locked_tournament.ratings,
        completed_matches=locked_tournament.completed_matches,
        rng=random.Random(11),
        score_simulator=lambda *_args: Score(0, 0),
    )

    locked_match = next(match for match in result.matches if match.match_id == locked_fixture.match_id)
    assert locked_match.score == Score(7, 0)


@pytest.mark.parametrize(
    "template_name",
    ("group-knockout", "group-two-leg-knockout"),
)
def test_group_template_readme_documents_copy_ready_fixture_listing(
    template_name: str,
) -> None:
    with resource_path("data", "templates", template_name, "README.md") as path:
        guidance = path.read_text(encoding="utf-8")

    assert "from tournament_forecaster import list_group_fixtures, load_tournament" in guidance
    assert 'list_group_fixtures(tournament, "group-stage")' in guidance

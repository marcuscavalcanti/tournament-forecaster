from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from tournament_forecaster.config import load_tournament
from tournament_forecaster.domain import SimulationOptions, Tournament
from tournament_forecaster.resources import copy_template, load_bundled_preset, resource_path
from tournament_forecaster.simulation import simulate_tournament


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

    assert group_stage["qualification"] == {"direct_per_group": 2, "best_additional": 4}
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

from pathlib import Path

from worldcup_brazil.bracket import (
    annotate_knockout_matches_with_bracket,
    brazil_bracket_path,
    hydrate_canonical_configs,
    invalid_configured_knockout_opponents,
)
from worldcup_brazil.pipeline import load_config


def test_example_config_hydrates_official_groups_and_bracket_files() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))

    assert config["groups_config"]["groups"]["C"] == [
        {"name": "Brasil", "code": "BRA"},
        {"name": "Marrocos", "code": "MAR"},
        {"name": "Haiti", "code": "HAI"},
        {"name": "Escócia", "code": "SCO"},
    ]
    assert config["bracket_config"]["round_of_32"][2]["match_id"] == 75
    assert config["bracket_config"]["round_of_32"][2]["slots"] == ["1F", "2C"]
    assert config["bracket_config"]["round_of_32"][3]["match_id"] == 76
    assert config["bracket_config"]["round_of_32"][3]["slots"] == ["1C", "2F"]
    assert config["group_fixtures"][0] == {
        "group": "A",
        "date": "2026-06-11",
        "team_a": "México",
        "team_b": "África do Sul",
        "venue": "Estadio Azteca; Mexico City, MEX",
    }
    assert len(config["group_fixtures"]) == 72


def test_example_group_fixtures_cover_all_group_pairs_once() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))

    for group, teams in config["groups_config"]["groups"].items():
        expected = {
            tuple(sorted((left["name"], right["name"])))
            for index, left in enumerate(teams)
            for right in teams[index + 1 :]
        }
        actual = {
            tuple(sorted((fixture["team_a"], fixture["team_b"])))
            for fixture in config["group_fixtures"]
            if fixture["group"] == group
        }
        assert actual == expected


def test_brazil_group_winner_round_of_32_is_restricted_to_group_f_runner_up() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))

    path = brazil_bracket_path(config, brazil_group="C", brazil_group_position=1)
    round_of_32 = path[0]

    assert round_of_32["phase"] == "16 avos"
    assert round_of_32["match_id"] == 76
    assert round_of_32["brazil_slot"] == "1C"
    assert round_of_32["opponent_slots"] == ["2F"]
    assert round_of_32["allowed_opponent_groups"] == ["F"]
    assert round_of_32["allowed_opponents"] == ["Holanda", "Japão", "Suécia", "Tunísia"]
    assert "Canadá" not in round_of_32["allowed_opponents"]
    assert "Suíça" not in round_of_32["allowed_opponents"]


def test_brazil_group_runner_up_round_of_32_is_restricted_to_group_f_winner() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))

    path = brazil_bracket_path(config, brazil_group="C", brazil_group_position=2)
    round_of_32 = path[0]

    assert round_of_32["match_id"] == 75
    assert round_of_32["brazil_slot"] == "2C"
    assert round_of_32["opponent_slots"] == ["1F"]
    assert round_of_32["allowed_opponent_groups"] == ["F"]
    assert round_of_32["allowed_opponents"] == ["Holanda", "Japão", "Suécia", "Tunísia"]


def test_annotated_knockout_matches_expose_allowed_opponents_to_model_prompts() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))

    annotated = annotate_knockout_matches_with_bracket(config, config["knockout_matches"])
    round_of_32 = [match for match in annotated if match["phase"] == "16 avos"]

    assert len(round_of_32) == 2
    assert all(match["bracket_match_id"] == 76 for match in round_of_32)
    assert all(match["bracket_brazil_slot"] == "1C" for match in round_of_32)
    assert all(match["bracket_opponent_slots"] == ["2F"] for match in round_of_32)
    assert all(match["allowed_opponents"] == ["Holanda", "Japão", "Suécia", "Tunísia"] for match in round_of_32)


def test_invalid_configured_knockout_opponent_is_reported_against_official_bracket() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    config["knockout_matches"] = [
        {
            "phase": "16 avos",
            "opponent": "Canadá",
            "most_likely": True,
            "scenario_pct": 40.0,
            "brazil_pct": 60.0,
        }
    ]

    errors = invalid_configured_knockout_opponents(config)

    assert errors == [
        "16 avos: Canadá não é possível para Brasil 1C; slots oficiais: 2F; candidatos: Holanda, Japão, Suécia, Tunísia."
    ]


def test_hydrate_canonical_configs_keeps_inline_configs_and_avoids_external_reads(tmp_path: Path) -> None:
    config = {
        "groups_config": {"groups": {"C": [{"name": "Brasil", "code": "BRA"}]}},
        "bracket_config": {"round_of_32": []},
    }

    hydrate_canonical_configs(config, base_dir=tmp_path)

    assert config["groups_config"]["groups"]["C"][0]["name"] == "Brasil"
    assert config["bracket_config"]["round_of_32"] == []

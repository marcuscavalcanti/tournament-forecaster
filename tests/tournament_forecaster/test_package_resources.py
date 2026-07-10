from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any
from zipfile import ZipFile


def _require_package() -> None:
    assert importlib.util.find_spec("tournament_forecaster") is not None, (
        "the generic tournament_forecaster package does not exist yet"
    )


def _representative_tournament_document() -> dict[str, object]:
    return {
        "schema_version": 2,
        "tournament": {
            "id": "synthetic-cup",
            "display_name": "Synthetic Cup",
            "season": "2026",
        },
        "focus_team_id": "north-city",
        "teams": [
            {"id": "north-city", "display_name": "North City", "aliases": ["North"]},
            {"id": "south-city", "display_name": "South City"},
            {"id": "east-city", "display_name": "East City"},
            {"id": "west-city", "display_name": "West City"},
        ],
        "stages": [
            {
                "id": "group-stage",
                "type": "round_robin_groups",
                "groups": {
                    "a": ["north-city", "south-city"],
                    "b": ["east-city", "west-city"],
                },
                "rounds_per_pair": 1,
                "points": {"win": 3, "draw": 1, "loss": 0},
                "tiebreakers": ["points", "goal_difference", "goals_for", "wins", "rating"],
                "qualification": {"direct_per_group": 1, "best_additional": 0},
            },
            {
                "id": "league-stage",
                "type": "league_table",
                "fixtures": [
                    {
                        "match_id": "league-1",
                        "home_team_id": "north-city",
                        "away_team_id": "east-city",
                    }
                ],
                "points": {"win": 3, "draw": 1, "loss": 0},
                "tiebreakers": ["points", "goal_difference", "goals_for", "wins"],
                "qualification_bands": [
                    {"ranks": [1, 2], "destination": "final"},
                    {"ranks": [3, 4], "destination": "eliminated"},
                ],
            },
            {
                "id": "final",
                "type": "knockout",
                "pairing": {"mode": "fixed", "ties": []},
                "legs": 1,
                "aggregate_tiebreak": "extra_time_then_penalties",
                "away_goals_rule": False,
            },
        ],
        "ratings": {
            "north-city": 1600,
            "south-city": 1500,
            "east-city": 1450,
            "west-city": 1400,
        },
        "completed_matches": [
            {
                "match_id": "group-a-1",
                "stage_id": "group-stage",
                "home_team_id": "north-city",
                "away_team_id": "south-city",
                "score": {"home": 2, "away": 1},
                "leg": 1,
                "winner_team_id": "north-city",
            }
        ],
    }


def _representative_forecast_document() -> dict[str, object]:
    from tournament_forecaster.domain import Forecast, MatchupProbability

    return Forecast(
        run_id="run-0001",
        generated_at="2026-07-10T12:00:00+00:00",
        tournament_id="synthetic-cup",
        focus_team_id="north-city",
        stage_probabilities={"group-stage": 1.0, "final": 0.25},
        matchup_probabilities=(
            MatchupProbability(
                stage_id="final",
                opponent_team_id="south-city",
                probability=0.4,
            ),
        ),
        championship_probability=0.18,
        confidence_intervals={"championship_probability": (0.12, 0.24)},
        input_provenance=({"kind": "preset", "name": "synthetic-cup"},),
        warnings=("rating coverage is incomplete",),
        council={"enabled": False},
    ).to_dict()


def _schema_resources() -> dict[str, dict[str, object]]:
    _require_package()
    from tournament_forecaster.resources import resource_path

    schemas: dict[str, dict[str, object]] = {}
    for filename in ("tournament.schema.json", "forecast.schema.json"):
        with resource_path("schemas", filename) as path:
            schemas[filename] = json.loads(path.read_text(encoding="utf-8"))
    return schemas


def _draft_2020_validator() -> Any:
    try:
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError as error:
        raise AssertionError(
            "schema contract tests require: uv run --with jsonschema"
        ) from error
    return Draft202012Validator


def test_schema_resources_validate_representative_domain_documents() -> None:
    Draft202012Validator = _draft_2020_validator()
    schemas = _schema_resources()

    tournament_schema = schemas["tournament.schema.json"]
    forecast_schema = schemas["forecast.schema.json"]
    assert {"stable_id", "probability", "score"} <= set(tournament_schema["$defs"])
    assert {"stable_id", "probability", "matchup", "confidence_interval", "provenance"} <= set(
        forecast_schema["$defs"]
    )
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)

    tournament_errors = list(
        Draft202012Validator(tournament_schema).iter_errors(_representative_tournament_document())
    )
    forecast_errors = list(
        Draft202012Validator(forecast_schema).iter_errors(_representative_forecast_document())
    )
    assert tournament_errors == []
    assert forecast_errors == []


def test_schema_resources_reject_adversarial_documents() -> None:
    Draft202012Validator = _draft_2020_validator()
    schemas = _schema_resources()
    tournament_validator = Draft202012Validator(schemas["tournament.schema.json"])
    forecast_validator = Draft202012Validator(schemas["forecast.schema.json"])

    tournament_cases: list[tuple[str, dict[str, object]]] = []

    missing_ratings = deepcopy(_representative_tournament_document())
    missing_ratings.pop("ratings")
    tournament_cases.append(("missing required ratings", missing_ratings))

    extra_root = deepcopy(_representative_tournament_document())
    extra_root["unexpected"] = True
    tournament_cases.append(("extra root property", extra_root))

    unstable_rating_key = deepcopy(_representative_tournament_document())
    ratings = unstable_rating_key["ratings"]
    assert isinstance(ratings, dict)
    ratings["North City"] = ratings.pop("north-city")
    tournament_cases.append(("unstable rating key", unstable_rating_key))

    missing_score_field = deepcopy(_representative_tournament_document())
    score = missing_score_field["completed_matches"][0]["score"]  # type: ignore[index]
    assert isinstance(score, dict)
    score.pop("away")
    tournament_cases.append(("score missing away", missing_score_field))

    invalid_leg = deepcopy(_representative_tournament_document())
    invalid_leg["completed_matches"][0]["leg"] = 0  # type: ignore[index]
    tournament_cases.append(("leg below one", invalid_leg))

    duplicate_group_team = deepcopy(_representative_tournament_document())
    duplicate_group_team["stages"][0]["groups"]["a"] = [  # type: ignore[index]
        "north-city",
        "north-city",
    ]
    tournament_cases.append(("duplicate group roster entry", duplicate_group_team))

    unknown_stage_type = deepcopy(_representative_tournament_document())
    unknown_stage_type["stages"][0]["type"] = "custom_stage"  # type: ignore[index]
    tournament_cases.append(("unknown stage type", unknown_stage_type))

    extra_team_property = deepcopy(_representative_tournament_document())
    extra_team_property["teams"][0]["seed"] = 1  # type: ignore[index]
    tournament_cases.append(("extra team property", extra_team_property))

    extra_fixture_property = deepcopy(_representative_tournament_document())
    extra_fixture_property["stages"][1]["fixtures"][0]["provider_id"] = "x"  # type: ignore[index]
    tournament_cases.append(("extra fixture property", extra_fixture_property))

    for label, document in tournament_cases:
        assert list(tournament_validator.iter_errors(document)), label

    forecast_cases: list[tuple[str, dict[str, object]]] = []

    extra_forecast_root = deepcopy(_representative_forecast_document())
    extra_forecast_root["unexpected"] = True
    forecast_cases.append(("extra forecast root property", extra_forecast_root))

    unstable_stage_key = deepcopy(_representative_forecast_document())
    stage_probabilities = unstable_stage_key["stage_probabilities"]
    assert isinstance(stage_probabilities, dict)
    stage_probabilities["Final Stage"] = 0.1
    forecast_cases.append(("unstable stage probability key", unstable_stage_key))

    missing_matchup_probability = deepcopy(_representative_forecast_document())
    missing_matchup_probability["matchup_probabilities"][0].pop("probability")  # type: ignore[index]
    forecast_cases.append(("matchup missing probability", missing_matchup_probability))

    extra_matchup_property = deepcopy(_representative_forecast_document())
    extra_matchup_property["matchup_probabilities"][0]["label"] = "South"  # type: ignore[index]
    forecast_cases.append(("extra matchup property", extra_matchup_property))

    short_interval = deepcopy(_representative_forecast_document())
    short_interval["confidence_intervals"]["championship_probability"] = [0.12]  # type: ignore[index]
    forecast_cases.append(("short confidence interval", short_interval))

    out_of_range_interval = deepcopy(_representative_forecast_document())
    out_of_range_interval["confidence_intervals"]["championship_probability"] = [  # type: ignore[index]
        0.12,
        1.2,
    ]
    forecast_cases.append(("out of range interval", out_of_range_interval))

    malformed_provenance = deepcopy(_representative_forecast_document())
    malformed_provenance["input_provenance"] = [{}]
    forecast_cases.append(("provenance missing kind", malformed_provenance))

    malformed_warning = deepcopy(_representative_forecast_document())
    malformed_warning["warnings"] = [7]
    forecast_cases.append(("non-text warning", malformed_warning))

    for label, document in forecast_cases:
        assert list(forecast_validator.iter_errors(document)), label


def test_hatchling_packages_generic_and_legacy_surfaces() -> None:
    repository_root = Path(__file__).parents[2]
    pyproject = tomllib.loads((repository_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["build-system"] == {
        "requires": ["hatchling"],
        "build-backend": "hatchling.build",
    }
    assert pyproject["project"]["name"] == "tournament-forecaster"
    assert pyproject["project"]["scripts"] == {
        "tournament-forecast": "tournament_forecaster.cli:main",
        "worldcup-brazil-report": "worldcup_brazil.cli:main",
    }
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/tournament_forecaster",
        "worldcup_brazil",
    ]


def test_built_wheel_exposes_packages_scripts_and_schema_resources_in_isolation(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).parents[2]
    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        cwd=repository_root,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    with ZipFile(wheel) as archive:
        members = set(archive.namelist())
    assert "tournament_forecaster/schemas/tournament.schema.json" in members
    assert "tournament_forecaster/schemas/forecast.schema.json" in members
    assert "worldcup_brazil/cli.py" in members

    venv = tmp_path / "venv"
    create_venv = subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert create_venv.returncode == 0, create_venv.stderr
    venv_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    install = subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python), "--no-deps", str(wheel)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, install.stderr

    probe = f"""
import json
from importlib.metadata import entry_points
from pathlib import Path
import tournament_forecaster
import worldcup_brazil
from tournament_forecaster.resources import resource_path

source_root = Path({str(repository_root)!r}).resolve()
assert source_root not in Path(tournament_forecaster.__file__).resolve().parents
assert source_root not in Path(worldcup_brazil.__file__).resolve().parents
for filename in ("tournament.schema.json", "forecast.schema.json"):
    with resource_path("schemas", filename) as path:
        schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema["$defs"]
commands = {{entry.name for entry in entry_points(group="console_scripts")}}
assert {{"tournament-forecast", "worldcup-brazil-report"}} <= commands
print("isolated wheel resources verified")
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    isolated_probe = subprocess.run(
        [str(venv_python), "-I", "-c", probe],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert isolated_probe.returncode == 0, isolated_probe.stderr
    assert isolated_probe.stdout.strip() == "isolated wheel resources verified"

from __future__ import annotations

import importlib.util
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


def _require_package() -> None:
    assert importlib.util.find_spec("tournament_forecaster") is not None, (
        "the generic tournament_forecaster package does not exist yet"
    )


def _document() -> dict[str, object]:
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
        ],
        "stages": [
            {
                "id": "group-stage",
                "type": "round_robin_groups",
                "groups": {"a": ["north-city", "south-city"]},
            }
        ],
        "ratings": {"north-city": 1600, "south-city": 1500},
        "completed_matches": [
            {
                "match_id": "group-a-1",
                "stage_id": "group-stage",
                "home_team_id": "north-city",
                "away_team_id": "south-city",
                "score": {"home": 2, "away": 1},
            }
        ],
    }


def test_load_tournament_returns_immutable_typed_domain(tmp_path: Path) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament
    from tournament_forecaster.domain import Team, validate_tournament

    path = tmp_path / "tournament.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")

    tournament = load_tournament(path)

    assert tournament.id == "synthetic-cup"
    assert tournament.focus_team_id == "north-city"
    assert tournament.teams == (
        Team(id="north-city", display_name="North City", aliases=("North",)),
        Team(id="south-city", display_name="South City"),
    )
    assert tournament.completed_matches[0].score.home == 2
    validate_tournament(tournament)
    with pytest.raises(FrozenInstanceError):
        tournament.teams[0].id = "rewritten"  # type: ignore[misc]
    with pytest.raises(TypeError):
        tournament.ratings["north-city"] = 0  # type: ignore[index]


def test_loader_rejects_non_ascii_stable_identifier() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    teams = document["teams"]
    assert isinstance(teams, list)
    assert isinstance(teams[0], dict)
    teams[0]["id"] = "north-city-2!"

    with pytest.raises(TournamentValidationError, match="stable ASCII identifier"):
        load_tournament_document(document)


def test_loader_rejects_duplicate_completed_match_and_leg() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    completed_matches = document["completed_matches"]
    assert isinstance(completed_matches, list)
    assert isinstance(completed_matches[0], dict)
    completed_matches.append(completed_matches[0].copy())

    with pytest.raises(TournamentValidationError, match="duplicate completed result"):
        load_tournament_document(document)


def test_loader_rejects_unrecognized_stage_type() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    stages = document["stages"]
    assert isinstance(stages, list)
    assert isinstance(stages[0], dict)
    stages[0]["type"] = "custom_stage"

    with pytest.raises(TournamentValidationError, match="recognized stage type"):
        load_tournament_document(document)


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ({"a": ["north-city", "unknown-city"]}, "configured teams"),
        ({"a": ["north-city", "north-city"]}, "duplicate team"),
        (
            {
                "a": ["north-city", "south-city"],
                "b": ["south-city", "north-city"],
            },
            "multiple groups",
        ),
    ],
)
def test_group_stage_rejects_invalid_roster_references(
    groups: dict[str, list[str]],
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    stages = document["stages"]
    assert isinstance(stages, list)
    assert isinstance(stages[0], dict)
    stages[0]["groups"] = groups

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


@pytest.mark.parametrize(
    ("fixtures", "message"),
    [
        (
            [
                {
                    "match_id": "league-1",
                    "home_team_id": "north-city",
                    "away_team_id": "unknown-city",
                }
            ],
            "configured teams",
        ),
        (
            [
                {
                    "match_id": "league-1",
                    "home_team_id": "north-city",
                    "away_team_id": "south-city",
                },
                {
                    "match_id": "league-1",
                    "home_team_id": "south-city",
                    "away_team_id": "north-city",
                },
            ],
            "fixture match ids must be unique",
        ),
        (
            [
                {
                    "match_id": "league-1",
                    "home_team_id": "north-city",
                    "away_team_id": "north-city",
                }
            ],
            "fixture teams must be distinct",
        ),
    ],
)
def test_league_stage_rejects_invalid_fixture_references(
    fixtures: list[dict[str, str]],
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    document["stages"] = [
        {"id": "league-stage", "type": "league_table", "fixtures": fixtures}
    ]
    document["completed_matches"] = []

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


@pytest.mark.parametrize(
    ("stage", "message"),
    [
        (
            {
                "id": "final",
                "type": "knockout",
                "pairing": {"mode": "random", "ties": []},
                "legs": 1,
            },
            "pairing mode",
        ),
        (
            {
                "id": "final",
                "type": "knockout",
                "pairing": {"mode": "fixed", "ties": []},
                "legs": 3,
            },
            "one or two legs",
        ),
        (
            {
                "id": "final",
                "type": "knockout",
                "pairing": {"mode": "fixed", "ties": "final-1"},
                "legs": 1,
            },
            "ties must be a sequence",
        ),
    ],
)
def test_knockout_stage_rejects_invalid_pairing_contract(
    stage: dict[str, object],
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    document["stages"] = [stage]
    document["completed_matches"] = []

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


@pytest.mark.parametrize(
    ("second_result", "message"),
    [
        ({"stage_id": "other-stage"}, "same stage"),
        ({"away_team_id": "east-city"}, "same team pair"),
    ],
)
def test_completed_match_legs_keep_stable_identity(
    second_result: dict[str, object],
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    teams = document["teams"]
    ratings = document["ratings"]
    assert isinstance(teams, list)
    assert isinstance(ratings, dict)
    teams.append({"id": "east-city", "display_name": "East City"})
    ratings["east-city"] = 1450
    document["stages"] = [
        {
            "id": "semi-final",
            "type": "knockout",
            "pairing": {"mode": "fixed", "ties": []},
            "legs": 2,
        },
        {
            "id": "other-stage",
            "type": "knockout",
            "pairing": {"mode": "fixed", "ties": []},
            "legs": 2,
        },
    ]
    first = {
        "match_id": "semi-final-1",
        "stage_id": "semi-final",
        "home_team_id": "north-city",
        "away_team_id": "south-city",
        "score": {"home": 1, "away": 0},
        "leg": 1,
    }
    second = {**first, "leg": 2, **second_result}
    document["completed_matches"] = [first, second]

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


def test_completed_match_allows_reversed_home_away_order_across_legs() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document

    document = _document()
    document["stages"] = [
        {
            "id": "semi-final",
            "type": "knockout",
            "pairing": {"mode": "fixed", "ties": []},
            "legs": 2,
        }
    ]
    document["completed_matches"] = [
        {
            "match_id": "semi-final-1",
            "stage_id": "semi-final",
            "home_team_id": "north-city",
            "away_team_id": "south-city",
            "score": {"home": 1, "away": 0},
            "leg": 1,
        },
        {
            "match_id": "semi-final-1",
            "stage_id": "semi-final",
            "home_team_id": "south-city",
            "away_team_id": "north-city",
            "score": {"home": 2, "away": 0},
            "leg": 2,
        },
    ]

    tournament = load_tournament_document(document)

    assert len(tournament.completed_matches) == 2


def test_completed_match_rejects_winner_contradicted_by_score() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    completed_matches = document["completed_matches"]
    assert isinstance(completed_matches, list)
    assert isinstance(completed_matches[0], dict)
    completed_matches[0]["winner_team_id"] = "south-city"

    with pytest.raises(TournamentValidationError, match="winner contradicts score"):
        load_tournament_document(document)


@pytest.mark.parametrize(
    "stage",
    [
        {"id": "group-stage", "type": "round_robin_groups", "groups": {"a": ["north-city", "south-city"]}},
        {"id": "league-stage", "type": "league_table", "fixtures": []},
        {
            "id": "final",
            "type": "knockout",
            "pairing": {"mode": "fixed", "ties": []},
            "legs": 1,
        },
    ],
)
def test_completed_match_leg_must_fit_stage_contract(stage: dict[str, object]) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    document["stages"] = [stage]
    completed_matches = document["completed_matches"]
    assert isinstance(completed_matches, list)
    assert isinstance(completed_matches[0], dict)
    completed_matches[0]["stage_id"] = stage["id"]
    completed_matches[0]["leg"] = 2

    with pytest.raises(TournamentValidationError, match="leg exceeds stage contract"):
        load_tournament_document(document)


def test_completed_group_match_rejects_cross_group_teams() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    teams = document["teams"]
    ratings = document["ratings"]
    assert isinstance(teams, list)
    assert isinstance(ratings, dict)
    teams.extend(
        [
            {"id": "east-city", "display_name": "East City"},
            {"id": "west-city", "display_name": "West City"},
        ]
    )
    ratings.update({"east-city": 1450, "west-city": 1400})
    document["stages"] = [
        {
            "id": "group-stage",
            "type": "round_robin_groups",
            "groups": {
                "a": ["north-city", "south-city"],
                "b": ["east-city", "west-city"],
            },
        }
    ]
    completed_matches = document["completed_matches"]
    assert isinstance(completed_matches, list)
    assert isinstance(completed_matches[0], dict)
    completed_matches[0]["away_team_id"] = "east-city"

    with pytest.raises(TournamentValidationError, match="same configured group"):
        load_tournament_document(document)


def test_completed_league_match_must_reference_configured_fixture() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    document["stages"] = [
        {
            "id": "league-stage",
            "type": "league_table",
            "fixtures": [
                {
                    "match_id": "league-1",
                    "home_team_id": "north-city",
                    "away_team_id": "south-city",
                }
            ],
        }
    ]
    completed_matches = document["completed_matches"]
    assert isinstance(completed_matches, list)
    assert isinstance(completed_matches[0], dict)
    completed_matches[0]["stage_id"] = "league-stage"
    completed_matches[0]["match_id"] = "league-2"

    with pytest.raises(TournamentValidationError, match="configured league fixture"):
        load_tournament_document(document)


@pytest.mark.parametrize("rating", [float("nan"), float("inf"), float("-inf")])
def test_loader_rejects_non_finite_rating_from_mapping(rating: float) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    ratings = document["ratings"]
    assert isinstance(ratings, dict)
    ratings["north-city"] = rating

    with pytest.raises(TournamentValidationError, match="finite"):
        load_tournament_document(document)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_json_loader_rejects_non_finite_number_syntax(tmp_path: Path, number: str) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament
    from tournament_forecaster.errors import TournamentValidationError

    path = tmp_path / "tournament.json"
    payload = json.dumps(_document()).replace("1600", number, 1)
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(TournamentValidationError, match="finite"):
        load_tournament(path)


def test_direct_domain_construction_rejects_non_team_values() -> None:
    _require_package()
    from tournament_forecaster.domain import Tournament
    from tournament_forecaster.errors import TournamentValidationError

    with pytest.raises(TournamentValidationError, match="teams must be Team values"):
        Tournament(
            id="synthetic-cup",
            display_name="Synthetic Cup",
            focus_team_id="north-city",
            teams=("north-city",),  # type: ignore[arg-type]
            stages=({"id": "group-stage", "type": "round_robin_groups"},),
            ratings={},
            completed_matches=(),
        )


@pytest.mark.parametrize("aliases", ["North", b"North", None])
def test_team_rejects_malformed_alias_containers(aliases: object) -> None:
    _require_package()
    from tournament_forecaster.domain import Team
    from tournament_forecaster.errors import TournamentValidationError

    with pytest.raises(TournamentValidationError, match="aliases must be a sequence"):
        Team(id="north-city", display_name="North City", aliases=aliases)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["teams", "stages", "completed_matches"])
@pytest.mark.parametrize("value", ["invalid", b"invalid", None])
def test_tournament_rejects_malformed_sequence_containers(field: str, value: object) -> None:
    _require_package()
    from tournament_forecaster.domain import Team, Tournament
    from tournament_forecaster.errors import TournamentValidationError

    values: dict[str, object] = {
        "id": "synthetic-cup",
        "display_name": "Synthetic Cup",
        "focus_team_id": "north-city",
        "teams": (
            Team(id="north-city", display_name="North City"),
            Team(id="south-city", display_name="South City"),
        ),
        "stages": (
            {
                "id": "group-stage",
                "type": "round_robin_groups",
                "groups": {"a": ["north-city", "south-city"]},
            },
        ),
        "ratings": {"north-city": 1600.0, "south-city": 1500.0},
        "completed_matches": (),
    }
    values[field] = value

    with pytest.raises(TournamentValidationError, match=f"{field.replace('_', ' ')} must be a sequence"):
        Tournament(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["matchup_probabilities", "input_provenance", "warnings"])
@pytest.mark.parametrize("value", ["invalid", b"invalid", None])
def test_forecast_rejects_malformed_sequence_containers(field: str, value: object) -> None:
    _require_package()
    from tournament_forecaster.domain import Forecast
    from tournament_forecaster.errors import TournamentValidationError

    values: dict[str, object] = {
        "run_id": "run-0001",
        "generated_at": "2026-07-10T12:00:00+00:00",
        "tournament_id": "synthetic-cup",
        "focus_team_id": "north-city",
        "stage_probabilities": {"group-stage": 1.0},
        "matchup_probabilities": (),
        "championship_probability": 0.18,
        "confidence_intervals": {"championship_probability": (0.12, 0.24)},
        "input_provenance": (),
        "warnings": (),
    }
    values[field] = value

    with pytest.raises(TournamentValidationError, match=field.replace("_", " ")):
        Forecast(**values)  # type: ignore[arg-type]


def test_forecast_serializes_the_versioned_generic_contract() -> None:
    _require_package()
    from tournament_forecaster.domain import Forecast, MatchupProbability

    forecast = Forecast(
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
    )

    assert forecast.to_dict() == {
        "schema_version": 2,
        "run_id": "run-0001",
        "generated_at": "2026-07-10T12:00:00+00:00",
        "tournament_id": "synthetic-cup",
        "focus_team_id": "north-city",
        "stage_probabilities": {"group-stage": 1.0, "final": 0.25},
        "matchup_probabilities": [
            {
                "stage_id": "final",
                "opponent_team_id": "south-city",
                "probability": 0.4,
            }
        ],
        "championship_probability": 0.18,
        "confidence_intervals": {"championship_probability": [0.12, 0.24]},
        "input_provenance": [{"kind": "preset", "name": "synthetic-cup"}],
        "warnings": ["rating coverage is incomplete"],
        "council": {"enabled": False},
    }

from __future__ import annotations

import json
import importlib.util
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

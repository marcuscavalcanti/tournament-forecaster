from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

from scripts.build_world_cup_2026_example import (
    _validate_stage_completion_frontier,
    normalize_fifa_fixture,
)
from tournament_forecaster.backtest import evaluate_backtest
from tournament_forecaster.config import load_tournament
from tournament_forecaster.domain import SimulationOptions
from tournament_forecaster.errors import TournamentValidationError
from tournament_forecaster.simulation import simulate_tournament


ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "examples" / "world-cup-2026-live"
EDGE_FIXTURE = Path(__file__).parent / "fixtures" / "fifa-calendar-edge-cases.json"


def test_saved_fifa_fixture_is_deterministic_and_handles_extra_time_and_quarter_final() -> None:
    payload = json.loads(EDGE_FIXTURE.read_text(encoding="utf-8"))
    codes = {"BEL", "SEN", "ARG", "CPV", "FRA", "MAR", "NOR", "ENG"}

    first = normalize_fifa_fixture(payload, known_codes=codes)
    replay = normalize_fifa_fixture(deepcopy(payload), known_codes=codes)

    assert first == replay
    assert [row["source_id"] for row in first.completed] == [
        "400021521",
        "400021525",
        "400021536",
    ]
    assert [row["result_type"] for row in first.completed[:2]] == [3, 3]
    assert first.completed[2]["stage_id"] == "quarter-finals"
    assert first.completed[2]["fifa_home_team_id"] == "43946"
    assert first.completed[2]["fifa_away_team_id"] == "43872"
    assert [row["source_id"] for row in first.pending] == ["400021539"]


def test_saved_fifa_fixture_fails_closed_on_unknown_conflicting_and_unsupported_rows() -> None:
    payload = json.loads(EDGE_FIXTURE.read_text(encoding="utf-8"))
    codes = {"BEL", "SEN", "ARG", "CPV", "FRA", "MAR", "NOR", "ENG"}

    unknown = deepcopy(payload)
    unknown["Results"][0]["Home"]["Abbreviation"] = "ZZZ"
    with pytest.raises(TournamentValidationError, match="unknown FIFA team"):
        normalize_fifa_fixture(unknown, known_codes=codes)

    conflict = deepcopy(payload)
    duplicate = deepcopy(conflict["Results"][0])
    duplicate["HomeTeamScore"] = 9
    conflict["Results"].append(duplicate)
    with pytest.raises(TournamentValidationError, match="conflicting FIFA match"):
        normalize_fifa_fixture(conflict, known_codes=codes)

    unsupported = deepcopy(payload)
    unsupported["Results"][0]["StageName"] = [{"Description": "Mystery round"}]
    with pytest.raises(TournamentValidationError, match="unsupported FIFA stage"):
        normalize_fifa_fixture(unsupported, known_codes=codes)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("HomeTeamScore", 2.9, "score must be a non-negative integer"),
        ("HomeTeamScore", True, "score must be a non-negative integer"),
        ("ResultType", 1.9, "result type must be an integer"),
        ("ResultType", True, "result type must be an integer"),
        ("MatchNumber", 97.5, "match number must be a positive integer"),
        ("MatchNumber", True, "match number must be a positive integer"),
    ],
)
def test_fifa_fixture_rejects_non_integer_numeric_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = json.loads(EDGE_FIXTURE.read_text(encoding="utf-8"))
    payload["Results"][2][field] = value

    with pytest.raises(TournamentValidationError, match=message):
        normalize_fifa_fixture(
            payload,
            known_codes={"BEL", "SEN", "ARG", "CPV", "FRA", "MAR", "NOR", "ENG"},
        )


def test_fifa_fixture_rejects_completed_kickoff_after_retrieval() -> None:
    payload = json.loads(EDGE_FIXTURE.read_text(encoding="utf-8"))
    payload["Results"][2]["Date"] = "2099-01-01T00:00:00Z"

    with pytest.raises(TournamentValidationError, match="kickoff_at must not be after retrieved_at"):
        normalize_fifa_fixture(
            payload,
            known_codes={"BEL", "SEN", "ARG", "CPV", "FRA", "MAR", "NOR", "ENG"},
            retrieved_at=datetime.fromisoformat("2026-07-13T12:21:03+00:00"),
        )


def test_final_group_draw_does_not_require_a_winner_id() -> None:
    payload = {
        "Results": [
            {
                "IdMatch": "400021449",
                "StageName": [{"Description": "First Stage"}],
                "Date": "2026-06-15T20:00:00Z",
                "Home": {"Abbreviation": "BEL", "IdTeam": "43935"},
                "Away": {"Abbreviation": "SEN", "IdTeam": "43879"},
                "HomeTeamScore": 1,
                "AwayTeamScore": 1,
                "Winner": None,
                "ResultType": 1,
                "MatchNumber": 9,
            }
        ]
    }

    fixture = normalize_fifa_fixture(payload, known_codes={"BEL", "SEN"})

    assert fixture.completed[0]["winner_code"] is None


def test_pending_bracket_row_reads_top_level_winner_placeholders() -> None:
    payload = {
        "Results": [
            {
                "IdMatch": "400021543",
                "StageName": [{"Description": "Final"}],
                "Date": "2026-07-19T19:00:00Z",
                "Home": {},
                "Away": {},
                "PlaceHolderA": "W101",
                "PlaceHolderB": "W102",
                "HomeTeamScore": None,
                "AwayTeamScore": None,
                "ResultType": 0,
                "MatchNumber": 104,
            }
        ]
    }

    fixture = normalize_fifa_fixture(payload, known_codes=set())

    assert fixture.pending[0]["home_code"] == "W101"
    assert fixture.pending[0]["away_code"] == "W102"


@pytest.mark.parametrize(
    "late_stage_counts",
    [
        {"quarter-finals": 2, "semi-finals": 0, "final": 0},
        {"quarter-finals": 4, "semi-finals": 0, "final": 0},
        {"quarter-finals": 4, "semi-finals": 1, "final": 0},
        {"quarter-finals": 4, "semi-finals": 2, "final": 1},
    ],
)
def test_live_builder_accepts_a_progressive_completion_frontier(
    late_stage_counts: dict[str, int],
) -> None:
    _validate_stage_completion_frontier(
        {
            "group-stage": 72,
            "round-of-32": 16,
            "round-of-16": 8,
            **late_stage_counts,
        }
    )


def test_live_builder_rejects_completed_later_stage_with_incomplete_parent() -> None:
    with pytest.raises(TournamentValidationError, match="completion frontier"):
        _validate_stage_completion_frontier(
            {
                "group-stage": 72,
                "round-of-32": 16,
                "round-of-16": 8,
                "quarter-finals": 3,
                "semi-finals": 1,
                "final": 0,
            }
        )


def test_checked_live_example_has_100_facts_and_simulates_full_path() -> None:
    tournament = load_tournament(EXAMPLE / "tournament.json")
    counts: dict[str, int] = {}
    for match in tournament.completed_matches:
        counts[match.stage_id] = counts.get(match.stage_id, 0) + 1

    assert len(tournament.teams) == 48
    assert len(tournament.completed_matches) == 100
    assert counts == {
        "group-stage": 72,
        "round-of-32": 16,
        "round-of-16": 8,
        "quarter-finals": 4,
    }
    assert tournament.focus_team_id == "france"
    assert {match.match_id for match in tournament.completed_matches} >= {
        "400021525",
        "400021521",
        "400021536",
        "400021538",
        "400021537",
        "400021539",
    }
    forecast = simulate_tournament(
        tournament,
        options=SimulationOptions(seed=11, iterations=20),
    )
    assert forecast.stage_order == (
        "group-stage",
        "round-of-32",
        "round-of-16",
        "quarter-finals",
        "semi-finals",
        "final",
    )
    assert forecast.stage_probabilities["semi-finals"] == 1.0


def test_checked_completed_match_metadata_uses_raw_fifa_team_ids() -> None:
    document = json.loads((EXAMPLE / "tournament.json").read_text(encoding="utf-8"))
    fifa_team_ids = {
        team["metadata"]["fifa_team_id"]
        for team in document["teams"]
    }
    completed = document["completed_matches"]

    assert all(
        match["metadata"][field] in fifa_team_ids
        for match in completed
        for field in ("fifa_home_team_id", "fifa_away_team_id")
    )
    france_morocco = next(match for match in completed if match["match_id"] == "400021536")
    assert france_morocco["metadata"]["fifa_home_team_id"] == "43946"
    assert france_morocco["metadata"]["fifa_away_team_id"] == "43872"


def test_checked_backtest_has_all_72_group_targets() -> None:
    document = json.loads((EXAMPLE / "backtest.json").read_text(encoding="utf-8"))
    report = evaluate_backtest(document, min_resolved=72)

    assert report.ok is True
    assert report.sample_size == 72
    assert report.to_dict() == json.loads(
        (EXAMPLE / "backtest-report.json").read_text(encoding="utf-8")
    )


def test_rating_provenance_uses_exact_frozen_commit_timestamp() -> None:
    tournament = json.loads((EXAMPLE / "tournament.json").read_text(encoding="utf-8"))
    backtest = json.loads((EXAMPLE / "backtest.json").read_text(encoding="utf-8"))
    exact_commit_time = "2026-06-09T23:27:23-03:00"

    assert tournament["metadata"]["ratings"]["frozen_at"] == exact_commit_time
    assert backtest["metadata"]["rating_provenance"]["captured_at"] == exact_commit_time
    assert {case["captured_at"] for case in backtest["cases"]} == {exact_commit_time}

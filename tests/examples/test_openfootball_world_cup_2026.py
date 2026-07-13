from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import build_world_cup_2026_example as builder
from tournament_forecaster.backtest import evaluate_backtest
from tournament_forecaster.config import load_tournament
from tournament_forecaster.domain import SimulationOptions
from tournament_forecaster.errors import TournamentValidationError
from tournament_forecaster.simulation import simulate_tournament


ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "examples" / "world-cup-2026-live"
EDGE_FIXTURE = Path(__file__).parent / "fixtures" / "openfootball-edge-cases.json"
SOURCE_URL = (
    "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"
)
LICENSE_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/LICENSE.md"
SOURCE_SHA256 = "b0aef8771d7fc3b6a5ec04cf7a9f9cd167c4e8b0be9152b3a35ae5629bb4e8d5"
RETRIEVED_AT = "2026-07-13T16:35:34Z"
BANNED_REDISTRIBUTION_MARKERS = (
    "https://api.fifa.com",
    "fifa calendar api",
    "fifa_team_id",
    "fifa_code",
)


def _load_edge_fixture() -> dict[str, object]:
    value = json.loads(EDGE_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_public_snapshot_has_a_cc0_source_instead_of_a_disclaimer() -> None:
    data_sources = (EXAMPLE / "DATA_SOURCES.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    example_readme = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
    policy = (ROOT / "docs" / "DATA_POLICY.md").read_text(encoding="utf-8")
    public_surface = "\n".join((data_sources, readme, example_readme, notice, policy))
    normalized = public_surface.casefold()

    for exact_value in (SOURCE_URL, LICENSE_URL, SOURCE_SHA256, RETRIEVED_AT):
        assert exact_value in data_sources
    assert SOURCE_URL in readme
    assert SOURCE_URL in example_readme
    assert "cc0 1.0" in data_sources.casefold()
    assert "database rights" in data_sources.casefold()
    assert "openfootball-derived match facts remain cc0" in notice.casefold()
    assert "redistribution license" in policy.casefold()
    assert "does not relicense" in normalized
    for marker in BANNED_REDISTRIBUTION_MARKERS:
        assert marker not in normalized


def test_checked_json_artifacts_pin_the_cc0_source_and_drop_fifa_provider_metadata() -> None:
    tournament = json.loads((EXAMPLE / "tournament.json").read_text(encoding="utf-8"))
    backtest = json.loads((EXAMPLE / "backtest.json").read_text(encoding="utf-8"))

    for document in (tournament, backtest):
        serialized = json.dumps(document, ensure_ascii=False, sort_keys=True).casefold()
        assert SOURCE_URL in serialized
        assert LICENSE_URL.casefold() in serialized
        assert SOURCE_SHA256 in serialized
        assert RETRIEVED_AT.casefold() in serialized
        assert "cc0 1.0" in serialized
        for marker in BANNED_REDISTRIBUTION_MARKERS:
            assert marker not in serialized

    snapshot = tournament["metadata"]["snapshot"]
    assert snapshot["source"] == "OpenFootball worldcup.json"
    assert snapshot["completed_fact_count"] == 100
    assert snapshot["source_match_count"] == 104
    assert all("metadata" not in team for team in tournament["teams"])
    assert {
        match["metadata"]["source_match_number"] for match in tournament["completed_matches"]
    } == set(range(1, 101))
    assert {case["metadata"]["source_match_number"] for case in backtest["cases"]} == set(
        range(1, 73)
    )


def test_openfootball_normalizer_is_deterministic_and_parses_result_semantics() -> None:
    payload = _load_edge_fixture()

    first = builder.normalize_openfootball_fixture(payload, retrieved_at=RETRIEVED_AT)
    replay = builder.normalize_openfootball_fixture(
        deepcopy(payload),
        retrieved_at=RETRIEVED_AT,
    )

    assert first == replay
    assert [match.match_number for match in first.completed] == [1, 2, 3, 4]
    assert [match.score_basis for match in first.completed] == [
        "full_time",
        "extra_time",
        "extra_time",
        "penalties",
    ]
    assert first.completed[0].kickoff_at == "2026-07-09T20:00:00Z"
    assert first.completed[1].score == (3, 2)
    assert first.completed[1].winner_team_id == "belgium"
    assert first.completed[2].away_team_id == "cabo-verde"
    assert first.completed[3].home_team_id == "korea-republic"
    assert first.completed[3].away_team_id == "cote-d-ivoire"
    assert first.completed[3].score == (0, 0)
    assert first.completed[3].winner_team_id == "korea-republic"
    assert first.pending[0].home_team_id == "W4"
    assert first.pending[0].away_team_id == "bosnia-and-herzegovina"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda payload: payload["matches"][0].update(team1="Atlantis"),
            "unknown OpenFootball team",
        ),
        (
            lambda payload: payload["matches"][0].update(round="Mystery round"),
            "unsupported OpenFootball stage",
        ),
        (lambda payload: payload["matches"][0].update(time="16:00 EDT"), "kickoff offset"),
        (
            lambda payload: payload["matches"][0]["score"].update(ft=[2.0, 0]),
            "score must contain integers",
        ),
    ],
)
def test_openfootball_normalizer_fails_closed_on_unknown_or_malformed_rows(
    mutation: object,
    message: str,
) -> None:
    payload = _load_edge_fixture()
    assert callable(mutation)
    mutation(payload)

    with pytest.raises(TournamentValidationError, match=message):
        builder.normalize_openfootball_fixture(payload, retrieved_at=RETRIEVED_AT)


def test_openfootball_normalizer_rejects_conflicting_match_numbers() -> None:
    payload = _load_edge_fixture()
    duplicate = deepcopy(payload["matches"][1])
    duplicate["team1"] = "France"
    payload["matches"].append(duplicate)

    with pytest.raises(TournamentValidationError, match="duplicate OpenFootball match number 2"):
        builder.normalize_openfootball_fixture(payload, retrieved_at=RETRIEVED_AT)


def test_openfootball_normalizer_rejects_conflicting_penalty_winner() -> None:
    payload = _load_edge_fixture()
    penalty_score = payload["matches"][3]["score"]
    penalty_score["p"] = [4, 4]

    with pytest.raises(TournamentValidationError, match="penalty score must identify one winner"):
        builder.normalize_openfootball_fixture(payload, retrieved_at=RETRIEVED_AT)


def test_openfootball_normalizer_rejects_result_at_or_before_kickoff() -> None:
    payload = _load_edge_fixture()

    with pytest.raises(TournamentValidationError, match="retrieved_at must be after kickoff_at"):
        builder.normalize_openfootball_fixture(
            payload,
            retrieved_at="2026-07-09T20:00:00Z",
        )


def test_openfootball_normalizer_rejects_a_gap_in_the_completion_frontier() -> None:
    payload = _load_edge_fixture()
    payload["matches"][2].pop("score")

    with pytest.raises(TournamentValidationError, match="chronological completion frontier"):
        builder.normalize_openfootball_fixture(payload, retrieved_at=RETRIEVED_AT)


def test_checked_live_example_has_100_facts_and_preserves_stable_topology_ids() -> None:
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


def test_checked_backtest_preserves_all_metrics_exactly() -> None:
    document = json.loads((EXAMPLE / "backtest.json").read_text(encoding="utf-8"))
    report = evaluate_backtest(document, min_resolved=72)

    assert report.ok is True
    assert report.sample_size == 72
    assert report.to_dict() == json.loads(
        (EXAMPLE / "backtest-report.json").read_text(encoding="utf-8")
    )
    assert report.to_dict()["metrics"] == {
        "rps": 0.14683765711470123,
        "brier": 0.49873843934489415,
        "log_loss": 0.8320301393116664,
        "top_pick_accuracy": 0.625,
    }


def test_rating_provenance_uses_exact_frozen_commit_timestamp() -> None:
    tournament = json.loads((EXAMPLE / "tournament.json").read_text(encoding="utf-8"))
    backtest = json.loads((EXAMPLE / "backtest.json").read_text(encoding="utf-8"))
    exact_commit_time = "2026-06-09T23:27:23-03:00"

    assert tournament["metadata"]["ratings"]["frozen_at"] == exact_commit_time
    assert backtest["metadata"]["rating_provenance"]["captured_at"] == exact_commit_time
    assert {case["captured_at"] for case in backtest["cases"]} == {exact_commit_time}

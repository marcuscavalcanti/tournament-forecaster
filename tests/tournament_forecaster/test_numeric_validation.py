from __future__ import annotations

import math

import pytest

from tournament_forecaster.backtest import evaluate_backtest, ratings_sha256
from tournament_forecaster.config import load_tournament_document
from tournament_forecaster.errors import TournamentValidationError
from tournament_forecaster.probabilities import compose_rating, stage_home_advantage_points
from tournament_forecaster.validation import bounded_finite_number


class NanCastingInt(int):
    def __float__(self) -> float:
        return float("nan")


class InfinityCastingInt(int):
    def __float__(self) -> float:
        return float("inf")


class FiniteCastingInt(int):
    def __float__(self) -> float:
        return 123.5


BAD_CASTING_INTS = (
    pytest.param(NanCastingInt(1), id="casts-to-nan"),
    pytest.param(InfinityCastingInt(1), id="casts-to-infinity"),
)


def _tournament_document(value: int, *, target: str) -> dict[str, object]:
    ratings: dict[str, object] = {"alpha": 1600.0, "bravo": 1500.0}
    metadata: dict[str, object] = {"home_advantage_rating_points": 0.0}
    if target == "rating":
        ratings["alpha"] = value
    else:
        metadata["home_advantage_rating_points"] = value
    return {
        "schema_version": 2,
        "tournament": {"id": "numeric-cup", "display_name": "Numeric Cup"},
        "focus_team_id": "alpha",
        "teams": [
            {"id": "alpha", "display_name": "Alpha"},
            {"id": "bravo", "display_name": "Bravo"},
        ],
        "stages": [
            {
                "id": "final",
                "type": "knockout",
                "pairing": {
                    "mode": "fixed",
                    "ties": [
                        {
                            "id": "final-1",
                            "entrants": [
                                {"type": "team", "team_id": "alpha"},
                                {"type": "team", "team_id": "bravo"},
                            ],
                        }
                    ],
                },
                "legs": 1,
                "home_away_order": "listed_team_first_leg_home",
                "aggregate_tiebreak": "extra_time_then_penalties",
                "away_goals_rule": False,
                "terminal": "championship",
                "metadata": metadata,
            }
        ],
        "ratings": ratings,
        "completed_matches": [],
    }


def _backtest_document(value: int, *, target: str) -> dict[str, object]:
    ratings: dict[str, object] = {"alpha": 1600.0, "bravo": 1500.0}
    home_advantage: object = 0.0
    if target == "rating":
        ratings["alpha"] = value
    else:
        home_advantage = value
    return {
        "schema_version": 1,
        "model_version": "poisson-elo-v1",
        "home_advantage_rating_points": home_advantage,
        "ratings": ratings,
        "ratings_sha256": "0" * 64,
        "cases": [],
    }


@pytest.mark.parametrize("value", BAD_CASTING_INTS)
def test_bounded_finite_number_rejects_non_finite_post_conversion(value: int) -> None:
    with pytest.raises(TournamentValidationError, match="finite numeric bounds"):
        bounded_finite_number(value, "adversarial value")


@pytest.mark.parametrize("value", BAD_CASTING_INTS)
def test_probability_paths_reject_non_finite_post_conversion(value: int) -> None:
    with pytest.raises(TournamentValidationError, match="finite numeric bounds"):
        compose_rating(value, 0.0)
    with pytest.raises(TournamentValidationError, match="finite numeric bounds"):
        stage_home_advantage_points(
            {"metadata": {"home_advantage_rating_points": value}}
        )


@pytest.mark.parametrize("target", ["rating", "stage_metadata"])
@pytest.mark.parametrize("value", BAD_CASTING_INTS)
def test_tournament_paths_reject_non_finite_post_conversion(
    target: str,
    value: int,
) -> None:
    with pytest.raises(TournamentValidationError, match="finite numeric bounds"):
        load_tournament_document(_tournament_document(value, target=target))


@pytest.mark.parametrize("target", ["rating", "home_advantage"])
@pytest.mark.parametrize("value", BAD_CASTING_INTS)
def test_backtest_paths_reject_non_finite_post_conversion(
    target: str,
    value: int,
) -> None:
    with pytest.raises(TournamentValidationError, match="finite numeric bounds"):
        evaluate_backtest(_backtest_document(value, target=target))


def test_finite_numeric_subclass_is_normalized_once_across_runtime_paths() -> None:
    value = FiniteCastingInt(1)
    tournament_document = _tournament_document(value, target="rating")
    tournament_document["stages"][0]["metadata"][  # type: ignore[index]
        "home_advantage_rating_points"
    ] = value

    tournament = load_tournament_document(tournament_document)

    assert tournament.ratings["alpha"] == 123.5
    assert type(tournament.ratings["alpha"]) is float
    stage_metadata = tournament.stages[0]["metadata"]
    assert stage_metadata["home_advantage_rating_points"] == 123.5  # type: ignore[index]
    assert type(stage_metadata["home_advantage_rating_points"]) is float  # type: ignore[index]
    assert stage_home_advantage_points(tournament.stages[0]) == 123.5
    assert compose_rating(value, 0.0) == 123.5

    ratings = {"alpha": 123.5, "bravo": 1500.0}
    backtest = _backtest_document(value, target="rating")
    backtest["home_advantage_rating_points"] = value
    backtest["ratings_sha256"] = ratings_sha256(ratings)
    report = evaluate_backtest(backtest)

    assert report.status == "no_resolved"
    assert all(math.isfinite(number) for number in ratings.values())

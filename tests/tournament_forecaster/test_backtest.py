from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy

import pytest

from tournament_forecaster.backtest import evaluate_backtest
from tournament_forecaster.errors import TournamentValidationError
from tournament_forecaster.probabilities import predict_match_outcomes


def _ratings_hash(ratings: dict[str, float]) -> str:
    payload = json.dumps(ratings, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _document() -> dict[str, object]:
    ratings = {"alpha": 1600.0, "bravo": 1500.0, "charlie": 1450.0}
    return {
        "schema_version": 1,
        "model_version": "poisson-elo-v1",
        "home_advantage_rating_points": 0,
        "ratings": ratings,
        "ratings_sha256": _ratings_hash(ratings),
        "cases": [
            {
                "source_id": "official-1",
                "captured_at": "2026-06-09T12:00:00+00:00",
                "kickoff_at": "2026-06-11T12:00:00+00:00",
                "home_team_id": "alpha",
                "away_team_id": "bravo",
                "result": {"home": 2, "away": 0},
            },
            {
                "source_id": "official-2",
                "captured_at": "2026-06-09T12:00:00+00:00",
                "kickoff_at": "2026-06-12T12:00:00+00:00",
                "home_team_id": "charlie",
                "away_team_id": "alpha",
                "result": {"home": 1, "away": 1},
            },
        ],
    }


def test_evaluate_backtest_uses_exact_probabilities_and_metric_definitions() -> None:
    document = _document()
    report = evaluate_backtest(document)
    first = predict_match_outcomes(1600.0, 1500.0)
    second = predict_match_outcomes(1450.0, 1600.0)
    expected_brier = (
        (first.home_win - 1) ** 2 + first.draw**2 + first.away_win**2
        + second.home_win**2 + (second.draw - 1) ** 2 + second.away_win**2
    ) / 2
    expected_rps = (
        (first.home_win - 1) ** 2
        + (first.home_win + first.draw - 1) ** 2
        + second.home_win**2
        + (second.home_win + second.draw - 1) ** 2
    ) / 4

    assert report.status == "ok"
    assert report.ok is True
    assert report.sample_size == 2
    assert report.metrics["brier"] == pytest.approx(expected_brier)
    assert report.metrics["rps"] == pytest.approx(expected_rps)
    assert report.metrics["log_loss"] == pytest.approx(
        (-math.log(first.home_win) - math.log(second.draw)) / 2
    )
    assert report.metrics["top_pick_accuracy"] == pytest.approx(0.5)
    assert report.uniform_baseline == pytest.approx(
        {
            "rps": (0.2777777777777778 + 0.1111111111111111) / 2,
            "brier": 2 / 3,
            "log_loss": math.log(3),
            "top_pick_accuracy": 1 / 3,
        }
    )


@pytest.mark.parametrize(
    "captured_at",
    ["2026-06-11T12:00:00+00:00", "2026-06-12T12:00:00+00:00"],
)
def test_backtest_rejects_predictions_captured_at_or_after_kickoff(captured_at: str) -> None:
    document = _document()
    document["cases"][0]["captured_at"] = captured_at  # type: ignore[index]

    with pytest.raises(TournamentValidationError, match="captured_at must be before kickoff_at"):
        evaluate_backtest(document)


def test_backtest_rejects_ratings_hash_drift() -> None:
    document = _document()
    document["ratings_sha256"] = "0" * 64

    with pytest.raises(TournamentValidationError, match="ratings_sha256"):
        evaluate_backtest(document)


def test_backtest_rejects_an_unsupported_model_version() -> None:
    document = _document()
    document["model_version"] = "future-model-that-is-not-implemented"

    with pytest.raises(TournamentValidationError, match="unsupported model_version"):
        evaluate_backtest(document)


def test_top_pick_accuracy_does_not_award_a_tied_maximum() -> None:
    document = _document()
    ratings = {"alpha": 1500.0, "bravo": 1500.0}
    document["ratings"] = ratings
    document["ratings_sha256"] = _ratings_hash(ratings)
    document["cases"] = [document["cases"][0]]  # type: ignore[index]

    report = evaluate_backtest(document)

    assert report.metrics["top_pick_accuracy"] == 0.0


def test_empty_and_insufficient_samples_are_non_ok_with_null_metrics() -> None:
    empty = _document()
    empty["cases"] = []
    empty_report = evaluate_backtest(empty, min_resolved=2)

    insufficient = _document()
    insufficient["cases"] = deepcopy(insufficient["cases"][:1])  # type: ignore[index]
    insufficient_report = evaluate_backtest(insufficient, min_resolved=2)

    assert empty_report.status == "no_resolved"
    assert empty_report.ok is False
    assert empty_report.metrics == {
        "rps": None,
        "brier": None,
        "log_loss": None,
        "top_pick_accuracy": None,
    }
    assert insufficient_report.status == "insufficient"
    assert insufficient_report.ok is False
    assert insufficient_report.sample_size == 1
    assert all(value is not None for value in insufficient_report.metrics.values())

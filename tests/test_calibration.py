import json
import subprocess
import sys

from worldcup_brazil.calibration import (
    append_prediction_log,
    evaluate_calibration,
    prediction_records_from_bundle,
    resolved_calibration_records,
)
from worldcup_brazil.models import ReportBundle
from worldcup_brazil.probabilities import MatchEstimate


def test_evaluate_calibration_reports_brier_log_loss_ece_and_multiplier() -> None:
    records = [
        {"id": "m1", "predicted_pct": 80.0, "outcome": 1},
        {"id": "m2", "predicted_pct": 80.0, "outcome": 0},
        {"id": "m3", "predicted_pct": 20.0, "outcome": 0},
        {"id": "m4", "predicted_pct": 20.0, "outcome": 0},
    ]

    report = evaluate_calibration(records, bins=5, target_ece=0.05)

    assert report["total_predictions"] == 4
    assert report["brier_score"] == 0.19
    assert report["log_loss"] > 0.5
    assert report["expected_calibration_error"] == 0.25
    assert report["recommended_width_multiplier"] > 1.0
    assert report["bins"][4]["count"] == 2
    assert report["bins"][4]["mean_predicted_pct"] == 80.0
    assert report["bins"][4]["observed_rate_pct"] == 50.0


def test_validate_calibration_cli_outputs_json_report(tmp_path) -> None:
    input_path = tmp_path / "predictions.json"
    input_path.write_text(
        json.dumps(
            [
                {"id": "m1", "predicted_pct": 70.0, "outcome": 1},
                {"id": "m2", "predicted_pct": 30.0, "outcome": 0},
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/validate_calibration.py",
            "--input",
            str(input_path),
            "--bins",
            "5",
            "--target-ece",
            "0.05",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)

    assert payload["total_predictions"] == 2
    assert "brier_score" in payload
    assert "expected_calibration_error" in payload


def test_prediction_logger_appends_pending_run_records_without_duplicates(tmp_path) -> None:
    bundle = ReportBundle(
        generated_at_iso="2026-06-14T12:00:00+00:00",
        group_matches=[
            MatchEstimate(
                brazil="Brasil",
                opponent="Marrocos",
                phase="Fase de grupos",
                brazil_pct=59.0,
                opponent_pct=17.0,
                draw_pct=24.0,
                statistical_weight=0.5,
                qualitative_weight=0.5,
                rationale="base",
            )
        ],
        knockout_matches=[
            MatchEstimate(
                brazil="Brasil",
                opponent="Japão",
                phase="16 avos",
                brazil_pct=58.0,
                opponent_pct=42.0,
                statistical_weight=0.5,
                qualitative_weight=0.5,
                rationale="base",
                scenario_pct=38.0,
            )
        ],
        stage_probabilities={"quartas": 60.0, "semifinal": 35.0, "final": 18.0, "titulo": 8.0},
        final_rationale="base",
        sources=[],
        agent_summaries={},
        warnings=[],
        custom_hashtag="#copaComAchismo",
    )
    log_path = tmp_path / "calibration_predictions.json"

    records = prediction_records_from_bundle(bundle, run_id="run-1", artifact_path="outputs/run-1.json")
    append_prediction_log(log_path, records)
    append_prediction_log(log_path, records)

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    record_ids = [record["id"] for record in payload]

    assert len(record_ids) == len(set(record_ids))
    assert any(record["target_type"] == "stage_reach" and record["target"] == "quartas" for record in payload)
    assert any(record["target_type"] == "title" and record["target"] == "titulo" for record in payload)
    assert any(record["target_type"] == "match" and record["target"] == "Fase de grupos: Brasil x Marrocos" for record in payload)
    assert all(record["resolved"] is False for record in payload)
    assert all(record["outcome"] is None for record in payload)
    assert all(record["artifact_path"] == "outputs/run-1.json" for record in payload)


def test_validate_calibration_cli_handles_missing_or_unresolved_prediction_log(tmp_path) -> None:
    missing_path = tmp_path / "missing_predictions.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/validate_calibration.py",
            "--input",
            str(missing_path),
            "--bins",
            "5",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)

    assert payload["input_exists"] is False
    assert payload["total_predictions"] == 0
    assert payload["pending_predictions"] == 0


def test_resolved_calibration_records_filters_pending_records() -> None:
    records = [
        {"id": "pending", "predicted_pct": 70.0, "outcome": None, "resolved": False},
        {"id": "resolved", "predicted_pct": 30.0, "outcome": 0, "resolved": True},
    ]

    resolved, pending = resolved_calibration_records(records)

    assert pending == 1
    assert resolved == [{"id": "resolved", "predicted_pct": 30.0, "outcome": 0, "resolved": True}]

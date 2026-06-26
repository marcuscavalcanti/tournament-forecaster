import json
import subprocess
import sys

import pytest

from worldcup_brazil.atomic_io import atomic_write_text
from worldcup_brazil.calibration import (
    _load_prediction_log,
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
    assert payload["status"] == "ok"
    assert "brier_score" in payload
    assert "expected_calibration_error" in payload


def test_validate_calibration_cli_fails_loudly_when_no_predictions_are_resolved(tmp_path) -> None:
    input_path = tmp_path / "predictions.json"
    input_path.write_text(
        json.dumps(
            [
                {"id": "pending-1", "predicted_pct": 70.0, "outcome": None, "resolved": False},
                {"id": "pending-2", "predicted_pct": 30.0, "outcome": None, "resolved": False},
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
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert payload["status"] == "no_resolved_predictions"
    assert payload["total_predictions"] == 0
    assert payload["pending_predictions"] == 2
    assert payload["brier_score"] is None
    assert payload["log_loss"] is None
    assert payload["expected_calibration_error"] is None


def test_validate_calibration_cli_enforces_minimum_resolved_predictions(tmp_path) -> None:
    input_path = tmp_path / "predictions.json"
    input_path.write_text(
        json.dumps(
            [
                {"id": "resolved-1", "predicted_pct": 70.0, "outcome": 1, "resolved": True},
                {"id": "pending-1", "predicted_pct": 30.0, "outcome": None, "resolved": False},
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
            "--min-resolved",
            "2",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert payload["status"] == "insufficient_resolved_predictions"
    assert payload["total_predictions"] == 1
    assert payload["pending_predictions"] == 1
    assert payload["min_resolved_predictions"] == 2
    assert isinstance(payload["brier_score"], float)


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
        custom_hashtag="#CopaComAchismo",
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


def test_validate_calibration_cli_reports_missing_prediction_log_as_no_resolved_predictions(tmp_path) -> None:
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
        check=False,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert payload["status"] == "no_resolved_predictions"
    assert payload["input_exists"] is False
    assert payload["total_predictions"] == 0
    assert payload["pending_predictions"] == 0
    assert payload["brier_score"] is None


def test_resolved_calibration_records_filters_pending_records() -> None:
    records = [
        {"id": "pending", "predicted_pct": 70.0, "outcome": None, "resolved": False},
        {"id": "resolved", "predicted_pct": 30.0, "outcome": 0, "resolved": True},
    ]

    resolved, pending = resolved_calibration_records(records)

    assert pending == 1
    assert resolved == [{"id": "resolved", "predicted_pct": 30.0, "outcome": 0, "resolved": True}]


def test_corrupt_calibration_log_is_quarantined_and_load_returns_empty(tmp_path) -> None:
    """Bug histórico (ITEM 5): um torn write de calibration_predictions.json fazia
    _load_prediction_log estourar JSONDecodeError em todo append subsequente —
    cada run completava o debate de US$6,43 e só então estourava ao gravar a
    calibração, diariamente, até reparo manual.

    No código antigo _load_prediction_log fazia json.loads sem try/except, então
    este teste levantaria JSONDecodeError em vez de retornar []. O fix captura
    (JSONDecodeError, ValueError), renomeia para sufixo .corrupt e retorna []."""
    log_path = tmp_path / "calibration_predictions.json"
    log_path.write_text('[{"id": "m1", "predicted_pct": 7', encoding="utf-8")  # torn JSON

    assert _load_prediction_log(log_path) == []
    assert not log_path.exists()
    # sufixo único (.corrupt.<timestamp>) para não clobbar forense de incidente anterior
    assert list(tmp_path.glob("calibration_predictions.json.corrupt*"))


def test_second_corruption_does_not_clobber_first_quarantined_log(tmp_path) -> None:
    """Bug histórico: a quarentena usava Path.replace para um sufixo .corrupt FIXO,
    que sobrescreve o destino incondicionalmente. Um segundo torn write do log de
    calibração clobava silenciosamente a forense do incidente anterior — a leitura
    seguia com log vazio, mas a trilha de inspeção do primeiro incidente sumia.

    No código antigo, este teste encontraria apenas 1 arquivo .corrupt com o
    conteúdo do segundo incidente. O fix gera nome único (.corrupt.<timestamp>[.<n>]),
    então os dois incidentes coexistem e o payload do primeiro permanece intacto."""
    log_path = tmp_path / "calibration_predictions.json"

    log_path.write_text('[{"id": "m1", "predicted_pct": 7', encoding="utf-8")  # incidente 1
    assert _load_prediction_log(log_path) == []
    first_quarantined = list(tmp_path.glob("calibration_predictions.json.corrupt*"))
    assert len(first_quarantined) == 1
    first_path = first_quarantined[0]
    first_payload = first_path.read_text(encoding="utf-8")

    log_path.write_text('[{"id": "m2", "predicted_pct": 8', encoding="utf-8")  # incidente 2 (distinto)
    assert _load_prediction_log(log_path) == []

    # ambos os incidentes preservados — nada foi clobado
    assert len(list(tmp_path.glob("calibration_predictions.json.corrupt*"))) == 2
    assert first_path.exists()
    assert first_path.read_text(encoding="utf-8") == first_payload == '[{"id": "m1", "predicted_pct": 7'


def test_atomic_write_text_writes_content_without_leaving_tmp_orphan(tmp_path) -> None:
    """ITEM 5: atomic_write_text grava o conteúdo e não deixa tempfile órfão no
    diretório do alvo após sucesso."""
    target = tmp_path / "out" / "state.json"

    atomic_write_text(target, '{"ok": true}')

    assert target.read_text(encoding="utf-8") == '{"ok": true}'
    assert [p.name for p in target.parent.iterdir()] == ["state.json"]


def test_atomic_write_text_cleans_tmp_when_write_fails(tmp_path, monkeypatch) -> None:
    """ITEM 5: se a escrita estourar no meio (aqui, no os.fsync logo após
    handle.write), o tempfile criado no diretório do alvo é removido e o alvo
    fica intocado — sem torn write, sem órfão. Sem o except/cleanup do
    atomic_write_text, o .tmp ficaria órfão no diretório e este assert falharia."""
    import worldcup_brazil.atomic_io as atomic_io

    target = tmp_path / "state.json"

    def boom_fsync(_fd):
        raise RuntimeError("fsync falhou no meio da escrita")

    monkeypatch.setattr(atomic_io.os, "fsync", boom_fsync)

    with pytest.raises(RuntimeError):
        atomic_write_text(target, "payload")

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []

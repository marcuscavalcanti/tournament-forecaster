import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _event(run_id: str, timestamp: str, step: str, status: str, detail: str = "") -> dict:
    return {"run_id": run_id, "timestamp": timestamp, "step": step, "status": status, "detail": detail}


def test_profile_run_reports_stage_and_round_breakdown(tmp_path) -> None:
    run_id = "abc123"
    events = [
        _event(run_id, "2026-06-09T18:00:00+00:00", "run", "start"),
        _event(run_id, "2026-06-09T18:00:01+00:00", "model_meeting", "start"),
        _event(run_id, "2026-06-09T18:00:10+00:00", "model_room", "question", "pergunta 1"),
        _event(run_id, "2026-06-09T18:01:10+00:00", "model_room", "response", "resposta a"),
        _event(run_id, "2026-06-09T18:01:10+00:00", "model_room", "response", "resposta b"),
        _event(run_id, "2026-06-09T18:01:40+00:00", "model_room", "question", "pergunta 2"),
        _event(run_id, "2026-06-09T18:02:40+00:00", "model_room", "response", "resposta c"),
        _event(run_id, "2026-06-09T18:02:41+00:00", "model_room", "early_exit", "estável"),
        _event(run_id, "2026-06-09T18:02:42+00:00", "model_meeting", "finish"),
        _event(run_id, "2026-06-09T18:02:43+00:00", "render_post", "start"),
        _event(run_id, "2026-06-09T18:02:44+00:00", "render_post", "finish"),
        _event(run_id, "2026-06-09T18:02:45+00:00", "run", "finish"),
    ]
    log_path = tmp_path / "watchdog.jsonl"
    log_path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "profile_run.py"), "--watchdog-log", str(log_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert f"run: {run_id}" in result.stdout
    assert "TOTAL: 165s" in result.stdout
    assert "status: run finish" in result.stdout
    assert "model_meeting" in result.stdout
    assert "response_phase" in result.stdout
    assert "early_exit=1" in result.stdout


def test_profile_run_defaults_to_latest_run_even_when_failed(tmp_path) -> None:
    older = "older-success"
    newer = "newer-fail"
    events = [
        _event(older, "2026-06-09T18:00:00+00:00", "run", "start"),
        _event(older, "2026-06-09T18:00:10+00:00", "render_post", "finish"),
        _event(older, "2026-06-09T18:00:11+00:00", "run", "finish"),
        _event(newer, "2026-06-10T18:00:00+00:00", "run", "start"),
        _event(newer, "2026-06-10T18:01:00+00:00", "model_meeting", "fail", "sala estéril"),
        _event(newer, "2026-06-10T18:01:01+00:00", "run", "fail", "sala estéril"),
    ]
    log_path = tmp_path / "watchdog.jsonl"
    log_path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "profile_run.py"), "--watchdog-log", str(log_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert f"run: {newer}" in result.stdout
    assert "status: run fail - sala estéril" in result.stdout


def test_profile_run_successful_flag_selects_latest_successful_run(tmp_path) -> None:
    older = "older-success"
    newer = "newer-fail"
    events = [
        _event(older, "2026-06-09T18:00:00+00:00", "run", "start"),
        _event(older, "2026-06-09T18:00:10+00:00", "render_post", "finish"),
        _event(older, "2026-06-09T18:00:11+00:00", "run", "finish"),
        _event(newer, "2026-06-10T18:00:00+00:00", "run", "start"),
        _event(newer, "2026-06-10T18:01:01+00:00", "run", "fail", "sala estéril"),
    ]
    log_path = tmp_path / "watchdog.jsonl"
    log_path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "profile_run.py"),
            "--watchdog-log",
            str(log_path),
            "--successful",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert f"run: {older}" in result.stdout
    assert f"run: {newer}" not in result.stdout

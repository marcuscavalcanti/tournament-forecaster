import json
import subprocess
import sys
from pathlib import Path

from worldcup_brazil.debate_report import (
    find_latest_run_json,
    latest_failed_watchdog_run_after_json,
    render_debate_report,
    render_failed_watchdog_run_report,
)


def _turn(round_index: int, protagonist: str, question: str, agent: str, answer: str) -> dict:
    return {
        "round": round_index,
        "protagonist": protagonist,
        "question": question,
        "responses": [
            {
                "agent": agent,
                "answer": answer,
                "title_pct": 8.4,
                "support_score": 0.91,
                "accepted": True,
                "disagreed": False,
                "used_fallback": False,
                "leadership_bid": True,
                "proposed_next_question": "Testar o proximo cruzamento oficial?",
                "leadership_rationale": "Traz top-2 por slot oficial.",
                "scenario_probabilities": {"Oitavas: Japao": 36.7},
                "match_probabilities": {"Oitavas: Japao": 71.8},
            }
        ],
        "next_protagonist": agent,
        "consensus_title_pct": 8.4,
        "consensus_spread_pct": 0.4,
    }


def test_render_debate_report_shows_opponent_room_feedback_into_brazil_room() -> None:
    payload = {
        "bundle": {
            "generated_at_iso": "2026-06-09T18:58:59+00:00",
            "stage_probabilities": {"quartas": 60.8, "semifinal": 23.1, "final": 9.9, "titulo": 9.6},
            "knockout_matches": [
                {
                    "phase": "Oitavas",
                    "opponent": "Japao",
                    "scenario_pct": 36.7,
                    "brazil_pct": 71.8,
                    "most_likely": True,
                    "venue": "Houston Stadium",
                },
                {
                    "phase": "Oitavas",
                    "opponent": "Holanda",
                    "scenario_pct": 30.0,
                    "brazil_pct": 57.9,
                    "most_likely": False,
                    "venue": "Houston Stadium",
                },
            ],
            "meeting_transcript": [
                _turn(
                    1,
                    "GPT 5.5",
                    "Com os adversarios recebidos da sala paralela, Brasil deve usar Japao/Holanda em Oitavas?",
                    "Gemini Pro",
                    "Concordo: a sala adversarios definiu Japao e Holanda como top-2 e isso alimenta a sala Brasil.",
                )
            ],
            "metadata": {
                "parallel_opponent_debriefing": {
                    "enabled": True,
                    "failed": False,
                    "rounds": 1,
                    "participants": ["GPT 5.5", "Gemini Pro"],
                    "meeting_transcript": [
                        _turn(
                            1,
                            "Gemini Pro",
                            "Dentro do slot oficial W78, quais sao os dois adversarios mais provaveis?",
                            "GPT 5.5",
                            "Japao 36.7% e Holanda 30.0% sao os top-2 para alimentar a sala Brasil.",
                        )
                    ],
                }
            },
        }
    }

    report = render_debate_report(payload, source_path=Path("outputs/linkedin_brazil_2026-06-09.json"))

    assert "Debate das salas" in report
    assert "SALA 1 - Adversarios do Brasil" in report
    assert "SALA 2 - Brasil" in report
    assert "Retroalimentacao" in report
    assert "Oitavas: Japao" in report
    assert "chance do confronto 36.7%" in report
    assert "Brasil passa 71.8%" in report
    assert "Oitavas: Holanda (segunda opcao)" in report
    assert "Japao e Holanda como top-2" in report
    assert "Arquivo fonte: outputs/linkedin_brazil_2026-06-09.json" in report


def test_render_debate_report_flags_scenario_match_probability_collision() -> None:
    payload = {
        "bundle": {
            "generated_at_iso": "2026-06-09T18:58:59+00:00",
            "stage_probabilities": {"titulo": 9.6},
            "knockout_matches": [
                {
                    "phase": "16 avos",
                    "opponent": "Japao",
                    "scenario_pct": 36.7,
                    "brazil_pct": 36.7,
                    "most_likely": True,
                }
            ],
            "meeting_transcript": [],
            "metadata": {"parallel_opponent_debriefing": {"enabled": False}},
        }
    }

    report = render_debate_report(payload)

    assert "ATENCAO: brazil_pct igual ao scenario_pct" in report


def test_find_latest_run_json_prefers_newest_linkedin_json(tmp_path: Path) -> None:
    older = tmp_path / "linkedin_brazil_2026-06-08.json"
    newer = tmp_path / "linkedin_brazil_2026-06-09.json"
    older.write_text(json.dumps({"bundle": {"generated_at_iso": "older"}}), encoding="utf-8")
    newer.write_text(json.dumps({"bundle": {"generated_at_iso": "newer"}}), encoding="utf-8")

    assert find_latest_run_json(tmp_path) == newer


def test_render_debate_report_does_not_use_brazil_room_noise_as_opponent_top_two() -> None:
    payload = {
        "bundle": {
            "generated_at_iso": "2026-06-09T18:58:59+00:00",
            "stage_probabilities": {"titulo": 9.6},
            "meeting_transcript": [
                {
                    "round": 1,
                    "protagonist": "GPT 5.5",
                    "question": "Sala Brasil discutindo fase de grupos.",
                    "responses": [
                        {
                            "agent": "DeepSeek V4 Pro",
                            "answer": "Grupo e campos agregados nao devem alimentar top-2 da sala adversarios.",
                            "title_pct": 9.6,
                            "support_score": 0.9,
                            "accepted": True,
                            "scenario_probabilities": {"Quartas: outros": 42.1},
                            "match_probabilities": {"Grupo: Haiti": 92.0},
                        }
                    ],
                    "next_protagonist": "DeepSeek V4 Pro",
                    "consensus_title_pct": 9.6,
                    "consensus_spread_pct": 0.0,
                }
            ],
            "metadata": {
                "parallel_opponent_debriefing": {
                    "enabled": True,
                    "failed": False,
                    "rounds": 1,
                    "participants": ["Gemini Pro"],
                    "meeting_transcript": [
                        {
                            "round": 1,
                            "protagonist": "Gemini Pro",
                            "question": "Top-2 oficial de Oitavas?",
                            "responses": [
                                {
                                    "agent": "GPT 5.5",
                                    "answer": "Japao e Holanda alimentam a sala Brasil.",
                                    "title_pct": 9.6,
                                    "support_score": 0.9,
                                    "accepted": True,
                                    "scenario_probabilities": {
                                        "Oitavas: Japao": 36.7,
                                        "Oitavas: Holanda": 30.0,
                                    },
                                    "match_probabilities": {
                                        "Oitavas: Japao": 71.8,
                                        "Oitavas: Holanda": 57.9,
                                    },
                                }
                            ],
                            "next_protagonist": "GPT 5.5",
                            "consensus_title_pct": 9.6,
                            "consensus_spread_pct": 0.0,
                        }
                    ],
                }
            },
        }
    }

    report = render_debate_report(payload)

    assert "Oitavas: Japao" in report
    assert "Oitavas: Holanda" in report
    assert "Grupo: Haiti" not in report
    assert "Quartas: outros" not in report


def test_debate_script_entrypoint_renders_latest_json(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "linkedin_brazil_2026-06-09.json").write_text(
        json.dumps(
            {
                "bundle": {
                    "generated_at_iso": "2026-06-09T18:58:59+00:00",
                    "stage_probabilities": {"titulo": 9.6},
                    "meeting_transcript": [],
                    "metadata": {"parallel_opponent_debriefing": {"enabled": False}},
                }
            }
        ),
        encoding="utf-8",
    )

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "scripts/render_debate_report.py",
            "--output-dir",
            str(output_dir),
            "--watchdog-log",
            str(tmp_path / "empty_watchdog.jsonl"),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Debate das salas" in result.stdout
    assert "Run: 2026-06-09T18:58:59+00:00" in result.stdout


def test_debate_report_detects_newer_failed_watchdog_run_than_latest_json(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    latest_json = output_dir / "linkedin_brazil_2026-06-09.json"
    latest_json.write_text(
        json.dumps({"bundle": {"generated_at_iso": "2026-06-09T18:58:59+00:00"}}),
        encoding="utf-8",
    )
    watchdog = tmp_path / "watchdog.jsonl"
    events = [
        {
            "run_id": "failed-run",
            "timestamp": "2026-06-10T18:00:00+00:00",
            "step": "run",
            "status": "start",
            "detail": "",
        },
        {
            "run_id": "failed-run",
            "timestamp": "2026-06-10T18:05:00+00:00",
            "step": "model_meeting",
            "status": "fail",
            "detail": "sala estéril",
        },
        {
            "run_id": "failed-run",
            "timestamp": "2026-06-10T18:05:01+00:00",
            "step": "run",
            "status": "fail",
            "detail": "sala estéril",
        },
    ]
    watchdog.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    failed = latest_failed_watchdog_run_after_json(watchdog_log=watchdog, latest_json=latest_json)

    assert failed is not None
    run_id, run = failed
    report = render_failed_watchdog_run_report(run_id, run, latest_json=latest_json)
    assert "run mais recente falhou" in report
    assert "nao esta mostrando o debate antigo" in report
    assert "sala estéril" in report


def test_debate_script_entrypoint_reports_failed_run_instead_of_stale_json(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "linkedin_brazil_2026-06-09.json").write_text(
        json.dumps(
            {
                "bundle": {
                    "generated_at_iso": "2026-06-09T18:58:59+00:00",
                    "stage_probabilities": {"titulo": 9.6},
                    "meeting_transcript": [],
                    "metadata": {"parallel_opponent_debriefing": {"enabled": False}},
                }
            }
        ),
        encoding="utf-8",
    )
    watchdog = tmp_path / "watchdog.jsonl"
    events = [
        {
            "run_id": "failed-run",
            "timestamp": "2026-06-10T18:00:00+00:00",
            "step": "run",
            "status": "start",
        },
        {
            "run_id": "failed-run",
            "timestamp": "2026-06-10T18:01:00+00:00",
            "step": "model_room",
            "status": "response",
            "detail": "Resposta removida por adversário impossível.",
            "extra": {"round": 3, "agent": "GPT 5.5"},
        },
        {
            "run_id": "failed-run",
            "timestamp": "2026-06-10T18:02:00+00:00",
            "step": "run",
            "status": "fail",
            "detail": "sala estéril",
        },
    ]
    watchdog.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "scripts/render_debate_report.py",
            "--output-dir",
            str(output_dir),
            "--watchdog-log",
            str(watchdog),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "run mais recente falhou" in result.stdout
    assert "Run: 2026-06-09T18:58:59+00:00" not in result.stdout
    assert "Resposta removida por adversário impossível" in result.stdout

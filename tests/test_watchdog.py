import json
from pathlib import Path

from worldcup_brazil.pipeline import (
    _agent_source_planning_watchdog_detail,
    _agent_source_planning_watchdog_extra,
)
from worldcup_brazil.watchdog import RunWatchdog


def test_watchdog_writes_jsonl_events_with_status_and_elapsed_time(tmp_path: Path) -> None:
    path = tmp_path / "watchdog.jsonl"
    watchdog = RunWatchdog(path=path, run_id="run-1", verbose=False)

    watchdog.start("fetch_sources", detail="starting external source refresh")
    watchdog.finish("fetch_sources", detail="sources fetched")

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert events[0]["run_id"] == "run-1"
    assert events[0]["step"] == "fetch_sources"
    assert events[0]["status"] == "start"
    assert events[1]["status"] == "finish"
    assert events[1]["elapsed_ms"] >= 0


def test_watchdog_writes_chat_room_events(tmp_path: Path) -> None:
    path = tmp_path / "watchdog.jsonl"
    watchdog = RunWatchdog(path=path, run_id="run-1", verbose=False)

    watchdog.chat("GPT 5.5", "Eu busco odds, rating e notícias de lesão.", round_name="source-planning")

    event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    assert event["status"] == "chat"
    assert event["step"] == "model_room"
    assert event["extra"]["agent"] == "GPT 5.5"
    assert event["extra"]["round"] == "source-planning"
    assert "odds" in event["detail"]


def test_watchdog_writes_meeting_question_and_response_events(tmp_path: Path) -> None:
    path = tmp_path / "watchdog.jsonl"
    watchdog = RunWatchdog(path=path, run_id="run-1", verbose=False)

    watchdog.meeting_question(round_index=2, protagonist="Perplexity Pro", question="Qual fonte pesa demais?")
    watchdog.meeting_response(
        round_index=2,
        agent="GPT 5.5",
        answer="Opta está dominando sem validação de mercado.",
        support_score=0.82,
    )

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert events[0]["status"] == "question"
    assert events[0]["extra"]["protagonist"] == "Perplexity Pro"
    assert events[1]["status"] == "response"
    assert events[1]["extra"]["support_score"] == 0.82


def test_agent_source_planning_watchdog_payload_includes_contract_and_operational_knobs() -> None:
    config = {
        "minimum_source_ready_agents": 3,
        "source_planning_repair_attempts": 2,
        "meeting_response_repair_attempts": 1,
        "meeting_require_full_path_coverage": True,
        "meeting_min_participants": 3,
        "agent_timeout_seconds": 90,
        "agents": [
            {"slot": "GPT 5.5"},
            {"slot": "Perplexity Pro"},
            {"slot": "Gemini Pro"},
        ],
        "group_matches": [{"opponent": "Marrocos"}],
        "knockout_matches": [{"phase": "16 avos", "opponent": "Uruguai"}],
    }

    detail = _agent_source_planning_watchdog_detail(config)
    extra = _agent_source_planning_watchdog_extra(config)

    assert "contrato único" in detail
    assert "quorum_min=3" in detail
    assert "self_heal_attempts=2" in detail
    assert "meeting_repair_attempts=1" in detail
    assert "meeting_quorum_rule=maioria simples" in detail
    assert "full_path_coverage=True" in detail
    assert "bracket_constraints=True" in detail
    assert "mediador=não faz fetch externo" in detail
    assert extra["contract"]["same_contract_for_all_models"] is True
    assert extra["contract"]["mediator_external_fetch"] is False
    assert extra["contract"]["agent_owned_fresh_search"] is True
    assert extra["contract"]["opta_exclusion_timing"] == "antes_da_busca"
    assert "Opta" in extra["contract"]["excluded_model_principal_sources"]
    assert "scenario_probabilities" in extra["contract"]["required_agent_outputs"]
    assert "team_context_signals" in extra["contract"]["required_agent_outputs"]
    assert "bets/prediction markets" in extra["contract"]["team_context_signal_families"]
    assert "lesões/cortes/notícias recentes" in extra["contract"]["team_context_signal_families"]
    assert extra["operational_knobs"]["minimum_source_ready_agents"] == 3
    assert extra["operational_knobs"]["source_planning_repair_attempts"] == 2
    assert extra["operational_knobs"]["meeting_response_repair_attempts"] == 1
    assert extra["operational_knobs"]["meeting_min_participants"] == 3
    assert extra["operational_knobs"]["meeting_quorum_rule"] == "maioria simples dos participantes ativos da sala"
    assert extra["operational_knobs"]["meeting_require_full_path_coverage"] is True
    assert extra["operational_knobs"]["enforce_bracket_constraints"] is True
    assert extra["operational_knobs"]["bracket_uncertainty_ci_widening"] is True
    assert extra["scope"]["group_matches_count"] == 1
    assert extra["scope"]["knockout_matches_count"] == 1
    assert "bracket_path" in extra["scope"]
    assert "bracket_validation_errors" in extra["scope"]
    assert extra["agents"]["slots"] == ["GPT 5.5", "Perplexity Pro", "Gemini Pro"]

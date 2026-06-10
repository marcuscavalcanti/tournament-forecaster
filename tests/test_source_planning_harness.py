import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from worldcup_brazil.consensus import AgentOpinion
from worldcup_brazil.consensus import build_consensus
from worldcup_brazil.pipeline import (
    _has_opta_marker,
    _sanitize_source_planning_opinions,
    _source_planning_readiness_report,
    build_report_bundle,
    load_config,
)
from worldcup_brazil.source_memory import SourceMemory
from worldcup_brazil.watchdog import RunWatchdog


def test_source_planning_readiness_report_explains_each_removed_agent() -> None:
    opinions = [
        AgentOpinion(
            agent="Perplexity Pro",
            title_pct=8.4,
            summary="Plano com fontes de mercado e rating.",
            source_urls=["https://example.com/odds"],
        ),
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=8.0,
            summary="Modelo sem resposta externa verificável porque GPT 5.5 browser_command timed out after 18s.",
            used_fallback=True,
        ),
        AgentOpinion(
            agent="Gemini Pro",
            title_pct=8.1,
            summary="Plano sem source_urls ou source_queries.",
        ),
        AgentOpinion(
            agent="DeepSeek V4 Pro",
            title_pct=8.2,
            summary="Plano só com benchmark reservado.",
            source_urls=["https://theanalyst.com/opta/world-cup-2026"],
        ),
    ]

    report = _source_planning_readiness_report(
        opinions,
        {
            "require_agent_source_plan": True,
        },
    )

    assert report["active_agents"] == ["Perplexity Pro"]
    removed = {entry["agent"]: entry for entry in report["removed_agents"]}
    assert "fallback operacional" in removed["GPT 5.5"]["reason"]
    assert "sem source_urls/source_queries não-Opta" in removed["Gemini Pro"]["reason"]
    assert "sem source_urls/source_queries não-Opta" in removed["DeepSeek V4 Pro"]["reason"]
    assert report["ready_count"] == 1
    assert report["required_count"] == 3
    assert report["quorum_met"] is False


def test_source_planning_sanitizer_keeps_repair_language_with_query_backed_plan() -> None:
    opinion = AgentOpinion(
        agent="Perplexity Pro",
        title_pct=8.7,
        summary=(
            "Concordo com o reparo operacional: usar fontes não-Opta e comparar Elo, odds e Sofascore "
            "para Brasil, Marrocos, Haiti e Escócia."
        ),
        answer="Plano de fontes com consulta auditável; isto ainda não é voto da sala de debriefing.",
        source_queries=[
            "World Football Elo Ratings Brazil Morocco Haiti Scotland June 2026",
            "Sofascore Brazil Morocco Haiti Scotland player ratings 2026",
        ],
        agrees_with_protagonist=True,
    )

    sanitized = _sanitize_source_planning_opinions(
        [opinion],
        baseline_title_pct=8.0,
        config={"require_auditable_source_urls_for_meeting_votes": True},
    )

    assert sanitized[0].used_fallback is False
    assert sanitized[0].title_pct == 8.7
    assert sanitized[0].source_queries == opinion.source_queries
    assert _has_opta_marker("fontes não-Opta com Elo e Sofascore") is False
    assert _has_opta_marker("usar Opta como benchmark") is True


def test_source_planning_rejects_permission_denied_search_even_with_queries() -> None:
    opinion = AgentOpinion(
        agent="Gemini Pro",
        title_pct=8.1,
        summary=(
            "Rodada de reparo, sem fechar consenso. Busca ao vivo no meu canal "
            "(WebSearch) retornou erro de permissao nao concedida."
        ),
        answer="Plano proposto com odds, rating, Sofascore e notícias recentes.",
        source_queries=[
            "Brazil Morocco Haiti Scotland World Cup 2026 odds ratings injuries",
            "Sofascore Brazil Morocco Haiti Scotland June 2026 player ratings",
        ],
    )

    sanitized = _sanitize_source_planning_opinions(
        [opinion],
        baseline_title_pct=8.0,
        config={"minimum_source_ready_agents": 1},
    )
    report = _source_planning_readiness_report(
        sanitized,
        {
            "require_agent_source_plan": True,
            "minimum_source_ready_agents": 1,
        },
    )

    assert sanitized[0].used_fallback is True
    assert report["quorum_met"] is False
    assert report["active_agents"] == []
    assert "busca/fetch externo indisponível" in report["removed_agents"][0]["reason"]


def test_opta_exclusion_acknowledgement_is_not_treated_as_using_opta() -> None:
    assert _has_opta_marker("Regra: dados da Opta não contam nesta sala.") is False
    assert _has_opta_marker("Não use dados de pesquisa da Opta; use Elo e odds.") is False
    assert _has_opta_marker("Sem Opta: busco Sofascore, odds e ratings independentes.") is False
    assert _has_opta_marker("Opta foi excluída antes da busca.") is False
    assert _has_opta_marker("Opta excluída do Modelo Principal; uso odds e Elo.") is False
    assert _has_opta_marker("Uso Opta Power Rankings como fonte.") is True
    assert _has_opta_marker("https://theanalyst.com/opta/world-cup-2026") is True


def test_source_planning_sanitizer_preserves_external_failure_reason() -> None:
    opinion = AgentOpinion(
        agent="Gemini Pro",
        title_pct=8.0,
        summary=(
            "Modelo sem resposta externa verificável porque HTTP Error 429: Too Many Requests. "
            "O slot não participa do consenso até trazer plano de fontes próprio."
        ),
        used_fallback=True,
    )

    sanitized = _sanitize_source_planning_opinions(
        [opinion],
        baseline_title_pct=8.0,
        config={},
    )

    assert sanitized[0].used_fallback is True
    assert "HTTP Error 429" in sanitized[0].summary


def test_source_planning_sanitizer_keeps_partial_payload_when_sources_are_parseable() -> None:
    opinion = AgentOpinion(
        agent="Perplexity Pro",
        title_pct=8.0,
        summary="Resposta em JSON parcial; mantive leitura conservadora até o modelo devolver campos auditáveis.",
        source_urls=[
            "https://www.oddschecker.com/football/world-cup-2026/winner",
            "https://www.eloratings.net/",
        ],
    )

    sanitized = _sanitize_source_planning_opinions(
        [opinion],
        baseline_title_pct=8.0,
        config={},
    )

    assert sanitized[0].used_fallback is False
    assert sanitized[0].source_urls == opinion.source_urls


def test_source_planning_readiness_report_honors_explicit_quorum_override() -> None:
    report = _source_planning_readiness_report(
        [
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.4,
                summary="Plano com fonte.",
                source_urls=["https://example.com/odds"],
            ),
            AgentOpinion(
                agent="Gemini Pro",
                title_pct=8.3,
                summary="Plano com busca.",
                source_queries=["Brazil World Cup 2026 odds"],
            ),
        ],
        {"minimum_source_ready_agents": 2},
    )

    assert report["required_count"] == 2
    assert report["quorum_met"] is True


def test_source_planning_readiness_rejects_jersey_font_sources_as_off_topic() -> None:
    report = _source_planning_readiness_report(
        [
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.4,
                summary="Planejamento de uso de fontes tipográficas oficiais nas camisas do Brasil.",
                source_urls=[
                    "https://www.footyheadlines.com/pt/lancadas-as-fontes-personalizadas-das-camisas-da-nike-para-o-mundial-de-2026-brasil-eua-e-canada.html",
                    "https://fontsport.com/blog/world-cup-football-jersey-font-brazil-2026-away/",
                ],
                source_queries=["Nike fontes personalizadas camisas Copa do Mundo 2026 Brasil EUA Canadá"],
            )
        ],
        {
            "require_agent_source_plan": True,
            "minimum_source_ready_agents": 1,
        },
    )

    assert report["quorum_met"] is False
    assert report["removed_agents"][0]["agent"] == "Perplexity Pro"
    assert "fora do escopo" in report["removed_agents"][0]["reason"]


def test_source_planning_readiness_rejects_off_topic_summary_even_with_extra_queries() -> None:
    report = _source_planning_readiness_report(
        [
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.4,
                summary="Proponho um plano de fontes tipográficas e numeração para a camisa do Brasil.",
                source_urls=[
                    "https://www.footyheadlines.com/pt/lancadas-as-fontes-personalizadas-das-camisas-da-nike-para-o-mundial-de-2026-brasil-eua-e-canada.html",
                    "https://www.oddsportal.com/football/world/world-cup-2026/",
                ],
                source_queries=["Brazil World Cup 2026 odds Elo Sofascore"],
            )
        ],
        {
            "require_agent_source_plan": True,
            "minimum_source_ready_agents": 1,
        },
    )

    assert report["quorum_met"] is False
    assert "fora do escopo" in report["removed_agents"][0]["reason"]


def test_source_planning_readiness_rejects_fixed_quanti_quali_split_without_repeating_anchor() -> None:
    report = _source_planning_readiness_report(
        [
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.4,
                summary="Plano de fontes frescas com 70% quantitativo e 30% qualitativo.",
                source_urls=["https://www.oddsportal.com/football/world/world-cup-2026/"],
                source_queries=["Brazil World Cup 2026 odds Elo Sofascore injuries"],
            )
        ],
        {
            "require_agent_source_plan": True,
            "minimum_source_ready_agents": 1,
        },
    )

    assert report["quorum_met"] is False
    assert "alocação fixa quanti/quali" in report["removed_agents"][0]["reason"]
    assert "70/30" not in report["removed_agents"][0]["reason"]
    assert "70%" not in report["removed_agents"][0]["reason"]
    assert "30%" not in report["removed_agents"][0]["reason"]


def test_source_planning_readiness_rejects_reserved_benchmark_mentions() -> None:
    report = _source_planning_readiness_report(
        [
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.4,
                summary="Plano usa odds, Elo e Opta Power Rankings como família de fonte.",
                source_urls=["https://www.oddsportal.com/football/world/world-cup-2026/"],
                source_queries=["Brazil World Cup 2026 odds Elo Sofascore injuries"],
            )
        ],
        {
            "require_agent_source_plan": True,
            "minimum_source_ready_agents": 1,
        },
    )

    assert report["quorum_met"] is False
    assert "benchmark reservado" in report["removed_agents"][0]["reason"]


def test_failed_source_planning_quorum_is_logged_with_agent_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_call_all_agents(*args, **kwargs):
        return [
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.4,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/market"],
            ),
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.0,
                summary="Modelo sem resposta externa verificável porque GPT CLI expirou.",
                used_fallback=True,
            ),
            AgentOpinion(
                agent="Gemini Pro",
                title_pct=8.1,
                summary="Resposta sem fontes auditáveis.",
            ),
        ]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)
    watchdog_path = tmp_path / "watchdog.jsonl"

    with pytest.raises(RuntimeError, match="Quórum insuficiente para debriefing"):
        asyncio.run(
            build_report_bundle(
                config={
                    "baseline_title_pct": 8.0,
                    "meeting_min_participants": 1,
                    "agents": [
                        {
                            "slot": "Perplexity Pro",
                            "provider": "openai-compatible",
                            "model": "sonar-pro",
                            "env_api_key": None,
                            "endpoint": "https://example.com/perplexity",
                        },
                        {
                            "slot": "GPT 5.5",
                            "provider": "openai",
                            "model": "gpt-5.5",
                            "env_api_key": None,
                            "endpoint": "https://example.com/openai",
                        },
                        {
                            "slot": "Gemini Pro",
                            "provider": "google-gemini",
                            "model": "gemini-flash-latest",
                            "env_api_key": None,
                            "endpoint": "https://example.com/gemini/{model}",
                        },
                    ],
                    "group_matches": [{"opponent": "Marrocos", "brazil_pct": 59.0}],
                    "knockout_matches": [],
                },
                source_memory=SourceMemory(tmp_path / "source_memory.json"),
                generated_at=datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
                watchdog=RunWatchdog(path=watchdog_path, verbose=False),
            )
        )

    events = [json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()]
    quorum_events = [event for event in events if event["step"] == "agent_source_quorum"]
    assert quorum_events[-1]["status"] == "fail"
    assert quorum_events[-1]["extra"]["ready_count"] == 1
    assert quorum_events[-1]["extra"]["required_count"] == 3
    assert quorum_events[-1]["extra"]["active_agents"] == ["Perplexity Pro"]
    removed = {entry["agent"]: entry for entry in quorum_events[-1]["extra"]["removed_agents"]}
    assert "fallback operacional" in removed["GPT 5.5"]["reason"]
    assert "sem source_urls/source_queries não-Opta" in removed["Gemini Pro"]["reason"]
    assert any(
        event["step"] == "model_room"
        and event["status"] == "chat"
        and event["extra"]["agent"] == "GPT 5.5"
        and "removido" in event["detail"]
        for event in events
    )


def test_source_planning_self_heals_by_retrying_only_unready_agents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, timeout, allow_local_fallback):
        calls.append([spec.slot for spec in specs])
        if len(calls) == 1:
            return [
                AgentOpinion(
                    agent="Perplexity Pro",
                    title_pct=8.4,
                    summary="Plano com fonte verificável.",
                    source_urls=["https://example.com/market"],
                ),
                AgentOpinion(
                    agent="GPT 5.5",
                    title_pct=8.2,
                    summary="Plano com busca verificável.",
                    source_queries=["Brazil 2026 World Cup betting odds"],
                ),
                AgentOpinion(
                    agent="Gemini Pro",
                    title_pct=8.1,
                    summary="Resposta sem fontes auditáveis.",
                ),
            ]
        assert "reparo operacional" in prompt.lower()
        assert calls[-1] == ["Gemini Pro"]
        return [
            AgentOpinion(
                agent="Gemini Pro",
                title_pct=8.1,
                summary="Reparo com fonte auditável.",
                source_urls=["https://example.com/gemini-source"],
            )
        ]

    async def fake_run_model_meeting(
        *,
        config,
        planning_opinions,
        generated_at,
        agent_specs,
        baseline_title_pct,
        allow_agent_fallback,
        watchdog,
        token_cost_ledger=None,
        **_kwargs,
    ):
        slots = [spec.slot for spec in agent_specs]
        assert slots == ["Perplexity Pro", "GPT 5.5", "Gemini Pro"]
        consensus = build_consensus(planning_opinions, agent_slots=slots)
        return consensus, planning_opinions, [], planning_opinions

    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)
    monkeypatch.setattr("worldcup_brazil.pipeline._run_model_meeting", fake_run_model_meeting)
    watchdog_path = tmp_path / "watchdog.jsonl"

    artifacts = asyncio.run(
        build_report_bundle(
            config={
                "baseline_title_pct": 8.0,
                "source_planning_repair_attempts": 1,
                "meeting_min_participants": 3,
                "agents": [
                    {
                        "slot": "Perplexity Pro",
                        "provider": "openai-compatible",
                        "model": "sonar-pro",
                        "env_api_key": None,
                        "endpoint": "https://example.com/perplexity",
                    },
                    {
                        "slot": "GPT 5.5",
                        "provider": "openai",
                        "model": "gpt-5.5",
                        "env_api_key": None,
                        "endpoint": "https://example.com/openai",
                    },
                    {
                        "slot": "Gemini Pro",
                        "provider": "google-gemini",
                        "model": "gemini-flash-latest",
                        "env_api_key": None,
                        "endpoint": "https://example.com/gemini/{model}",
                    },
                ],
                "group_matches": [{"opponent": "Marrocos", "brazil_pct": 59.0}],
                "knockout_matches": [],
            },
            source_memory=SourceMemory(tmp_path / "source_memory.json"),
            generated_at=datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
            watchdog=RunWatchdog(path=watchdog_path, verbose=False),
        )
    )

    assert calls == [
        ["Perplexity Pro", "GPT 5.5", "Gemini Pro"],
        ["Gemini Pro"],
    ]
    assert artifacts.bundle.source_plan_by_model["Gemini Pro"]["source_urls"] == [
        "https://example.com/gemini-source"
    ]
    events = [json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()]
    assert any(event["step"] == "agent_source_self_heal" and event["status"] == "start" for event in events)
    assert [event for event in events if event["step"] == "agent_source_quorum"][-1]["status"] == "finish"


def test_agent_removal_watchdog_keeps_source_planning_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_call_all_agents(*args, **kwargs):
        return [
            AgentOpinion(
                agent="Opus 4.8",
                title_pct=8.0,
                summary="Modelo sem resposta externa verificável porque Opus CLI timed out after 120s.",
                used_fallback=True,
            ),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.4,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/market"],
            ),
            AgentOpinion(
                agent="DeepSeek V4 Pro",
                title_pct=8.2,
                summary="Plano com odds e rating.",
                source_urls=["https://example.com/deepseek"],
            ),
            AgentOpinion(
                agent="Gemini Pro",
                title_pct=8.1,
                summary="Plano com Sofascore e Elo.",
                source_queries=["Brazil Morocco Sofascore Elo 2026"],
            ),
        ]

    async def fake_run_model_meeting(
        *,
        config,
        planning_opinions,
        generated_at,
        agent_specs,
        baseline_title_pct,
        allow_agent_fallback,
        watchdog,
        token_cost_ledger=None,
        **_kwargs,
    ):
        slots = [spec.slot for spec in agent_specs]
        consensus = build_consensus(planning_opinions, agent_slots=slots)
        return consensus, planning_opinions, [], planning_opinions

    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)
    monkeypatch.setattr("worldcup_brazil.pipeline._run_model_meeting", fake_run_model_meeting)
    watchdog_path = tmp_path / "watchdog.jsonl"

    asyncio.run(
        build_report_bundle(
            config={
                "baseline_title_pct": 8.0,
                "minimum_source_ready_agents": 3,
                "source_planning_repair_attempts": 0,
                "meeting_min_participants": 3,
                "agents": [
                    {"slot": "Opus 4.8", "provider": "anthropic", "model": "opus", "endpoint": "x"},
                    {"slot": "Perplexity Pro", "provider": "openai-compatible", "model": "sonar", "endpoint": "x"},
                    {"slot": "DeepSeek V4 Pro", "provider": "openai-compatible", "model": "deepseek", "endpoint": "x"},
                    {"slot": "Gemini Pro", "provider": "google-gemini", "model": "gemini", "endpoint": "x"},
                ],
                "group_matches": [{"opponent": "Marrocos", "brazil_pct": 59.0}],
                "knockout_matches": [],
            },
            source_memory=SourceMemory(tmp_path / "source_memory.json"),
            generated_at=datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
            watchdog=RunWatchdog(path=watchdog_path, verbose=False),
        )
    )

    events = [json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()]
    removal = [
        event
        for event in events
        if event["step"] == "model_room"
        and event["status"] == "chat"
        and event["extra"].get("round") == "agent-removal"
    ][0]
    assert removal["extra"]["agent"] == "Opus 4.8"
    assert "timed out after 120s" in removal["detail"]
    assert "sem resposta auditável, não entra no pool ativo da sala" in removal["detail"]


def test_parallel_opponent_debriefing_runs_side_room_and_updates_bracket_scenarios(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_call_all_agents(*args, **kwargs):
        return [
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.0,
                summary="Plano com fontes próprias para Brasil e adversários.",
                source_urls=["https://example.com/gpt-odds"],
            ),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.1,
                summary="Plano com ratings e Sofascore.",
                source_urls=["https://example.com/perplexity-ratings"],
            ),
            AgentOpinion(
                agent="Gemini Pro",
                title_pct=7.9,
                summary="Plano com lesões, mercado e arbitragem.",
                source_queries=["Brazil Japan Sweden World Cup 2026 odds injuries referee"],
            ),
        ]

    meeting_rooms: list[str] = []
    main_room_first_knockout_opponent: list[str] = []

    async def fake_run_model_meeting(
        *,
        config,
        planning_opinions,
        generated_at,
        agent_specs,
        baseline_title_pct,
        allow_agent_fallback,
        watchdog,
        token_cost_ledger=None,
        **_kwargs,
    ):
        room = str(config.get("_meeting_room", "main_brazil"))
        meeting_rooms.append(room)
        if room == "main_brazil":
            main_room_first_knockout_opponent.append(
                str((config.get("knockout_matches") or [{}])[0].get("opponent", ""))
            )
        await asyncio.sleep(0)
        slots = [spec.slot for spec in agent_specs]
        if room == "opponent_path":
            opinions = [
                AgentOpinion(
                    agent=slot,
                    title_pct=8.0,
                    summary="Sala paralela calibrando adversários prováveis do 16 avos.",
                    answer=(
                        "Japão é o cenário mais provável do slot 2F; Suécia fica em segundo, "
                        "ambos dentro do bracket oficial."
                    ),
                    scenario_probabilities={"16 avos: Japão": 41.0, "16 avos: Suécia": 26.0},
                    match_probabilities={"16 avos: Japão": 58.0, "16 avos: Suécia": 57.0},
                    source_urls=["https://example.com/slot-2f"],
                    agrees_with_protagonist=True,
                    consensus_check_question=(
                        "Os demais concordam integralmente com Japão e Suécia como top-2 do 16 avos?"
                    ),
                )
                for slot in slots
            ]
            transcript = [
                {
                    "round": 1,
                    "protagonist": slots[0],
                    "question": "Concordam com Japão e Suécia como top-2 do 16 avos?",
                    "responses": [],
                    "next_protagonist": slots[0],
                    "consensus_title_pct": 8.0,
                    "consensus_spread_pct": 0.0,
                }
            ]
        else:
            opinions = [
                AgentOpinion(
                    agent=slot,
                    title_pct=8.0,
                    summary="Sala principal mantém chance de título do Brasil.",
                    answer="Brasil segue calibrado no caminho completo.",
                    source_urls=["https://example.com/main"],
                    agrees_with_protagonist=True,
                    consensus_check_question=(
                        "Os demais concordam integralmente com esta chance de título?"
                    ),
                )
                for slot in slots
            ]
            transcript = [
                {
                    "round": 1,
                    "protagonist": slots[0],
                    "question": "Concordam com a chance de título do Brasil?",
                    "responses": [],
                    "next_protagonist": slots[0],
                    "consensus_title_pct": 8.0,
                    "consensus_spread_pct": 0.0,
                }
            ]
        consensus = build_consensus(opinions, agent_slots=slots)
        return consensus, opinions, transcript, opinions

    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)
    monkeypatch.setattr("worldcup_brazil.pipeline._run_model_meeting", fake_run_model_meeting)
    config = load_config(Path("config/worldcup_brazil.example.json"))
    config.update(
        {
            "baseline_title_pct": 8.0,
            "minimum_source_ready_agents": 3,
            "source_planning_repair_attempts": 0,
            "meeting_min_participants": 3,
            "parallel_opponent_debriefing_enabled": True,
            "monte_carlo": {"enabled": False},
            "agents": [
                {"slot": "GPT 5.5", "provider": "openai", "model": "gpt-5.5", "endpoint": "x"},
                {
                    "slot": "Perplexity Pro",
                    "provider": "openai-compatible",
                    "model": "sonar",
                    "endpoint": "x",
                },
                {
                    "slot": "Gemini Pro",
                    "provider": "google-gemini",
                    "model": "gemini",
                    "endpoint": "x",
                },
            ],
        }
    )

    artifacts = asyncio.run(
        build_report_bundle(
            config=config,
            source_memory=SourceMemory(tmp_path / "source_memory.json"),
            generated_at=datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
            watchdog=None,
        )
    )

    assert set(meeting_rooms) == {"main_brazil", "opponent_path"}
    assert artifacts.bundle.metadata["parallel_opponent_debriefing"]["enabled"] is True
    assert artifacts.bundle.metadata["parallel_opponent_debriefing"]["rounds"] == 1
    assert main_room_first_knockout_opponent == ["Japão"]
    round_of_32 = [match for match in artifacts.bundle.knockout_matches if match.phase == "16 avos"]
    assert round_of_32[0].opponent == "Japão"
    assert round_of_32[0].scenario_pct == 41.0
    assert round_of_32[1].opponent == "Suécia"
    assert round_of_32[1].scenario_pct == 26.0


def test_source_planning_self_heal_accepts_query_backed_repair_language(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, timeout, allow_local_fallback):
        calls.append([spec.slot for spec in specs])
        if len(calls) == 1:
            return [
                AgentOpinion(
                    agent="Perplexity Pro",
                    title_pct=8.4,
                    summary="Plano com fonte verificável.",
                    source_urls=["https://example.com/market"],
                ),
                AgentOpinion(
                    agent="GPT 5.5",
                    title_pct=8.0,
                    summary="Resposta sem fontes auditáveis.",
                ),
                AgentOpinion(
                    agent="Gemini Pro",
                    title_pct=8.1,
                    summary="Resposta sem fontes auditáveis.",
                ),
            ]
        assert "reparo operacional" in prompt.lower()
        return [
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.2,
                summary=(
                    "Concordo com o reparo: usar fontes não-Opta, Elo e odds para Brasil, "
                    "Marrocos, Haiti e Escócia."
                ),
                answer="Plano de fontes, não voto de debriefing.",
                source_queries=["Brazil Morocco Haiti Scotland World Cup 2026 odds Elo Sofascore"],
                agrees_with_protagonist=True,
            ),
            AgentOpinion(
                agent="Gemini Pro",
                title_pct=8.1,
                summary="Plano de reparo com fonte auditável.",
                source_urls=["https://example.com/gemini-source"],
            ),
        ]

    async def fake_run_model_meeting(
        *,
        config,
        planning_opinions,
        generated_at,
        agent_specs,
        baseline_title_pct,
        allow_agent_fallback,
        watchdog,
        token_cost_ledger=None,
        **_kwargs,
    ):
        slots = [spec.slot for spec in agent_specs]
        assert slots == ["Perplexity Pro", "GPT 5.5", "Gemini Pro"]
        consensus = build_consensus(planning_opinions, agent_slots=slots)
        return consensus, planning_opinions, [], planning_opinions

    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)
    monkeypatch.setattr("worldcup_brazil.pipeline._run_model_meeting", fake_run_model_meeting)
    watchdog_path = tmp_path / "watchdog.jsonl"

    artifacts = asyncio.run(
        build_report_bundle(
            config={
                "baseline_title_pct": 8.0,
                "source_planning_repair_attempts": 1,
                "meeting_min_participants": 3,
                "agents": [
                    {
                        "slot": "Perplexity Pro",
                        "provider": "openai-compatible",
                        "model": "sonar-pro",
                        "env_api_key": None,
                        "endpoint": "https://example.com/perplexity",
                    },
                    {
                        "slot": "GPT 5.5",
                        "provider": "openai",
                        "model": "gpt-5.5",
                        "env_api_key": None,
                        "endpoint": "https://example.com/openai",
                    },
                    {
                        "slot": "Gemini Pro",
                        "provider": "google-gemini",
                        "model": "gemini-flash-latest",
                        "env_api_key": None,
                        "endpoint": "https://example.com/gemini/{model}",
                    },
                ],
                "group_matches": [
                    {"opponent": "Marrocos", "brazil_pct": 59.0},
                    {"opponent": "Haiti", "brazil_pct": 92.0},
                    {"opponent": "Escócia", "brazil_pct": 73.0},
                ],
                "knockout_matches": [],
            },
            source_memory=SourceMemory(tmp_path / "source_memory.json"),
            generated_at=datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
            watchdog=RunWatchdog(path=watchdog_path, verbose=False),
        )
    )

    assert artifacts.bundle.source_plan_by_model["GPT 5.5"]["source_queries"] == [
        "Brazil Morocco Haiti Scotland World Cup 2026 odds Elo Sofascore"
    ]
    assert [event for event in (
        json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()
    ) if event["step"] == "agent_source_quorum"][-1]["status"] == "finish"

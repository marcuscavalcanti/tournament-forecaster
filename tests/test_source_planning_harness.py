import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from worldcup_brazil.consensus import AgentOpinion
from worldcup_brazil.consensus import build_consensus
from worldcup_brazil.pipeline import (
    _has_opta_marker,
    _new_token_cost_ledger,
    _is_format_repairable_planning_reason,
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
    assert _has_opta_marker("Dados da Opta ficam excluídos como fonte, benchmark, ranking ou âncora.") is False
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

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, timeout, allow_local_fallback, progress_callback=None):
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


def test_source_planning_repairs_partial_json_before_meeting_even_when_quorum_is_met(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, timeout, allow_local_fallback, progress_callback=None):
        calls.append(
            {
                "slots": [spec.slot for spec in specs],
                "timeout": timeout,
                "prompt": prompt,
            }
        )
        if len(calls) == 1:
            return [
                AgentOpinion(
                    agent="Opus 4.8",
                    title_pct=8.0,
                    summary=(
                        "Resposta em JSON parcial; mantive leitura conservadora "
                        "até o modelo devolver campos auditáveis."
                    ),
                ),
                AgentOpinion(
                    agent="GPT 5.5",
                    title_pct=8.3,
                    summary="Plano com fonte verificável.",
                    source_urls=["https://example.com/gpt"],
                ),
                AgentOpinion(
                    agent="Perplexity Pro",
                    title_pct=8.4,
                    summary="Plano com fonte verificável.",
                    source_urls=["https://example.com/perplexity"],
                ),
                AgentOpinion(
                    agent="DeepSeek V4 Pro",
                    title_pct=8.1,
                    summary="Plano com busca verificável.",
                    source_queries=["Brazil Morocco Haiti Scotland World Cup 2026 odds Elo Sofascore"],
                ),
            ]
        assert calls[-1]["slots"] == ["Opus 4.8"]
        assert calls[-1]["timeout"] == 45
        repair_prompt = str(calls[-1]["prompt"])
        assert "JSON veio parcial" in repair_prompt
        assert "SOMENTE o objeto JSON completo" in repair_prompt
        return [
            AgentOpinion(
                agent="Opus 4.8",
                title_pct=8.2,
                summary="Reparo de formato com fontes auditáveis.",
                source_urls=["https://example.com/opus-repaired"],
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
        assert slots == ["Opus 4.8", "GPT 5.5", "Perplexity Pro", "DeepSeek V4 Pro"]
        consensus = build_consensus(planning_opinions, agent_slots=slots)
        return consensus, planning_opinions, [], planning_opinions

    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)
    monkeypatch.setattr("worldcup_brazil.pipeline._run_model_meeting", fake_run_model_meeting)
    watchdog_path = tmp_path / "watchdog.jsonl"

    artifacts = asyncio.run(
        build_report_bundle(
            config={
                "baseline_title_pct": 8.0,
                "minimum_source_ready_agents": 3,
                "source_planning_repair_attempts": 1,
                "source_planning_format_repair_timeout_seconds": 45,
                "meeting_min_participants": 3,
                "agents": [
                    {"slot": "Opus 4.8", "provider": "anthropic", "model": "opus", "endpoint": "x"},
                    {"slot": "GPT 5.5", "provider": "openai", "model": "gpt-5.5", "endpoint": "x"},
                    {"slot": "Perplexity Pro", "provider": "openai-compatible", "model": "sonar", "endpoint": "x"},
                    {"slot": "DeepSeek V4 Pro", "provider": "openai-compatible", "model": "deepseek", "endpoint": "x"},
                ],
                "group_matches": [{"opponent": "Marrocos", "brazil_pct": 59.0}],
                "knockout_matches": [],
            },
            source_memory=SourceMemory(tmp_path / "source_memory.json"),
            generated_at=datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
            watchdog=RunWatchdog(path=watchdog_path, verbose=False),
        )
    )

    assert calls[0]["slots"] == ["Opus 4.8", "GPT 5.5", "Perplexity Pro", "DeepSeek V4 Pro"]
    assert calls[1]["slots"] == ["Opus 4.8"]
    assert artifacts.bundle.source_plan_by_model["Opus 4.8"]["source_urls"] == [
        "https://example.com/opus-repaired"
    ]
    events = [json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()]
    assert any(
        event["step"] == "agent_source_format_repair"
        and event["status"] == "start"
        and event["extra"].get("agents") == ["Opus 4.8"]
        for event in events
    )


def test_source_planning_format_repair_does_not_catch_environment_failures() -> None:
    assert _is_format_repairable_planning_reason(
        "fallback operacional: Resposta removida do planejamento de fontes por devolver resposta parcial "
        "ou sem campos auditáveis."
    )
    assert not _is_format_repairable_planning_reason(
        "fallback operacional: Modelo sem resposta externa verificável porque HTTP Error 429: Too Many Requests."
    )
    assert not _is_format_repairable_planning_reason(
        "fallback operacional: busca/fetch externo indisponível ou sem permissão; "
        "source_queries não provam busca executada."
    )


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


def test_gemini_billing_depleted_reason_reaches_watchdog_and_bundle_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    billing_reason = (
        "Modelo sem resposta externa verificável porque Gemini API model fallback chain failed: "
        "gemini-3.5-flash: Google Gemini billing action required: prepayment credits are depleted; "
        "buy/prepay credits in AI Studio at https://ai.studio/projects before expecting Gemini to rejoin "
        "the model room. Raw API message: Your prepayment credits are depleted."
    )

    async def fake_call_all_agents(*args, **kwargs):
        return [
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.0,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/gpt"],
            ),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.1,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/perplexity"],
            ),
            AgentOpinion(
                agent="DeepSeek V4 Pro",
                title_pct=8.2,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/deepseek"],
            ),
            AgentOpinion(
                agent="Gemini Pro",
                title_pct=8.0,
                summary=billing_reason,
                used_fallback=True,
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

    artifacts = asyncio.run(
        build_report_bundle(
            config={
                "baseline_title_pct": 8.0,
                "minimum_source_ready_agents": 3,
                "source_planning_repair_attempts": 0,
                "meeting_min_participants": 3,
                "parallel_opponent_debriefing_enabled": False,
                "monte_carlo": {"enabled": False},
                "agents": [
                    {"slot": "GPT 5.5", "provider": "openai", "model": "gpt-5.5", "endpoint": "x"},
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

    removed_reasons = artifacts.bundle.metadata["removed_agent_reasons"]
    assert "Gemini Pro" in removed_reasons
    assert "Google Gemini billing action required" in removed_reasons["Gemini Pro"]
    assert "buy/prepay credits" in removed_reasons["Gemini Pro"]
    assert "https://ai.studio/projects" in removed_reasons["Gemini Pro"]
    assert any(
        "Gemini Pro" in warning and "comprar créditos" in warning and "https://ai.studio/projects" in warning
        for warning in artifacts.bundle.warnings
    )

    events = [json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()]
    removal = [
        event
        for event in events
        if event["step"] == "model_room"
        and event["status"] == "chat"
        and event["extra"].get("round") == "agent-removal"
        and event["extra"].get("agent") == "Gemini Pro"
    ][0]
    assert "Google Gemini billing action required" in removal["detail"]
    assert "https://ai.studio/projects" in removal["detail"]


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
    assert artifacts.bundle.metadata["parallel_opponent_debriefing"]["usable_for_main_room"] is True
    assert artifacts.bundle.metadata["parallel_opponent_debriefing"]["rounds"] == 1
    assert main_room_first_knockout_opponent == ["Japão"]
    round_of_32 = [match for match in artifacts.bundle.knockout_matches if match.phase == "16 avos"]
    assert round_of_32[0].opponent == "Japão"
    assert round_of_32[0].scenario_pct == 41.0
    assert round_of_32[1].opponent == "Suécia"
    assert round_of_32[1].scenario_pct == 26.0


def test_parallel_opponent_debriefing_without_explicit_consensus_does_not_rewrite_main_room(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_call_all_agents(*args, **kwargs):
        return [
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.0,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/gpt"],
            ),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.1,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/perplexity"],
            ),
            AgentOpinion(
                agent="DeepSeek V4 Pro",
                title_pct=7.9,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/deepseek"],
            ),
        ]

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
        slots = [spec.slot for spec in agent_specs]
        if room == "opponent_path":
            opinions = [
                AgentOpinion(
                    agent=slot,
                    title_pct=8.0,
                    summary="Sala paralela propõe adversários, mas bateu teto sem consenso explícito.",
                    answer="Japão e Suécia aparecem nos cenários, mas a sala não encerrou por consenso.",
                    scenario_probabilities={"16 avos: Japão": 41.0, "16 avos: Suécia": 26.0},
                    match_probabilities={"16 avos: Japão": 58.0, "16 avos: Suécia": 57.0},
                    source_urls=["https://example.com/slot-2f"],
                    agrees_with_protagonist=True,
                )
                for slot in slots
            ]
            consensus = build_consensus(opinions, agent_slots=slots)
            object.__setattr__(consensus, "exit_status", "max_rounds_no_consensus")
            object.__setattr__(
                consensus,
                "exit_warning",
                "Sala paralela atingiu o teto de rodadas sem consenso explícito.",
            )
            return consensus, opinions, [{"round": 3, "responses": []}], opinions

        main_room_first_knockout_opponent.append(
            str((config.get("knockout_matches") or [{}])[0].get("opponent", ""))
        )
        opinions = [
            AgentOpinion(
                agent=slot,
                title_pct=8.0,
                summary="Sala principal não deve receber top-2 lateral sem consenso.",
                answer="Brasil usa os cenários base quando a sala lateral não fechou consenso.",
                source_urls=["https://example.com/main"],
                agrees_with_protagonist=True,
            )
            for slot in slots
        ]
        return build_consensus(opinions, agent_slots=slots), opinions, [], opinions

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
            "knockout_matches": [
                {
                    "phase": "16 avos",
                    "opponent": "Adversário base",
                    "most_likely": True,
                    "scenario_pct": 46.0,
                    "brazil_pct": 57.0,
                },
                {
                    "phase": "16 avos",
                    "opponent": "Alternativa base",
                    "most_likely": False,
                    "scenario_pct": 24.0,
                    "brazil_pct": 56.0,
                },
            ],
            "agents": [
                {"slot": "GPT 5.5", "provider": "openai", "model": "gpt-5.5", "endpoint": "x"},
                {"slot": "Perplexity Pro", "provider": "openai-compatible", "model": "sonar", "endpoint": "x"},
                {"slot": "DeepSeek V4 Pro", "provider": "openai-compatible", "model": "deepseek", "endpoint": "x"},
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

    assert main_room_first_knockout_opponent == ["Adversário base"]
    assert artifacts.bundle.knockout_matches[0].opponent == "Adversário base"
    side_room = artifacts.bundle.metadata["parallel_opponent_debriefing"]
    assert side_room["usable_for_main_room"] is False
    assert side_room["exit_status"] == "max_rounds_no_consensus"
    assert artifacts.bundle.metadata["_parallel_opponent_briefing"]["usable_for_main_room"] is False
    assert any("sala paralela" in warning.lower() and "sem consenso" in warning.lower() for warning in artifacts.bundle.warnings)


def test_parallel_opponent_degraded_side_room_can_rewrite_when_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_call_all_agents(*args, **kwargs):
        return [
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.0,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/gpt"],
            ),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.1,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/perplexity"],
            ),
            AgentOpinion(
                agent="DeepSeek V4 Pro",
                title_pct=7.9,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/deepseek"],
            ),
        ]

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
        slots = [spec.slot for spec in agent_specs]
        if room == "opponent_path":
            opinions = [
                AgentOpinion(
                    agent=slot,
                    title_pct=8.0,
                    summary="Sala paralela trouxe quase-consenso bracket-safe com fontes.",
                    answer=(
                        "16 avos Japão e Suécia; Oitavas Equador e Noruega; Quartas Inglaterra e França; "
                        "Semifinal Argentina e Portugal; Final França e Espanha. Título 8%."
                    ),
                    scenario_probabilities={"16 avos: Japão": 41.0, "16 avos: Suécia": 26.0},
                    match_probabilities={"16 avos: Japão": 58.0, "16 avos: Suécia": 57.0},
                    source_urls=["https://example.com/slot-2f"],
                    agrees_with_protagonist=True,
                )
                for slot in slots
            ]
            consensus = build_consensus(opinions, agent_slots=slots)
            object.__setattr__(consensus, "exit_status", "degraded_last_valid")
            object.__setattr__(consensus, "exit_warning", "Publicado último consenso válido em modo degradado.")
            return (
                consensus,
                opinions,
                [
                    {
                        "round": 2,
                        "coverage": {"complete": True},
                        "responses": [],
                    }
                ],
                opinions,
            )

        main_room_first_knockout_opponent.append(
            str((config.get("knockout_matches") or [{}])[0].get("opponent", ""))
        )
        opinions = [
            AgentOpinion(
                agent=slot,
                title_pct=8.0,
                summary="Sala principal recebeu adversário lateral degradado habilitado.",
                answer="Brasil usa os cenários laterais quando o modo degradado foi habilitado explicitamente.",
                source_urls=["https://example.com/main"],
                agrees_with_protagonist=True,
            )
            for slot in slots
        ]
        return build_consensus(opinions, agent_slots=slots), opinions, [], opinions

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
            "opponent_debriefing_degraded_consensus_enabled": True,
            "opponent_debriefing_degraded_shadow_only": False,
            "monte_carlo": {"enabled": False},
            "knockout_matches": [
                {
                    "phase": "16 avos",
                    "opponent": "Adversário base",
                    "most_likely": True,
                    "scenario_pct": 46.0,
                    "brazil_pct": 57.0,
                },
                {
                    "phase": "16 avos",
                    "opponent": "Alternativa base",
                    "most_likely": False,
                    "scenario_pct": 24.0,
                    "brazil_pct": 56.0,
                },
            ],
            "agents": [
                {"slot": "GPT 5.5", "provider": "openai", "model": "gpt-5.5", "endpoint": "x"},
                {"slot": "Perplexity Pro", "provider": "openai-compatible", "model": "sonar", "endpoint": "x"},
                {"slot": "DeepSeek V4 Pro", "provider": "openai-compatible", "model": "deepseek", "endpoint": "x"},
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

    assert main_room_first_knockout_opponent == ["Japão"]
    assert artifacts.bundle.knockout_matches[0].opponent == "Japão"
    side_room = artifacts.bundle.metadata["parallel_opponent_debriefing"]
    assert side_room["usable_for_main_room"] is True
    assert side_room["degraded"] is True
    assert side_room["degraded_shadow_only"] is False


def test_parallel_opponent_degraded_side_room_shadow_only_does_not_rewrite_main_room(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_call_all_agents(*args, **kwargs):
        return [
            AgentOpinion(agent="GPT 5.5", title_pct=8.0, summary="Plano.", source_urls=["https://example.com/gpt"]),
            AgentOpinion(agent="Perplexity Pro", title_pct=8.1, summary="Plano.", source_urls=["https://example.com/p"]),
            AgentOpinion(agent="DeepSeek V4 Pro", title_pct=7.9, summary="Plano.", source_urls=["https://example.com/d"]),
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
        room = str(config.get("_meeting_room", "main_brazil"))
        slots = [spec.slot for spec in agent_specs]
        if room == "opponent_path":
            opinions = [
                AgentOpinion(
                    agent=slot,
                    title_pct=8.0,
                    summary="Quase-consenso bracket-safe com fontes.",
                    answer=(
                        "16 avos Japão e Suécia; Oitavas Equador e Noruega; Quartas Inglaterra e França; "
                        "Semifinal Argentina e Portugal; Final França e Espanha. Título 8%."
                    ),
                    scenario_probabilities={"16 avos: Japão": 41.0},
                    source_urls=["https://example.com/slot-2f"],
                    agrees_with_protagonist=True,
                )
                for slot in slots
            ]
            consensus = build_consensus(opinions, agent_slots=slots)
            object.__setattr__(consensus, "exit_status", "degraded_last_valid")
            return consensus, opinions, [{"round": 2, "coverage": {"complete": True}, "responses": []}], opinions

        opinions = [
            AgentOpinion(
                agent=slot,
                title_pct=8.0,
                summary="Sala principal manteve base porque degradado esta em shadow.",
                answer="Sem habilitação explícita, top-2 lateral degradado não reescreve o main room.",
                source_urls=["https://example.com/main"],
                agrees_with_protagonist=True,
            )
            for slot in slots
        ]
        return build_consensus(opinions, agent_slots=slots), opinions, [], opinions

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
            "opponent_debriefing_degraded_consensus_enabled": True,
            "opponent_debriefing_degraded_shadow_only": True,
            "monte_carlo": {"enabled": False},
            "knockout_matches": [
                {
                    "phase": "16 avos",
                    "opponent": "Adversário base",
                    "most_likely": True,
                    "scenario_pct": 46.0,
                    "brazil_pct": 57.0,
                }
            ],
            "agents": [
                {"slot": "GPT 5.5", "provider": "openai", "model": "gpt-5.5", "endpoint": "x"},
                {"slot": "Perplexity Pro", "provider": "openai-compatible", "model": "sonar", "endpoint": "x"},
                {"slot": "DeepSeek V4 Pro", "provider": "openai-compatible", "model": "deepseek", "endpoint": "x"},
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

    assert artifacts.bundle.knockout_matches[0].opponent == "Adversário base"
    side_room = artifacts.bundle.metadata["parallel_opponent_debriefing"]
    assert side_room["usable_for_main_room"] is False
    assert side_room["degraded"] is False
    assert side_room["degraded_shadow_only"] is True
    assert side_room["degraded_would_be_usable"] is True


def test_main_meeting_degraded_publish_is_persisted_in_bundle_warnings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_call_all_agents(*args, **kwargs):
        return [
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.0,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/gpt"],
            ),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.1,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/perplexity"],
            ),
            AgentOpinion(
                agent="DeepSeek V4 Pro",
                title_pct=7.9,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/deepseek"],
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
        opinions = [
            AgentOpinion(
                agent=slot,
                title_pct=8.0,
                summary="Sala principal publica último consenso válido em modo degradado.",
                answer="Houve rodada final estéril, então este consenso precisa aparecer com alerta.",
                source_urls=["https://example.com/main"],
                agrees_with_protagonist=True,
            )
            for slot in slots
        ]
        consensus = build_consensus(opinions, agent_slots=slots)
        object.__setattr__(consensus, "exit_status", "degraded_last_valid")
        object.__setattr__(
            consensus,
            "exit_warning",
            "Sala principal publicou o último consenso válido em modo degradado.",
        )
        return consensus, opinions, [], opinions

    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)
    monkeypatch.setattr("worldcup_brazil.pipeline._run_model_meeting", fake_run_model_meeting)
    config = load_config(Path("config/worldcup_brazil.example.json"))
    config.update(
        {
            "baseline_title_pct": 8.0,
            "minimum_source_ready_agents": 3,
            "source_planning_repair_attempts": 0,
            "meeting_min_participants": 3,
            "parallel_opponent_debriefing_enabled": False,
            "monte_carlo": {"enabled": False},
            "agents": [
                {"slot": "GPT 5.5", "provider": "openai", "model": "gpt-5.5", "endpoint": "x"},
                {"slot": "Perplexity Pro", "provider": "openai-compatible", "model": "sonar", "endpoint": "x"},
                {"slot": "DeepSeek V4 Pro", "provider": "openai-compatible", "model": "deepseek", "endpoint": "x"},
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

    assert artifacts.bundle.metadata["meeting_exit_status"] == "degraded_last_valid"
    assert artifacts.bundle.metadata["meeting_exit_warning"]
    assert any("modo degradado" in warning.lower() for warning in artifacts.bundle.warnings)


def test_blind_peer_review_shadow_metadata_is_persisted_without_decision_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, **kwargs):
        call_role = kwargs.get("call_role")
        if call_role == "blind_peer_review":
            return [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=8.0,
                    summary="Revisão cega em shadow.",
                    answer=json.dumps(
                        {
                            "scores": [
                                {"position_id": "position_1", "score": 0.81, "accepted": True},
                                {"position_id": "position_2", "score": 0.76, "accepted": True},
                                {"position_id": "position_3", "score": 0.64, "accepted": False},
                            ]
                        }
                    ),
                    raw_text=json.dumps(
                        {
                            "scores": [
                                {"position_id": "position_1", "score": 0.81, "accepted": True},
                                {"position_id": "position_2", "score": 0.76, "accepted": True},
                                {"position_id": "position_3", "score": 0.64, "accepted": False},
                            ]
                        }
                    ),
                    source_urls=["https://example.com/blind-review"],
                )
                for spec in specs
            ]
        if "planejamento de fontes" in prompt.lower() or "source_urls" in prompt.lower():
            return [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=8.0,
                    summary="Plano com fonte verificável.",
                    source_urls=[f"https://example.com/{spec.slot.lower().replace(' ', '-')}"],
                )
                for spec in specs
            ]
        return [
            AgentOpinion(
                agent=spec.slot,
                title_pct=8.0,
                summary="Sala principal mantém chance de título do Brasil.",
                answer="Concordo com odds, Elo e chaveamento; Brasil fica em 8%.",
                source_urls=["https://example.com/main"],
                agrees_with_protagonist=True,
            )
            for spec in specs
        ]

    async def fake_call_agent(spec, prompt, **kwargs):
        return AgentOpinion(
            agent=spec.slot,
            title_pct=8.0,
            summary="Pergunta com fonte verificável.",
            question="Concordam com Brasil em 8% após odds e Elo?",
            answer="Odds e Elo sustentam Brasil em 8%.",
            source_urls=["https://example.com/question"],
            agrees_with_protagonist=True,
        )

    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    config = load_config(Path("config/worldcup_brazil.example.json"))
    config.update(
        {
            "baseline_title_pct": 8.0,
            "minimum_source_ready_agents": 3,
            "source_planning_repair_attempts": 0,
            "meeting_min_participants": 3,
            "meeting_min_rounds": 1,
            "meeting_max_rounds": 1,
            "meeting_require_full_path_coverage": False,
            "parallel_opponent_debriefing_enabled": False,
            "monte_carlo": {"enabled": False},
            "blind_peer_review_enabled": True,
            "blind_peer_review_shadow_only": True,
            "blind_peer_review_timeout_seconds": 12,
            "agents": [
                {"slot": "GPT 5.5", "provider": "openai", "model": "gpt-5.5", "endpoint": "x"},
                {"slot": "Perplexity Pro", "provider": "openai-compatible", "model": "sonar", "endpoint": "x"},
                {"slot": "DeepSeek V4 Pro", "provider": "openai-compatible", "model": "deepseek", "endpoint": "x"},
            ],
        }
    )

    watchdog_path = tmp_path / "watchdog.jsonl"
    artifacts = asyncio.run(
        build_report_bundle(
            config=config,
            source_memory=SourceMemory(tmp_path / "source_memory.json"),
            generated_at=datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
            watchdog=RunWatchdog(path=watchdog_path, verbose=False),
        )
    )

    assert artifacts.bundle.metadata["agent_title_consensus_pct"] == 8.0
    chairman = artifacts.bundle.metadata["numeric_chairman"]
    assert chairman["enabled"] is True
    assert chairman["number_owner"] == "agent_scaled_fallback"
    assert chairman["primary_number_owner"] == "monte_carlo_reconciled_funnel"
    assert chairman["llm_role"] == "narrative_and_bounded_adjustment"
    assert chairman["llm_decides_number"] is False
    assert chairman["hard_gate"] == "ReportCoherenceError"
    assert chairman["stage_probability_source"] == "agent_scaled_fallback"
    fast_path = artifacts.bundle.metadata["llm_council_fast_path"]
    assert fast_path["enabled"] is False
    assert fast_path["acted_on_decision"] is False
    shadow = artifacts.bundle.metadata["blind_peer_review_shadow"]
    assert shadow["enabled"] is True
    assert shadow["shadow_only"] is True
    assert shadow["acted_on_decision"] is False
    assert shadow["rounds_reviewed"] == [1]
    assert shadow["blind_top_position_id"] == "position_1"
    assert shadow["blind_acceptance_count"] == 2
    assert shadow["blind_review_score"]["position_1"] == 0.81
    assert shadow["self_preference_leakage"]["reviewer_count"] == 3
    events = [json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()]
    assert any(event["step"] == "blind_peer_review" and event["status"] == "start" for event in events)
    finish_events = [
        event for event in events if event["step"] == "blind_peer_review" and event["status"] == "finish"
    ]
    assert finish_events
    assert finish_events[-1]["extra"]["acted_on_decision"] is False


def test_parallel_opponent_debriefing_timeout_does_not_block_main_room(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_call_all_agents(*args, **kwargs):
        return [
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.0,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/gpt"],
            ),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.1,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/perplexity"],
            ),
            AgentOpinion(
                agent="DeepSeek V4 Pro",
                title_pct=7.9,
                summary="Plano com fonte verificável.",
                source_urls=["https://example.com/deepseek"],
            )
        ]

    rooms: list[str] = []

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
        rooms.append(room)
        if room == "opponent_path":
            progress_sink = _kwargs.get("progress_sink")
            if progress_sink is not None:
                progress_sink["participants"] = [spec.slot for spec in agent_specs]
                progress_sink["pending_round"] = {
                    "round": 1,
                    "protagonist": "GPT 5.5",
                    "question": "Quais sao os top-2 por fase no cruzamento oficial?",
                }
            await asyncio.sleep(0.05)
        slots = [spec.slot for spec in agent_specs]
        opinions = [
            AgentOpinion(
                agent=slot,
                title_pct=8.0,
                summary="Consenso auditável.",
                answer="Sala principal segue mesmo se a lateral falhar.",
                source_urls=["https://example.com/source"],
                agrees_with_protagonist=True,
            )
            for slot in slots
        ]
        return build_consensus(opinions, agent_slots=slots), opinions, [], opinions

    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)
    monkeypatch.setattr("worldcup_brazil.pipeline._run_model_meeting", fake_run_model_meeting)
    watchdog_path = tmp_path / "watchdog.jsonl"
    config = load_config(Path("config/worldcup_brazil.example.json"))
    config.update(
        {
            "baseline_title_pct": 8.0,
            "minimum_source_ready_agents": 3,
            "source_planning_repair_attempts": 0,
            "meeting_min_participants": 3,
            "parallel_opponent_debriefing_enabled": True,
            "parallel_opponent_debriefing_timeout_seconds": 0.01,
            "monte_carlo": {"enabled": False},
            "agents": [
                {"slot": "GPT 5.5", "provider": "openai", "model": "gpt-5.5", "endpoint": "x"},
                {"slot": "Perplexity Pro", "provider": "openai-compatible", "model": "sonar", "endpoint": "x"},
                {"slot": "DeepSeek V4 Pro", "provider": "openai-compatible", "model": "deepseek", "endpoint": "x"},
            ],
        }
    )

    artifacts = asyncio.run(
        build_report_bundle(
            config=config,
            source_memory=SourceMemory(tmp_path / "source_memory.json"),
            generated_at=datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
            watchdog=RunWatchdog(path=watchdog_path, verbose=False),
        )
    )

    assert "opponent_path" in rooms
    assert "main_brazil" in rooms
    assert artifacts.bundle.metadata["parallel_opponent_debriefing"]["failed"] is True
    assert artifacts.bundle.metadata["parallel_opponent_debriefing"]["timed_out"] is True
    assert artifacts.bundle.metadata["parallel_opponent_debriefing"]["participants"] == [
        "GPT 5.5",
        "Perplexity Pro",
        "DeepSeek V4 Pro",
    ]
    assert artifacts.bundle.metadata["parallel_opponent_debriefing"]["pending_round"] == {
        "round": 1,
        "protagonist": "GPT 5.5",
        "question": "Quais sao os top-2 por fase no cruzamento oficial?",
    }
    events = [json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()]
    assert any(
        event["step"] == "opponent_model_meeting" and event["status"] == "fail"
        for event in events
    )


def test_source_planning_self_heal_accepts_query_backed_repair_language(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, timeout, allow_local_fallback, progress_callback=None):
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


def test_format_repair_recovers_truncated_slot_even_with_quorum_met(monkeypatch) -> None:
    """Regressão do run 615b0948 (11/jun): Opus caiu no planejamento por JSON
    truncado com o quórum já fechado em 3/3 e foi para o caminho caro (probe na
    sala). Defeito de formato agora ganha um retry curto ANTES das salas."""
    from worldcup_brazil.agents import AgentSpec
    from worldcup_brazil.pipeline import _repair_format_only_planning_removals

    config = {"require_agent_source_plan": True, "minimum_source_ready_agents": 3}
    specs = [
        AgentSpec(slot="Opus 4.8", provider="anthropic", model="claude-opus-4-8",
                  env_api_key="ANTHROPIC_API_KEY", endpoint="https://api.anthropic.com/v1/messages"),
        AgentSpec(slot="GPT 5.5", provider="openai", model="gpt-5.5",
                  env_api_key="OPENAI_API_KEY", endpoint="https://api.openai.com/v1/responses"),
    ]
    healthy = [
        AgentOpinion(agent="GPT 5.5", title_pct=8.0, summary="Plano ok.",
                     source_urls=["https://example.com/a"]),
        AgentOpinion(agent="Perplexity Pro", title_pct=8.1, summary="Plano ok.",
                     source_urls=["https://example.com/b"]),
        AgentOpinion(agent="DeepSeek V4 Pro", title_pct=8.2, summary="Plano ok.",
                     source_urls=["https://example.com/c"]),
    ]
    report = {
        "quorum_met": True,
        "ready_count": 3,
        "required_count": 3,
        "removed_agents": [
            {"agent": "Opus 4.8",
             "reason": "devolver resposta parcial ou sem campos auditáveis"},
        ],
    }
    repair_calls: list[list[str]] = []

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        repair_calls.append([spec.slot for spec in specs])
        assert "SOMENTE o objeto JSON completo" in prompt
        return [
            AgentOpinion(agent="Opus 4.8", title_pct=7.9,
                         summary="Plano reenviado completo com as mesmas fontes.",
                         source_urls=["https://example.com/opus"])
        ]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    merged, updated = asyncio.run(
        _repair_format_only_planning_removals(
            config=config,
            planning_opinions=healthy,
            source_readiness_report=report,
            agent_specs=specs,
            generated_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=None,
            token_cost_ledger=_new_token_cost_ledger({}),
        )
    )

    assert repair_calls == [["Opus 4.8"]]
    removed_now = {entry["agent"] for entry in updated.get("removed_agents", [])}
    assert "Opus 4.8" not in removed_now
    assert any(op.agent == "Opus 4.8" and op.source_urls for op in merged)


def test_format_repair_skips_environment_class_removals(monkeypatch) -> None:
    """Remoção por ambiente (busca indisponível, 429) não ganha retry de formato:
    retry não conserta quota — o caminho dela é a política seletiva de reentrada."""
    from worldcup_brazil.agents import AgentSpec
    from worldcup_brazil.pipeline import _repair_format_only_planning_removals

    config = {"require_agent_source_plan": True, "minimum_source_ready_agents": 3}
    specs = [
        AgentSpec(slot="Gemini Pro", provider="google-gemini", model="gemini-3.5-flash",
                  env_api_key="GEMINI_API_KEY",
                  endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"),
    ]
    report = {
        "quorum_met": True,
        "ready_count": 3,
        "required_count": 3,
        "removed_agents": [
            {"agent": "Gemini Pro",
             "reason": "falha operacional sem resposta externa verificável (HTTP 429)"},
        ],
    }

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        raise AssertionError("não deveria chamar reparo para remoção de ambiente")

    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    merged, updated = asyncio.run(
        _repair_format_only_planning_removals(
            config=config,
            planning_opinions=[],
            source_readiness_report=report,
            agent_specs=specs,
            generated_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=None,
            token_cost_ledger=_new_token_cost_ledger({}),
        )
    )

    assert updated is report

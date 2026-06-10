import asyncio
from datetime import datetime, timezone

from worldcup_brazil.agents import AgentSpec
from worldcup_brazil.consensus import AgentOpinion
from worldcup_brazil.pipeline import _run_model_meeting


def _specs() -> list[AgentSpec]:
    return [
        AgentSpec(
            slot="GPT 5.5",
            provider="openai",
            model="gpt-5.5",
            env_api_key="OPENAI_API_KEY",
            endpoint="https://api.openai.com/v1/responses",
        ),
        AgentSpec(
            slot="Perplexity Pro",
            provider="openai-compatible",
            model="sonar-pro",
            env_api_key="PERPLEXITY_API_KEY",
            endpoint="https://api.perplexity.ai/chat/completions",
        ),
        AgentSpec(
            slot="Gemini Pro",
            provider="google-gemini",
            model="gemini-flash-latest",
            env_api_key="GEMINI_API_KEY",
            endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        ),
    ]


def _planning_opinions() -> list[AgentOpinion]:
    return [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=10.0,
            summary="Planejou fontes com cobertura ampla de odds, Elo e elenco.",
            opening_argument="Odds, Elo e elenco apontam leitura estável para Marrocos e título em 10%.",
            source_urls=[
                "https://example.com/odds",
                "https://example.com/elo",
                "https://example.com/squad",
            ],
        ),
        AgentOpinion(agent="Perplexity Pro", title_pct=10.1, summary="Planejou fontes.", source_urls=["https://example.com/b"]),
        AgentOpinion(agent="Gemini Pro", title_pct=9.9, summary="Planejou fontes.", source_urls=["https://example.com/c"]),
    ]


def _healthy_question_opinion(agent: str) -> AgentOpinion:
    return AgentOpinion(
        agent=agent,
        title_pct=10.0,
        summary="Odds e Elo sustentam a leitura de Marrocos.",
        question="Com odds e Elo, concordam com a leitura de Marrocos e título em 10%?",
        answer="Odds e Elo sustentam Marrocos como rival direto e título em 10%.",
        source_queries=["Brazil Morocco odds Elo World Cup 2026"],
        agrees_with_protagonist=True,
    )


def _healthy_response(agent: str, title_pct: float) -> AgentOpinion:
    return AgentOpinion(
        agent=agent,
        title_pct=title_pct,
        summary="Concordo: odds e Elo sustentam a leitura de Marrocos.",
        answer=(
            "Concordo com o racional do protagonista: odds em 1.85 e Elo 1850 sustentam Brasil 59% "
            f"contra Marrocos e título em {title_pct}%, com fonte pública verificável."
        ),
        source_urls=["https://example.com/elo"],
        agrees_with_protagonist=True,
    )


def _invalid_response(agent: str) -> AgentOpinion:
    return AgentOpinion(
        agent=agent,
        title_pct=11.0,
        summary="Falha operacional sem resposta externa.",
        answer="Falha operacional sem resposta externa utilizável.",
        used_fallback=True,
    )


def _base_config() -> dict:
    return {
        "group_matches": [{"opponent": "Marrocos"}],
        "knockout_matches": [],
        "meeting_min_participants": 2,
        "meeting_require_peer_acceptance": False,
        "meeting_require_full_path_coverage": False,
        "require_auditable_source_urls_for_meeting_votes": False,
        "meeting_response_repair_attempts": 0,
        "agent_reentry_probe_enabled": False,
        "baseline_title_pct": 11.0,
    }


def test_circuit_breaker_removes_slot_after_consecutive_invalid_votes(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 5,
        "meeting_consensus_threshold_pct": 0.01,
        "meeting_slot_breaker_threshold": 2,
        "meeting_stability_rounds": 99,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        opinions = []
        for spec in specs:
            if spec.slot == "Perplexity Pro":
                opinions.append(_invalid_response("Perplexity Pro"))
            elif spec.slot == "Gemini Pro":
                opinions.append(_healthy_response("Gemini Pro", 9.0))
            else:
                opinions.append(_healthy_response("GPT 5.5", 10.0))
        return opinions

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    _consensus, _opinions, transcript, _all = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=_planning_opinions(),
            generated_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            agent_specs=_specs(),
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=None,
        )
    )

    assert len(transcript) == 5
    round_1_agents = {response["agent"] for response in transcript[0]["responses"]}
    round_3_agents = {response["agent"] for response in transcript[2]["responses"]}
    assert "Perplexity Pro" in round_1_agents
    assert "Perplexity Pro" not in round_3_agents
    assert "Perplexity Pro" not in {response["agent"] for response in transcript[4]["responses"]}


def test_circuit_breaker_never_drops_room_below_minimum_participants(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_participants": 3,
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 4,
        "meeting_consensus_threshold_pct": 0.01,
        "meeting_slot_breaker_threshold": 2,
        "meeting_stability_rounds": 99,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        opinions = []
        for spec in specs:
            if spec.slot == "Perplexity Pro":
                opinions.append(_invalid_response("Perplexity Pro"))
            elif spec.slot == "Gemini Pro":
                opinions.append(_healthy_response("Gemini Pro", 9.0))
            else:
                opinions.append(_healthy_response("GPT 5.5", 10.0))
        return opinions

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    _consensus, _opinions, transcript, _all = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=_planning_opinions(),
            generated_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            agent_specs=_specs(),
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=None,
        )
    )

    for turn in transcript:
        assert "Perplexity Pro" in {response["agent"] for response in turn["responses"]}


def test_meeting_early_exit_when_consensus_is_stable_with_acceptance(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_require_peer_acceptance": True,
        "meeting_min_rounds": 2,
        "meeting_max_rounds": 8,
        "meeting_consensus_threshold_pct": 2.5,
        "meeting_stability_delta_pp": 1.0,
        "meeting_stability_rounds": 2,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        opinions = []
        for spec in specs:
            if spec.slot == "Perplexity Pro":
                opinions.append(_healthy_response("Perplexity Pro", 13.0))
            elif spec.slot == "Gemini Pro":
                opinions.append(_healthy_response("Gemini Pro", 10.2))
            else:
                opinions.append(_healthy_response("GPT 5.5", 10.0))
        return opinions

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    _consensus, _opinions, transcript, _all = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=_planning_opinions(),
            generated_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            agent_specs=_specs(),
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=None,
        )
    )

    assert len(transcript) == 3


def test_protagonist_question_uses_dedicated_timeout(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 1,
        "meeting_consensus_threshold_pct": 50.0,
        "agent_timeout_seconds": 222,
        "protagonist_timeout_seconds": 33,
    }
    question_timeouts: list[int] = []
    response_timeouts: list[int] = []

    async def fake_call_agent(spec, prompt, **kwargs):
        question_timeouts.append(int(kwargs.get("timeout", -1)))
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        response_timeouts.append(int(kwargs.get("timeout", -1)))
        return [
            _healthy_response("Perplexity Pro", 10.1) if spec.slot == "Perplexity Pro" else _healthy_response("Gemini Pro", 9.9)
            for spec in specs
        ]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=_planning_opinions(),
            generated_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            agent_specs=_specs(),
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=None,
        )
    )

    assert question_timeouts == [33]
    assert response_timeouts == [222]

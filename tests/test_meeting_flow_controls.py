import asyncio
from datetime import datetime, timezone

import pytest

from worldcup_brazil.agents import AgentSpec
from worldcup_brazil.consensus import AgentOpinion
from worldcup_brazil.pipeline import (
    MeetingConsensusError,
    _format_impossible_opponent_reason,
    _run_model_meeting,
)


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


def test_meeting_aborts_with_error_after_consecutive_sterile_rounds(monkeypatch) -> None:
    """Regressão do run de 10/jun/2026: a sala rodou 18 rodadas com 95/97 respostas
    removidas e publicou consenso fabricado de fallbacks. Sala sem nenhum voto válido
    por N rodadas consecutivas agora aborta com MeetingConsensusError."""
    config = {
        **_base_config(),
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 8,
        "meeting_sterile_round_limit": 2,
        "meeting_slot_breaker_threshold": 99,
        "meeting_stability_rounds": 99,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _invalid_response(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        return [_invalid_response(spec.slot) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    with pytest.raises(MeetingConsensusError):
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


def test_zombie_protagonist_loses_leadership_after_invalid_round(monkeypatch) -> None:
    """Regressão do run de 10/jun/2026: Gemini segurou o protagonismo por 10 rodadas
    sem nenhum voto válido porque 'não houve discordância útil'. Protagonista com voto
    inválido na rodada perde a liderança para o melhor respondente válido."""
    config = {
        **_base_config(),
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 4,
        "meeting_consensus_threshold_pct": 0.01,
        "meeting_stability_rounds": 99,
        "meeting_slot_breaker_threshold": 99,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        if spec.slot == "GPT 5.5":
            return _invalid_response("GPT 5.5")
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        return [
            _healthy_response(spec.slot, 9.0 if spec.slot == "Gemini Pro" else 10.0)
            for spec in specs
        ]

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

    assert transcript[0]["protagonist"] == "GPT 5.5"
    assert transcript[1]["protagonist"] != "GPT 5.5"


def test_slot_broken_twice_stays_out_for_rest_of_run(monkeypatch) -> None:
    """Regressão da porta giratória de 10/jun/2026: slot removido pelo breaker era
    readmitido pelo probe e removido de novo, em ciclo. Agora: 1 reentrada por run;
    quebrou de novo, fora até o fim."""
    config = {
        **_base_config(),
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 8,
        "meeting_consensus_threshold_pct": 0.01,
        "meeting_stability_rounds": 99,
        "meeting_slot_breaker_threshold": 2,
        "agent_reentry_probe_enabled": True,
        "meeting_max_reentries_per_slot": 1,
        "agent_reentry_probe_max_attempts": 8,
    }
    readmitted_opinion = AgentOpinion(
        agent="Perplexity Pro",
        title_pct=10.1,
        summary="Plano novo com fontes próprias.",
        source_urls=["https://example.com/reentry"],
    )

    async def fake_probe(*, spec, config, generated_at, baseline_title_pct, removed_reason, timeout):
        return readmitted_opinion, "", "prompt de reentrada"

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        opinions = []
        for spec in specs:
            if spec.slot == "Perplexity Pro":
                opinions.append(_invalid_response("Perplexity Pro"))
            else:
                opinions.append(_healthy_response(spec.slot, 9.0 if spec.slot == "Gemini Pro" else 10.0))
        return opinions

    monkeypatch.setattr("worldcup_brazil.pipeline._run_agent_reentry_probe", fake_probe)
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

    rounds_with_perplexity = [
        index
        for index, turn in enumerate(transcript)
        if "Perplexity Pro" in {response["agent"] for response in turn["responses"]}
    ]
    assert 0 in rounds_with_perplexity
    assert any(index >= 2 for index in rounds_with_perplexity), "reentrada única deveria ter acontecido"
    assert rounds_with_perplexity[-1] < len(transcript) - 2, "após a segunda quebra o slot não pode voltar"


def test_reentry_probe_attempts_are_capped(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 8,
        "meeting_consensus_threshold_pct": 0.01,
        "meeting_stability_rounds": 99,
        "meeting_slot_breaker_threshold": 2,
        "agent_reentry_probe_enabled": True,
        "agent_reentry_probe_max_attempts": 2,
    }
    probe_calls: list[str] = []

    async def fake_probe(*, spec, config, generated_at, baseline_title_pct, removed_reason, timeout):
        probe_calls.append(spec.slot)
        return None, "HTTP 429", ""

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        opinions = []
        for spec in specs:
            if spec.slot == "Perplexity Pro":
                opinions.append(_invalid_response("Perplexity Pro"))
            else:
                opinions.append(_healthy_response(spec.slot, 9.0 if spec.slot == "Gemini Pro" else 10.0))
        return opinions

    monkeypatch.setattr("worldcup_brazil.pipeline._run_agent_reentry_probe", fake_probe)
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

    assert probe_calls.count("Perplexity Pro") == 2


def test_reentry_probe_quorum_risk_policy_skips_when_active_room_is_healthy(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_participants": 3,
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 1,
        "meeting_consensus_threshold_pct": 10.0,
        "agent_reentry_probe_enabled": True,
        "agent_reentry_probe_policy": "quorum_risk",
        "agent_reentry_probe_min_round": 3,
    }
    opus_spec = AgentSpec(
        slot="Opus 4.8",
        provider="anthropic",
        model="claude-opus-4-8",
        env_api_key="ANTHROPIC_API_KEY",
        endpoint="https://api.anthropic.com/v1/messages",
    )
    probe_calls: list[str] = []

    async def fake_probe(*, spec, config, generated_at, baseline_title_pct, removed_reason, timeout):
        probe_calls.append(spec.slot)
        return None, "should not run", ""

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        return [_healthy_response(spec.slot, 10.0) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline._run_agent_reentry_probe", fake_probe)
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
            reentry_candidate_specs=[opus_spec],
            reentry_removed_reasons={
                "Opus 4.8": (
                    "fallback operacional: Resposta removida do planejamento de fontes por devolver "
                    "resposta parcial ou sem campos auditáveis"
                )
            },
        )
    )

    assert probe_calls == []


def test_format_impossible_opponent_reason_includes_phase_country_and_candidates() -> None:
    reason = _format_impossible_opponent_reason(
        {
            "phase": "Oitavas",
            "invalid_opponents": ["França"],
            "allowed_opponents": ["Alemanha", "Equador", "Senegal"],
        }
    )
    assert reason.startswith("citar adversário impossível para o cruzamento oficial")
    assert "Oitavas" in reason
    assert "França" in reason
    assert "Alemanha" in reason


def test_breaker_removing_protagonist_in_sterile_round_raises_typed_error(monkeypatch) -> None:
    """Regressão da revisão pós-fix: breaker removendo o protagonista numa rodada estéril
    abaixo do limite deixava um slot fora da sala como próximo protagonista e estourava
    ValueError cru no build_consensus da rodada seguinte. Agora: ou o protagonismo é
    forçado para um slot ativo, ou a sala aborta com MeetingConsensusError tipado."""
    config = {
        **_base_config(),
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 8,
        "meeting_sterile_round_limit": 3,
        "meeting_slot_breaker_threshold": 2,
        "meeting_stability_rounds": 99,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _invalid_response(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        return [_invalid_response(spec.slot) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    with pytest.raises(MeetingConsensusError):
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


def test_planning_removed_slot_respects_reentry_cooldown(monkeypatch) -> None:
    """Regressão da revisão pós-fix: candidatos de reentrada vindos do PLANEJAMENTO
    ignoravam meeting_max_reentries_per_slot (max=1 permitia 2 reentradas). O cooldown
    agora conta reentradas efetivas, valendo para qualquer origem do candidato."""
    config = {
        **_base_config(),
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 8,
        "meeting_consensus_threshold_pct": 0.01,
        "meeting_stability_rounds": 99,
        "meeting_slot_breaker_threshold": 2,
        "agent_reentry_probe_enabled": True,
        "meeting_max_reentries_per_slot": 1,
        "agent_reentry_probe_max_attempts": 8,
    }
    perplexity_spec = next(spec for spec in _specs() if spec.slot == "Perplexity Pro")
    active_specs = [spec for spec in _specs() if spec.slot != "Perplexity Pro"]
    planning = [opinion for opinion in _planning_opinions() if opinion.agent != "Perplexity Pro"]
    readmitted_opinion = AgentOpinion(
        agent="Perplexity Pro",
        title_pct=10.1,
        summary="Plano novo com fontes próprias.",
        source_urls=["https://example.com/reentry"],
    )

    async def fake_probe(*, spec, config, generated_at, baseline_title_pct, removed_reason, timeout):
        return readmitted_opinion, "", "prompt de reentrada"

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        opinions = []
        for spec in specs:
            if spec.slot == "Perplexity Pro":
                opinions.append(_invalid_response("Perplexity Pro"))
            else:
                opinions.append(_healthy_response(spec.slot, 9.0 if spec.slot == "Gemini Pro" else 10.0))
        return opinions

    monkeypatch.setattr("worldcup_brazil.pipeline._run_agent_reentry_probe", fake_probe)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    _consensus, _opinions, transcript, _all = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=planning,
            generated_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            agent_specs=active_specs,
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=None,
            reentry_candidate_specs=[perplexity_spec],
            reentry_removed_reasons={"Perplexity Pro": "removido no planejamento"},
        )
    )

    rounds_with_perplexity = [
        index
        for index, turn in enumerate(transcript)
        if "Perplexity Pro" in {response["agent"] for response in turn["responses"]}
    ]
    assert rounds_with_perplexity, "a única reentrada permitida deveria ter acontecido"
    assert rounds_with_perplexity[-1] < len(transcript) - 2, (
        "após quebrar pós-reentrada, slot vindo do planejamento não pode voltar"
    )


def test_production_shape_fallback_question_increments_streak(monkeypatch) -> None:
    """Regressão da revisão pós-fix: o fallback de produção carrega pergunta enlatada
    NÃO-vazia, que era classificada como pergunta válida e resetava o streak. A detecção
    agora considera used_fallback/removed_from_main."""
    config = {
        **_base_config(),
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 4,
        "meeting_consensus_threshold_pct": 0.01,
        "meeting_stability_rounds": 99,
        "meeting_slot_breaker_threshold": 99,
    }
    canned_question = "Qual premissa ainda precisa de evidência externa antes de mover o consenso?"

    async def fake_call_agent(spec, prompt, **kwargs):
        if spec.slot == "GPT 5.5":
            return AgentOpinion(
                agent="GPT 5.5",
                title_pct=11.0,
                summary="Fallback operacional com pergunta enlatada.",
                question=canned_question,
                answer="Falha operacional sem resposta externa utilizável.",
                used_fallback=True,
            )
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        return [
            _healthy_response(spec.slot, 9.0 if spec.slot == "Gemini Pro" else 10.0)
            for spec in specs
        ]

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

    assert transcript[0]["protagonist"] == "GPT 5.5"
    assert transcript[1]["protagonist"] != "GPT 5.5"


def test_opponent_debriefing_room_has_own_round_contract_fitting_budget() -> None:
    """Regressão do run 615b0948 (11/jun): a sala paralela herdava meeting_min_rounds=6,
    max_rounds=18 e agent_timeout=240s da sala principal — 6 rodadas × ~225-300s nunca
    cabem no orçamento de 900s, então o timeout era matematicamente garantido em TODO
    run diário e as rodadas completas eram descartadas (rounds=0). Contrato próprio."""
    from pathlib import Path

    from worldcup_brazil.pipeline import (
        _opponent_debriefing_budget_warning,
        _opponent_debriefing_config,
        load_config,
    )

    config = load_config(Path("config/worldcup_brazil.example.json"))
    assert int(config.get("meeting_min_rounds", 6)) >= 6  # sala principal intacta

    sub_config = _opponent_debriefing_config(config)

    assert int(sub_config["meeting_min_rounds"]) <= 2
    assert int(sub_config["meeting_max_rounds"]) <= 3
    assert int(sub_config["meeting_stability_rounds"]) == 1
    assert int(sub_config["agent_timeout_seconds"]) <= 120
    assert int(sub_config["protagonist_timeout_seconds"]) <= 120  # 1ª chamada de toda rodada
    assert _opponent_debriefing_budget_warning(config) is None

    tight = dict(config)
    tight["parallel_opponent_debriefing_timeout_seconds"] = 60
    warning = _opponent_debriefing_budget_warning(tight)
    assert warning is not None
    assert "não cabe no orçamento" in warning

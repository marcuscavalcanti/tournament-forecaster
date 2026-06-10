import asyncio
from datetime import datetime, timezone

from worldcup_brazil.agents import AgentSpec
from worldcup_brazil.consensus import AgentOpinion, build_consensus
from worldcup_brazil.meeting import (
    MeetingResponse,
    build_meeting_turn,
    consensus_reached,
    choose_next_protagonist,
)
from worldcup_brazil.pipeline import (
    _initial_protagonist,
    _meeting_coverage_report,
    _repair_invalid_meeting_responses,
    _run_model_meeting,
    _sanitize_main_meeting_opinions,
)


def test_choose_next_protagonist_stays_when_no_model_disagrees() -> None:
    responses = [
        MeetingResponse(agent="Opus 4.8", answer="ok", title_pct=11.0, support_score=0.82, accepted=True),
        MeetingResponse(agent="GPT 5.5", answer="aceito", title_pct=12.0, support_score=0.91, accepted=True),
    ]

    assert choose_next_protagonist(responses, current_protagonist="Opus 4.8") == "Opus 4.8"


def test_choose_next_protagonist_uses_disagreement_and_prior_protagonism_as_tiebreak() -> None:
    responses = [
        MeetingResponse(agent="GPT 5.5", answer="discordo", title_pct=12.0, support_score=0.91, disagreed=True),
        MeetingResponse(
            agent="Perplexity Pro",
            answer="também discordo",
            title_pct=10.0,
            support_score=0.95,
            disagreed=True,
        ),
    ]

    assert (
        choose_next_protagonist(
            responses,
            current_protagonist="Opus 4.8",
            protagonist_counts={"GPT 5.5": 2, "Perplexity Pro": 1},
        )
        == "GPT 5.5"
    )


def test_choose_next_protagonist_allows_accepted_merit_bid_without_fake_disagreement() -> None:
    responses = [
        MeetingResponse(
            agent="GPT 5.5",
            answer="Concordo com o racional e proponho testar cartões por árbitro com fonte X.",
            title_pct=11.2,
            support_score=0.93,
            accepted=True,
            leadership_bid=True,
            source_count=2,
        ),
        MeetingResponse(
            agent="Perplexity Pro",
            answer="Concordo sem nova pergunta.",
            title_pct=11.1,
            support_score=0.96,
            accepted=True,
            leadership_bid=False,
            source_count=5,
        ),
    ]

    assert (
        choose_next_protagonist(
            responses,
            current_protagonist="Opus 4.8",
            protagonist_counts={"GPT 5.5": 0, "Perplexity Pro": 2},
        )
        == "GPT 5.5"
    )


def test_choose_next_protagonist_prioritizes_real_disagreement_over_accepted_merit_bid() -> None:
    responses = [
        MeetingResponse(
            agent="GPT 5.5",
            answer="Concordo com o racional, mas proponho a próxima pergunta.",
            title_pct=11.2,
            support_score=1.0,
            accepted=True,
            leadership_bid=True,
            source_count=6,
        ),
        MeetingResponse(
            agent="DeepSeek V4 Pro",
            answer="Discordo: o ajuste de descanso está superestimado.",
            title_pct=9.5,
            support_score=0.82,
            disagreed=True,
            source_count=1,
        ),
    ]

    assert choose_next_protagonist(responses, current_protagonist="Opus 4.8") == "DeepSeek V4 Pro"


def test_meeting_turn_records_question_answers_and_next_protagonist() -> None:
    opinions = [
        AgentOpinion(
            agent="Opus 4.8",
            title_pct=11.0,
            summary="A pergunta boa é sobre odds.",
            answer="Mercado pesa menos sem liquidez.",
            agrees_with_protagonist=True,
        ),
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=12.0,
            summary="Resposta mais aceita.",
            answer="Ratings e odds convergem.",
            agrees_with_protagonist=True,
        ),
        AgentOpinion(
            agent="Perplexity Pro",
            title_pct=10.0,
            summary="Discordo do peso da fonte dominante.",
            answer="Discordo: uma fonte não pode dominar.",
            agrees_with_protagonist=False,
        ),
    ]

    turn = build_meeting_turn(
        round_index=2,
        protagonist="Opus 4.8",
        question="Qual fonte está distorcendo o consenso?",
        opinions=opinions,
        consensus_title_pct=11.5,
        protagonist_counts={"Perplexity Pro": 1, "GPT 5.5": 0},
    )

    assert turn["round"] == 2
    assert turn["protagonist"] == "Opus 4.8"
    assert turn["question"] == "Qual fonte está distorcendo o consenso?"
    assert turn["next_protagonist"] == "Perplexity Pro"
    assert turn["responses"][1]["agent"] == "GPT 5.5"
    assert "Ratings e odds" in turn["responses"][1]["answer"]
    assert turn["responses"][2]["disagreed"] is True


def test_meeting_turn_records_accepted_leadership_bid_as_next_question_candidate() -> None:
    turn = build_meeting_turn(
        round_index=2,
        protagonist="Opus 4.8",
        question="Concordam ou discordam?",
        opinions=[
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=11.0,
                summary="Aceito e proponho próximo teste.",
                answer="Concordo com a tese. Para avançar, eu testaria o risco de cartão dos volantes.",
                source_urls=["https://example.com/cards"],
                agrees_with_protagonist=True,
                leadership_bid=True,
                proposed_next_question="Cartões dos volantes mudam o Brasil nas quartas?",
                leadership_rationale="Nova pergunta auditável com fonte disciplinar.",
            )
        ],
        consensus_title_pct=11.0,
    )

    assert turn["next_protagonist"] == "GPT 5.5"
    assert turn["responses"][0]["accepted"] is True
    assert turn["responses"][0]["disagreed"] is False
    assert turn["responses"][0]["leadership_bid"] is True
    assert turn["responses"][0]["proposed_next_question"] == "Cartões dos volantes mudam o Brasil nas quartas?"


def test_meeting_turn_treats_disagreement_text_as_disagreement_even_if_boolean_is_wrong() -> None:
    turn = build_meeting_turn(
        round_index=1,
        protagonist="DeepSeek V4 Pro",
        question="Concordam ou discordam?",
        opinions=[
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=18.0,
                summary="Discordo do corte para 55%; eu usaria 58%.",
                answer="Discordo do racional do protagonista.",
                agrees_with_protagonist=True,
            ),
            AgentOpinion(
                agent="DeepSeek V4 Pro",
                title_pct=11.0,
                summary="Concordo.",
                answer="Concordo.",
                agrees_with_protagonist=True,
            ),
        ],
        consensus_title_pct=14.0,
    )

    assert turn["responses"][0]["disagreed"] is True
    assert turn["next_protagonist"] == "Perplexity Pro"


def test_meeting_turn_does_not_treat_partial_json_as_disagreement_or_leader() -> None:
    turn = build_meeting_turn(
        round_index=1,
        protagonist="Perplexity Pro",
        question="Concordam ou discordam?",
        opinions=[
            AgentOpinion(
                agent="DeepSeek V4 Pro",
                title_pct=11.0,
                summary="Resposta em JSON parcial; aproveitei as probabilidades jogo a jogo.",
                answer="Resposta em JSON parcial; aproveitei as probabilidades jogo a jogo.",
            ),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=6.5,
                summary="Concordo.",
                answer="Concordo.",
                agrees_with_protagonist=True,
            ),
        ],
        consensus_title_pct=8.2,
    )

    assert turn["responses"][0]["accepted"] is False
    assert turn["responses"][0]["disagreed"] is False
    assert turn["next_protagonist"] == "Perplexity Pro"


def test_meeting_turn_does_not_treat_removed_benchmark_response_as_disagreement() -> None:
    turn = build_meeting_turn(
        round_index=1,
        protagonist="Perplexity Pro",
        question="Concordam ou discordam?",
        opinions=[
            AgentOpinion(
                agent="DeepSeek V4 Pro",
                title_pct=9.0,
                summary="Resposta removida da sala principal por tentar usar benchmark reservado.",
                answer="Resposta removida da sala principal por tentar usar benchmark reservado.",
                used_fallback=True,
            ),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=9.0,
                summary="Concordo.",
                answer="Concordo.",
                agrees_with_protagonist=True,
            ),
        ],
        consensus_title_pct=9.0,
    )

    assert turn["responses"][0]["accepted"] is False
    assert turn["responses"][0]["disagreed"] is False


def test_meeting_turn_uses_structured_removed_flag_even_with_sources() -> None:
    turn = build_meeting_turn(
        round_index=1,
        protagonist="Perplexity Pro",
        question="Concordam ou discordam?",
        opinions=[
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=20.0,
                summary="texto sem marcador humano de remoção",
                answer="Concordo e tenho fonte.",
                source_urls=["https://example.com/source"],
                agrees_with_protagonist=True,
                removed_from_main=True,
                removal_reason="resposta invalidada pelo contrato da sala",
            ),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.0,
                summary="Concordo.",
                answer="Concordo.",
                agrees_with_protagonist=True,
            ),
        ],
        consensus_title_pct=8.0,
    )

    assert turn["responses"][0]["accepted"] is False
    assert turn["responses"][0]["disagreed"] is False
    assert turn["responses"][0]["removed_from_main"] is True
    assert turn["next_protagonist"] == "Perplexity Pro"


def test_initial_protagonist_uses_source_quality_not_raw_url_count() -> None:
    opinions = [
        AgentOpinion(
            agent="DeepSeek V4 Pro",
            title_pct=8.8,
            summary="Resposta em JSON parcial; mantive leitura conservadora.",
            source_urls=[f"https://example.com/deepseek/{index}" for index in range(14)],
        ),
        AgentOpinion(
            agent="Gemini Pro",
            title_pct=8.7,
            summary=(
                "Plano completo com odds, ratings, Sofascore, lesões, chaveamento, "
                "grupo e mata-mata cobertos."
            ),
            opening_argument="Cobertura por fase e por adversário, com hipóteses auditáveis.",
            source_urls=[f"https://example.com/gemini/{index}" for index in range(8)],
            source_queries=[
                "Brazil Morocco World Cup 2026 odds",
                "Brazil World Cup 2026 Elo ratings",
                "Brazil Morocco Sofascore recent form",
                "World Cup 2026 bracket Brazil 1C 2F",
                "Brazil squad injuries June 2026",
            ],
            scenario_probabilities={"16 avos: Holanda": 28.0, "Oitavas: França": 14.0},
            team_context_signals=[
                {
                    "team": "Brasil",
                    "category": "lesões/cortes/notícias recentes",
                    "rating_delta": 8.0,
                    "confidence": 0.7,
                    "source_url": "https://example.com/gemini/0",
                }
            ],
        ),
        AgentOpinion(
            agent="Perplexity Pro",
            title_pct=8.9,
            summary="Plano bom, mas com menos cobertura de cenário.",
            source_urls=[f"https://example.com/perplexity/{index}" for index in range(4)],
            source_queries=[f"query {index}" for index in range(4)],
        ),
    ]

    assert _initial_protagonist(opinions) == "Gemini Pro"


def test_targeted_meeting_repair_replaces_bad_response_without_reasking_all_models(monkeypatch) -> None:
    raw_bad = AgentOpinion(
        agent="GPT 5.5",
        title_pct=11.0,
        summary="Cito Sérvia como adversário de grupo.",
        answer="Brasil deve testar Sérvia, Marrocos e Escócia.",
        agrees_with_protagonist=False,
        source_queries=["Brazil Morocco Scotland Haiti odds"],
    )
    config = {
        "meeting_response_repair_attempts": 1,
        "group_matches": [
            {"opponent": "Marrocos"},
            {"opponent": "Haiti"},
            {"opponent": "Escócia"},
        ],
        "knockout_matches": [{"phase": "16 avos", "opponent": "Adversário mais provável a definir"}],
        "require_auditable_source_urls_for_meeting_votes": True,
    }
    sanitized = _sanitize_main_meeting_opinions([raw_bad], baseline_title_pct=11.0, config=config)
    calls = []

    async def fake_call_agent(spec, prompt, **kwargs):
        calls.append((spec.slot, prompt))
        return AgentOpinion(
            agent=spec.slot,
            title_pct=10.7,
            summary="Reparo: removi adversário fora do escopo e foquei Marrocos/Haiti/Escócia.",
            answer="Discordo parcialmente: Marrocos deve ficar em 58%, Haiti 90% e Escócia 72%, com título 10.7%.",
            critique="A resposta anterior citou adversário fora do grupo configurado.",
            adjustment="Voltar aos adversários configurados e aos cenários de mata-mata a definir.",
            agrees_with_protagonist=False,
            source_urls=["https://example.com/world-cup-2026-odds"],
            source_queries=["Brazil Morocco Haiti Scotland World Cup 2026 odds"],
        )

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)

    repaired, repair_opinions = asyncio.run(
        _repair_invalid_meeting_responses(
            config=config,
            round_index=2,
            protagonist="Perplexity Pro",
            question="Qual premissa muda?",
            previous_turn=None,
            generated_at=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
            responder_specs=[
                AgentSpec(
                    slot="GPT 5.5",
                    provider="openai",
                    model="gpt-5.5",
                    env_api_key=None,
                    endpoint="https://api.openai.com/v1/responses",
                )
            ],
            raw_opinions=[raw_bad],
            sanitized_opinions=sanitized,
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            timeout=30,
        )
    )

    assert calls == [("GPT 5.5", calls[0][1])]
    assert "Sua resposta anterior foi removida" in calls[0][1]
    assert "Sérvia" in calls[0][1]
    assert repaired[0].used_fallback is False
    assert repaired[0].title_pct == 10.7
    assert repair_opinions[0].agent == "GPT 5.5"


def test_meeting_coverage_requires_group_knockout_and_title_before_consensus_close() -> None:
    config = {
        "group_matches": [{"opponent": "Marrocos"}, {"opponent": "Haiti"}, {"opponent": "Escócia"}],
        "knockout_matches": [
            {"phase": "16 avos", "opponent": "Adversário mais provável a definir"},
            {"phase": "Oitavas", "opponent": "Adversário mais provável a definir"},
            {"phase": "Quartas", "opponent": "Adversário mais provável a definir"},
            {"phase": "Semifinal", "opponent": "Adversário mais provável a definir"},
            {"phase": "Final", "opponent": "Adversário mais provável a definir"},
        ],
    }
    partial = [
        {
            "round": 1,
            "question": "Brasil x Marrocos, Haiti e Escócia. Título 10%.",
            "responses": [{"answer": "Grupo coberto, mas sem mata-mata completo."}],
        }
    ]
    complete = [
        {
            "round": 1,
            "question": (
                "Brasil x Marrocos, Haiti e Escócia; 16 avos, Oitavas, Quartas, "
                "Semifinal, Final e título."
            ),
            "responses": [
                {
                    "answer": (
                        "Recalibro grupo e mata-mata: 16 avos, Oitavas, Quartas, "
                        "Semifinal e Final mantêm Brasil perto de 50-57%; título 9%."
                    )
                }
            ],
        }
    ]

    assert _meeting_coverage_report(partial, config)["complete"] is False
    report = _meeting_coverage_report(complete, config)
    assert report["complete"] is True
    assert report["missing_group_opponents"] == []
    assert report["missing_knockout_phases"] == []
    assert report["title_covered"] is True


def test_model_meeting_invalidates_bad_protagonist_question_and_continues_with_peer_leader(monkeypatch) -> None:
    config = {
        "group_matches": [
            {"opponent": "Marrocos", "venue": "Nova Jersey"},
            {"opponent": "Haiti", "venue": "Filadélfia"},
            {"opponent": "Escócia", "venue": "Miami"},
        ],
        "meeting_max_rounds": 1,
        "meeting_min_rounds": 1,
        "meeting_min_participants": 2,
        "meeting_require_peer_acceptance": False,
        "require_auditable_source_urls_for_meeting_votes": True,
        "_allowed_fact_source_urls": ["https://example.com/elo", "https://example.com/odds"],
    }
    agent_specs = [
        AgentSpec(
            slot="DeepSeek V4 Pro",
            provider="openai-compatible",
            model="deepseek-v4-pro",
            env_api_key="DEEPSEEK_API_KEY",
            endpoint="https://api.deepseek.com/chat/completions",
        ),
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
    ]
    planning_opinions = [
        AgentOpinion(
            agent="DeepSeek V4 Pro",
            title_pct=8.0,
            summary="Planejamento com muitas fontes.",
            source_urls=["https://example.com/elo", "https://example.com/odds"],
        ),
        AgentOpinion(agent="GPT 5.5", title_pct=8.1, summary="Planejamento menor."),
    ]

    async def fake_call_agent(*args, **kwargs):
        return AgentOpinion(
            agent="DeepSeek V4 Pro",
            title_pct=8.0,
            summary="Pergunta sobre grupo antigo.",
            question="Com base em odds e Elo, qual a chance do Brasil vencer a Sérvia na fase de grupos?",
            answer="Pergunta sobre grupo antigo.",
            source_urls=["https://example.com/elo"],
        )

    async def fake_call_all_agents(*args, **kwargs):
        return [
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.3,
                summary="Aceito ignorar a fala inválida e recalibrar Marrocos em 59% usando odds e Elo.",
                answer=(
                    "Concordo em ignorar a fala inválida: com odds e Elo, Marrocos fica perto de 59%, "
                    "Haiti segue acima de 90% e Escócia perto de 73%."
                ),
                source_urls=["https://example.com/elo"],
                agrees_with_protagonist=True,
            ),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=8.2,
                summary="Aceito a correção de escopo com mercado e rating.",
                answer=(
                    "Concordo com a correção de escopo: mercado e rating sustentam 59% contra Marrocos, "
                    "sem inventar Sérvia ou outro rival fora do JSON."
                ),
                source_urls=["https://example.com/odds"],
                agrees_with_protagonist=True,
            ),
        ]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    _consensus, _opinions, transcript, _all_opinions = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=planning_opinions,
            generated_at=__import__("datetime").datetime(2026, 6, 14),
            agent_specs=agent_specs,
            baseline_title_pct=8.0,
            allow_agent_fallback=True,
            watchdog=None,
        )
    )

    turn = transcript[0]
    assert "Sérvia" not in turn["question"]
    assert all("Sérvia" not in response["answer"] for response in turn["responses"])
    assert turn["invalidated_protagonist_question"]["agent"] == "DeepSeek V4 Pro"
    assert "fora do grupo configurado" in turn["invalidated_protagonist_question"]["reason"]
    assert turn["next_protagonist"] != "DeepSeek V4 Pro"


def test_model_meeting_does_not_close_consensus_before_full_path_coverage(monkeypatch) -> None:
    config = {
        "group_matches": [
            {"opponent": "Marrocos"},
            {"opponent": "Haiti"},
            {"opponent": "Escócia"},
        ],
        "knockout_matches": [
            {"phase": "16 avos", "opponent": "Adversário mais provável a definir", "most_likely": True},
            {"phase": "Oitavas", "opponent": "Adversário mais provável a definir", "most_likely": True},
            {"phase": "Quartas", "opponent": "Adversário mais provável a definir", "most_likely": True},
            {"phase": "Semifinal", "opponent": "Adversário mais provável a definir", "most_likely": True},
            {"phase": "Final", "opponent": "Adversário mais provável a definir", "most_likely": True},
        ],
        "meeting_max_rounds": 3,
        "meeting_min_rounds": 1,
        "meeting_min_participants": 2,
        "meeting_consensus_threshold_pct": 10.0,
        "meeting_require_peer_acceptance": False,
        "meeting_require_full_path_coverage": True,
        "require_auditable_source_urls_for_meeting_votes": False,
    }
    agent_specs = [
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
    planning_opinions = [
        AgentOpinion(agent="GPT 5.5", title_pct=10.0, summary="Planejou fontes."),
        AgentOpinion(agent="Perplexity Pro", title_pct=10.1, summary="Planejou fontes."),
        AgentOpinion(agent="Gemini Pro", title_pct=9.9, summary="Planejou fontes."),
    ]
    protagonist_calls = {"count": 0}

    async def fake_call_agent(*args, **kwargs):
        protagonist_calls["count"] += 1
        if protagonist_calls["count"] == 1:
            return AgentOpinion(
                agent="GPT 5.5",
                title_pct=10.0,
                summary="Pergunta só sobre grupo.",
                question=(
                    "Com odds e Elo, concordam com Marrocos, Haiti, Escócia e chance de título em 10%?"
                ),
                answer=(
                    "Marrocos, Haiti e Escócia calibram a fase de grupos; odds e Elo sustentam "
                    "probabilidade de título em 10%."
                ),
                source_queries=["Brazil Morocco Haiti Scotland odds Elo World Cup 2026"],
                agrees_with_protagonist=True,
            )
        return AgentOpinion(
            agent="GPT 5.5",
            title_pct=10.0,
            summary="Pergunta cobre caminho completo.",
            question=(
                "Com odds e Elo, concordam com 16 avos, Oitavas, Quartas, Semifinal, Final e chance de título "
                "usando apenas adversários a definir do JSON?"
            ),
            answer=(
                "Cobertura completa do caminho até título: odds e Elo mantêm probabilidade em 10% "
                "ao cobrir 16 avos, Oitavas, Quartas, Semifinal e Final."
            ),
            source_queries=["Brazil World Cup 2026 knockout odds Elo title probability"],
            agrees_with_protagonist=True,
        )

    async def fake_call_all_agents(prompt, *args, **kwargs):
        if "Rodada: 2\n" in prompt:
            answer = (
                "Aceito com probabilidade em 10%: odds e Elo cobrem 16 avos, Oitavas, Quartas, "
                "Semifinal, Final e chance de título, sem inventar país fora do JSON."
            )
        else:
            answer = (
                "Aceito apenas a leitura de grupo: odds e Elo sustentam probabilidade de 10% "
                "ao calibrar Marrocos, Haiti e Escócia."
            )
        return [
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=10.1,
                summary=answer,
                answer=answer,
                source_queries=["Brazil World Cup 2026 odds Elo"],
                agrees_with_protagonist=True,
            ),
            AgentOpinion(
                agent="Gemini Pro",
                title_pct=9.9,
                summary=answer,
                answer=answer,
                source_queries=["Brazil World Cup 2026 odds Elo"],
                agrees_with_protagonist=True,
            ),
        ]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    _consensus, _opinions, transcript, _all_opinions = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=planning_opinions,
            generated_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            agent_specs=agent_specs,
            baseline_title_pct=10.0,
            allow_agent_fallback=True,
            watchdog=None,
        )
    )

    assert len(transcript) == 2
    assert transcript[0]["coverage"]["complete"] is False
    assert transcript[0]["coverage"]["missing_knockout_phases"] == [
        "16 avos",
        "Oitavas",
        "Quartas",
        "Semifinal",
        "Final",
    ]
    assert transcript[1]["coverage"]["complete"] is True


def test_model_meeting_reenters_removed_agent_after_async_source_probe(monkeypatch) -> None:
    config = {
        "group_matches": [{"opponent": "Marrocos"}],
        "knockout_matches": [{"phase": "16 avos", "opponent": "Adversário mais provável a definir"}],
        "meeting_max_rounds": 2,
        "meeting_min_rounds": 2,
        "meeting_min_participants": 2,
        "meeting_consensus_threshold_pct": 10.0,
        "meeting_require_peer_acceptance": False,
        "meeting_require_full_path_coverage": False,
        "require_agent_source_plan": True,
        "require_auditable_source_urls_for_meeting_votes": False,
        "minimum_source_ready_agents": 1,
        "agent_reentry_probe_enabled": True,
        "agent_reentry_probe_timeout_seconds": 180,
    }
    active_specs = [
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
    ]
    reentry_specs = [
        AgentSpec(
            slot="DeepSeek V4 Pro",
            provider="openai-compatible",
            model="deepseek-v4-pro",
            env_api_key="DEEPSEEK_API_KEY",
            endpoint="https://api.deepseek.com/chat/completions",
        )
    ]
    planning_opinions = [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=8.0,
            summary="Planejamento completo com odds, ratings e contexto.",
            source_urls=["https://example.com/odds", "https://example.com/ratings"],
            source_queries=["Brazil World Cup 2026 odds injuries"],
        ),
        AgentOpinion(
            agent="Perplexity Pro",
            title_pct=8.1,
            summary="Planejamento auditável com dados de desempenho.",
            source_urls=["https://example.com/performance"],
        ),
    ]
    response_round_slots: list[list[str]] = []
    reentry_probe_timeouts: list[int] = []

    async def fake_call_agent(spec, prompt, *, baseline_title_pct, timeout, allow_local_fallback):
        if spec.slot == "DeepSeek V4 Pro":
            reentry_probe_timeouts.append(timeout)
            assert "REENTRADA ASSÍNCRONA" in prompt
            return AgentOpinion(
                agent="DeepSeek V4 Pro",
                title_pct=8.3,
                summary="Plano de reentrada com odds, Sofascore e arbitragem.",
                opening_argument="Volto com fonte própria e verificável, sem cache.",
                source_urls=["https://example.com/deepseek-odds"],
                source_queries=["Brazil Morocco World Cup 2026 referee Sofascore odds"],
            )
        return AgentOpinion(
            agent=spec.slot,
            title_pct=8.0,
            summary="Pergunta cobre grupo, 16 avos e título.",
            question=(
                "Com odds, ratings e desempenho recente, há consenso sobre Marrocos, 16 avos "
                "e chance de título do Brasil?"
            ),
            answer="Grupo, 16 avos e título calibrados com fontes auditáveis.",
            source_urls=["https://example.com/odds"],
            agrees_with_protagonist=True,
        )

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, timeout, allow_local_fallback):
        response_round_slots.append([spec.slot for spec in specs])
        return [
            AgentOpinion(
                agent=spec.slot,
                title_pct=8.2,
                summary="Aceito a tese com odds, ratings e performance.",
                answer=(
                    "Concordo: Marrocos, 16 avos e título ficam coerentes com odds, ratings "
                    "e performance recente."
                ),
                source_urls=[f"https://example.com/{spec.slot.lower().replace(' ', '-')}"],
                agrees_with_protagonist=True,
            )
            for spec in specs
        ]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    _consensus, _opinions, transcript, _all_opinions = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=planning_opinions,
            generated_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            agent_specs=active_specs,
            baseline_title_pct=8.0,
            allow_agent_fallback=True,
            watchdog=None,
            reentry_candidate_specs=reentry_specs,
            reentry_removed_reasons={"DeepSeek V4 Pro": "timeout no planejamento de fontes"},
        )
    )

    assert reentry_probe_timeouts == [180]
    assert response_round_slots[0] == ["Perplexity Pro"]
    assert "DeepSeek V4 Pro" in response_round_slots[1]
    assert any(response["agent"] == "DeepSeek V4 Pro" for response in transcript[1]["responses"])


def test_consensus_reached_requires_low_dispersion_after_minimum_rounds() -> None:
    consensus = build_consensus(
        [
            AgentOpinion(agent="Opus 4.8", title_pct=11.0, summary="a"),
            AgentOpinion(agent="GPT 5.5", title_pct=11.4, summary="b"),
            AgentOpinion(agent="Perplexity Pro", title_pct=10.9, summary="c"),
            AgentOpinion(agent="DeepSeek V4 Pro", title_pct=11.2, summary="dp"),
            AgentOpinion(agent="Gemini Pro", title_pct=11.3, summary="f"),
        ]
    )
    accepted_turn = {
        "responses": [
            {"agent": "Opus 4.8", "accepted": True, "used_fallback": False},
            {"agent": "GPT 5.5", "accepted": True, "used_fallback": False},
            {"agent": "Perplexity Pro", "accepted": True, "used_fallback": False},
            {"agent": "DeepSeek V4 Pro", "accepted": True, "used_fallback": False},
            {"agent": "Gemini Pro", "accepted": True, "used_fallback": False},
        ]
    }

    assert consensus_reached(consensus, round_index=1, minimum_rounds=2, threshold_pct=2.0, last_turn=accepted_turn) is False
    assert consensus_reached(consensus, round_index=2, minimum_rounds=2, threshold_pct=2.0, last_turn=accepted_turn) is True


def test_consensus_reached_requires_room_majority_acceptance_current_turn() -> None:
    consensus = build_consensus(
        [
            AgentOpinion(agent="Opus 4.8", title_pct=11.0, summary="a"),
            AgentOpinion(agent="GPT 5.5", title_pct=11.1, summary="b"),
            AgentOpinion(agent="Perplexity Pro", title_pct=11.2, summary="c"),
            AgentOpinion(agent="DeepSeek V4 Pro", title_pct=11.2, summary="dp"),
            AgentOpinion(agent="Gemini Pro", title_pct=11.1, summary="f"),
        ]
    )
    majority_turn = {
        "responses": [
            {"agent": "Opus 4.8", "accepted": True, "used_fallback": False},
            {"agent": "GPT 5.5", "accepted": True, "used_fallback": False},
            {"agent": "Perplexity Pro", "accepted": True, "used_fallback": False},
            {"agent": "DeepSeek V4 Pro", "accepted": False, "used_fallback": False},
            {"agent": "Gemini Pro", "accepted": True, "used_fallback": False},
        ]
    }

    assert (
        consensus_reached(
            consensus,
            round_index=6,
            minimum_rounds=6,
            threshold_pct=2.0,
            last_turn=majority_turn,
        )
        is True
    )

    below_majority_turn = {
        "responses": [
            {"agent": "Opus 4.8", "accepted": True, "used_fallback": False},
            {"agent": "GPT 5.5", "accepted": False, "used_fallback": False},
            {"agent": "Perplexity Pro", "accepted": False, "used_fallback": False},
            {"agent": "DeepSeek V4 Pro", "accepted": False, "used_fallback": False},
            {"agent": "Gemini Pro", "accepted": True, "used_fallback": False},
        ]
    }

    assert (
        consensus_reached(
            consensus,
            round_index=6,
            minimum_rounds=6,
            threshold_pct=2.0,
            last_turn=below_majority_turn,
        )
        is False
    )


def test_consensus_reached_allows_protagonist_real_plus_two_peer_acceptances() -> None:
    consensus = build_consensus(
        [
            AgentOpinion(agent="Perplexity Pro", title_pct=12.0, summary="protagonista"),
            AgentOpinion(agent="DeepSeek V4 Pro", title_pct=12.1, summary="aceita"),
            AgentOpinion(agent="Gemini Pro", title_pct=12.0, summary="aceita"),
            AgentOpinion(agent="Opus 4.8", title_pct=11.0, summary="fallback", used_fallback=True),
            AgentOpinion(agent="GPT 5.5", title_pct=11.0, summary="fallback", used_fallback=True),
        ],
        agent_slots=[
            "Perplexity Pro",
            "DeepSeek V4 Pro",
            "Gemini Pro",
            "Opus 4.8",
            "GPT 5.5",
        ],
    )

    assert (
        consensus_reached(
            consensus,
            round_index=6,
            minimum_rounds=6,
            threshold_pct=2.0,
            minimum_participants=3,
            minimum_peer_acceptances=2,
            last_turn={
                "responses": [
                    {"agent": "DeepSeek V4 Pro", "accepted": True, "used_fallback": False},
                    {"agent": "Gemini Pro", "accepted": True, "used_fallback": False},
                    {"agent": "Opus 4.8", "accepted": False, "used_fallback": True},
                    {"agent": "GPT 5.5", "accepted": False, "used_fallback": True},
                ]
            },
        )
        is True
    )


def test_consensus_reached_uses_room_majority_and_counts_valid_fallback_response() -> None:
    consensus = build_consensus(
        [
            AgentOpinion(agent="Perplexity Pro", title_pct=12.0, summary="protagonista"),
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=12.1,
                summary="aceita via bridge fallback com fonte",
                used_fallback=True,
                source_urls=["https://example.com/market"],
            ),
            AgentOpinion(agent="DeepSeek V4 Pro", title_pct=12.0, summary="aceita"),
            AgentOpinion(agent="Gemini Pro", title_pct=11.9, summary="discorda"),
        ],
        agent_slots=["Perplexity Pro", "GPT 5.5", "DeepSeek V4 Pro", "Gemini Pro"],
    )

    assert (
        consensus_reached(
            consensus,
            round_index=6,
            minimum_rounds=6,
            threshold_pct=2.0,
            minimum_participants=3,
            minimum_peer_acceptances=2,
            last_turn={
                "responses": [
                    {"agent": "GPT 5.5", "accepted": True, "used_fallback": True, "source_count": 1},
                    {"agent": "DeepSeek V4 Pro", "accepted": True, "used_fallback": False},
                    {"agent": "Gemini Pro", "accepted": False, "used_fallback": False},
                ]
            },
        )
        is True
    )


def test_consensus_reached_does_not_count_removed_fallback_even_with_preserved_sources() -> None:
    consensus = build_consensus(
        [
            AgentOpinion(agent="Perplexity Pro", title_pct=8.0, summary="protagonista real"),
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.1,
                summary="Resposta removida do Modelo Principal por citar adversário impossível.",
                answer="Resposta removida do Modelo Principal por citar adversário impossível.",
                used_fallback=True,
                source_urls=["https://example.com/audit-source"],
            ),
            AgentOpinion(agent="DeepSeek V4 Pro", title_pct=8.0, summary="aceita"),
        ],
        agent_slots=["Perplexity Pro", "GPT 5.5", "DeepSeek V4 Pro"],
    )

    assert (
        consensus_reached(
            consensus,
            round_index=6,
            minimum_rounds=6,
            threshold_pct=2.0,
            minimum_participants=3,
            minimum_peer_acceptances=2,
            last_turn={
                "responses": [
                    {
                        "agent": "GPT 5.5",
                        "accepted": True,
                        "used_fallback": True,
                        "source_count": 1,
                        "answer": "Resposta removida do Modelo Principal por citar adversário impossível.",
                    },
                    {"agent": "DeepSeek V4 Pro", "accepted": True, "used_fallback": False},
                ]
            },
        )
        is False
    )


def test_consensus_reached_does_not_count_structured_removed_response_with_sources() -> None:
    consensus = build_consensus(
        [
            AgentOpinion(agent="Perplexity Pro", title_pct=8.0, summary="protagonista real"),
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=30.0,
                summary="texto limpo com fonte",
                answer="Aceito e tenho fonte.",
                source_urls=["https://example.com/audit-source"],
                removed_from_main=True,
                removal_reason="resposta invalidada",
            ),
            AgentOpinion(agent="DeepSeek V4 Pro", title_pct=8.0, summary="aceita"),
        ],
        agent_slots=["Perplexity Pro", "GPT 5.5", "DeepSeek V4 Pro"],
    )

    assert (
        consensus_reached(
            consensus,
            round_index=6,
            minimum_rounds=6,
            threshold_pct=2.0,
            minimum_participants=3,
            minimum_peer_acceptances=2,
            last_turn={
                "responses": [
                    {
                        "agent": "GPT 5.5",
                        "accepted": True,
                        "used_fallback": False,
                        "removed_from_main": True,
                        "source_count": 1,
                        "answer": "Aceito e tenho fonte.",
                    },
                    {"agent": "DeepSeek V4 Pro", "accepted": True, "used_fallback": False},
                ]
            },
        )
        is False
    )


def test_consensus_reached_does_not_count_search_unavailable_fallback_with_sources() -> None:
    consensus = build_consensus(
        [
            AgentOpinion(agent="Perplexity Pro", title_pct=8.0, summary="protagonista real"),
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.1,
                summary=(
                    "Nesta chamada não há ferramenta de busca externa/fetch disponível para eu confirmar "
                    "páginas em tempo real; mantive prior provisório."
                ),
                answer="Concordo com a tese, mas não consegui executar busca externa nesta chamada.",
                used_fallback=True,
                source_urls=["https://example.com/audit-source"],
            ),
            AgentOpinion(agent="DeepSeek V4 Pro", title_pct=8.0, summary="aceita"),
        ],
        agent_slots=["Perplexity Pro", "GPT 5.5", "DeepSeek V4 Pro"],
    )

    assert (
        consensus_reached(
            consensus,
            round_index=6,
            minimum_rounds=6,
            threshold_pct=2.0,
            minimum_participants=3,
            minimum_peer_acceptances=2,
            last_turn={
                "responses": [
                    {
                        "agent": "GPT 5.5",
                        "accepted": True,
                        "used_fallback": True,
                        "source_count": 1,
                        "answer": (
                            "Nesta chamada não há ferramenta de busca externa/fetch disponível para eu "
                            "confirmar páginas em tempo real."
                        ),
                    },
                    {"agent": "DeepSeek V4 Pro", "accepted": True, "used_fallback": False},
                ]
            },
        )
        is False
    )


def test_consensus_reached_requires_minimum_room_participation() -> None:
    consensus = build_consensus(
        [
            AgentOpinion(agent="Opus 4.8", title_pct=8.0, summary="fallback", used_fallback=True),
            AgentOpinion(agent="GPT 5.5", title_pct=8.0, summary="fallback", used_fallback=True),
            AgentOpinion(agent="Perplexity Pro", title_pct=7.8, summary="real"),
            AgentOpinion(agent="DeepSeek V4 Pro", title_pct=8.1, summary="fallback", used_fallback=True),
            AgentOpinion(agent="Gemini Pro", title_pct=8.2, summary="fallback", used_fallback=True),
        ]
    )

    assert (
        consensus_reached(
            consensus,
            round_index=8,
            minimum_rounds=6,
            threshold_pct=2.0,
            minimum_participants=3,
            last_turn={
                "responses": [
                    {"agent": "Opus 4.8", "accepted": False, "used_fallback": True},
                    {"agent": "GPT 5.5", "accepted": False, "used_fallback": True},
                    {"agent": "Perplexity Pro", "accepted": True, "used_fallback": False},
                    {"agent": "DeepSeek V4 Pro", "accepted": False, "used_fallback": True},
                    {"agent": "Gemini Pro", "accepted": False, "used_fallback": True},
                ]
            },
        )
        is False
    )


def test_consensus_reached_requires_more_than_a_short_three_round_exchange() -> None:
    consensus = build_consensus(
        [
            AgentOpinion(agent="Opus 4.8", title_pct=7.0, summary="a"),
            AgentOpinion(agent="GPT 5.5", title_pct=7.4, summary="b"),
            AgentOpinion(agent="Perplexity Pro", title_pct=7.2, summary="c"),
            AgentOpinion(agent="DeepSeek V4 Pro", title_pct=7.3, summary="dp"),
            AgentOpinion(agent="Gemini Pro", title_pct=7.2, summary="f"),
        ]
    )
    accepted_turn = {
        "responses": [
            {"agent": "Opus 4.8", "accepted": True, "used_fallback": False},
            {"agent": "GPT 5.5", "accepted": True, "used_fallback": False},
            {"agent": "Perplexity Pro", "accepted": True, "used_fallback": False},
            {"agent": "DeepSeek V4 Pro", "accepted": True, "used_fallback": False},
            {"agent": "Gemini Pro", "accepted": True, "used_fallback": False},
        ]
    }

    assert consensus_reached(consensus, round_index=3, minimum_rounds=6, threshold_pct=2.0, last_turn=accepted_turn) is False
    assert consensus_reached(consensus, round_index=6, minimum_rounds=6, threshold_pct=2.0, last_turn=accepted_turn) is True

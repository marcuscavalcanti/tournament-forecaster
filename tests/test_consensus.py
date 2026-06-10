import json

from worldcup_brazil.consensus import AgentOpinion, build_consensus
from worldcup_brazil.agents import parse_agent_opinion


def test_build_consensus_requires_all_agent_slots_to_be_represented() -> None:
    opinions = [
        AgentOpinion(agent="Opus 4.8", title_pct=11.0, summary="forte, mas não dominante", critique="cuidado com lesões"),
        AgentOpinion(agent="GPT 5.5", title_pct=12.5, summary="mercado precifica bem", critique="odds ainda têm vig"),
        AgentOpinion(agent="Perplexity Pro", title_pct=10.0, summary="fontes públicas convergem", critique="notícias são ruidosas"),
        AgentOpinion(agent="DeepSeek V4 Pro", title_pct=11.0, summary="modelo pro repondera ratings", critique="testar liquidez"),
        AgentOpinion(agent="Gemini Pro", title_pct=8.5, summary="risco de chave mais alto", critique="não confundir volume de fonte com calibração"),
    ]

    consensus = build_consensus(opinions)

    assert consensus.title_pct == 10.6
    assert "Opus 4.8" in consensus.agent_summaries
    assert "Gemini Pro" in consensus.agent_summaries
    assert any("Rodada 1" in line for line in consensus.debate_transcript)
    assert any("Rodada 2" in line for line in consensus.debate_transcript)
    assert any("Consenso" in line for line in consensus.debate_transcript)


def test_build_consensus_fails_when_an_agent_is_missing() -> None:
    opinions = [
        AgentOpinion(agent="Opus 4.8", title_pct=11.0, summary="ok"),
        AgentOpinion(agent="GPT 5.5", title_pct=12.5, summary="ok"),
    ]

    try:
        build_consensus(opinions)
    except ValueError as exc:
        assert "5 agent opinions" in str(exc)
        assert "Gemini Pro" in str(exc)
        assert "DeepSeek V4 Pro" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_consensus_accepts_dynamic_active_slots_subset() -> None:
    opinions = [
        AgentOpinion(agent="Opus 4.8", title_pct=11.0, summary="a"),
        AgentOpinion(agent="GPT 5.5", title_pct=12.0, summary="b"),
        AgentOpinion(agent="Perplexity Pro", title_pct=10.0, summary="c"),
        AgentOpinion(agent="DeepSeek V4 Pro", title_pct=11.0, summary="e"),
        AgentOpinion(agent="Gemini Pro", title_pct=8.0, summary="f"),
    ]

    consensus = build_consensus(
        opinions,
        agent_slots=(
            "Opus 4.8",
            "GPT 5.5",
            "Perplexity Pro",
            "DeepSeek V4 Pro",
            "Gemini Pro",
        ),
    )

    assert consensus.agent_slots[-1] == "Gemini Pro"


def test_build_consensus_does_not_let_removed_fallback_slots_vote() -> None:
    opinions = [
        AgentOpinion(
            agent="Opus 4.8",
            title_pct=11.0,
            summary="Resposta removida do Modelo Principal por falha operacional.",
            answer="Resposta removida do Modelo Principal por falha operacional.",
            used_fallback=True,
        ),
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=11.0,
            summary="Resposta removida do Modelo Principal por usar alocação proibida.",
            answer="Resposta removida do Modelo Principal por usar alocação proibida.",
            used_fallback=True,
        ),
        AgentOpinion(
            agent="Perplexity Pro",
            title_pct=11.0,
            summary="Resposta removida do Modelo Principal por adversário impossível.",
            answer="Resposta removida do Modelo Principal por adversário impossível.",
            used_fallback=True,
        ),
        AgentOpinion(agent="DeepSeek V4 Pro", title_pct=7.4, summary="real"),
        AgentOpinion(agent="Gemini Pro", title_pct=7.4, summary="real"),
    ]

    consensus = build_consensus(opinions)

    assert consensus.title_pct == 7.4
    assert consensus.dispersion_pct == 0.0


def test_build_consensus_uses_structured_removed_flag_not_only_text_markers() -> None:
    opinions = [
        AgentOpinion(
            agent="Opus 4.8",
            title_pct=30.0,
            summary="texto aparentemente válido com fonte",
            answer="Concordo com 30% baseado em fonte.",
            source_urls=["https://example.com/source"],
            removed_from_main=True,
            removal_reason="adversário impossível",
        ),
        AgentOpinion(agent="GPT 5.5", title_pct=8.0, summary="real"),
        AgentOpinion(agent="Perplexity Pro", title_pct=8.0, summary="real"),
        AgentOpinion(agent="DeepSeek V4 Pro", title_pct=8.0, summary="real"),
        AgentOpinion(agent="Gemini Pro", title_pct=8.0, summary="real"),
    ]

    consensus = build_consensus(opinions)

    assert consensus.title_pct == 8.0
    assert consensus.dispersion_pct == 0.0


def test_parse_agent_opinion_does_not_treat_unlabeled_percent_as_title_probability() -> None:
    opinion = parse_agent_opinion(
        "Perplexity Pro",
        "Brasil tem 52% nas quartas, 35% na semi, mas o título exige cautela.",
        fallback_title_pct=11.0,
    )

    assert opinion.title_pct == 11.0
    assert "52% nas quartas" in opinion.summary


def test_parse_agent_opinion_accepts_explicit_title_probability() -> None:
    opinion = parse_agent_opinion(
        "Perplexity Pro",
        "Probabilidade de título: 8.5%. Quartas: 52%.",
        fallback_title_pct=11.0,
    )

    assert opinion.title_pct == 8.5


def test_parse_agent_opinion_reads_explicit_protagonist_agreement() -> None:
    opinion = parse_agent_opinion(
        "Perplexity Pro",
        '{"title_pct": 8.5, "summary": "discordo do racional", "agrees_with_protagonist": false}',
        fallback_title_pct=11.0,
    )

    assert opinion.agrees_with_protagonist is False


def test_parse_agent_opinion_reads_self_declared_identity_from_model_response() -> None:
    opinion = parse_agent_opinion(
        "GPT 5.5",
        (
            '{"title_pct": 8.5, "summary": "calibrado", '
            '"self_identification": {"name": "ChatGPT", "version": "GPT-5.5 Thinking"}}'
        ),
        fallback_title_pct=11.0,
    )

    assert opinion.self_declared_name == "ChatGPT"
    assert opinion.self_declared_version == "GPT-5.5 Thinking"


def test_parse_agent_opinion_extracts_loose_source_urls_and_queries() -> None:
    opinion = parse_agent_opinion(
        "Perplexity Pro",
        """
        Nesta rodada de reparo, defino fontes não-Opta e consultas auditáveis.

        source_urls:
        - https://www.eloratings.net/
        - https://www.sofascore.com/team/football/brazil/420

        source_queries:
        - Brazil Morocco Haiti Scotland World Cup 2026 odds Elo Sofascore
        - Transfermarkt Brazil Morocco squad value June 2026
        """,
        fallback_title_pct=11.0,
    )

    assert opinion.source_urls == [
        "https://www.eloratings.net/",
        "https://www.sofascore.com/team/football/brazil/420",
    ]
    assert opinion.source_queries == [
        "Brazil Morocco Haiti Scotland World Cup 2026 odds Elo Sofascore",
        "Transfermarkt Brazil Morocco squad value June 2026",
    ]


def test_parse_agent_opinion_reads_merit_based_leadership_bid_without_forcing_disagreement() -> None:
    opinion = parse_agent_opinion(
        "GPT 5.5",
        (
            '{"title_pct": 8.5, "summary": "aceito, mas proponho aprofundar cartões", '
            '"agrees_with_protagonist": true, "leadership_bid": true, '
            '"proposed_next_question": "Cartões mudam o risco nas quartas?", '
            '"leadership_rationale": "Tenho fonte disciplinar nova e pergunta auditável."}'
        ),
        fallback_title_pct=11.0,
    )

    assert opinion.agrees_with_protagonist is True
    assert opinion.leadership_bid is True
    assert opinion.proposed_next_question == "Cartões mudam o risco nas quartas?"
    assert "fonte disciplinar" in opinion.leadership_rationale


def test_parse_agent_opinion_ignores_non_numeric_json_title_pct() -> None:
    opinion = parse_agent_opinion(
        "Perplexity Pro",
        '{"title_pct": "Fontes para estimar o Brasil", "summary": "buscaria odds e Elo"}',
        fallback_title_pct=11.0,
    )

    assert opinion.title_pct == 11.0
    assert opinion.summary == "buscaria odds e Elo"


def test_parse_agent_opinion_reuses_title_pct_object_as_match_probabilities() -> None:
    opinion = parse_agent_opinion(
        "Perplexity Pro",
        """
        {
          "title_pct": {
            "Grupo: Marrocos": 59,
            "Oitavas: Uruguai": "56%"
          },
          "summary": "vetor jogo a jogo sem título numérico",
          "answer": "mantive o título no prior e ajustei jogos"
        }
        """,
        fallback_title_pct=11.0,
    )

    assert opinion.title_pct == 11.0
    assert opinion.match_probabilities == {"Grupo: Marrocos": 59.0, "Oitavas: Uruguai": 56.0}


def test_parse_agent_opinion_reads_scenario_probabilities_for_candidate_opponents() -> None:
    payload = {
        "self_identification": {"name": "Modelo Teste", "version": "1"},
        "title_pct": 10.0,
        "summary": "Cenários calibrados.",
        "scenario_probabilities": {
            "16 avos: Holanda": 31.0,
            "16 avos: Japão": "24%",
        },
    }

    opinion = parse_agent_opinion("GPT 5.5", json.dumps(payload), fallback_title_pct=9.0)

    assert opinion.scenario_probabilities == {"16 avos: Holanda": 31.0, "16 avos: Japão": 24.0}


def test_parse_agent_opinion_reads_consensus_check_question() -> None:
    payload = {
        "title_pct": 10.0,
        "summary": "Cenário calibrado.",
        "answer": "Minha leitura fecha Brasil 58% contra Japão.",
        "consensus_check_question": (
            "Os demais concordam integralmente com esta leitura e aceitam sair com consenso?"
        ),
    }

    opinion = parse_agent_opinion("GPT 5.5", json.dumps(payload), fallback_title_pct=9.0)

    assert (
        opinion.consensus_check_question
        == "Os demais concordam integralmente com esta leitura e aceitam sair com consenso?"
    )


def test_parse_agent_opinion_reads_team_context_signals_for_symmetric_opponent_modeling() -> None:
    payload = {
        "self_identification": {"name": "Modelo Teste", "version": "1"},
        "title_pct": 10.0,
        "summary": "Contexto simétrico por seleção.",
        "team_context_signals": [
            {
                "team": "Suécia",
                "category": "bets_prediction_markets",
                "rating_delta": 95,
                "confidence": 0.72,
                "source_url": "https://example.com/sweden-odds",
                "rationale": "Mercado encurtou Suécia para avançar em F.",
            },
            {
                "team": "Japão",
                "category": "injuries_cuts_news",
                "probability_delta_pct": -2.5,
                "confidence": 0.8,
                "source_query": "Japan World Cup 2026 injuries cuts recent news",
                "rationale": "Lesão no setor defensivo reduz força efetiva.",
            },
        ],
    }

    opinion = parse_agent_opinion("GPT 5.5", json.dumps(payload), fallback_title_pct=9.0)

    assert opinion.team_context_signals == payload["team_context_signals"]


def test_parse_agent_opinion_sanitizes_partial_json_instead_of_leaking_it() -> None:
    opinion = parse_agent_opinion(
        "Perplexity Pro",
        '{ "title_pct": { "Grupo: Marrocos": 60, "Grupo: Haiti": 93, "Quartas: Adversário": 51',
        fallback_title_pct=11.0,
    )

    assert opinion.title_pct == 11.0
    assert opinion.match_probabilities["Grupo: Marrocos"] == 60.0
    assert opinion.match_probabilities["Grupo: Haiti"] == 93.0
    assert opinion.match_probabilities["Quartas: Adversário"] == 51.0
    assert not opinion.answer.strip().startswith("{")
    assert "JSON parcial" in opinion.answer


def test_parse_agent_opinion_sanitizes_fenced_partial_json_and_scratch_text() -> None:
    opinion = parse_agent_opinion(
        "Gemini Pro",
        '```json { "title_pct": 14.5, "summary": "comecei a responder"\n``` Wait, let me fix the keys.',
        fallback_title_pct=11.0,
    )

    assert opinion.title_pct == 11.0
    assert "JSON parcial" in opinion.answer
    assert "```json" not in opinion.answer
    assert "Wait" not in opinion.answer

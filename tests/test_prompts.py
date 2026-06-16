from datetime import datetime, timezone
from pathlib import Path

from worldcup_brazil.pipeline import (
    _agent_debate_prompt,
    _agent_prompt,
    _configured_matches_for_prompt,
    _has_fixed_quanti_quali_allocation,
    _invalid_protagonist_question_reason,
    _opponent_debriefing_config,
    load_config,
    _meeting_response_prompt,
    _protagonist_question_prompt,
    _sanitize_main_meeting_opinions,
    _sanitize_protagonist_question,
    _source_planning_prompt,
)
from worldcup_brazil.consensus import AgentOpinion
from worldcup_brazil.monte_carlo import run_brazil_monte_carlo


def test_agent_prompt_tells_models_to_choose_sources_and_write_for_linkedin() -> None:
    prompt = _agent_prompt(
        config={},
        evidence=[],
        generated_at=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
    )

    assert "escolha as fontes" in prompt.lower()
    assert "source_urls" in prompt
    assert "self_identification" in prompt
    assert "name" in prompt
    assert "version" in prompt
    assert "linkedin" in prompt.lower()
    assert "entendível" in prompt.lower()
    assert "nível de raciocínio configurado" in prompt.lower()
    assert "responda rápido" in prompt.lower()
    assert "sofascore" in prompt.lower()
    assert "arbitragem" in prompt.lower()
    assert "mediador não faz busca externa" in prompt.lower()
    assert "busca atualizada" in prompt.lower()
    assert "nunca use cache" in prompt.lower()
    assert "dados quantitativos e qualitativos" in prompt.lower()
    assert "decida quanto peso" not in prompt.lower()
    assert "alocação quanti/quali" not in prompt.lower()
    assert "hipótese auditável" in prompt.lower()
    assert "aceitar ou contestar" in prompt.lower()
    assert "70/30" not in prompt
    assert "70%" not in prompt
    assert "30%" not in prompt
    assert "dados da opta não contam" in prompt.lower()


def test_meeting_prompts_force_question_answer_and_source_diversity() -> None:
    question_prompt = _protagonist_question_prompt(
        config={},
        protagonist="Perplexity Pro",
        previous_turn=None,
        generated_at=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
    )
    source_prompt = _source_planning_prompt(
        config={},
        generated_at=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
    )
    response_prompt = _meeting_response_prompt(
        config={},
        round_index=1,
        protagonist="Perplexity Pro",
        question="O mercado está pesando demais?",
        previous_turn=None,
        generated_at=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
    )

    assert "faça a próxima pergunta" in question_prompt.lower()
    assert "protagonista" in question_prompt.lower()
    assert "não trate a sala como uma lista fixa de falas" in question_prompt.lower()
    assert "concordam ou discordam" in question_prompt.lower()
    assert "reunião de debriefing" in response_prompt.lower()
    assert "pode falar várias vezes" in response_prompt.lower()
    assert "maioria simples dos participantes ativos" in response_prompt.lower()
    assert "agrees_with_protagonist" in response_prompt
    assert "consensus_check_question" in response_prompt
    assert "self_identification" in response_prompt
    assert "leadership_bid" in response_prompt
    assert "proposed_next_question" in response_prompt
    assert "não invente discordância" in response_prompt.lower()
    assert "não fique passivo" in response_prompt.lower()
    assert "mérito" in response_prompt.lower()
    assert "false se discorda" in response_prompt.lower()
    assert "responda à pergunta" in response_prompt.lower()
    assert "nível de raciocínio configurado" in response_prompt.lower()
    assert "resposta rápida" in response_prompt.lower()
    assert "modelo principal" in response_prompt.lower()
    assert "sofascore" in response_prompt.lower()
    assert "arbitragem" in response_prompt.lower()
    assert "sem source_urls auditáveis" in response_prompt.lower()
    assert "não invente fonte" in response_prompt.lower()
    assert "não invente adversário" in question_prompt.lower()
    assert "escopo obrigatório dos jogos/cenários" in response_prompt.lower()
    assert "concordam integralmente" in response_prompt.lower()
    assert "sair com consenso" in response_prompt.lower()
    assert "avançar para as próximas etapas" in response_prompt.lower()
    assert "nunca responda com metacomentário operacional" in response_prompt.lower()
    assert "fase faltante" in response_prompt.lower()
    assert "dados da opta não contam" in source_prompt.lower()
    assert "dados da opta não contam" in question_prompt.lower()
    assert "dados da opta não contam" in response_prompt.lower()
    assert "opta_comparison" not in response_prompt


def test_source_planning_prompt_is_compact_enough_for_cli_bridges() -> None:
    prompt = _source_planning_prompt(
        config={
            "group_matches": [
                {"opponent": "Marrocos", "date": "13/jun", "venue": "Nova Jersey"},
                {"opponent": "Haiti", "date": "19/jun", "venue": "Filadélfia"},
                {"opponent": "Escócia", "date": "24/jun", "venue": "Miami"},
            ],
            "knockout_matches": [
                {"phase": "16 avos", "opponent": "Uruguai", "scenario_pct": 46.0, "most_likely": True},
                {"phase": "Oitavas", "opponent": "Espanha", "scenario_pct": 39.0, "most_likely": True},
                {"phase": "Quartas", "opponent": "França", "scenario_pct": 31.0, "most_likely": True},
                {"phase": "Semifinal", "opponent": "Argentina", "scenario_pct": 22.0, "most_likely": True},
                {"phase": "Final", "opponent": "Inglaterra", "scenario_pct": 15.0, "most_likely": True},
            ],
            "recent_event_impacts": [
                {
                    "id": "brasil-egito",
                    "date": "2026-06-06",
                    "team": "Brasil",
                    "category": "statistical",
                    "summary": "Brasil 2x1 Egito",
                    "source_query": "Brasil Egito 2x1 amistoso",
                }
            ],
        },
        generated_at=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
    )

    assert len(prompt) <= 9000
    assert "recent_event_impacts" in prompt
    assert "event_impact_scenarios" in prompt
    assert "16 avos" in prompt
    assert "Final" in prompt


def test_source_planning_prompt_for_example_config_stays_under_bridge_budget() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    prompt = _source_planning_prompt(
        config=config,
        generated_at=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
    )

    assert len(prompt) <= 9000
    assert "16 avos" in prompt
    assert "Final" in prompt


def test_meeting_prompt_exposes_official_bracket_constraints_and_scenario_probabilities() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    source_prompt = _source_planning_prompt(
        config=config,
        generated_at=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
    )
    prompt = _meeting_response_prompt(
        config=config,
        round_index=1,
        protagonist="GPT 5.5",
        question="Concordam com os candidatos do 16 avos?",
        previous_turn=None,
        generated_at=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
    )

    assert "bracket_path" in source_prompt
    assert "2F" in source_prompt
    assert "Holanda" in source_prompt
    assert "scenario_probabilities" in prompt
    assert "bracket_match_id" in prompt
    assert "bracket_brazil_slot" in prompt
    assert "1C" in prompt
    assert "2F" in prompt
    assert "Holanda" in prompt
    assert "Japão" in prompt
    assert "Suécia" in prompt
    assert "Tunísia" in prompt
    round_of_32_scope = prompt[prompt.index('"phase": "16 avos"') : prompt.index('"phase": "Oitavas"')]
    assert "Canadá" not in round_of_32_scope
    assert "Suíça" not in round_of_32_scope


def test_model_contract_is_identical_and_forces_agent_owned_fresh_search() -> None:
    generated_at = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)
    prompts = [
        _source_planning_prompt(config={}, generated_at=generated_at),
        _agent_prompt(config={}, evidence=[], generated_at=generated_at),
        _agent_debate_prompt(
            config={},
            evidence=[],
            generated_at=generated_at,
            opening_opinions=[AgentOpinion(agent="GPT 5.5", title_pct=8.0, summary="base")],
        ),
        _protagonist_question_prompt(
            config={},
            protagonist="Perplexity Pro",
            previous_turn=None,
            generated_at=generated_at,
        ),
        _meeting_response_prompt(
            config={},
            round_index=1,
            protagonist="Perplexity Pro",
            question="Qual premissa deve mudar?",
            previous_turn=None,
            generated_at=generated_at,
        ),
    ]

    for prompt in prompts:
        lowered = prompt.lower()
        assert "contrato único" in lowered
        assert "todos os modelos recebem as mesmas regras" in lowered
        assert "mediador não faz busca externa" in lowered
        assert "cada modelo decide suas próprias fontes" in lowered
        assert "busca atualizada" in lowered
        assert "nunca use cache" in lowered
        assert "source_urls" in prompt
        assert "source_queries" in prompt
        assert "self_identification" in prompt


def test_model_contract_explicitly_bans_opta_without_excluding_models_for_acknowledging_it() -> None:
    generated_at = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)
    prompts = [
        _source_planning_prompt(config={}, generated_at=generated_at),
        _agent_prompt(config={}, evidence=[], generated_at=generated_at),
        _agent_debate_prompt(
            config={},
            evidence=[],
            generated_at=generated_at,
            opening_opinions=[AgentOpinion(agent="GPT 5.5", title_pct=8.0, summary="base")],
        ),
        _protagonist_question_prompt(
            config={},
            protagonist="Perplexity Pro",
            previous_turn=None,
            generated_at=generated_at,
        ),
        _meeting_response_prompt(
            config={},
            round_index=1,
            protagonist="Perplexity Pro",
            question="Qual premissa deve mudar?",
            previous_turn=None,
            generated_at=generated_at,
        ),
    ]

    for prompt in prompts:
        lowered = prompt.lower()
        assert "dados da opta não contam" in lowered
        assert "antes da busca" in lowered
        assert "não inclua opta em source_urls/source_queries" in lowered


def test_all_model_prompts_include_quantitative_and_qualitative_decision_contract() -> None:
    generated_at = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)
    prompts = [
        _source_planning_prompt(config={}, generated_at=generated_at),
        _agent_prompt(config={}, evidence=[], generated_at=generated_at),
        _agent_debate_prompt(
            config={},
            evidence=[],
            generated_at=generated_at,
            opening_opinions=[AgentOpinion(agent="GPT 5.5", title_pct=8.0, summary="base")],
        ),
        _protagonist_question_prompt(
            config={},
            protagonist="Perplexity Pro",
            previous_turn=None,
            generated_at=generated_at,
        ),
        _meeting_response_prompt(
            config={},
            round_index=1,
            protagonist="Perplexity Pro",
            question="Qual premissa deve mudar?",
            previous_turn=None,
            generated_at=generated_at,
        ),
    ]

    for prompt in prompts:
        lowered = prompt.lower()
        assert "números importam" in lowered
        assert "análise quantitativa" in lowered
        assert "análise qualitativa" in lowered
        assert "fatos" in lowered
        assert "especialistas" in lowered
        assert "futebol" in lowered


def test_main_model_prompts_include_transfermarkt_market_value_contract() -> None:
    generated_at = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)
    prompts = [
        _source_planning_prompt(config={}, generated_at=generated_at),
        _agent_prompt(config={}, evidence=[], generated_at=generated_at),
        _agent_debate_prompt(
            config={},
            evidence=[],
            generated_at=generated_at,
            opening_opinions=[AgentOpinion(agent="GPT 5.5", title_pct=8.0, summary="base")],
        ),
        _protagonist_question_prompt(
            config={},
            protagonist="Perplexity Pro",
            previous_turn=None,
            generated_at=generated_at,
        ),
        _meeting_response_prompt(
            config={},
            round_index=1,
            protagonist="Perplexity Pro",
            question="Qual premissa deve mudar?",
            previous_turn=None,
            generated_at=generated_at,
        ),
    ]

    for prompt in prompts:
        lowered = prompt.lower()
        assert "transfermarkt" in lowered
        assert "valor de mercado" in lowered
        assert "delta nominal" in lowered
        assert "percentual isolado" in lowered
        assert "50m->55m" in lowered


def test_main_model_prompts_force_fresh_symmetric_opponent_research() -> None:
    generated_at = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)
    config = {
        "group_matches": [
            {"opponent": "Marrocos"},
            {"opponent": "Haiti"},
            {"opponent": "Escócia"},
        ],
        "knockout_matches": [
            {"phase": "Oitavas", "opponent": "Uruguai", "most_likely": True},
        ],
    }
    prompts = [
        _source_planning_prompt(config=config, generated_at=generated_at),
        _agent_prompt(config=config, evidence=[], generated_at=generated_at),
        _agent_debate_prompt(
            config=config,
            evidence=[],
            generated_at=generated_at,
            opening_opinions=[AgentOpinion(agent="GPT 5.5", title_pct=8.0, summary="base")],
        ),
        _protagonist_question_prompt(
            config=config,
            protagonist="Perplexity Pro",
            previous_turn=None,
            generated_at=generated_at,
        ),
        _meeting_response_prompt(
            config=config,
            round_index=1,
            protagonist="Perplexity Pro",
            question="Qual premissa deve mudar?",
            previous_turn=None,
            generated_at=generated_at,
        ),
    ]

    for prompt in prompts:
        lowered = prompt.lower()
        assert "pesquisa simétrica" in lowered
        assert "brasil e adversários" in lowered
        assert "mesmas famílias de fontes" in lowered
        assert "source_urls" in lowered
        assert "source_queries" in lowered
        assert "team_context_signals" in prompt
        assert "bets/prediction markets" in lowered
        assert "lesões/cortes" in lowered
        assert "amistosos" in lowered
        assert "imprensa especializada" in lowered
        assert "marrocos" in lowered
        assert "haiti" in lowered
        assert "escócia" in lowered
        assert "uruguai" in lowered


def test_sanitize_protagonist_question_rejects_unconfigured_group_opponent() -> None:
    config = {
        "group_matches": [
            {"opponent": "Marrocos", "venue": "Nova Jersey"},
            {"opponent": "Haiti", "venue": "Filadélfia"},
            {"opponent": "Escócia", "venue": "Miami"},
        ]
    }

    question = _sanitize_protagonist_question(
        "Com base na Betfair, qual a chance do Brasil vencer a Sérvia na fase de grupos?",
        config=config,
        protagonist="DeepSeek V4 Pro",
    )

    assert "Sérvia" not in question
    assert "fala do protagonista foi invalidada" in question
    assert "ignorem essa fala" in question
    assert "Marrocos, Haiti, Escócia" in question
    assert "concorda ou discorda" in question


def test_sanitize_protagonist_question_rejects_impossible_knockout_opponent_for_phase() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))

    question = _sanitize_protagonist_question(
        "Minha tese: Brasil x Canadá nos 16 avos é o cenário mais provável; concordam?",
        config=config,
        protagonist="GPT 5.5",
    )

    assert "Canadá" not in question
    assert "2F" in question
    assert "Holanda" in question
    assert "Japão" in question
    assert "concorda ou discorda" in question


def test_protagonist_question_rejects_future_match_claimed_as_completed_context() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    config["completed_group_matches"] = [
        {"group": "C", "team_a": "Brasil", "team_b": "Marrocos", "score_a": 1, "score_b": 1},
        {"group": "C", "team_a": "Escócia", "team_b": "Haiti", "score_a": 1, "score_b": 0},
    ]

    reason = _invalid_protagonist_question_reason(
        "Depois dos jogos França x Senegal e Inglaterra x Croácia, concordam com reduzir o Brasil?",
        config,
    )

    assert reason is not None
    assert "sem placar no ledger" in reason


def test_protagonist_question_accepts_completed_match_context_from_ledger() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    config["completed_group_matches"] = [
        {"group": "C", "team_a": "Brasil", "team_b": "Marrocos", "score_a": 1, "score_b": 1},
        {"group": "C", "team_a": "Escócia", "team_b": "Haiti", "score_a": 1, "score_b": 0},
    ]

    reason = _invalid_protagonist_question_reason(
        "Depois dos jogos Brasil x Marrocos e Escócia x Haiti, concordam com atualizar o Grupo C?",
        config,
    )

    assert reason is None


def test_main_room_scope_includes_live_tables_for_brazil_crossing_groups() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    config["monte_carlo"]["iterations"] = 3000
    config["completed_group_matches"] = [
        {
            "group": "F",
            "team_a": "Holanda",
            "team_b": "Japão",
            "score_a": 2,
            "score_b": 2,
            "date": "2026-06-14",
        },
    ]
    config["_monte_carlo_result"] = run_brazil_monte_carlo(config)

    scope = _configured_matches_for_prompt(config)

    assert scope["path_phase_relevant_groups"]["16 avos"] == ["F"]
    assert scope["path_relevant_group_states"]["F"]["completed_results"][0]["score"] == "Holanda 2-2 Japão"
    assert scope["monte_carlo"]["relevant_group_states"]["F"]["completed_results"][0]["score"] == "Holanda 2-2 Japão"


def test_sanitize_protagonist_question_removes_reserved_benchmark_from_main_room() -> None:
    question = _sanitize_protagonist_question(
        "Minha leitura usa Opta/Exame como benchmark; vocês concordam ou discordam?",
        config={},
        protagonist="Perplexity Pro",
    )

    assert "opta" not in question.lower()
    assert "benchmark reservado" in question
    assert "concorda ou discorda" in question


def test_sanitize_protagonist_question_removes_fixed_quanti_quali_allocation() -> None:
    question = _sanitize_protagonist_question(
        "Minha tese usa 70% quanti e 30% quali; vocês concordam?",
        config={},
        protagonist="GPT 5.5",
    )

    lowered = question.lower()
    assert "70%" not in lowered
    assert "30%" not in lowered
    assert "alocação fixa" in lowered
    assert "fala do protagonista foi invalidada" in lowered
    assert "concorda ou discorda" in lowered


def test_sanitize_main_meeting_opinions_removes_reserved_benchmark_response() -> None:
    opinions = [
        AgentOpinion(
            agent="Perplexity Pro",
            title_pct=5.8,
            summary="Uso Opta como âncora.",
            answer="A Opta derruba o Brasil para 6%.",
            agrees_with_protagonist=False,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(opinions, baseline_title_pct=9.0)

    assert sanitized[0].used_fallback is True
    assert sanitized[0].title_pct == 9.0
    assert "Opta" not in sanitized[0].answer
    assert "Resposta removida" in sanitized[0].summary


def test_sanitize_main_meeting_opinions_removes_fixed_quanti_quali_allocation() -> None:
    opinions = [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=9.5,
            summary="Uso 70% quantitativo e 30% qualitativo.",
            answer="Com essa régua fixa, aceito a tese.",
            agrees_with_protagonist=True,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(opinions, baseline_title_pct=9.0)

    assert sanitized[0].used_fallback is True
    assert sanitized[0].removed_from_main is True
    assert sanitized[0].removal_reason == "usar alocação fixa quanti/quali proibida"
    assert sanitized[0].title_pct == 9.0
    assert "70%" not in sanitized[0].answer
    assert "30%" not in sanitized[0].answer
    assert "alocação fixa" in sanitized[0].summary


def test_fixed_quanti_quali_detector_accepts_match_probabilities() -> None:
    text = (
        "Uso dados quantitativos e qualitativos. Para Brasil x Marrocos, proponho "
        "55% Brasil, 26% empate e 19% Marrocos com base em odds e notícia de elenco."
    )

    assert _has_fixed_quanti_quali_allocation(text) is False


def test_fixed_quanti_quali_detector_rejects_method_percentages() -> None:
    assert _has_fixed_quanti_quali_allocation("Uso 70% quantitativo e 30% qualitativo.") is True
    assert _has_fixed_quanti_quali_allocation("Mix 60/40 entre dados estatísticos e contexto.") is True


def test_sanitize_main_meeting_opinions_removes_unconfigured_group_opponents() -> None:
    config = {
        "group_matches": [
            {"opponent": "Marrocos", "venue": "Nova Jersey"},
            {"opponent": "Haiti", "venue": "Filadélfia"},
            {"opponent": "Escócia", "venue": "Miami"},
        ]
    }
    opinions = [
        AgentOpinion(
            agent="DeepSeek V4 Pro",
            title_pct=8.2,
            summary="Brasil vence a fase de grupos contra Sérvia, Camarões e Suíça.",
            answer="Grupo antigo usado como base.",
            agrees_with_protagonist=False,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(opinions, baseline_title_pct=9.0, config=config)

    assert sanitized[0].used_fallback is True
    assert sanitized[0].title_pct == 9.0
    assert "Sérvia" not in sanitized[0].answer
    assert "fora do JSON configurado" in sanitized[0].summary


def test_sanitize_main_meeting_opinions_scopes_knockout_opponents_by_phase() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    opinions = [
        AgentOpinion(
            agent="Gemini Pro",
            title_pct=8.4,
            summary=(
                "Oitavas: França e Senegal são candidatos oficiais com 13-15% de cenário. "
                "Quartas: México e Inglaterra aparecem como candidatos prováveis pelo Monte Carlo, "
                "ambos em torno de 18% de cenário."
            ),
            answer=(
                "Nas Oitavas eu testaria França/Senegal; nas Quartas eu testaria México/Inglaterra. "
                "Isso usa chaveamento oficial, odds e ratings para manter o Brasil em 57.5% de alcance "
                "às quartas sem misturar os slots."
            ),
            source_urls=["https://example.com/bracket"],
            agrees_with_protagonist=True,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(
        opinions,
        baseline_title_pct=8.0,
        config={**config, "require_auditable_source_urls_for_meeting_votes": True},
    )

    assert sanitized[0].used_fallback is False


def test_sanitize_main_meeting_opinions_rejects_impossible_opponent_inside_phase_scope() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    opinions = [
        AgentOpinion(
            agent="Gemini Pro",
            title_pct=8.4,
            summary="Oitavas: Japão é o adversário mais provável.",
            answer="Japão nas Oitavas exigiria um cruzamento que não existe em nenhum caminho do Brasil.",
            source_urls=["https://example.com/bracket"],
            agrees_with_protagonist=False,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(
        opinions,
        baseline_title_pct=8.0,
        config={**config, "require_auditable_source_urls_for_meeting_votes": True},
    )

    assert sanitized[0].used_fallback is True
    assert "adversário impossível" in sanitized[0].summary


def test_sanitize_main_meeting_opinions_does_not_treat_configured_universe_as_phase_claim() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    opinions = [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=6.9,
            summary=(
                "Minha posicao usa odds, rating Elo e probabilidade para Brasil e todos os adversarios "
                "explicitamente configurados no JSON (Marrocos, Haiti, Escocia, Japao, Holanda, Equador, "
                "Noruega, Inglaterra, Mexico, Portugal, Argentina, Alemanha e Franca) antes de refinar "
                "16 avos e fases finais."
            ),
            answer=(
                "Isso nao atribui Alemanha ou Franca aos 16 avos; e apenas o universo configurado. "
                "Com mercado, rating e probabilidade, mantenho titulo em 6.9% e peço consenso."
            ),
            source_urls=["https://www.eloratings.net"],
            agrees_with_protagonist=True,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(
        opinions,
        baseline_title_pct=6.9,
        config={**config, "require_auditable_source_urls_for_meeting_votes": True},
    )

    assert sanitized[0].used_fallback is False


def test_sanitize_main_meeting_opinions_rejects_impossible_16_avos_claim() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    opinions = [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=6.9,
            summary="16 avos: Alemanha é o adversário mais provável do Brasil.",
            answer=(
                "Discordo do bracket anterior porque odds, rating e probabilidade colocam Alemanha "
                "contra o Brasil nos 16 avos."
            ),
            source_urls=["https://www.eloratings.net"],
            agrees_with_protagonist=False,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(
        opinions,
        baseline_title_pct=6.9,
        config={**config, "require_auditable_source_urls_for_meeting_votes": True},
    )

    assert sanitized[0].used_fallback is True
    assert "16 avos" in sanitized[0].summary
    assert "alemanha" in sanitized[0].summary


def test_sanitize_main_meeting_opinions_neutralizes_partial_json_title() -> None:
    opinions = [
        AgentOpinion(
            agent="DeepSeek V4 Pro",
            title_pct=57.0,
            summary="Resposta em JSON parcial; aproveitei as probabilidades jogo a jogo.",
            answer="Resposta em JSON parcial; aproveitei as probabilidades jogo a jogo.",
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(opinions, baseline_title_pct=9.0)

    assert sanitized[0].used_fallback is True
    assert sanitized[0].title_pct == 9.0
    assert "sem campos auditáveis" in sanitized[0].summary


def test_sanitize_main_meeting_opinions_does_not_treat_evidence_caveat_as_partial_payload() -> None:
    opinions = [
        AgentOpinion(
            agent="DeepSeek V4 Pro",
            title_pct=5.0,
            summary=(
                "Concordo parcialmente: sem resposta externa utilizável nova para mover além do ajuste "
                "ancorado em odds e Elo."
            ),
            answer=(
                "Com odds +900/+950, Elo Brasil 1991 vs Inglaterra 2024 e Brasil-Inglaterra em 45%, "
                "aceito título em 5.0% e shift adicional 0. Pergunta de consenso: os demais concordam?"
            ),
            source_urls=["https://example.com/odds", "https://example.com/elo"],
            agrees_with_protagonist=True,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(
        opinions,
        baseline_title_pct=3.5,
        config={"require_auditable_source_urls_for_meeting_votes": True},
    )

    assert sanitized[0].used_fallback is False
    assert sanitized[0].removed_from_main is False
    assert sanitized[0].title_pct == 5.0


def test_sanitize_main_meeting_opinions_neutralizes_implausible_title_jump() -> None:
    opinions = [
        AgentOpinion(
            agent="Perplexity Pro",
            title_pct=21.0,
            summary="Aumentar Marrocos de 59% para 65% adiciona só 1-2 p.p. ao título.",
            answer="Mesmo assim, title_pct do Brasil em 21%.",
            source_urls=["https://example.com/markets"],
            agrees_with_protagonist=False,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(
        opinions,
        baseline_title_pct=11.0,
        config={"max_agent_title_shift_pct": 5.0},
    )

    assert sanitized[0].used_fallback is True
    assert sanitized[0].title_pct == 11.0
    assert "inconsistência quantitativa" in sanitized[0].summary


def test_sanitize_main_meeting_opinions_allows_source_backed_title_recalibration_near_market_band() -> None:
    opinions = [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=8.6,
            summary=(
                "Discordo da âncora de 3.5% porque odds de mercado, Elo e chaveamento indicam Brasil "
                "em faixa de elite, com título perto de 8.6%."
            ),
            answer=(
                "A hipótese auditável é recalibrar o título de 3.5% para 8.6%: odds +900/+950, "
                "Elo Brasil 1991 e gargalo Brasil-Inglaterra em 45% sustentam o ajuste. "
                "Pergunta de consenso: concordam integralmente com esta recalibração?"
            ),
            source_urls=["https://example.com/odds", "https://example.com/elo"],
            agrees_with_protagonist=False,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(
        opinions,
        baseline_title_pct=3.5,
        config={"max_agent_title_shift_pct": 5.0, "require_auditable_source_urls_for_meeting_votes": True},
    )

    assert sanitized[0].used_fallback is False
    assert sanitized[0].removed_from_main is False
    assert sanitized[0].title_pct == 8.6


def test_sanitize_main_meeting_opinions_removes_agreement_without_auditable_sources() -> None:
    opinions = [
        AgentOpinion(
            agent="Gemini Pro",
            title_pct=8.5,
            summary="Concordo integralmente.",
            answer="Concordo. Estudos históricos mostram que o descanso adiciona 2 p.p.",
            agrees_with_protagonist=True,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(
        opinions,
        baseline_title_pct=8.0,
        config={"require_auditable_source_urls_for_meeting_votes": True},
    )

    assert sanitized[0].used_fallback is True
    assert sanitized[0].title_pct == 8.0
    assert "sem hipótese auditável" in sanitized[0].summary


def test_sanitize_main_meeting_opinions_inherits_same_run_planning_sources_for_rational_vote() -> None:
    opinions = [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=7.1,
            summary="Concordo parcialmente: odds de mercado e rating Elo sustentam titulo perto de 7.1%.",
            answer=(
                "Aceito a tese central porque odds, rating Elo, probabilidade de chave e descanso "
                "apontam variacao menor que 1 p.p.; a pergunta pode avançar para o consenso."
            ),
            agrees_with_protagonist=True,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(
        opinions,
        baseline_title_pct=6.9,
        config={
            "require_auditable_source_urls_for_meeting_votes": True,
            "_agent_source_context_by_agent": {
                "GPT 5.5": {
                    "source_urls": ["https://www.eloratings.net"],
                    "source_queries": ["Brazil World Cup 2026 odds injuries ratings"],
                }
            },
        },
    )

    assert sanitized[0].used_fallback is False
    assert sanitized[0].source_urls == ["https://www.eloratings.net"]
    assert sanitized[0].source_queries == ["Brazil World Cup 2026 odds injuries ratings"]


def test_sanitize_main_meeting_opinions_does_not_let_inherited_sources_replace_rationale() -> None:
    opinions = [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=7.1,
            summary="Concordo integralmente.",
            answer="Concordo.",
            agrees_with_protagonist=True,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(
        opinions,
        baseline_title_pct=6.9,
        config={
            "require_auditable_source_urls_for_meeting_votes": True,
            "_agent_source_context_by_agent": {
                "GPT 5.5": {
                    "source_urls": ["https://www.eloratings.net"],
                    "source_queries": ["Brazil World Cup 2026 odds injuries ratings"],
                }
            },
        },
    )

    assert sanitized[0].used_fallback is True
    assert "sem hipótese auditável" in sanitized[0].summary


def test_sanitize_main_meeting_opinions_removes_external_fetch_unavailable_response() -> None:
    opinions = [
        AgentOpinion(
            agent="Opus 4.8",
            title_pct=8.1,
            summary=(
                "Rodada de reparo operacional, sem fechar consenso. Nesta chamada não há ferramenta "
                "de busca externa/fetch disponível para eu confirmar páginas em tempo real; por isso "
                "não invento ranking, odd, lesão, escalação, nota ou projeção fresca. Mantive 3.6% "
                "como prior provisório apenas porque está no Monte Carlo do escopo fornecido."
            ),
            answer=(
                "Concordo com 3.6% porque odds, rating, probabilidade e Monte Carlo sustentam cautela; "
                "pergunta de consenso: os demais concordam?"
            ),
            source_urls=["https://example.com/market"],
            source_queries=["Brazil World Cup 2026 odds ratings injuries"],
            agrees_with_protagonist=True,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(
        opinions,
        baseline_title_pct=8.0,
        config={"require_auditable_source_urls_for_meeting_votes": True},
    )

    assert sanitized[0].used_fallback is True
    assert sanitized[0].title_pct == 8.0
    assert "busca/fetch externo indisponível" in sanitized[0].summary
    assert "não conta como consenso" in sanitized[0].summary


def test_sanitize_main_meeting_opinions_keeps_source_backed_hypothesis() -> None:
    opinions = [
        AgentOpinion(
            agent="DeepSeek V4 Pro",
            title_pct=8.5,
            summary="Discordo: odds e Elo sustentam 59% nas oitavas, não 65%.",
            answer=(
                "Discordo do ajuste para 65% porque a diferença Elo de 160 pontos "
                "e a probabilidade de empate em mata-mata deixam a faixa em 57-59%."
            ),
            source_urls=["https://www.eloratings.net"],
            agrees_with_protagonist=False,
        )
    ]

    sanitized = _sanitize_main_meeting_opinions(
        opinions,
        baseline_title_pct=8.0,
        config={"require_auditable_source_urls_for_meeting_votes": True},
    )

    assert sanitized[0].used_fallback is False
    assert sanitized[0].title_pct == 8.5


def test_main_prompts_do_not_cite_fixed_quantitative_qualitative_percentages() -> None:
    generated_at = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)
    prompts = [
        _source_planning_prompt(config={}, generated_at=generated_at),
        _agent_prompt(config={}, evidence=[], generated_at=generated_at),
        _agent_debate_prompt(
            config={},
            evidence=[],
            generated_at=generated_at,
            opening_opinions=[AgentOpinion(agent="GPT 5.5", title_pct=8.0, summary="base")],
        ),
        _protagonist_question_prompt(
            config={},
            protagonist="Perplexity Pro",
            previous_turn=None,
            generated_at=generated_at,
        ),
        _meeting_response_prompt(
            config={},
            round_index=1,
            protagonist="Perplexity Pro",
            question="Qual premissa deve mudar?",
            previous_turn=None,
            generated_at=generated_at,
        ),
    ]

    for prompt in prompts:
        lowered = prompt.lower()
        assert "70/30" not in lowered
        assert "70%" not in lowered
        assert "30%" not in lowered
        assert "decida quanto peso" not in lowered
        assert "hipótese auditável" in lowered
        assert "alocação quanti/quali" not in lowered
        assert "dados quantitativos e qualitativos" in lowered
        assert "use 70% estatística e 30% qualitativo" not in lowered
    assert "title_pct" in prompt


def test_opponent_room_prompt_asks_for_decisive_top_two_and_challengeable_mc_baseline() -> None:
    generated_at = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)
    config = load_config(Path("config/worldcup_brazil.example.json"))
    opponent_config = _opponent_debriefing_config(config)

    opponent_prompt = _protagonist_question_prompt(
        config=opponent_config,
        protagonist="GPT 5.5",
        previous_turn=None,
        generated_at=generated_at,
    )
    main_prompt = _protagonist_question_prompt(
        config=config,
        protagonist="GPT 5.5",
        previous_turn=None,
        generated_at=generated_at,
    )

    assert "top-2 por fase" in opponent_prompt.lower()
    assert "baseline auditável e desafiável" in opponent_prompt.lower()
    assert "premissa forte" not in opponent_prompt.lower()
    assert "top-2 por fase" not in main_prompt.lower()

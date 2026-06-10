from worldcup_brazil.consensus import AgentOpinion, build_consensus
from worldcup_brazil.pipeline import (
    _drop_fallback_only_agent_slots,
    _room_majority_quorum,
    _new_token_cost_ledger,
    _record_token_costs,
    calculate_model_influence,
    calculate_model_participation,
)


def test_calculate_model_influence_normalizes_and_penalizes_fallbacks() -> None:
    opening = [
        AgentOpinion(agent="Opus 4.8", title_pct=11.0, summary="fallback", used_fallback=True),
        AgentOpinion(agent="GPT 5.5", title_pct=12.0, summary="fallback", used_fallback=True),
        AgentOpinion(agent="Perplexity Pro", title_pct=10.0, summary="real", source_urls=["https://example.com/a"]),
        AgentOpinion(agent="DeepSeek V4 Pro", title_pct=10.8, summary="real", source_urls=["https://example.com/d"]),
        AgentOpinion(agent="Gemini Pro", title_pct=8.5, summary="fallback", used_fallback=True),
    ]
    final = [
        AgentOpinion(agent="Opus 4.8", title_pct=11.0, summary="fallback", used_fallback=True),
        AgentOpinion(agent="GPT 5.5", title_pct=12.0, summary="fallback", used_fallback=True),
        AgentOpinion(agent="Perplexity Pro", title_pct=10.5, summary="real", critique="ajustei contra o mercado"),
        AgentOpinion(agent="DeepSeek V4 Pro", title_pct=10.9, summary="real", critique="recalibrei Elo"),
        AgentOpinion(agent="Gemini Pro", title_pct=8.5, summary="fallback", used_fallback=True),
    ]
    consensus = build_consensus(final)

    influence = calculate_model_influence(opening, final, consensus)

    assert round(sum(influence.values()), 1) == 100.0
    assert influence["Perplexity Pro"] > influence["Opus 4.8"]
    assert influence["DeepSeek V4 Pro"] > influence["GPT 5.5"]


def test_calculate_model_participation_counts_questions_responses_and_total_messages() -> None:
    transcript = [
        {
            "round": 1,
            "protagonist": "Perplexity Pro",
            "question": "Qual premissa testar?",
            "responses": [
                {"agent": "Perplexity Pro", "answer": "A", "title_pct": 11.0},
                {"agent": "DeepSeek V4 Pro", "answer": "B", "title_pct": 10.5},
            ],
        },
        {
            "round": 2,
            "protagonist": "DeepSeek V4 Pro",
            "question": "Ajustamos oitavas?",
            "responses": [
                {"agent": "Perplexity Pro", "answer": "C", "title_pct": 11.0},
                {"agent": "DeepSeek V4 Pro", "answer": "D", "title_pct": 10.8},
            ],
        },
    ]

    participation = calculate_model_participation(transcript)

    assert participation["total_messages"] == 6
    assert participation["total_questions"] == 2
    assert participation["total_responses"] == 4
    assert participation["total_rounds"] == 2
    assert participation["protagonist_counts"] == {"Perplexity Pro": 1, "DeepSeek V4 Pro": 1}
    assert participation["rounds"] == [
        {
            "round": 1,
            "protagonist": "Perplexity Pro",
            "protagonist_count": 1,
            "participants": ["Perplexity Pro", "DeepSeek V4 Pro"],
        },
        {
            "round": 2,
            "protagonist": "DeepSeek V4 Pro",
            "protagonist_count": 1,
            "participants": ["DeepSeek V4 Pro", "Perplexity Pro"],
        },
    ]
    assert participation["last_consensus_round"] == 2
    assert participation["last_consensus_protagonist"] == "DeepSeek V4 Pro"
    assert participation["last_consensus_question"] == "Ajustamos oitavas?"
    assert participation["last_consensus_participants"] == ["DeepSeek V4 Pro", "Perplexity Pro"]
    assert participation["by_model"]["Perplexity Pro"] == {"messages": 3, "questions": 1, "responses": 2}
    assert participation["by_model"]["DeepSeek V4 Pro"] == {"messages": 3, "questions": 1, "responses": 2}


def test_calculate_model_participation_tracks_consensus_question_by_phase() -> None:
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
    transcript = [
        {
            "round": 1,
            "protagonist": "GPT 5.5",
            "question": "Concordam com Marrocos, Haiti e Escócia na fase de grupos?",
            "responses": [],
        },
        {
            "round": 2,
            "protagonist": "Gemini Pro",
            "question": "Concordam com 16 avos e Oitavas usando odds e Elo?",
            "responses": [],
        },
        {
            "round": 3,
            "protagonist": "DeepSeek V4 Pro",
            "question": "Concordam com Quartas, Semifinal e Final antes da chance de título?",
            "responses": [],
        },
    ]

    participation = calculate_model_participation(transcript, config=config)

    assert participation["consensus_questions_by_phase"] == [
        {
            "phase": "Fase de grupos",
            "round": 1,
            "protagonist": "GPT 5.5",
            "question": "Concordam com Marrocos, Haiti e Escócia na fase de grupos?",
        },
        {
            "phase": "16 avos",
            "round": 2,
            "protagonist": "Gemini Pro",
            "question": "Concordam com 16 avos e Oitavas usando odds e Elo?",
        },
        {
            "phase": "Oitavas",
            "round": 2,
            "protagonist": "Gemini Pro",
            "question": "Concordam com 16 avos e Oitavas usando odds e Elo?",
        },
        {
            "phase": "Quartas",
            "round": 3,
            "protagonist": "DeepSeek V4 Pro",
            "question": "Concordam com Quartas, Semifinal e Final antes da chance de título?",
        },
        {
            "phase": "Semifinal",
            "round": 3,
            "protagonist": "DeepSeek V4 Pro",
            "question": "Concordam com Quartas, Semifinal e Final antes da chance de título?",
        },
        {
            "phase": "Final",
            "round": 3,
            "protagonist": "DeepSeek V4 Pro",
            "question": "Concordam com Quartas, Semifinal e Final antes da chance de título?",
        },
    ]


def test_drop_fallback_only_agent_slots_removes_fallbacks_by_default() -> None:
    removed = _drop_fallback_only_agent_slots(
        [
            AgentOpinion(agent="GPT 5.5", title_pct=11.0, summary="fallback", used_fallback=True),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=10.0,
                summary="real",
                used_fallback=False,
                source_urls=["https://example.com/odds"],
            ),
        ],
        {},
    )

    assert removed == ["GPT 5.5"]


def test_drop_fallback_only_agent_slots_requires_each_model_to_bring_sources_by_default() -> None:
    removed = _drop_fallback_only_agent_slots(
        [
            AgentOpinion(
                agent="Opus 4.8",
                title_pct=11.0,
                summary="fallback",
                used_fallback=True,
                source_queries=["Brazil World Cup odds"],
            ),
            AgentOpinion(agent="GPT 5.5", title_pct=11.0, summary="real but no sources", used_fallback=False),
            AgentOpinion(
                agent="Perplexity Pro",
                title_pct=10.0,
                summary="real",
                used_fallback=False,
                source_urls=["https://example.com/markets"],
            ),
            AgentOpinion(
                agent="DeepSeek V4 Pro",
                title_pct=10.5,
                summary="real",
                used_fallback=False,
                source_queries=["Sofascore Brazil player ratings referee performance"],
            ),
        ],
        {},
    )

    assert removed == ["Opus 4.8", "GPT 5.5"]


def test_room_majority_quorum_uses_active_room_size() -> None:
    assert _room_majority_quorum(configured_min=3, active_count=3) == 2
    assert _room_majority_quorum(configured_min=3, active_count=4) == 3
    assert _room_majority_quorum(configured_min=3, active_count=5) == 3


def test_record_token_costs_counts_tokens_and_zeroes_fallback_cost() -> None:
    ledger = _new_token_cost_ledger(
        {
            "usd_to_brl": 5.0,
            "model_pricing_usd_per_million_tokens": {
                "GPT 5.5": {"input": 10.0, "output": 20.0},
                "Opus 4.8": {"input": 10.0, "output": 20.0},
            },
        }
    )

    _record_token_costs(
        ledger,
        config={
            "model_pricing_usd_per_million_tokens": {
                "GPT 5.5": {"input": 10.0, "output": 20.0},
                "Opus 4.8": {"input": 10.0, "output": 20.0},
            }
        },
        prompt="abcd" * 100,
        opinions=[
            AgentOpinion(agent="GPT 5.5", title_pct=11.0, summary="real", raw_text="efgh" * 50),
            AgentOpinion(agent="Opus 4.8", title_pct=11.0, summary="fallback", used_fallback=True),
        ],
        stage="meeting",
    )

    assert ledger["total"]["calls"] == 2
    assert ledger["by_model"]["GPT 5.5"]["cost_usd"] > 0
    assert ledger["by_model"]["Opus 4.8"]["cost_usd"] == 0
    assert ledger["total"]["fallback_calls"] == 1

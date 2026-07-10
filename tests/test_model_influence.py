from worldcup_brazil.consensus import AgentOpinion, build_consensus
from worldcup_brazil.pipeline import (
    _drop_fallback_only_agent_slots,
    _emit_low_influence_cost_alerts,
    _room_majority_quorum,
    _new_token_cost_ledger,
    _record_token_costs,
    calculate_model_influence,
    calculate_model_participation,
)


class _RecordingWatchdog:
    """Watchdog mínimo que registra os eventos emitidos, sem I/O de arquivo."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def event(self, step, status, *, detail="", extra=None) -> None:
        self.events.append({"step": step, "status": status, "detail": detail, "extra": extra or {}})


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
    assert participation["valid_messages"] == 6
    assert participation["valid_responses"] == 4
    assert participation["invalid_responses"] == 0
    assert participation["by_model"]["Perplexity Pro"] == {
        "messages": 3,
        "questions": 1,
        "responses": 2,
        "valid_responses": 2,
        "invalid_responses": 0,
    }
    assert participation["by_model"]["DeepSeek V4 Pro"] == {
        "messages": 3,
        "questions": 1,
        "responses": 2,
        "valid_responses": 2,
        "invalid_responses": 0,
    }


def test_calculate_model_participation_counts_removed_responses_as_invalid() -> None:
    transcript = [
        {
            "round": 1,
            "protagonist": "Perplexity Pro",
            "question": "Qual premissa testar?",
            "responses": [
                {"agent": "Perplexity Pro", "answer": "válida", "title_pct": 5.0},
                {
                    "agent": "DeepSeek V4 Pro",
                    "answer": "removida",
                    "title_pct": 11.0,
                    "removed_from_main": True,
                },
                {
                    "agent": "Gemini Pro",
                    "answer": "fallback sem fonte",
                    "title_pct": 5.0,
                    "used_fallback": True,
                    "source_count": 0,
                },
            ],
        }
    ]

    participation = calculate_model_participation(transcript)

    assert participation["total_messages"] == 4
    assert participation["valid_messages"] == 2
    assert participation["valid_responses"] == 1
    assert participation["invalid_responses"] == 2
    assert participation["by_model"]["Perplexity Pro"]["valid_responses"] == 1
    assert participation["by_model"]["DeepSeek V4 Pro"]["invalid_responses"] == 1
    assert participation["by_model"]["Gemini Pro"]["invalid_responses"] == 1


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


def test_low_influence_cost_alert_fires_for_expensive_silent_agent() -> None:
    """Regressão do run 615b0948 (11/jun): o Perplexity Pro custou US$0,954 (15% do total)
    por 0,5% de influência, com exit 0 e zero sinais — agente que cobra mas quase não move o
    consenso passava silencioso. No código antigo este teste falharia porque não havia nenhum
    alerta; agora um agente abaixo de low_influence_alert_pct com custo > 0 gera watchdog event
    'degraded' (step model_influence) e um aviso em bundle.warnings."""
    watchdog = _RecordingWatchdog()
    warnings: list[str] = []
    model_influence_pct = {
        "GPT 5.5": 60.0,
        "Perplexity Pro": 0.5,  # caro mas quase sem influência
        "Gemini Pro": 39.5,
    }
    model_token_costs = {
        "by_model": {
            "GPT 5.5": {"cost_usd": 0.30},
            "Perplexity Pro": {"cost_usd": 0.954},
            "Gemini Pro": {"cost_usd": 0.20},
        }
    }

    _emit_low_influence_cost_alerts(
        model_influence_pct=model_influence_pct,
        model_token_costs=model_token_costs,
        config={},  # usa default low_influence_alert_pct=2.0
        warnings=warnings,
        watchdog=watchdog,
    )

    degraded = [
        e for e in watchdog.events if e["step"] == "model_influence" and e["status"] == "degraded"
    ]
    assert len(degraded) == 1
    assert degraded[0]["extra"]["agent"] == "Perplexity Pro"
    assert degraded[0]["extra"]["influence_pct"] == 0.5
    assert degraded[0]["extra"]["cost_usd"] == 0.954
    assert len(warnings) == 1
    assert "Perplexity Pro" in warnings[0]
    # GPT 5.5 e Gemini Pro estão acima do limiar: não alertam.


def test_low_influence_cost_alert_silent_above_threshold_or_zero_cost() -> None:
    """Acima do limiar de influência OU custo zero não deve alertar: o alerta existe só para
    o caso patológico de gasto sem sinal. Um agente abaixo do limiar mas com custo zero (fallback
    local, custo externo zero) também não alerta, porque não há gasto a questionar."""
    watchdog = _RecordingWatchdog()
    warnings: list[str] = []
    model_influence_pct = {
        "GPT 5.5": 50.0,  # acima do limiar, custo > 0 => sem alerta
        "Gemini Pro": 1.0,  # abaixo do limiar mas custo 0 => sem alerta
    }
    model_token_costs = {
        "by_model": {
            "GPT 5.5": {"cost_usd": 0.40},
            "Gemini Pro": {"cost_usd": 0.0},
        }
    }

    _emit_low_influence_cost_alerts(
        model_influence_pct=model_influence_pct,
        model_token_costs=model_token_costs,
        config={"low_influence_alert_pct": 2.0},
        warnings=warnings,
        watchdog=watchdog,
    )

    assert watchdog.events == []
    assert warnings == []

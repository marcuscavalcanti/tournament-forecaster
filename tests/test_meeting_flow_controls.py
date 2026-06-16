import asyncio
import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from worldcup_brazil.agents import AgentSpec
from worldcup_brazil.consensus import AgentOpinion
from worldcup_brazil.pipeline import (
    MeetingConsensusError,
    _aggregate_blind_peer_reviews,
    _blind_peer_review_positions,
    _blind_peer_review_public_text,
    _format_impossible_opponent_reason,
    _run_model_meeting,
)
from worldcup_brazil.watchdog import RunWatchdog


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


def _source_backed_recalibration(agent: str, title_pct: float) -> AgentOpinion:
    return AgentOpinion(
        agent=agent,
        title_pct=title_pct,
        summary=(
            "Discordo da âncora baixa: odds de mercado, Elo e chaveamento sustentam recalibração "
            f"do título para {title_pct}%."
        ),
        answer=(
            "Mesmo sem resposta externa utilizável nova além das fontes auditáveis desta rodada, "
            f"odds +900/+950, Elo Brasil 1991 e Brasil-Inglaterra em 45% sustentam título em {title_pct}%. "
            "Pergunta de consenso: os demais concordam integralmente e a sala pode avançar?"
        ),
        source_urls=["https://example.com/odds", "https://example.com/elo"],
        agrees_with_protagonist=True,
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


def test_blind_peer_review_positions_are_deterministically_shuffled_before_ids() -> None:
    opinions = [
        AgentOpinion(agent="GPT 5.5", title_pct=10.0, summary="GPT summary", source_urls=["https://example.com/a"]),
        AgentOpinion(agent="Perplexity Pro", title_pct=10.1, summary="Perplexity summary", source_urls=["https://example.com/b"]),
        AgentOpinion(agent="Gemini Pro", title_pct=9.9, summary="Gemini summary", source_urls=["https://example.com/c"]),
    ]
    slots = ["GPT 5.5", "Perplexity Pro", "Gemini Pro"]

    positions = _blind_peer_review_positions(
        opinions,
        agent_slots=slots,
        round_index=1,
        shuffle_seed="anti-positional-fingerprint",
    )
    repeated = _blind_peer_review_positions(
        opinions,
        agent_slots=slots,
        round_index=1,
        shuffle_seed="anti-positional-fingerprint",
    )

    assert [position["position_id"] for position in positions] == ["position_1", "position_2", "position_3"]
    assert [position["_agent"] for position in positions] == [position["_agent"] for position in repeated]
    assert [position["_agent"] for position in positions] != slots


def test_blind_peer_review_public_text_masks_partial_provider_and_model_tokens() -> None:
    text = (
        "GPT/OpenAI citou gpt-5.5; Claude Opus e Anthropic divergiram; "
        "Gemini gemini-flash-latest e DeepSeek deepseek-v4-pro concordaram; "
        "Perplexity sonar-pro trouxe outra fonte. Versões 5.5, 4.8 e V4 Pro apareceram. "
        "Brasil x Marrocos tem odds 1.85, Elo 1850, xG 1.4 e Google Trends como fonte contextual."
    )

    public = _blind_peer_review_public_text(
        text,
        agent_slots=["GPT 5.5", "Opus 4.8", "Perplexity Pro", "DeepSeek V4 Pro", "Gemini Pro"],
        mask_terms=[
            "openai",
            "gpt-5.5",
            "claude",
            "opus",
            "anthropic",
            "gemini-flash-latest",
            "deepseek-v4-pro",
            "sonar-pro",
        ],
    ).lower()

    for forbidden in (
        "gpt",
        "openai",
        "claude",
        "opus",
        "anthropic",
        "gemini",
        "deepseek",
        "perplexity",
        "sonar",
        "5.5",
        "4.8",
        "v4",
    ):
        assert forbidden not in public
    for preserved in ("brasil", "marrocos", "1.85", "1850", "xg 1.4", "google trends"):
        assert preserved in public


def test_blind_peer_review_self_preference_leakage_is_measured_and_self_scores_excluded() -> None:
    positions = [
        {"position_id": "position_1", "_agent": "GPT 5.5"},
        {"position_id": "position_2", "_agent": "Perplexity Pro"},
        {"position_id": "position_3", "_agent": "Gemini Pro"},
    ]
    payloads = {
        "GPT 5.5": {
            "scores": [
                {"position_id": "position_1", "score": 1.0, "accepted": True},
                {"position_id": "position_2", "score": 0.5, "accepted": False},
                {"position_id": "position_3", "score": 0.5, "accepted": False},
            ]
        },
        "Perplexity Pro": {
            "scores": [
                {"position_id": "position_1", "score": 0.5, "accepted": False},
                {"position_id": "position_2", "score": 1.0, "accepted": True},
                {"position_id": "position_3", "score": 0.5, "accepted": False},
            ]
        },
        "Gemini Pro": {
            "scores": [
                {"position_id": "position_1", "score": 0.5, "accepted": False},
                {"position_id": "position_2", "score": 0.5, "accepted": False},
                {"position_id": "position_3", "score": 1.0, "accepted": True},
            ]
        },
    }
    reviews = [
        AgentOpinion(
            agent=agent,
            title_pct=10.0,
            summary="Revisão cega",
            raw_text=json.dumps(payload),
            answer=json.dumps(payload),
            source_urls=["https://example.com/r"],
        )
        for agent, payload in payloads.items()
    ]

    metadata = _aggregate_blind_peer_reviews(reviews, positions=positions, config={"blind_peer_review_enabled": True})

    assert metadata["self_preference_leakage"]["self_score_count"] == 3
    assert metadata["self_preference_leakage"]["value"] == 0.5
    assert metadata["blind_review_score"] == {
        "position_1": 0.5,
        "position_2": 0.5,
        "position_3": 0.5,
    }
    assert metadata["blind_acceptance_by_position"] == {
        "position_1": 0,
        "position_2": 0,
        "position_3": 0,
    }


def test_blind_peer_review_self_preference_leakage_stays_zero_without_bias() -> None:
    positions = [
        {"position_id": "position_1", "_agent": "GPT 5.5"},
        {"position_id": "position_2", "_agent": "Perplexity Pro"},
    ]
    payload = {
        "scores": [
            {"position_id": "position_1", "score": 0.7, "accepted": False},
            {"position_id": "position_2", "score": 0.7, "accepted": False},
        ]
    }
    reviews = [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=10.0,
            summary="Revisão cega",
            raw_text=json.dumps(payload),
            answer=json.dumps(payload),
        ),
        AgentOpinion(
            agent="Perplexity Pro",
            title_pct=10.0,
            summary="Revisão cega",
            raw_text=json.dumps(payload),
            answer=json.dumps(payload),
        ),
    ]

    metadata = _aggregate_blind_peer_reviews(reviews, positions=positions, config={"blind_peer_review_enabled": True})

    assert metadata["self_preference_leakage"]["self_score_count"] == 2
    assert metadata["self_preference_leakage"]["value"] == 0.0


def test_blind_peer_review_self_preference_leakage_handles_asymmetric_bias() -> None:
    positions = [
        {"position_id": "position_1", "_agent": "GPT 5.5"},
        {"position_id": "position_2", "_agent": "Perplexity Pro"},
        {"position_id": "position_3", "_agent": "Gemini Pro"},
    ]
    payloads = {
        "GPT 5.5": {
            "scores": [
                {"position_id": "position_1", "score": 1.0, "accepted": True},
                {"position_id": "position_2", "score": 0.4, "accepted": False},
                {"position_id": "position_3", "score": 0.6, "accepted": False},
            ]
        },
        "Perplexity Pro": {
            "scores": [
                {"position_id": "position_1", "score": 0.7, "accepted": False},
                {"position_id": "position_2", "score": 0.7, "accepted": False},
                {"position_id": "position_3", "score": 0.7, "accepted": False},
            ]
        },
        "Gemini Pro": {
            "scores": [
                {"position_id": "position_1", "score": 0.8, "accepted": True},
                {"position_id": "position_2", "score": 0.8, "accepted": True},
                {"position_id": "position_3", "score": 0.6, "accepted": False},
            ]
        },
    }
    reviews = [
        AgentOpinion(
            agent=agent,
            title_pct=10.0,
            summary="Revisão cega",
            raw_text=json.dumps(payload),
            answer=json.dumps(payload),
        )
        for agent, payload in payloads.items()
    ]

    metadata = _aggregate_blind_peer_reviews(reviews, positions=positions, config={"blind_peer_review_enabled": True})

    assert metadata["self_preference_leakage"]["value"] == 0.1
    assert metadata["self_preference_by_reviewer"]["GPT 5.5"]["leakage"] == 0.5
    assert metadata["self_preference_by_reviewer"]["Perplexity Pro"]["leakage"] == 0.0
    assert metadata["self_preference_by_author"]["GPT 5.5"]["self_mean"] == 1.0
    assert metadata["self_preference_by_author"]["GPT 5.5"]["external_mean"] == 0.75
    assert metadata["blind_review_score"] == {
        "position_1": 0.75,
        "position_2": 0.6,
        "position_3": 0.65,
    }


def test_meeting_does_not_abort_sterile_for_source_backed_title_recalibration(monkeypatch) -> None:
    """Regressão do run 13/jun/2026: a sala tinha respostas reais e auditáveis, mas
    morreu estéril porque o gate tratava qualquer title_pct > baseline+5pp como
    inconsistência e também removia caveats legítimos com 'sem resposta externa utilizável'."""
    specs = [
        AgentSpec(
            slot="Opus 4.8",
            provider="anthropic",
            model="opus",
            env_api_key="ANTHROPIC_API_KEY",
            endpoint="https://api.anthropic.com/v1/messages",
        ),
        AgentSpec(
            slot="GPT 5.5",
            provider="openai",
            model="gpt-5.5",
            env_api_key="OPENAI_API_KEY",
            endpoint="https://api.openai.com/v1/responses",
        ),
        AgentSpec(
            slot="DeepSeek V4 Pro",
            provider="openai-compatible",
            model="deepseek-v4-pro",
            env_api_key="DEEPSEEK_API_KEY",
            endpoint="https://api.deepseek.com/chat/completions",
        ),
    ]
    planning = [
        AgentOpinion(agent=spec.slot, title_pct=7.0, summary="Planejou fontes.", source_urls=["https://example.com/odds"])
        for spec in specs
    ]
    config = {
        **_base_config(),
        "knockout_matches": [{"phase": "Quartas", "opponent": "Inglaterra"}],
        "meeting_min_participants": 3,
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 2,
        "meeting_consensus_threshold_pct": 50.0,
        "meeting_sterile_round_limit": 2,
        "meeting_slot_breaker_threshold": 99,
        "meeting_stability_rounds": 1,
        "meeting_response_repair_attempts": 0,
        "require_auditable_source_urls_for_meeting_votes": True,
        "max_agent_title_shift_pct": 5.0,
        "baseline_title_pct": 3.5,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _source_backed_recalibration(spec.slot, 8.6)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        return [_source_backed_recalibration(spec.slot, 8.6) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    consensus, opinions, transcript, _all = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=planning,
            generated_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            agent_specs=specs,
            baseline_title_pct=3.5,
            allow_agent_fallback=True,
            watchdog=None,
        )
    )

    assert consensus.title_pct == 8.6
    assert len(opinions) == 3
    assert transcript[0]["responses"]


def test_blind_peer_review_shadow_records_metrics_without_changing_consensus(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_rounds": 1,
        "meeting_max_rounds": 1,
        "meeting_consensus_threshold_pct": 2.5,
        "blind_peer_review_enabled": True,
        "blind_peer_review_shadow_only": True,
        "blind_peer_review_timeout_seconds": 12,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    blind_prompts: list[str] = []

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, **kwargs):
        call_role = kwargs.get("call_role")
        if call_role == "blind_peer_review":
            blind_prompts.append(prompt)
            return [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=10.0,
                    summary="Revisão cega em shadow.",
                    answer=json.dumps(
                        {
                            "scores": [
                                {"position_id": "position_1", "score": 0.96, "accepted": True},
                                {"position_id": "position_2", "score": 0.92, "accepted": True},
                                {"position_id": "position_3", "score": 0.88, "accepted": True},
                            ]
                        }
                    ),
                    raw_text=json.dumps(
                        {
                            "scores": [
                                {"position_id": "position_1", "score": 0.96, "accepted": True},
                                {"position_id": "position_2", "score": 0.92, "accepted": True},
                                {"position_id": "position_3", "score": 0.88, "accepted": True},
                            ]
                        }
                    ),
                    source_urls=["https://example.com/blind-review"],
                )
                for spec in specs
            ]
        return [_healthy_response(spec.slot, 10.0) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    consensus, opinions, transcript, _all = asyncio.run(
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

    assert consensus.title_pct == 10.0
    assert [opinion.title_pct for opinion in opinions] == [10.0, 10.0, 10.0]
    review = transcript[0]["blind_peer_review"]
    assert review["enabled"] is True
    assert review["shadow_only"] is True
    assert review["acted_on_decision"] is False
    assert review["blind_acceptance_count"] == 2
    assert review["blind_top_position_id"] == "position_1"
    assert review["blind_review_score"]["position_1"] == 0.96
    assert review["self_preference_leakage"]["reviewer_count"] == 3
    assert len(blind_prompts) == 1
    lowered_prompt = blind_prompts[0].lower()
    for forbidden in (
        '"_agent"',
        "gpt 5.5",
        "gpt",
        "perplexity pro",
        "perplexity",
        "gemini pro",
        "gemini",
        "protagonista",
        "líder",
        "leader",
    ):
        assert forbidden not in lowered_prompt


def test_llm_council_fast_path_exits_only_when_explicitly_enabled_and_gated(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_rounds": 6,
        "meeting_max_rounds": 8,
        "meeting_consensus_threshold_pct": 2.5,
        "meeting_require_peer_acceptance": True,
        "meeting_require_full_path_coverage": False,
        "blind_peer_review_enabled": True,
        "blind_peer_review_shadow_only": True,
        "blind_peer_review_timeout_seconds": 12,
        "llm_council_fast_path_enabled": True,
        "llm_council_fast_path_shadow_only": False,
        "llm_council_fast_path_min_participants": 3,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    blind_prompts: list[str] = []

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, **kwargs):
        if kwargs.get("call_role") == "blind_peer_review":
            blind_prompts.append(prompt)
            payload = {
                "scores": [
                    {"position_id": "position_1", "score": 0.94, "accepted": True},
                    {"position_id": "position_2", "score": 0.91, "accepted": True},
                    {"position_id": "position_3", "score": 0.89, "accepted": True},
                ]
            }
            return [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=10.0,
                    summary="Revisão cega aceita.",
                    answer=json.dumps(payload),
                    raw_text=json.dumps(payload),
                    source_urls=["https://example.com/blind-review"],
                )
                for spec in specs
            ]
        return [_healthy_response(spec.slot, 10.0) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    consensus, opinions, transcript, _all = asyncio.run(
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

    assert consensus.title_pct == 10.0
    assert getattr(consensus, "exit_status") == "fast_path_consensus"
    assert len(opinions) == 3
    assert len(transcript) == 1
    fast_path = transcript[0]["llm_council_fast_path"]
    assert fast_path["enabled"] is True
    assert fast_path["eligible"] is True
    assert fast_path["shadow_only"] is False
    assert fast_path["acted_on_decision"] is True
    assert fast_path["round"] == 1
    assert fast_path["blind_acceptance_count"] == 2
    assert transcript[0]["blind_peer_review"]["acted_on_decision"] is True
    assert transcript[0]["blind_peer_review"]["mode"] == "fast_path_gate"
    assert blind_prompts
    assert "não altera a decisão do run" not in blind_prompts[0]
    assert "gate auxiliar do fast path" in blind_prompts[0]


def test_llm_council_fast_path_shadow_records_candidate_without_shortening_meeting(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_rounds": 6,
        "meeting_max_rounds": 2,
        "meeting_consensus_threshold_pct": 2.5,
        "meeting_require_peer_acceptance": True,
        "meeting_require_full_path_coverage": False,
        "blind_peer_review_enabled": True,
        "blind_peer_review_shadow_only": True,
        "blind_peer_review_timeout_seconds": 12,
        "llm_council_fast_path_enabled": True,
        "llm_council_fast_path_shadow_only": True,
        "llm_council_fast_path_min_participants": 3,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, **kwargs):
        if kwargs.get("call_role") == "blind_peer_review":
            payload = {
                "scores": [
                    {"position_id": "position_1", "score": 0.94, "accepted": True},
                    {"position_id": "position_2", "score": 0.91, "accepted": True},
                    {"position_id": "position_3", "score": 0.89, "accepted": True},
                ]
            }
            return [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=10.0,
                    summary="Revisão cega aceita.",
                    answer=json.dumps(payload),
                    raw_text=json.dumps(payload),
                    source_urls=["https://example.com/blind-review"],
                )
                for spec in specs
            ]
        return [_healthy_response(spec.slot, 10.0) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    consensus, _opinions, transcript, _all = asyncio.run(
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

    assert getattr(consensus, "exit_status") == "max_rounds_no_consensus"
    assert len(transcript) == 2
    fast_path = transcript[0]["llm_council_fast_path"]
    assert fast_path["eligible"] is True
    assert fast_path["shadow_only"] is True
    assert fast_path["acted_on_decision"] is False


def test_blind_peer_review_runs_on_round_one_and_consensus_exit_candidate(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_rounds": 3,
        "meeting_max_rounds": 3,
        "meeting_consensus_threshold_pct": 2.5,
        "meeting_require_peer_acceptance": True,
        "meeting_require_full_path_coverage": False,
        "blind_peer_review_enabled": True,
        "blind_peer_review_shadow_only": True,
        "blind_peer_review_on_consensus_exit": True,
        "blind_peer_review_timeout_seconds": 12,
        "llm_council_fast_path_enabled": False,
        "llm_council_fast_path_shadow_only": True,
        "llm_council_fast_path_min_participants": 3,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    blind_prompts: list[str] = []

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, **kwargs):
        if kwargs.get("call_role") == "blind_peer_review":
            blind_prompts.append(prompt)
            payload = {
                "scores": [
                    {"position_id": "position_1", "score": 0.94, "accepted": True},
                    {"position_id": "position_2", "score": 0.91, "accepted": True},
                    {"position_id": "position_3", "score": 0.89, "accepted": True},
                ]
            }
            return [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=10.0,
                    summary="Revisão cega aceita.",
                    answer=json.dumps(payload),
                    raw_text=json.dumps(payload),
                    source_urls=["https://example.com/blind-review"],
                )
                for spec in specs
            ]
        return [_healthy_response(spec.slot, 10.0) for spec in specs]

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
    assert len(blind_prompts) == 2
    assert "blind_peer_review" in transcript[0]
    assert "blind_peer_review" not in transcript[1]
    assert "blind_peer_review" in transcript[2]
    assert transcript[0]["blind_peer_review"]["mode"] == "shadow"
    assert transcript[2]["blind_peer_review"]["mode"] == "consensus_exit_candidate"


def test_meeting_round_budget_exhaustion_preserves_partial_consensus(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_rounds": 99,
        "meeting_max_rounds": 5,
        "meeting_round_budget_seconds": 0.001,
        "meeting_stability_rounds": 99,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        await asyncio.sleep(0.003)
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, **kwargs):
        return [_healthy_response(spec.slot, 10.0) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    consensus, _opinions, transcript, _all = asyncio.run(
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

    assert len(transcript) == 1
    assert getattr(consensus, "exit_status") == "round_budget_exhausted"
    assert "orçamento acumulado" in getattr(consensus, "exit_warning")


def test_blind_peer_review_non_shadow_blocks_consensus_exit_when_acceptance_fails(monkeypatch, tmp_path) -> None:
    config = {
        **_base_config(),
        "meeting_min_rounds": 2,
        "meeting_max_rounds": 3,
        "meeting_consensus_threshold_pct": 2.5,
        "meeting_require_peer_acceptance": True,
        "meeting_require_full_path_coverage": False,
        "blind_peer_review_enabled": True,
        "blind_peer_review_shadow_only": False,
        "blind_peer_review_on_consensus_exit": True,
        "blind_peer_review_timeout_seconds": 12,
        "llm_council_fast_path_enabled": False,
        "llm_council_fast_path_shadow_only": True,
        "llm_council_fast_path_min_participants": 3,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, **kwargs):
        if kwargs.get("call_role") == "blind_peer_review":
            payload = {
                "scores": [
                    {"position_id": "position_1", "score": 0.30, "accepted": False},
                    {"position_id": "position_2", "score": 0.25, "accepted": False},
                    {"position_id": "position_3", "score": 0.20, "accepted": False},
                ]
            }
            return [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=10.0,
                    summary="Revisão cega rejeita.",
                    answer=json.dumps(payload),
                    raw_text=json.dumps(payload),
                    source_urls=["https://example.com/blind-review"],
                )
                for spec in specs
            ]
        return [_healthy_response(spec.slot, 10.0) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)
    watchdog_path = tmp_path / "watchdog.jsonl"

    consensus, _opinions, transcript, _all = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=_planning_opinions(),
            generated_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            agent_specs=_specs(),
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=RunWatchdog(path=watchdog_path, verbose=False),
        )
    )

    assert getattr(consensus, "exit_status") == "max_rounds_no_consensus"
    assert len(transcript) == 3
    review = transcript[1]["blind_peer_review"]
    assert review["mode"] == "consensus_exit_candidate"
    assert review["shadow_only"] is False
    assert review["gate_blocked"] is True
    assert "blind_acceptance_missing" in review["gate_blocked_reasons"]
    events = [json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()]
    assert any(
        event["step"] == "blind_peer_review"
        and event["status"] == "blocked"
        and "blind_acceptance_missing" in event["extra"]["blocked_reasons"]
        for event in events
    )


def test_blind_peer_review_non_shadow_blocks_consensus_exit_when_self_preference_leaks(monkeypatch) -> None:
    generated_at = datetime(2026, 6, 14, tzinfo=timezone.utc)
    config = {
        **_base_config(),
        "meeting_min_rounds": 2,
        "meeting_max_rounds": 3,
        "meeting_consensus_threshold_pct": 2.5,
        "meeting_require_peer_acceptance": True,
        "meeting_require_full_path_coverage": False,
        "blind_peer_review_enabled": True,
        "blind_peer_review_shadow_only": False,
        "blind_peer_review_on_consensus_exit": True,
        "blind_peer_review_timeout_seconds": 12,
        "blind_peer_review_max_self_preference_leakage": 0.20,
        "llm_council_fast_path_enabled": False,
        "llm_council_fast_path_shadow_only": True,
        "llm_council_fast_path_min_participants": 3,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, **kwargs):
        if kwargs.get("call_role") == "blind_peer_review":
            round_match = re.search(r"Rodada:\s*(\d+)", prompt)
            round_index = int(round_match.group(1)) if round_match else 1
            position_opinions = [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=10.0,
                    summary="Posição para revisão cega.",
                    source_urls=["https://example.com/blind-review"],
                )
                for spec in specs
            ]
            positions = _blind_peer_review_positions(
                position_opinions,
                agent_slots=[spec.slot for spec in specs],
                round_index=round_index,
                shuffle_seed=f"{generated_at.isoformat()}|room=main_brazil|round={round_index}",
            )
            position_author = {position["position_id"]: position["_agent"] for position in positions}
            # Cada modelo infla sua própria posição e aceita as demais, produzindo
            # blind_acceptance_count suficiente, mas vazamento claro de autopreferência.
            return [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=10.0,
                    summary="Revisão cega aceita com viés próprio.",
                    answer=json.dumps(
                        {
                            "scores": [
                                {
                                    "position_id": position_id,
                                    "score": 1.0 if author == spec.slot else 0.72,
                                    "accepted": True,
                                }
                                for position_id, author in position_author.items()
                            ]
                        }
                    ),
                    raw_text=json.dumps(
                        {
                            "scores": [
                                {
                                    "position_id": position_id,
                                    "score": 1.0 if author == spec.slot else 0.72,
                                    "accepted": True,
                                }
                                for position_id, author in position_author.items()
                            ]
                        }
                    ),
                    source_urls=["https://example.com/blind-review"],
                )
                for spec in specs
            ]
        return [_healthy_response(spec.slot, 10.0) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    consensus, _opinions, transcript, _all = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=_planning_opinions(),
            generated_at=generated_at,
            agent_specs=_specs(),
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=None,
        )
    )

    assert getattr(consensus, "exit_status") == "max_rounds_no_consensus"
    review = transcript[1]["blind_peer_review"]
    assert review["gate_blocked"] is True
    assert "self_preference_leakage_high" in review["gate_blocked_reasons"]
    assert review["self_preference_leakage"]["exceeds_threshold"] is True


def test_llm_council_fast_path_blocks_when_report_coherence_would_fail(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_rounds": 6,
        "meeting_max_rounds": 2,
        "meeting_consensus_threshold_pct": 2.5,
        "meeting_require_peer_acceptance": True,
        "meeting_require_full_path_coverage": False,
        "blind_peer_review_enabled": True,
        "blind_peer_review_shadow_only": True,
        "blind_peer_review_timeout_seconds": 12,
        "llm_council_fast_path_enabled": True,
        "llm_council_fast_path_shadow_only": False,
        "llm_council_fast_path_min_participants": 3,
        "stage_probabilities": {
            "quartas": 10.0,
            "semifinal": 8.0,
            "final": 2.0,
            "titulo": 4.0,
        },
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, **kwargs):
        if kwargs.get("call_role") == "blind_peer_review":
            payload = {
                "scores": [
                    {"position_id": "position_1", "score": 0.94, "accepted": True},
                    {"position_id": "position_2", "score": 0.91, "accepted": True},
                    {"position_id": "position_3", "score": 0.89, "accepted": True},
                ]
            }
            return [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=10.0,
                    summary="Revisão cega aceita.",
                    answer=json.dumps(payload),
                    raw_text=json.dumps(payload),
                    source_urls=["https://example.com/blind-review"],
                )
                for spec in specs
            ]
        return [_healthy_response(spec.slot, 10.0) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    consensus, _opinions, transcript, _all = asyncio.run(
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

    assert getattr(consensus, "exit_status") == "max_rounds_no_consensus"
    assert len(transcript) == 2
    fast_path = transcript[0]["llm_council_fast_path"]
    assert fast_path["eligible"] is False
    assert fast_path["acted_on_decision"] is False
    assert "report_coherence_failed" in fast_path["blocked_reasons"]
    assert "funil incoerente" in fast_path["report_coherence_error"]


def test_llm_council_fast_path_blocks_when_blind_review_self_preference_leaks(monkeypatch) -> None:
    generated_at = datetime(2026, 6, 14, tzinfo=timezone.utc)
    config = {
        **_base_config(),
        "meeting_min_rounds": 6,
        "meeting_max_rounds": 2,
        "meeting_consensus_threshold_pct": 2.5,
        "meeting_require_peer_acceptance": True,
        "meeting_require_full_path_coverage": False,
        "blind_peer_review_enabled": True,
        "blind_peer_review_shadow_only": True,
        "blind_peer_review_timeout_seconds": 12,
        "blind_peer_review_max_self_preference_leakage": 0.20,
        "llm_council_fast_path_enabled": True,
        "llm_council_fast_path_shadow_only": False,
        "llm_council_fast_path_min_participants": 3,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, **kwargs):
        if kwargs.get("call_role") == "blind_peer_review":
            positions = _blind_peer_review_positions(
                [
                    AgentOpinion(
                        agent=spec.slot,
                        title_pct=10.0,
                        summary="Posição para revisão cega.",
                        source_urls=["https://example.com/blind-review"],
                    )
                    for spec in specs
                ],
                agent_slots=[spec.slot for spec in specs],
                round_index=1,
                shuffle_seed=f"{generated_at.isoformat()}|room=main_brazil|round=1",
            )
            position_author = {position["position_id"]: position["_agent"] for position in positions}
            return [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=10.0,
                    summary="Revisão cega aceita com viés próprio.",
                    answer=json.dumps(
                        {
                            "scores": [
                                {
                                    "position_id": position_id,
                                    "score": 1.0 if author == spec.slot else 0.72,
                                    "accepted": True,
                                }
                                for position_id, author in position_author.items()
                            ]
                        }
                    ),
                    raw_text=json.dumps(
                        {
                            "scores": [
                                {
                                    "position_id": position_id,
                                    "score": 1.0 if author == spec.slot else 0.72,
                                    "accepted": True,
                                }
                                for position_id, author in position_author.items()
                            ]
                        }
                    ),
                    source_urls=["https://example.com/blind-review"],
                )
                for spec in specs
            ]
        return [_healthy_response(spec.slot, 10.0) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    consensus, _opinions, transcript, _all = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=_planning_opinions(),
            generated_at=generated_at,
            agent_specs=_specs(),
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=None,
        )
    )

    assert getattr(consensus, "exit_status") == "max_rounds_no_consensus"
    fast_path = transcript[0]["llm_council_fast_path"]
    assert fast_path["eligible"] is False
    assert fast_path["acted_on_decision"] is False
    assert "self_preference_leakage_high" in fast_path["blocked_reasons"]


def test_llm_council_fast_path_uses_full_report_coherence_callback_before_exit(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_rounds": 6,
        "meeting_max_rounds": 2,
        "meeting_consensus_threshold_pct": 2.5,
        "meeting_require_peer_acceptance": True,
        "meeting_require_full_path_coverage": False,
        "blind_peer_review_enabled": True,
        "blind_peer_review_shadow_only": True,
        "blind_peer_review_timeout_seconds": 12,
        "llm_council_fast_path_enabled": True,
        "llm_council_fast_path_shadow_only": False,
        "llm_council_fast_path_min_participants": 3,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, **kwargs):
        if kwargs.get("call_role") == "blind_peer_review":
            payload = {
                "scores": [
                    {"position_id": "position_1", "score": 0.94, "accepted": True},
                    {"position_id": "position_2", "score": 0.91, "accepted": True},
                    {"position_id": "position_3", "score": 0.89, "accepted": True},
                ]
            }
            return [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=10.0,
                    summary="Revisão cega aceita.",
                    answer=json.dumps(payload),
                    raw_text=json.dumps(payload),
                    source_urls=["https://example.com/blind-review"],
                )
                for spec in specs
            ]
        return [_healthy_response(spec.slot, 10.0) for spec in specs]

    rejected_estimate = SimpleNamespace(phase="Final", opponent="França", brazil_pct=28.0, scenario_pct=28.0)

    def fake_full_report_coherence_check(consensus) -> str:
        assert consensus.title_pct == 10.0
        return (
            "Gate de coerência pré-render falhou: "
            f"{rejected_estimate.phase} vs {rejected_estimate.opponent}: "
            f"brazil_pct={rejected_estimate.brazil_pct:.1f}% ecoa scenario_pct={rejected_estimate.scenario_pct:.1f}%"
        )

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    consensus, _opinions, transcript, _all = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=_planning_opinions(),
            generated_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            agent_specs=_specs(),
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=None,
            fast_path_report_coherence_check=fake_full_report_coherence_check,
        )
    )

    assert getattr(consensus, "exit_status") == "max_rounds_no_consensus"
    assert len(transcript) == 2
    fast_path = transcript[0]["llm_council_fast_path"]
    assert fast_path["eligible"] is False
    assert fast_path["acted_on_decision"] is False
    assert "report_coherence_failed" in fast_path["blocked_reasons"]
    assert "ecoa scenario_pct" in fast_path["report_coherence_error"]


def test_llm_council_fast_path_rejects_unusable_parallel_opponent_room(monkeypatch) -> None:
    config = {
        **_base_config(),
        "meeting_min_rounds": 6,
        "meeting_max_rounds": 2,
        "meeting_consensus_threshold_pct": 2.5,
        "meeting_require_peer_acceptance": True,
        "meeting_require_full_path_coverage": False,
        "blind_peer_review_enabled": True,
        "llm_council_fast_path_enabled": True,
        "llm_council_fast_path_shadow_only": False,
        "llm_council_fast_path_min_participants": 3,
        "_parallel_opponent_briefing": {
            "enabled": True,
            "failed": False,
            "rounds": 3,
            "exit_status": "max_rounds_no_consensus",
            "usable_for_main_room": False,
        },
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, **kwargs):
        if kwargs.get("call_role") == "blind_peer_review":
            payload = {
                "scores": [
                    {"position_id": "position_1", "score": 0.94, "accepted": True},
                    {"position_id": "position_2", "score": 0.91, "accepted": True},
                    {"position_id": "position_3", "score": 0.89, "accepted": True},
                ]
            }
            return [
                AgentOpinion(
                    agent=spec.slot,
                    title_pct=10.0,
                    summary="Revisão cega aceita.",
                    answer=json.dumps(payload),
                    raw_text=json.dumps(payload),
                    source_urls=["https://example.com/blind-review"],
                )
                for spec in specs
            ]
        return [_healthy_response(spec.slot, 10.0) for spec in specs]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    consensus, _opinions, transcript, _all = asyncio.run(
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

    assert getattr(consensus, "exit_status") == "max_rounds_no_consensus"
    fast_path = transcript[0]["llm_council_fast_path"]
    assert fast_path["eligible"] is False
    assert "parallel_opponent_room_unusable" in fast_path["blocked_reasons"]


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


def test_opponent_debriefing_room_keeps_live_crossing_group_results_in_scope() -> None:
    from pathlib import Path

    from worldcup_brazil.monte_carlo import run_brazil_monte_carlo
    from worldcup_brazil.pipeline import _opponent_debriefing_config, load_config

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

    sub_config = _opponent_debriefing_config(config)

    assert sub_config["completed_group_matches"][0]["group"] == "F"
    assert sub_config["_path_relevant_group_states"]["F"]["completed_results"][0]["score"] == "Holanda 2-2 Japão"
    direction = sub_config["macro_direction"].lower()
    assert "placares realizados" in direction
    assert "tabelas vivas" in direction
    assert "grupos de cruzamento" in direction


def test_meeting_at_ceiling_publishes_last_valid_consensus_when_final_round_is_sterile(monkeypatch, tmp_path) -> None:
    """Regressão do run 10/jun/2026: um burst de rate-limit tornava a ÚLTIMA rodada estéril
    (DegenerateConsensusError) ao bater o teto de rodadas, mesmo após rodadas anteriores terem
    produzido consenso válido. O guard do teto levantava MeetingConsensusError e o run morria
    exit 1 após ~8 rodadas e quase todo o gasto. No código antigo este teste falharia porque
    o teto sempre levantava quando a rodada final era estéril; agora a sala publica DEGRADADO o
    último consenso válido e emite watchdog event 'degraded_publish'. A streak estéril (1) fica
    abaixo de meeting_sterile_round_limit (2), então o abort de sala estéril não dispara."""
    config = {
        **_base_config(),
        "meeting_min_rounds": 99,  # nunca sair por consensus_reached
        "meeting_max_rounds": 3,
        "meeting_sterile_round_limit": 2,
        "meeting_slot_breaker_threshold": 99,
        "meeting_stability_rounds": 99,  # nunca sair por estabilidade
    }

    # O protagonista (call_agent) abre cada rodada; usamos isso como contador de rodada.
    # A rodada final precisa ser estéril POR INTEIRO (protagonista + respostas todas fallback)
    # para que build_consensus levante DegenerateConsensusError — se só as respostas forem
    # fallback, o voto do protagonista ainda dá peso e a rodada não fica estéril.
    round_counter = {"n": 0}

    async def fake_call_agent(spec, prompt, **kwargs):
        round_counter["n"] += 1
        if round_counter["n"] >= 3:
            return _invalid_response(spec.slot)
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        # Rodada final (3) é estéril: todos fallback => build_consensus levanta
        # DegenerateConsensusError. Rodadas anteriores são válidas.
        if round_counter["n"] >= 3:
            return [_invalid_response(spec.slot) for spec in specs]
        return [
            _healthy_response(spec.slot, 9.0 if spec.slot == "Gemini Pro" else 10.0)
            for spec in specs
        ]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    watchdog_path = tmp_path / "watchdog.jsonl"
    consensus, opinions, transcript, _all = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=_planning_opinions(),
            generated_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            agent_specs=_specs(),
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=RunWatchdog(path=watchdog_path, verbose=False),
        )
    )

    # Bateu o teto: 3 rodadas no transcript.
    assert len(transcript) == 3
    # Publicou degradado o consenso da rodada VÁLIDA anterior, não levantou.
    # No código antigo o guard do teto sempre levantava MeetingConsensusError aqui;
    # chegar a esta linha com um consenso já prova a publicação degradada.
    assert consensus is not None
    assert abs(float(consensus.title_pct) - 10.0) < 1.0  # consenso da rodada válida, não baseline fallback (11.0)
    assert getattr(consensus, "exit_status") == "degraded_last_valid"
    assert "modo degradado" in getattr(consensus, "exit_warning")
    assert opinions, "deveria devolver as opiniões da última rodada válida"

    events = [json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()]
    degraded = [e for e in events if e.get("step") == "model_meeting" and e.get("status") == "degraded_publish"]
    assert degraded, "deveria emitir watchdog event degraded_publish no teto com rodada final estéril"
    assert degraded[0]["extra"]["last_valid_title_pct"] == consensus.title_pct
    assert not any(e.get("step") == "model_meeting" and e.get("status") == "fail" for e in events)


def test_meeting_at_ceiling_with_valid_round_but_no_exit_marks_no_consensus(monkeypatch, tmp_path) -> None:
    """Teto com rodada final válida, mas sem consenso explícito, não pode parecer
    consenso normal. Esse caso ficou mais provável na sala paralela ao reduzir
    max_rounds para caber no orçamento."""
    config = {
        **_base_config(),
        "meeting_min_rounds": 99,
        "meeting_max_rounds": 2,
        "meeting_sterile_round_limit": 99,
        "meeting_slot_breaker_threshold": 99,
        "meeting_stability_rounds": 99,
    }

    async def fake_call_agent(spec, prompt, **kwargs):
        return _healthy_question_opinion(spec.slot)

    async def fake_call_all_agents(prompt, *, specs, **kwargs):
        return [
            _healthy_response(spec.slot, 9.0 if spec.slot == "Gemini Pro" else 10.0)
            for spec in specs
        ]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_agent", fake_call_agent)
    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)

    watchdog_path = tmp_path / "watchdog.jsonl"
    consensus, _opinions, transcript, _all = asyncio.run(
        _run_model_meeting(
            config=config,
            planning_opinions=_planning_opinions(),
            generated_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
            agent_specs=_specs(),
            baseline_title_pct=11.0,
            allow_agent_fallback=True,
            watchdog=RunWatchdog(path=watchdog_path, verbose=False),
        )
    )

    assert len(transcript) == 2
    assert getattr(consensus, "exit_status") == "max_rounds_no_consensus"
    assert "sem consenso explícito" in getattr(consensus, "exit_warning")
    events = [json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()]
    assert any(
        event["step"] == "model_meeting" and event["status"] == "max_rounds_no_consensus"
        for event in events
    )


def test_meeting_at_ceiling_still_raises_when_no_round_was_ever_valid(monkeypatch) -> None:
    """Contraprova do degraded publish: se NENHUMA rodada produziu consenso válido (todas
    estéreis) e o teto é atingido, a sala continua levantando MeetingConsensusError — não há
    consenso anterior para publicar. Aqui sterile_round_limit é alto o bastante para não abortar
    antes do teto, isolando o guard do teto."""
    config = {
        **_base_config(),
        "meeting_min_rounds": 99,
        "meeting_max_rounds": 3,
        "meeting_sterile_round_limit": 99,  # não abortar por sala estéril; chegar ao teto
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

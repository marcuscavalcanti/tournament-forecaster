import asyncio
from datetime import datetime, timezone

from worldcup_brazil.consensus import AgentOpinion, Consensus
from worldcup_brazil.pipeline import (
    build_report_bundle,
    _market_value_momentum_report,
    _market_value_momentum_signal,
    _market_value_player_weighted_delta_eur,
    _signals_for_match,
)
from worldcup_brazil.source_memory import SourceMemory


def test_market_value_weight_prefers_larger_nominal_gain_over_higher_percentage_gain() -> None:
    small_nominal_high_pct = {"player": "A", "old_value_eur": 10_000_000, "new_value_eur": 13_000_000}
    large_nominal_low_pct = {"player": "B", "old_value_eur": 50_000_000, "new_value_eur": 55_000_000}

    assert _market_value_player_weighted_delta_eur(large_nominal_low_pct) > _market_value_player_weighted_delta_eur(
        small_nominal_high_pct
    )


def test_market_value_momentum_signal_moves_probability_toward_stronger_weighted_team() -> None:
    config = {
        "market_value_momentum": {
            "enabled": True,
            "eur_per_probability_point": 10_000_000,
            "max_shift_pct": 3.0,
            "teams": {
                "Brasil": [
                    {"player": "Brasil A", "old_value_eur": 50_000_000, "new_value_eur": 55_000_000},
                ],
                "Marrocos": [
                    {"player": "Marrocos A", "old_value_eur": 10_000_000, "new_value_eur": 13_000_000},
                ],
            },
        }
    }

    signal = _market_value_momentum_signal(
        {"opponent": "Marrocos"},
        base_pct=60.0,
        config=config,
    )

    assert signal is not None
    assert signal.brazil_pct > 60.0
    assert "50.0M->55.0M" in signal.detail
    assert "10.0M->13.0M" in signal.detail
    assert "delta nominal" in signal.detail


def test_signals_for_match_includes_transfermarkt_market_value_momentum_when_configured() -> None:
    config = {
        "market_value_momentum": {
            "enabled": True,
            "teams": {
                "Brasil": [{"player": "Brasil A", "old_value_eur": 50_000_000, "new_value_eur": 55_000_000}],
                "Marrocos": [{"player": "Marrocos A", "old_value_eur": 10_000_000, "new_value_eur": 13_000_000}],
            },
        }
    }

    _statistical, qualitative = _signals_for_match(
        {"opponent": "Marrocos", "brazil_pct": 60.0},
        evidence=[],
        knockout=False,
        config=config,
    )

    assert any(signal.source == "Transfermarkt market value momentum" for signal in qualitative)


def test_market_value_momentum_report_summarizes_positive_players_and_weighted_delta() -> None:
    report = _market_value_momentum_report(
        {
            "market_value_momentum": {
                "enabled": True,
                "teams": {
                    "Brasil": [
                        {"player": "Brasil A", "old_value_eur": 50_000_000, "new_value_eur": 55_000_000},
                        {"player": "Brasil B", "old_value_eur": 10_000_000, "new_value_eur": 13_000_000},
                    ],
                    "Marrocos": [
                        {"player": "Marrocos A", "old_value_eur": 20_000_000, "new_value_eur": 18_000_000},
                    ],
                },
            }
        }
    )

    assert report["available"] is True
    assert report["teams"]["Brasil"]["positive_players"] == 2
    assert report["teams"]["Marrocos"]["positive_players"] == 0
    assert report["teams"]["Brasil"]["weighted_delta_eur"] > report["teams"]["Brasil"]["nominal_delta_eur"]


def test_build_report_bundle_carries_market_value_momentum_to_metadata_and_post(monkeypatch, tmp_path) -> None:
    async def fake_call_all_agents(*_args, **_kwargs):
        return [
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=11.0,
                summary="Planejei fontes com Transfermarkt, odds e Elo.",
                source_urls=["https://example.com/markets"],
            )
        ]

    async def fail_if_moderator_fetches_sources(*_args, **_kwargs):
        raise AssertionError("o mediador não deve fazer fetch central de fontes externas")

    async def fake_run_model_meeting(**_kwargs):
        opinion = AgentOpinion(agent="GPT 5.5", title_pct=11.0, summary="Consenso controlado.")
        consensus = Consensus(
            title_pct=11.0,
            agent_summaries={"GPT 5.5": "Consenso controlado."},
            dispersion_pct=0.0,
            raw_opinions=[opinion],
            debate_transcript=[],
            agent_slots=("GPT 5.5",),
        )
        transcript = [
            {
                "round": 1,
                "protagonist": "GPT 5.5",
                "question": "Transfermarkt muda o sinal contra Marrocos?",
                "responses": [
                    {
                        "agent": "GPT 5.5",
                        "answer": "Sim, mas como sinal limitado.",
                        "title_pct": 11.0,
                        "support_score": 0.9,
                    }
                ],
                "next_protagonist": "GPT 5.5",
                "consensus_title_pct": 11.0,
                "consensus_spread_pct": 0.0,
            }
        ]
        return consensus, [opinion], transcript, [opinion]

    monkeypatch.setattr("worldcup_brazil.pipeline.call_all_agents", fake_call_all_agents)
    monkeypatch.setattr(
        "worldcup_brazil.pipeline.fetch_sources_concurrently",
        fail_if_moderator_fetches_sources,
        raising=False,
    )
    monkeypatch.setattr("worldcup_brazil.pipeline._run_model_meeting", fake_run_model_meeting)

    artifacts = asyncio.run(
        build_report_bundle(
            config={
                "baseline_title_pct": 11.0,
                "custom_hashtag": "#CopaComAchismo",
                "minimum_source_ready_agents": 1,
                "meeting_min_participants": 1,
                "sources": [
                    {
                        "name": "Fonte teste",
                        "category": "statistical",
                        "url": "https://example.com/markets",
                        "confidence": 0.8,
                        "notes": "Fonte controlada.",
                    }
                ],
                "agents": [
                    {
                        "slot": "GPT 5.5",
                        "provider": "openai",
                        "model": "gpt-5.5",
                        "env_api_key": None,
                        "endpoint": "https://example.com/api",
                    }
                ],
                "group_matches": [{"opponent": "Marrocos", "brazil_pct": 60.0}],
                "knockout_matches": [],
                "market_value_momentum": {
                    "enabled": True,
                    "teams": {
                        "Brasil": [
                            {"player": "Brasil A", "old_value_eur": 50_000_000, "new_value_eur": 55_000_000}
                        ],
                        "Marrocos": [
                            {"player": "Marrocos A", "old_value_eur": 10_000_000, "new_value_eur": 13_000_000}
                        ],
                    },
                },
            },
            source_memory=SourceMemory(tmp_path / "source_memory.json"),
            generated_at=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
        )
    )

    momentum = artifacts.bundle.metadata["market_value_momentum"]
    assert momentum["available"] is True
    assert momentum["teams"]["Brasil"]["positive_players"] == 1
    assert artifacts.raw_evidence == []
    assert artifacts.bundle.metadata["mediator_role"]["external_fetch"] is False
    assert artifacts.bundle.metadata["selected_sources"] == []
    assert artifacts.bundle.sources == ["GPT 5.5: https://example.com/markets"]
    assert artifacts.bundle.opta_benchmark == {}
    assert artifacts.bundle.model_vs_opta == {}
    assert "Destaques de valorização (Transfermarkt):" in artifacts.post
    assert "Opta" not in artifacts.post

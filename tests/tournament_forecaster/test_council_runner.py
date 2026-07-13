from __future__ import annotations

import json
from collections.abc import Mapping

from tournament_forecaster.council.config import CouncilAgentConfig, CouncilConfig
from tournament_forecaster.council.providers import CouncilProviderError, ProviderResponse
from tournament_forecaster.council.runner import run_council
from tournament_forecaster.domain import Forecast, Tournament
from tournament_forecaster.resources import load_bundled_preset


def _forecast() -> Forecast:
    return Forecast(
        run_id="run-baseline",
        generated_at="2026-07-13T12:00:00+00:00",
        tournament_id="synthetic-cup",
        focus_team_id="north-city",
        stage_probabilities={"group-stage": 1.0, "semi-finals": 0.6, "final": 0.3},
        stage_order=("group-stage", "semi-finals", "final"),
        matchup_probabilities=(),
        championship_probability=0.15,
        confidence_intervals={
            "group-stage": (1.0, 1.0),
            "semi-finals": (0.55, 0.65),
            "final": (0.25, 0.35),
            "championship_probability": (0.12, 0.18),
        },
        input_provenance=({"kind": "tournament", "source_id": "synthetic-cup"},),
        warnings=(),
        tournament_display_name="Synthetic Cup",
        team_display_names={"north-city": "North City"},
        simulation={"seed": 7, "iterations": 1000, "confidence_level": 0.95},
    )


def _agent(agent_id: str) -> CouncilAgentConfig:
    return CouncilAgentConfig(
        id=agent_id,
        display_name=agent_id.title(),
        provider="openai-compatible",
        model=f"{agent_id}-model",
        api_key_env=f"{agent_id.replace('-', '_').upper()}_API_KEY",
        endpoint="https://provider.example/v1",
    )


def _config(*, minimum_valid_agents: int = 2) -> CouncilConfig:
    return CouncilConfig(
        enabled=True,
        engine_weight=0.55,
        council_weight=0.45,
        rounds=2,
        minimum_valid_agents=minimum_valid_agents,
        timeout_seconds=30,
        max_attempts=1,
        agents=(_agent("agent-a"), _agent("agent-b"), _agent("agent-c")),
    )


def _opinion(stages: Mapping[str, float], championship: float, summary: str) -> str:
    return json.dumps(
        {
            "stage_probabilities": dict(stages),
            "championship_probability": championship,
            "confidence": 0.8,
            "summary": summary,
            "key_factors": ["availability", "path strength"],
        }
    )


def test_runs_independent_then_anonymized_debrief_and_uses_median_consensus() -> None:
    prompts: list[tuple[str, str]] = []
    values = {
        "agent-a": (0.70, 0.40, 0.20),
        "agent-b": (0.65, 0.35, 0.18),
        "agent-c": (0.60, 0.30, 0.15),
    }

    def caller(agent: CouncilAgentConfig, prompt: str) -> ProviderResponse:
        prompts.append((agent.id, prompt))
        semi, final, championship = values[agent.id]
        return ProviderResponse(
            text=_opinion(
                {"group-stage": 1.0, "semi-finals": semi, "final": final},
                championship,
                f"Final view from {agent.id}",
            ),
            runtime_model=agent.model,
        )

    result = run_council(
        _forecast(),
        load_bundled_preset("synthetic-cup"),
        _config(),
        caller=caller,
    )

    assert result.status == "consensus"
    assert result.consensus is not None
    assert dict(result.consensus.stage_probabilities) == {
        "group-stage": 1.0,
        "semi-finals": 0.65,
        "final": 0.35,
    }
    assert result.consensus.championship_probability == 0.18
    assert len(result.rounds) == 2
    assert [len(round_result.opinions) for round_result in result.rounds] == [3, 3]
    round_one = [prompt for _, prompt in prompts if "DEBATE ROUND 1" in prompt]
    round_two = [prompt for _, prompt in prompts if "DEBATE ROUND 2" in prompt]
    assert len(round_one) == 3
    assert len(round_two) == 3
    assert all("peer positions" not in prompt.casefold() for prompt in round_one)
    assert all("Position 1" in prompt and "Position 3" in prompt for prompt in round_two)
    assert all("agent-a" not in prompt and "agent-b" not in prompt for prompt in round_two)


def test_invalid_or_failed_agents_do_not_gain_weight_and_below_quorum_falls_back() -> None:
    def caller(agent: CouncilAgentConfig, _prompt: str) -> ProviderResponse:
        if agent.id == "agent-a":
            return ProviderResponse(
                text=_opinion(
                    {"group-stage": 1.0, "semi-finals": 0.62, "final": 0.31},
                    0.16,
                    "Only valid position",
                ),
                runtime_model=agent.model,
            )
        if agent.id == "agent-b":
            raise CouncilProviderError("quota", "credits depleted")
        return ProviderResponse(text="not JSON", runtime_model=agent.model)

    result = run_council(
        _forecast(),
        load_bundled_preset("synthetic-cup"),
        _config(minimum_valid_agents=2),
        caller=caller,
    )

    assert result.status == "fallback"
    assert result.consensus is None
    assert result.reason == "round 1 produced 1 valid opinion(s); 2 required"
    assert len(result.rounds) == 1
    failures = {failure.agent_id: failure.category for failure in result.rounds[0].failures}
    assert failures == {"agent-b": "quota", "agent-c": "invalid_response"}


def test_prompts_include_engine_facts_and_legal_matchups_without_raw_secrets() -> None:
    tournament: Tournament = load_bundled_preset("synthetic-cup")
    observed = ""

    def caller(agent: CouncilAgentConfig, prompt: str) -> ProviderResponse:
        nonlocal observed
        observed = prompt
        return ProviderResponse(
            text=_opinion(
                {"group-stage": 1.0, "semi-finals": 0.6, "final": 0.3},
                0.15,
                "Baseline retained",
            ),
            runtime_model=agent.model,
        )

    one_agent = CouncilConfig(
        enabled=True,
        engine_weight=0.55,
        council_weight=0.45,
        rounds=1,
        minimum_valid_agents=1,
        timeout_seconds=30,
        max_attempts=1,
        agents=(_agent("agent-a"),),
    )
    result = run_council(_forecast(), tournament, one_agent, caller=caller)

    assert result.status == "consensus"
    assert "Synthetic Cup" in observed
    assert "North City" in observed
    assert '"championship_probability": 0.15' in observed
    assert "PROVIDER_API_KEY" not in observed
    assert "provider.example" not in observed

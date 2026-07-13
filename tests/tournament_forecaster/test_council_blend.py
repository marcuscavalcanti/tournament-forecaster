from __future__ import annotations

import pytest

from tournament_forecaster.council.blend import apply_council
from tournament_forecaster.council.config import CouncilAgentConfig, CouncilConfig
from tournament_forecaster.council.models import CouncilOpinion
from tournament_forecaster.council.runner import CouncilRun
from tournament_forecaster.domain import Forecast, MatchupProbability


def _config() -> CouncilConfig:
    return CouncilConfig(
        enabled=True,
        engine_weight=0.55,
        council_weight=0.45,
        rounds=2,
        minimum_valid_agents=2,
        timeout_seconds=30,
        max_attempts=1,
        agents=(
            CouncilAgentConfig(
                id="agent-a",
                display_name="Agent A",
                provider="openai",
                model="model-a",
                api_key_env="A_API_KEY",
                endpoint="https://api.openai.com/v1/responses",
                reasoning_effort="high",
            ),
            CouncilAgentConfig(
                id="agent-b",
                display_name="Agent B",
                provider="anthropic",
                model="model-b",
                api_key_env="B_API_KEY",
                endpoint="https://api.anthropic.com/v1/messages",
                thinking_budget_tokens=4096,
            ),
        ),
    )


def _forecast() -> Forecast:
    return Forecast(
        run_id="run-baseline",
        generated_at="2026-07-13T12:00:00+00:00",
        tournament_id="sample-cup",
        focus_team_id="focus-team",
        stage_probabilities={"group-stage": 1.0, "semi-finals": 0.6, "final": 0.3},
        stage_order=("group-stage", "semi-finals", "final"),
        matchup_probabilities=(
            MatchupProbability("semi-finals", "other-team", 0.4),
        ),
        championship_probability=0.15,
        confidence_intervals={
            "group-stage": (1.0, 1.0),
            "semi-finals": (0.55, 0.65),
            "final": (0.25, 0.35),
            "championship_probability": (0.12, 0.18),
        },
        input_provenance=({"kind": "tournament", "source_id": "sample-cup"},),
        warnings=(),
        tournament_display_name="Sample Cup",
        team_display_names={"focus-team": "Focus Team", "other-team": "Other Team"},
        simulation={"seed": 1, "iterations": 1000, "confidence_level": 0.95},
    )


def _consensus() -> CouncilOpinion:
    return CouncilOpinion(
        agent_id="consensus",
        round_number=2,
        stage_probabilities={"group-stage": 1.0, "semi-finals": 0.7, "final": 0.4},
        championship_probability=0.2,
        confidence=0.8,
        summary="Median consensus from two agents.",
        key_factors=("availability", "path strength"),
    )


def test_applies_exact_55_45_blend_and_preserves_engine_owned_matchups() -> None:
    run = CouncilRun(
        status="consensus",
        rounds=(),
        consensus=_consensus(),
        reason=None,
    )

    blended = apply_council(_forecast(), _config(), run)

    assert blended.run_id != "run-baseline"
    assert dict(blended.stage_probabilities) == {
        "group-stage": 1.0,
        "semi-finals": 0.645,
        "final": 0.34500000000000003,
    }
    assert blended.championship_probability == 0.17250000000000001
    assert blended.confidence_intervals["semi-finals"] == pytest.approx(
        (0.6175, 0.6725)
    )
    assert blended.confidence_intervals["championship_probability"] == pytest.approx(
        (0.156, 0.189)
    )
    assert blended.matchup_probabilities == _forecast().matchup_probabilities
    assert blended.council is not None
    assert blended.council["status"] == "applied"
    assert blended.council["engine_weight"] == 0.55
    assert blended.council["council_weight"] == 0.45
    assert blended.council["matchup_probabilities_basis"] == "engine_only"
    serialized = str(blended.to_dict())
    assert "A_API_KEY" not in serialized
    assert "B_API_KEY" not in serialized
    assert "api.openai.com" not in serialized


def test_failed_council_preserves_every_engine_probability_and_records_reason() -> None:
    baseline = _forecast()
    run = CouncilRun(
        status="fallback",
        rounds=(),
        consensus=None,
        reason="1 valid opinion; 2 required",
    )

    result = apply_council(baseline, _config(), run)

    assert result.stage_probabilities == baseline.stage_probabilities
    assert result.championship_probability == baseline.championship_probability
    assert result.confidence_intervals == baseline.confidence_intervals
    assert result.matchup_probabilities == baseline.matchup_probabilities
    assert result.council is not None
    assert result.council["status"] == "fallback"
    assert result.council["reason"] == "1 valid opinion; 2 required"
    assert any("deterministic engine baseline" in warning for warning in result.warnings)

"""Apply a validated council consensus to a deterministic Forecast."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

from ..domain import Forecast
from .config import CouncilConfig
from .runner import CouncilRun


def _participant(config: CouncilConfig, agent_id: str) -> dict[str, object]:
    agent = next(agent for agent in config.agents if agent.id == agent_id)
    return {
        "id": agent.id,
        "display_name": agent.display_name,
        "provider": agent.provider,
        "model": agent.model,
        "reasoning_effort": agent.reasoning_effort,
        "thinking_budget_tokens": agent.thinking_budget_tokens,
        "max_output_tokens": agent.max_output_tokens,
    }


def _metadata(
    baseline: Forecast,
    config: CouncilConfig,
    run: CouncilRun,
    *,
    status: str,
) -> dict[str, object]:
    participant_ids = {
        opinion.agent_id
        for round_result in run.rounds
        for opinion in round_result.opinions
    } | {
        failure.agent_id
        for round_result in run.rounds
        for failure in round_result.failures
    }
    return {
        "status": status,
        "reason": run.reason,
        "engine_weight": config.engine_weight,
        "council_weight": config.council_weight,
        "rounds_configured": config.rounds,
        "rounds_completed": len(run.rounds),
        "minimum_valid_agents": config.minimum_valid_agents,
        "participants": [
            _participant(config, agent.id)
            for agent in config.agents
            if agent.id in participant_ids
        ],
        "engine_baseline": {
            "stage_probabilities": dict(baseline.stage_probabilities),
            "championship_probability": baseline.championship_probability,
        },
        "consensus": run.consensus.to_dict() if run.consensus else None,
        "rounds": [round_result.to_dict() for round_result in run.rounds],
        "matchup_probabilities_basis": "engine_only",
        "uncertainty_basis": "engine_sampling_with_fixed_council_consensus",
    }


def _derived_run_id(
    baseline: Forecast,
    config: CouncilConfig,
    run: CouncilRun,
) -> str:
    payload = {
        "baseline_run_id": baseline.run_id,
        "engine_weight": config.engine_weight,
        "council_weight": config.council_weight,
        "run": run.to_dict(),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return f"run-{digest[:16]}"


def _conditional_intervals(
    baseline: Forecast,
    config: CouncilConfig,
    consensus_stages: dict[str, float],
    consensus_championship: float,
) -> dict[str, tuple[float, float]]:
    intervals: dict[str, tuple[float, float]] = {}
    for stage_id, bounds in baseline.confidence_intervals.items():
        council_probability = (
            consensus_championship
            if stage_id == "championship_probability"
            else consensus_stages.get(stage_id)
        )
        if council_probability is None:
            intervals[stage_id] = (float(bounds[0]), float(bounds[1]))
            continue
        intervals[stage_id] = (
            config.engine_weight * bounds[0]
            + config.council_weight * council_probability,
            config.engine_weight * bounds[1]
            + config.council_weight * council_probability,
        )
    return intervals


def apply_council(
    baseline: Forecast,
    config: CouncilConfig,
    run: CouncilRun,
) -> Forecast:
    """Return a blended forecast or an auditable deterministic fallback."""

    run_id = _derived_run_id(baseline, config, run)
    generated_at = datetime.now(UTC).isoformat()
    if run.status == "disabled":
        return replace(
            baseline,
            run_id=run_id,
            generated_at=generated_at,
            council=_metadata(baseline, config, run, status="disabled"),
        )
    if run.status != "consensus" or run.consensus is None:
        reason = run.reason or "council did not produce a usable consensus"
        return replace(
            baseline,
            run_id=run_id,
            generated_at=generated_at,
            warnings=baseline.warnings
            + (
                f"Council fallback: {reason}; published the deterministic engine baseline.",
            ),
            council=_metadata(baseline, config, run, status=run.status),
        )
    consensus = run.consensus
    stages = {
        stage_id: config.engine_weight * baseline.stage_probabilities[stage_id]
        + config.council_weight * consensus.stage_probabilities[stage_id]
        for stage_id in baseline.stage_order
    }
    championship = (
        config.engine_weight * baseline.championship_probability
        + config.council_weight * consensus.championship_probability
    )
    valid_agent_ids = {
        opinion.agent_id for opinion in run.rounds[-1].opinions
    } if run.rounds else set()
    provenance = baseline.input_provenance + tuple(
        {
            "kind": "council_model",
            "source_id": agent.id,
            "name": agent.display_name,
            "source": agent.provider,
            "metadata": {"model": agent.model},
        }
        for agent in config.agents
        if agent.id in valid_agent_ids
    )
    return replace(
        baseline,
        run_id=run_id,
        generated_at=generated_at,
        stage_probabilities=stages,
        championship_probability=championship,
        confidence_intervals=_conditional_intervals(
            baseline,
            config,
            dict(consensus.stage_probabilities),
            consensus.championship_probability,
        ),
        input_provenance=provenance,
        warnings=baseline.warnings
        + (
            "Council-adjusted probabilities use 55% deterministic engine and 45% "
            "multi-model consensus.",
            "Matchup probabilities remain deterministic engine output.",
        ),
        council=_metadata(baseline, config, run, status="applied"),
    )

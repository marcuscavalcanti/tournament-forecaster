"""Two-pass, quorum-gated multi-model tournament debriefing."""

from __future__ import annotations

import statistics
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from ..domain import Forecast, Tournament
from ..errors import TournamentValidationError
from .config import CouncilAgentConfig, CouncilConfig
from .models import CouncilOpinion, parse_opinion
from .prompts import build_council_prompt
from .providers import CouncilProviderError, ProviderResponse, call_configured_agent


AgentCaller = Callable[[CouncilAgentConfig, str], ProviderResponse]


@dataclass(frozen=True, slots=True)
class AgentFailure:
    """One classified agent failure in one council round."""

    agent_id: str
    category: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "agent_id": self.agent_id,
            "category": self.category,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class CouncilRound:
    """Deterministically ordered valid and invalid outcomes for one round."""

    round_number: int
    opinions: tuple[CouncilOpinion, ...]
    failures: tuple[AgentFailure, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "round": self.round_number,
            "opinions": [opinion.to_dict() for opinion in self.opinions],
            "failures": [failure.to_dict() for failure in self.failures],
        }


@dataclass(frozen=True, slots=True)
class CouncilRun:
    """A usable consensus or a safe fallback decision."""

    status: str
    rounds: tuple[CouncilRound, ...]
    consensus: CouncilOpinion | None
    reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "rounds": [round_result.to_dict() for round_result in self.rounds],
            "consensus": self.consensus.to_dict() if self.consensus else None,
            "reason": self.reason,
        }


def _locked_stages(forecast: Forecast) -> dict[str, float]:
    return {
        stage_id: probability
        for stage_id, probability in forecast.stage_probabilities.items()
        if probability in {0.0, 1.0}
    }


def _one_round(
    forecast: Forecast,
    tournament: Tournament,
    *,
    round_number: int,
    agents: tuple[CouncilAgentConfig, ...],
    peer_opinions: tuple[CouncilOpinion, ...],
    caller: AgentCaller,
) -> CouncilRound:
    prompt = build_council_prompt(
        forecast,
        tournament,
        round_number=round_number,
        peer_opinions=peer_opinions,
    )
    responses: dict[str, ProviderResponse] = {}
    failures: dict[str, AgentFailure] = {}
    with ThreadPoolExecutor(max_workers=len(agents)) as executor:
        futures = {executor.submit(caller, agent, prompt): agent for agent in agents}
        for future in as_completed(futures):
            agent = futures[future]
            try:
                responses[agent.id] = future.result()
            except CouncilProviderError as error:
                failures[agent.id] = AgentFailure(
                    agent.id,
                    error.category,
                    error.detail,
                )
    opinions: dict[str, CouncilOpinion] = {}
    for agent in agents:
        response = responses.get(agent.id)
        if response is None:
            continue
        try:
            opinions[agent.id] = parse_opinion(
                response.text,
                agent_id=agent.id,
                round_number=round_number,
                stage_order=forecast.stage_order,
                locked_stage_probabilities=_locked_stages(forecast),
            )
        except TournamentValidationError as error:
            failures[agent.id] = AgentFailure(
                agent.id,
                "invalid_response",
                str(error)[:600],
            )
    return CouncilRound(
        round_number=round_number,
        opinions=tuple(opinions[agent.id] for agent in agents if agent.id in opinions),
        failures=tuple(failures[agent.id] for agent in agents if agent.id in failures),
    )


def _consensus(opinions: tuple[CouncilOpinion, ...]) -> CouncilOpinion:
    first = opinions[0]
    stage_probabilities = {
        stage_id: float(
            statistics.median(
                opinion.stage_probabilities[stage_id] for opinion in opinions
            )
        )
        for stage_id in first.stage_probabilities
    }
    factors: list[str] = []
    for opinion in opinions:
        for factor in opinion.key_factors:
            if factor not in factors:
                factors.append(factor)
    return CouncilOpinion(
        agent_id="consensus",
        round_number=first.round_number,
        stage_probabilities=stage_probabilities,
        championship_probability=float(
            statistics.median(
                opinion.championship_probability for opinion in opinions
            )
        ),
        confidence=float(statistics.median(opinion.confidence for opinion in opinions)),
        summary=f"Median consensus from {len(opinions)} valid council participants.",
        key_factors=tuple(factors[:12]),
    )


def run_council(
    forecast: Forecast,
    tournament: Tournament,
    config: CouncilConfig,
    *,
    caller: AgentCaller | None = None,
) -> CouncilRun:
    """Run configured rounds and return consensus only after final-round quorum."""

    if not config.enabled:
        return CouncilRun("disabled", (), None, "council disabled by configuration")
    if caller is None:
        def configured_caller(
            agent: CouncilAgentConfig,
            prompt: str,
        ) -> ProviderResponse:
            return call_configured_agent(
                agent,
                prompt,
                timeout_seconds=config.timeout_seconds,
                max_attempts=config.max_attempts,
            )

        caller = configured_caller
    active_agents = config.enabled_agents
    peer_opinions: tuple[CouncilOpinion, ...] = ()
    rounds: list[CouncilRound] = []
    for round_number in range(1, config.rounds + 1):
        round_result = _one_round(
            forecast,
            tournament,
            round_number=round_number,
            agents=active_agents,
            peer_opinions=peer_opinions,
            caller=caller,
        )
        rounds.append(round_result)
        valid_count = len(round_result.opinions)
        if valid_count < config.minimum_valid_agents:
            return CouncilRun(
                "fallback",
                tuple(rounds),
                None,
                f"round {round_number} produced {valid_count} valid opinion(s); "
                f"{config.minimum_valid_agents} required",
            )
        valid_ids = {opinion.agent_id for opinion in round_result.opinions}
        active_agents = tuple(
            agent for agent in active_agents if agent.id in valid_ids
        )
        peer_opinions = round_result.opinions
    return CouncilRun(
        "consensus",
        tuple(rounds),
        _consensus(peer_opinions),
        None,
    )

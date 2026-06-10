from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable


REQUIRED_AGENT_SLOTS = (
    "Opus 4.8",
    "GPT 5.5",
    "Perplexity Pro",
    "DeepSeek V4 Pro",
    "Gemini Pro",
)

AGENT_WEIGHTS = {
    "Opus 4.8": 1.0,
    "GPT 5.5": 1.0,
    "Perplexity Pro": 1.0,
    "DeepSeek V4 Pro": 1.0,
    "Gemini Pro": 1.0,
}


def _looks_removed_or_unusable(opinion: AgentOpinion) -> bool:
    if bool(getattr(opinion, "removed_from_main", False)):
        return True
    text = " ".join(
        str(value or "")
        for value in (
            opinion.summary,
            opinion.answer,
            opinion.opening_argument,
            opinion.critique,
            opinion.adjustment,
        )
    ).lower()
    return any(
        marker in text
        for marker in (
            "resposta removida do modelo principal",
            "resposta removida da sala principal",
            "resposta removida do planejamento",
            "sem resposta parseável",
            "sem resposta parseavel",
            "sem resposta externa utilizável",
            "sem resposta externa utilizavel",
            "falha operacional sem resposta externa",
            "busca/fetch externo indisponível",
            "busca/fetch externo indisponivel",
            "permissao nao concedida",
            "permissão não concedida",
            "permission denied",
            "não há ferramenta de busca",
            "nao ha ferramenta de busca",
            "sem ferramenta de busca externa",
            "não consigo confirmar páginas em tempo real",
            "nao consigo confirmar paginas em tempo real",
            "não posso confirmar páginas em tempo real",
            "nao posso confirmar paginas em tempo real",
            "no external search",
            "no websearch",
            "no fetch tool",
        )
    )


def _source_count(opinion: AgentOpinion) -> int:
    return len(opinion.source_urls or []) + len(opinion.source_queries or [])


def _consensus_weight(agent: str, opinion: AgentOpinion) -> float:
    if _looks_removed_or_unusable(opinion):
        return 0.0
    if opinion.used_fallback and _source_count(opinion) == 0:
        return 0.0
    return float(AGENT_WEIGHTS.get(agent, 1.0))


@dataclass(frozen=True)
class AgentOpinion:
    agent: str
    title_pct: float
    summary: str
    opening_argument: str = ""
    question: str = ""
    answer: str = ""
    critique: str = ""
    adjustment: str = ""
    source_urls: list[str] = None
    source_queries: list[str] = None
    match_probabilities: dict[str, float] = None
    scenario_probabilities: dict[str, float] = None
    team_context_signals: list[dict] = None
    agrees_with_protagonist: bool | None = None
    leadership_bid: bool = False
    proposed_next_question: str = ""
    leadership_rationale: str = ""
    consensus_check_question: str = ""
    self_declared_name: str = ""
    self_declared_version: str = ""
    raw_text: str = ""
    used_fallback: bool = False
    removed_from_main: bool = False
    removal_reason: str = ""

    def __post_init__(self) -> None:
        if self.source_urls is None:
            object.__setattr__(self, "source_urls", [])
        if self.source_queries is None:
            object.__setattr__(self, "source_queries", [])
        if self.match_probabilities is None:
            object.__setattr__(self, "match_probabilities", {})
        if self.scenario_probabilities is None:
            object.__setattr__(self, "scenario_probabilities", {})
        if self.team_context_signals is None:
            object.__setattr__(self, "team_context_signals", [])


@dataclass(frozen=True)
class Consensus:
    title_pct: float
    agent_summaries: dict[str, str]
    dispersion_pct: float
    raw_opinions: list[AgentOpinion]
    debate_transcript: list[str]
    agent_slots: tuple[str, ...] = REQUIRED_AGENT_SLOTS


def _build_debate_transcript(
    by_agent: dict[str, AgentOpinion],
    *,
    agent_slots: tuple[str, ...],
    title_pct: float,
    dispersion_pct: float,
) -> list[str]:
    lines: list[str] = []
    for agent in agent_slots:
        opinion = by_agent[agent]
        argument = opinion.opening_argument or opinion.summary
        lines.append(f"Rodada 1 - {agent}: {argument} Projeção de título: {opinion.title_pct:.1f}%.")

    for agent in agent_slots:
        opinion = by_agent[agent]
        critique = opinion.critique or "Sem objeção adicional além da incerteza estatística e do risco de chave."
        lines.append(f"Rodada 2 - {agent}: {critique}")

    adjustment_lines = []
    for agent in agent_slots:
        opinion = by_agent[agent]
        if opinion.adjustment:
            adjustment_lines.append(f"{agent}: {opinion.adjustment}")
    if adjustment_lines:
        lines.append("Rodada 3 - Ajustes após crítica cruzada: " + " | ".join(adjustment_lines))

    lines.append(
        f"Consenso: média ponderada truncada por peso de confiabilidade dos slots, "
        f"com título em {title_pct:.1f}% e dispersão entre modelos de {dispersion_pct:.1f} p.p."
    )
    return lines


def build_consensus(
    opinions: Iterable[AgentOpinion],
    *,
    agent_slots: Iterable[str] | None = None,
) -> Consensus:
    opinion_list = list(opinions)
    slots = tuple(agent_slots or REQUIRED_AGENT_SLOTS)
    by_agent = {opinion.agent: opinion for opinion in opinion_list}
    missing = [agent for agent in slots if agent not in by_agent]
    extra = [opinion.agent for opinion in opinion_list if opinion.agent not in slots]
    if missing or extra or len(opinion_list) != len(slots):
        raise ValueError(
            f"build_consensus requires exactly {len(slots)} agent opinions: "
            + ", ".join(slots)
        )

    numerator = 0.0
    denominator = 0.0
    voting_title_values: list[float] = []
    for agent in slots:
        weight = _consensus_weight(agent, by_agent[agent])
        if weight <= 0.0:
            continue
        numerator += by_agent[agent].title_pct * weight
        denominator += weight
        voting_title_values.append(by_agent[agent].title_pct)

    if denominator <= 0.0:
        title_values = [by_agent[agent].title_pct for agent in slots]
        title_pct = round(sum(title_values) / len(title_values), 1)
        voting_title_values = title_values
    else:
        title_pct = round(numerator / denominator, 1)
    title_values = voting_title_values
    dispersion_pct = round(max(title_values) - min(title_values), 1)

    # Keep a robust central tendency available in the object for downstream
    # inspection without changing the headline weighted consensus.
    _ = median(title_values)

    debate_transcript = _build_debate_transcript(
        by_agent,
        agent_slots=slots,
        title_pct=title_pct,
        dispersion_pct=dispersion_pct,
    )

    return Consensus(
        title_pct=title_pct,
        agent_summaries={agent: by_agent[agent].summary for agent in slots},
        dispersion_pct=dispersion_pct,
        raw_opinions=[by_agent[agent] for agent in slots],
        debate_transcript=debate_transcript,
        agent_slots=slots,
    )

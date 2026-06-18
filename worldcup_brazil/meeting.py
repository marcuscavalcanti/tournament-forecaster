from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class MeetingResponse:
    agent: str
    answer: str
    title_pct: float | None
    support_score: float
    title_pct_source: str = "explicit"
    source_count: int = 0
    accepted: bool = False
    disagreed: bool = False
    used_fallback: bool = False
    removed_from_main: bool = False
    removal_reason: str = ""
    leadership_bid: bool = False
    proposed_next_question: str = ""
    leadership_rationale: str = ""
    consensus_check_question: str = ""
    match_probabilities: dict[str, float] = None
    scenario_probabilities: dict[str, float] = None
    validation_issues: list[dict[str, Any]] = None


def _support_score(
    title_pct: float | None,
    consensus_title_pct: float,
    *,
    used_fallback: bool,
    has_critique: bool,
    has_adjustment: bool,
    source_count: int,
    answer_length: int,
) -> float:
    distance = abs((consensus_title_pct if title_pct is None else title_pct) - consensus_title_pct)
    score = max(0.05, 1.0 - distance / 35.0)
    if has_critique:
        score += 0.16
    if has_adjustment:
        score += 0.14
    if source_count:
        score += min(0.18, source_count * 0.04)
    if answer_length >= 500:
        score += 0.08
    if used_fallback and source_count == 0:
        score *= 0.35
    return round(min(score, 1.0), 3)


def _looks_like_disagreement(text: str) -> bool:
    lowered = text.lower()
    disagreement_markers = (
        "discordo",
        "não concordo",
        "nao concordo",
        "contesto",
        "não aceito",
        "nao aceito",
        "rejeito",
        "premissa errada",
        "racional errado",
    )
    return any(marker in lowered for marker in disagreement_markers)


def _looks_unusable(text: str) -> bool:
    lowered = text.strip().lower()
    unusable_markers = (
        "resposta em json parcial",
        "sem resposta parseável",
        "sem resposta parseavel",
        "modelo não participa do consenso",
        "modelo nao participa do consenso",
        "falha operacional sem resposta externa",
        "resposta removida da sala principal",
        "resposta removida do modelo principal",
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
    return any(marker in lowered for marker in unusable_markers)


def _source_count_from_opinion(opinion: Any) -> int:
    return len(getattr(opinion, "source_urls", []) or []) + len(getattr(opinion, "source_queries", []) or [])


def _opinion_text(opinion: Any) -> str:
    return " ".join(
        str(getattr(opinion, attr, "") or "")
        for attr in ("answer", "summary", "critique", "adjustment")
    )


def _counts_as_consensus_participant(opinion: Any) -> bool:
    if bool(getattr(opinion, "removed_from_main", False)):
        return False
    used_fallback = bool(getattr(opinion, "used_fallback", False))
    source_count = _source_count_from_opinion(opinion)
    if _looks_unusable(_opinion_text(opinion)):
        return False
    return not used_fallback or source_count > 0


def _turn_response_counts_for_acceptance(response: dict[str, Any]) -> bool:
    if bool(response.get("removed_from_main", False)):
        return False
    if _looks_unusable(str(response.get("answer", ""))):
        return False
    if bool(response.get("used_fallback", False)) and int(response.get("source_count", 0) or 0) <= 0:
        return False
    return True


def _meeting_response_counts_for_spread(response: MeetingResponse) -> bool:
    if response.removed_from_main:
        return False
    if _looks_unusable(response.answer):
        return False
    if response.used_fallback and response.source_count <= 0:
        return False
    return True


def _response_acceptance(
    opinion: Any,
    *,
    support_score: float,
    used_fallback: bool,
    source_count: int,
) -> tuple[bool, bool]:
    explicit = getattr(opinion, "agrees_with_protagonist", None)
    answer_text = " ".join(
        str(getattr(opinion, attr, "") or "")
        for attr in ("answer", "summary", "critique", "adjustment")
    )
    if bool(getattr(opinion, "removed_from_main", False)):
        return False, False
    if _looks_unusable(answer_text):
        return False, False
    if used_fallback and source_count == 0:
        return False, False
    if _looks_like_disagreement(answer_text):
        return False, True
    if explicit is True:
        return True, False
    if explicit is False:
        return False, True
    accepted = support_score >= 0.72
    return accepted, not accepted


def _default_consensus_check_question(agent: str) -> str:
    return (
        f"{agent}: os demais modelos concordam integralmente com esta opinião e aceitam sair com consenso "
        "para avançar para as próximas etapas?"
    )


def _answer_with_consensus_check(answer: str, question: str) -> str:
    answer = str(answer or "").strip()
    question = str(question or "").strip()
    if not question:
        return answer
    if question in answer:
        return answer
    separator = "\n" if answer else ""
    return f"{answer}{separator}Pergunta de consenso: {question}"


def choose_next_protagonist(
    responses: Iterable[MeetingResponse],
    *,
    current_protagonist: str,
    protagonist_counts: dict[str, int] | None = None,
) -> str:
    response_list = list(responses)
    eligible = [
        response
        for response in response_list
        if not response.removed_from_main and (not response.used_fallback or response.source_count > 0)
    ]
    dissenters = [response for response in eligible if response.disagreed]
    if not dissenters:
        protagonist_counts = protagonist_counts or {}
        merit_bidders = [
            response
            for response in eligible
            if response.accepted
            and response.leadership_bid
            and response.agent != current_protagonist
            and response.support_score >= 0.72
        ]
        if not merit_bidders:
            return current_protagonist
        winner = max(
            merit_bidders,
            key=lambda response: (
                response.support_score,
                response.source_count,
                len(response.answer),
                -protagonist_counts.get(response.agent, 0),
                response.agent,
            ),
        )
        return winner.agent
    protagonist_counts = protagonist_counts or {}
    winner = max(
        dissenters,
        key=lambda response: (
            protagonist_counts.get(response.agent, 0),
            response.support_score,
            len(response.answer),
            response.agent != current_protagonist,
        ),
    )
    return winner.agent


def build_meeting_turn(
    *,
    round_index: int,
    protagonist: str,
    question: str,
    opinions: list[Any],
    consensus_title_pct: float,
    protagonist_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    responses: list[MeetingResponse] = []
    for opinion in opinions:
        used_fallback = bool(getattr(opinion, "used_fallback", False))
        removed_from_main = bool(getattr(opinion, "removed_from_main", False))
        source_count = _source_count_from_opinion(opinion)
        raw_title_pct = getattr(opinion, "title_pct", None)
        try:
            numeric_title_pct = float(raw_title_pct) if raw_title_pct is not None else None
        except (TypeError, ValueError):
            numeric_title_pct = None
        raw_answer = getattr(opinion, "answer", "") or opinion.summary
        consensus_check_question = ""
        if not removed_from_main and (not used_fallback or source_count > 0):
            consensus_check_question = (
                str(getattr(opinion, "consensus_check_question", "") or "").strip()
                or _default_consensus_check_question(str(getattr(opinion, "agent", "") or "Modelo"))
            )
        answer = _answer_with_consensus_check(raw_answer, consensus_check_question)
        support_score = _support_score(
            numeric_title_pct,
            consensus_title_pct,
            used_fallback=used_fallback,
            has_critique=bool(getattr(opinion, "critique", "")),
            has_adjustment=bool(getattr(opinion, "adjustment", "")),
            source_count=source_count,
            answer_length=len(answer),
        )
        accepted, disagreed = _response_acceptance(
            opinion,
            support_score=support_score,
            used_fallback=used_fallback,
            source_count=source_count,
        )
        title_pct_source = str(getattr(opinion, "title_pct_source", "") or "explicit")
        effective_title_pct = numeric_title_pct
        if (
            effective_title_pct is None
            and accepted
            and not disagreed
            and not removed_from_main
            and (not used_fallback or source_count > 0)
        ):
            effective_title_pct = float(consensus_title_pct)
            title_pct_source = "inherited_from_current_consensus"
        responses.append(
            MeetingResponse(
                agent=opinion.agent,
                answer=answer,
                title_pct=round(effective_title_pct, 1) if effective_title_pct is not None else None,
                title_pct_source=title_pct_source,
                support_score=support_score,
                source_count=source_count,
                accepted=accepted,
                disagreed=disagreed,
                used_fallback=used_fallback,
                removed_from_main=removed_from_main,
                removal_reason=str(getattr(opinion, "removal_reason", "") or ""),
                leadership_bid=bool(getattr(opinion, "leadership_bid", False))
                and (not used_fallback or source_count > 0)
                and not removed_from_main,
                proposed_next_question=str(getattr(opinion, "proposed_next_question", "") or ""),
                leadership_rationale=str(getattr(opinion, "leadership_rationale", "") or ""),
                consensus_check_question=consensus_check_question,
                match_probabilities=dict(getattr(opinion, "match_probabilities", {}) or {}),
                scenario_probabilities=dict(getattr(opinion, "scenario_probabilities", {}) or {}),
                validation_issues=list(getattr(opinion, "validation_issues", []) or []),
            )
        )

    next_protagonist = choose_next_protagonist(
        responses,
        current_protagonist=protagonist,
        protagonist_counts=protagonist_counts,
    )
    values = [
        float(response.title_pct)
        for response in responses
        if response.title_pct is not None and _meeting_response_counts_for_spread(response)
    ]
    spread = round(max(values) - min(values), 1) if values else 0.0
    return {
        "round": round_index,
        "protagonist": protagonist,
        "question": question,
        "responses": [
            {
                "agent": response.agent,
                "answer": response.answer,
                "title_pct": response.title_pct,
                "title_pct_source": response.title_pct_source,
                "support_score": response.support_score,
                "source_count": response.source_count,
                "accepted": response.accepted,
                "disagreed": response.disagreed,
                "used_fallback": response.used_fallback,
                "removed_from_main": response.removed_from_main,
                "removal_reason": response.removal_reason,
                "validation_issues": response.validation_issues or [],
                "leadership_bid": response.leadership_bid,
                "proposed_next_question": response.proposed_next_question,
                "leadership_rationale": response.leadership_rationale,
                "consensus_check_question": response.consensus_check_question,
                "match_probabilities": response.match_probabilities or {},
                "scenario_probabilities": response.scenario_probabilities or {},
            }
            for response in responses
        ],
        "next_protagonist": next_protagonist,
        "consensus_title_pct": round(consensus_title_pct, 1),
        "consensus_spread_pct": spread,
}


def _enough_peer_acceptances(last_turn: dict[str, Any] | None, *, required_acceptances: int) -> bool:
    if not last_turn:
        return False
    responses = list(last_turn.get("responses", []))
    accepted_count = sum(
        1
        for response in responses
        if bool(response.get("accepted", False)) and _turn_response_counts_for_acceptance(response)
    )
    if accepted_count < required_acceptances:
        return False
    return True


def consensus_reached(
    consensus: Any,
    *,
    round_index: int,
    minimum_rounds: int,
    threshold_pct: float,
    minimum_participants: int | None = None,
    minimum_peer_acceptances: int | None = None,
    minimum_real_agents: int | None = None,
    minimum_real_acceptances: int | None = None,
    last_turn: dict[str, Any] | None = None,
    require_peer_acceptance: bool = True,
) -> bool:
    if round_index < minimum_rounds:
        return False
    participant_floor = (
        int(minimum_participants)
        if minimum_participants is not None
        else int(minimum_real_agents if minimum_real_agents is not None else 3)
    )
    participants = [
        opinion
        for opinion in list(getattr(consensus, "raw_opinions", []))
        if _counts_as_consensus_participant(opinion)
    ]
    if len(participants) < participant_floor:
        return False
    required_acceptances = (
        int(minimum_peer_acceptances)
        if minimum_peer_acceptances is not None
        else int(minimum_real_acceptances if minimum_real_acceptances is not None else participant_floor)
    )
    if require_peer_acceptance and required_acceptances > 0 and not _enough_peer_acceptances(
        last_turn,
        required_acceptances=required_acceptances,
    ):
        return False
    return float(consensus.dispersion_pct) <= threshold_pct

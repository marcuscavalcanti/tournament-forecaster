"""Competition-neutral prompt construction for the multi-LLM council."""

from __future__ import annotations

import json
from collections.abc import Sequence

from ..domain import Forecast, Tournament
from .models import CouncilOpinion


def _peer_position(opinion: CouncilOpinion) -> dict[str, object]:
    return {
        "stage_probabilities": dict(opinion.stage_probabilities),
        "championship_probability": opinion.championship_probability,
        "confidence": opinion.confidence,
        "key_factors": list(opinion.key_factors),
    }


def build_council_prompt(
    forecast: Forecast,
    tournament: Tournament,
    *,
    round_number: int,
    peer_opinions: Sequence[CouncilOpinion] = (),
) -> str:
    """Build a bounded JSON-only review prompt without provider secrets."""

    team_names = {team.id: team.display_name for team in tournament.teams}
    completed = [
        {
            "stage_id": match.stage_id,
            "home": team_names.get(match.home_team_id, match.home_team_id),
            "away": team_names.get(match.away_team_id, match.away_team_id),
            "score": [match.score.home, match.score.away],
            "winner": (
                team_names.get(match.winner_team_id, match.winner_team_id)
                if match.winner_team_id
                else None
            ),
        }
        for match in tournament.completed_matches
    ]
    matchups = [
        {
            "stage_id": matchup.stage_id,
            "opponent": team_names.get(
                matchup.opponent_team_id, matchup.opponent_team_id
            ),
            "probability": matchup.probability,
        }
        for matchup in forecast.matchup_probabilities
    ]
    baseline = {
        "stage_probabilities": dict(forecast.stage_probabilities),
        "championship_probability": forecast.championship_probability,
        "matchup_probabilities_engine_only": matchups,
    }
    lines = [
        f"DEBATE ROUND {round_number}",
        "You are one independent member of a multi-model tournament debriefing council.",
        "Review probabilities, not tournament facts. Completed results, legal opponents, "
        "and bracket topology are authoritative and cannot be changed.",
        f"Tournament: {tournament.display_name}",
        "Focus team: "
        + team_names.get(forecast.focus_team_id, forecast.focus_team_id),
        "Deterministic engine baseline:",
        json.dumps(baseline, ensure_ascii=False, sort_keys=True),
        "Completed results:",
        json.dumps(completed, ensure_ascii=False, sort_keys=True),
    ]
    if peer_opinions:
        lines.append("Peer positions are anonymized. Challenge them before converging:")
        for index, opinion in enumerate(peer_opinions, start=1):
            lines.append(
                f"Position {index}: "
                + json.dumps(
                    _peer_position(opinion),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    schema_example = {
        "stage_probabilities": {
            stage_id: forecast.stage_probabilities[stage_id]
            for stage_id in forecast.stage_order
        },
        "championship_probability": forecast.championship_probability,
        "confidence": 0.75,
        "summary": "Concise audit-ready conclusion.",
        "key_factors": ["factor one", "factor two"],
    }
    lines.extend(
        [
            "Return exactly one JSON object and no prose outside it.",
            "Include every stage key exactly as shown. Reach probabilities must be "
            "non-increasing, and stages already fixed at 0 or 1 must remain fixed.",
            json.dumps(schema_example, ensure_ascii=False, sort_keys=True),
        ]
    )
    return "\n".join(lines)

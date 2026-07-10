"""Typed stage-entrant resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .errors import TournamentValidationError


@dataclass(slots=True)
class QualificationState:
    group_rankings: dict[str, Mapping[str, Sequence[str]]] = field(default_factory=dict)
    best_additional: dict[str, Sequence[str]] = field(default_factory=dict)
    league_rankings: dict[str, Sequence[str]] = field(default_factory=dict)
    match_winners: dict[str, str] = field(default_factory=dict)


def _ranked_team(values: Sequence[str] | None, rank: object, label: str) -> str:
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
        raise TournamentValidationError(f"{label} rank must be an integer greater than or equal to 1")
    if values is None or rank > len(values):
        raise TournamentValidationError(f"{label} rank does not resolve to an entrant")
    return values[rank - 1]


def resolve_entrant(source: Mapping[str, object], state: QualificationState) -> str:
    """Resolve one strict typed source against prior stage results."""

    if not isinstance(source, Mapping):
        raise TournamentValidationError("knockout entrant must be a mapping")
    source_type = source.get("type")
    if source_type == "group_rank":
        stage_id = source.get("stage_id")
        group = source.get("group")
        if not isinstance(stage_id, str) or not isinstance(group, str):
            raise TournamentValidationError("group_rank entrant requires stage_id and group")
        groups = state.group_rankings.get(stage_id)
        return _ranked_team(groups.get(group) if groups is not None else None, source.get("rank"), "group")
    if source_type == "best_additional":
        stage_id = source.get("stage_id")
        if not isinstance(stage_id, str):
            raise TournamentValidationError("best_additional entrant requires stage_id")
        return _ranked_team(state.best_additional.get(stage_id), source.get("rank"), "best additional")
    if source_type == "league_rank":
        stage_id = source.get("stage_id")
        if not isinstance(stage_id, str):
            raise TournamentValidationError("league_rank entrant requires stage_id")
        return _ranked_team(state.league_rankings.get(stage_id), source.get("rank"), "league")
    if source_type == "match_winner":
        match_id = source.get("match_id")
        if not isinstance(match_id, str) or match_id not in state.match_winners:
            raise TournamentValidationError("match_winner entrant does not resolve to a completed tie")
        return state.match_winners[match_id]
    raise TournamentValidationError("unsupported knockout entrant type")

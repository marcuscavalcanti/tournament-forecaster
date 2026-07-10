"""Fixed, seeded, and open knockout pairing."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .errors import TournamentValidationError
from .qualification import QualificationState, resolve_entrant


@dataclass(frozen=True, slots=True)
class Pairing:
    match_id: str
    first_team_id: str
    second_team_id: str


def _resolved_ties(
    ties: Sequence[Mapping[str, object]],
    state: QualificationState,
) -> list[tuple[str, str, str]]:
    resolved: list[tuple[str, str, str]] = []
    for tie in sorted(ties, key=lambda item: str(item.get("id", ""))):
        match_id = tie.get("id")
        entrants = tie.get("entrants")
        if not isinstance(match_id, str):
            raise TournamentValidationError("knockout tie requires a stable id")
        if not isinstance(entrants, Sequence) or isinstance(entrants, (str, bytes)) or len(entrants) != 2:
            raise TournamentValidationError("knockout tie must contain exactly two entrants")
        first, second = entrants
        if not isinstance(first, Mapping) or not isinstance(second, Mapping):
            raise TournamentValidationError("knockout entrant must be a mapping")
        resolved.append((match_id, resolve_entrant(first, state), resolve_entrant(second, state)))
    return resolved


def build_pairings(
    mode: str,
    ties: Sequence[Mapping[str, object]],
    state: QualificationState,
    rng: random.Random,
) -> tuple[Pairing, ...]:
    """Resolve sources and pair all entrants using one supplied random stream."""

    resolved = _resolved_ties(ties, state)
    tie_ids = [tie_id for tie_id, _, _ in resolved]
    if mode == "fixed":
        pairings = [Pairing(tie_id, first, second) for tie_id, first, second in resolved]
    elif mode == "seeded_draw":
        seeded = [first for _, first, _ in resolved]
        unseeded = [second for _, _, second in resolved]
        rng.shuffle(seeded)
        rng.shuffle(unseeded)
        pairings = [
            Pairing(tie_id, seeded[index], unseeded[index])
            for index, tie_id in enumerate(tie_ids)
        ]
    elif mode == "open_draw":
        entrants = [team_id for _, first, second in resolved for team_id in (first, second)]
        rng.shuffle(entrants)
        pairings = [
            Pairing(tie_id, entrants[index * 2], entrants[index * 2 + 1])
            for index, tie_id in enumerate(tie_ids)
        ]
    else:
        raise TournamentValidationError("knockout pairing mode must be fixed, seeded_draw, or open_draw")

    all_entrants = [
        team_id
        for pairing in pairings
        for team_id in (pairing.first_team_id, pairing.second_team_id)
    ]
    if len(all_entrants) != len(set(all_entrants)):
        raise TournamentValidationError("a knockout stage cannot contain duplicate entrants")
    return tuple(pairings)

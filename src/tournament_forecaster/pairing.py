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
    *,
    locked_pairs: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[Pairing, ...]:
    """Resolve sources and pair all entrants using one supplied random stream."""

    resolved = _resolved_ties(ties, state)
    tie_ids = [tie_id for tie_id, _, _ in resolved]
    locked = dict(locked_pairs or {})
    unknown_locks = sorted(set(locked) - set(tie_ids))
    if unknown_locks:
        raise TournamentValidationError("completed knockout match is not a configured tie")
    reserved: set[str] = set()
    for first, second in locked.values():
        if first == second or first in reserved or second in reserved:
            raise TournamentValidationError(
                "a locked entrant cannot be reserved in more than one tie"
            )
        reserved.update((first, second))

    if mode == "fixed":
        pairings = []
        for tie_id, first, second in resolved:
            locked_pair = locked.get(tie_id)
            if locked_pair is not None and set(locked_pair) != {first, second}:
                raise TournamentValidationError(
                    "completed tie contradicts fixed pairing sources"
                )
            pairings.append(Pairing(tie_id, first, second))
    elif mode == "seeded_draw":
        seeded = [first for _, first, _ in resolved]
        unseeded = [second for _, _, second in resolved]
        locked_by_id: dict[str, Pairing] = {}
        for tie_id, locked_pair in locked.items():
            seeded_team = [team_id for team_id in locked_pair if team_id in seeded]
            unseeded_team = [team_id for team_id in locked_pair if team_id in unseeded]
            if len(seeded_team) != 1 or len(unseeded_team) != 1:
                raise TournamentValidationError(
                    "completed seeded tie contradicts configured seed pots"
                )
            seeded.remove(seeded_team[0])
            unseeded.remove(unseeded_team[0])
            locked_by_id[tie_id] = Pairing(
                tie_id,
                seeded_team[0],
                unseeded_team[0],
            )
        rng.shuffle(seeded)
        rng.shuffle(unseeded)
        unlocked_ids = [tie_id for tie_id in tie_ids if tie_id not in locked]
        pairings = list(locked_by_id.values()) + [
            Pairing(tie_id, seeded[index], unseeded[index])
            for index, tie_id in enumerate(unlocked_ids)
        ]
    elif mode == "open_draw":
        entrants = [team_id for _, first, second in resolved for team_id in (first, second)]
        locked_by_id = {}
        for tie_id, locked_pair in locked.items():
            if any(team_id not in entrants for team_id in locked_pair):
                raise TournamentValidationError(
                    "completed open tie contains an undeclared entrant"
                )
            entrants.remove(locked_pair[0])
            entrants.remove(locked_pair[1])
            locked_by_id[tie_id] = Pairing(tie_id, locked_pair[0], locked_pair[1])
        rng.shuffle(entrants)
        unlocked_ids = [tie_id for tie_id in tie_ids if tie_id not in locked]
        pairings = list(locked_by_id.values()) + [
            Pairing(tie_id, entrants[index * 2], entrants[index * 2 + 1])
            for index, tie_id in enumerate(unlocked_ids)
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
    return tuple(sorted(pairings, key=lambda pairing: pairing.match_id))

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


ResolvedTie = tuple[str, str, str]


def resolve_ties(
    ties: Sequence[Mapping[str, object]],
    state: QualificationState,
) -> list[ResolvedTie]:
    """Resolve configured tie sources without drawing the resulting pool."""

    resolved: list[ResolvedTie] = []
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


def validate_locked_pairs(
    mode: str,
    resolved_ties: Sequence[ResolvedTie],
    locked_pairs: Mapping[str, tuple[str, str]],
    *,
    configured_tie_ids: Sequence[str] | None = None,
    fixed_pair_order: bool = True,
) -> None:
    """Validate observed tie entrants against resolved fixed or draw pools."""

    resolved_by_id = {
        tie_id: (first_team_id, second_team_id)
        for tie_id, first_team_id, second_team_id in resolved_ties
    }
    known_tie_ids = set(configured_tie_ids or resolved_by_id)
    unknown_locks = sorted(set(locked_pairs) - known_tie_ids)
    if unknown_locks:
        raise TournamentValidationError("completed knockout match is not a configured tie")

    reserved: set[str] = set()
    for first_team_id, second_team_id in locked_pairs.values():
        if (
            first_team_id == second_team_id
            or first_team_id in reserved
            or second_team_id in reserved
        ):
            raise TournamentValidationError(
                "a locked entrant cannot be reserved in more than one tie"
            )
        reserved.update((first_team_id, second_team_id))

    declared_entrants = [
        team_id
        for _, first_team_id, second_team_id in resolved_ties
        for team_id in (first_team_id, second_team_id)
    ]
    if len(declared_entrants) != len(set(declared_entrants)):
        raise TournamentValidationError("a knockout stage cannot contain duplicate entrants")

    if mode == "fixed":
        for tie_id, locked_pair in locked_pairs.items():
            resolved_pair = resolved_by_id.get(tie_id)
            pairs_match = (
                locked_pair == resolved_pair
                if fixed_pair_order
                else resolved_pair is not None and set(locked_pair) == set(resolved_pair)
            )
            if not pairs_match:
                raise TournamentValidationError(
                    "completed tie contradicts fixed pairing sources"
                )
    elif mode == "seeded_draw":
        seeded = {first_team_id for _, first_team_id, _ in resolved_ties}
        unseeded = {second_team_id for _, _, second_team_id in resolved_ties}
        for locked_pair in locked_pairs.values():
            if locked_pair[0] not in seeded or locked_pair[1] not in unseeded:
                raise TournamentValidationError(
                    "completed seeded tie contradicts configured seed pots"
                )
    elif mode == "open_draw":
        declared_pool = set(declared_entrants)
        for locked_pair in locked_pairs.values():
            if any(team_id not in declared_pool for team_id in locked_pair):
                raise TournamentValidationError(
                    "completed open tie contains an undeclared entrant"
                )
    else:
        raise TournamentValidationError(
            "knockout pairing mode must be fixed, seeded_draw, or open_draw"
        )


def build_pairings(
    mode: str,
    ties: Sequence[Mapping[str, object]],
    state: QualificationState,
    rng: random.Random,
    *,
    locked_pairs: Mapping[str, tuple[str, str]] | None = None,
    fixed_pair_order: bool = True,
) -> tuple[Pairing, ...]:
    """Resolve sources and pair all entrants using one supplied random stream."""

    resolved = resolve_ties(ties, state)
    tie_ids = [tie_id for tie_id, _, _ in resolved]
    locked = dict(locked_pairs or {})
    validate_locked_pairs(
        mode,
        resolved,
        locked,
        configured_tie_ids=tie_ids,
        fixed_pair_order=fixed_pair_order,
    )

    if mode == "fixed":
        pairings = [
            Pairing(tie_id, first_team_id, second_team_id)
            for tie_id, first_team_id, second_team_id in resolved
        ]
    elif mode == "seeded_draw":
        seeded = [first for _, first, _ in resolved]
        unseeded = [second for _, _, second in resolved]
        locked_by_id: dict[str, Pairing] = {}
        for tie_id, locked_pair in locked.items():
            seeded_team = [team_id for team_id in locked_pair if team_id in seeded]
            unseeded_team = [team_id for team_id in locked_pair if team_id in unseeded]
            assert len(seeded_team) == len(unseeded_team) == 1
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

"""Shared deterministic fixture identity for round-robin group stages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

from .errors import TournamentValidationError


@dataclass(frozen=True, slots=True)
class GroupFixtureSpec:
    match_id: str
    group_id: str
    round_number: int
    home_team_id: str
    away_team_id: str
    leg: int = 1


def group_fixture_match_id(
    stage_id: str,
    group_id: str,
    first_team_id: str,
    second_team_id: str,
    round_number: int,
) -> str:
    """Build a collision-free stable ID from separately encoded components."""

    first, second = sorted((first_team_id, second_team_id))
    encoded_group = group_id.encode("ascii").hex()
    encoded_first = first.encode("ascii").hex()
    encoded_second = second.encode("ascii").hex()
    return (
        f"{stage_id}-group-{encoded_group}-round-{round_number}"
        f"-match-{encoded_first}-{encoded_second}"
    )


def generate_group_fixture_specs(
    stage: Mapping[str, object],
) -> tuple[GroupFixtureSpec, ...]:
    """Generate the canonical fixture identities and orientations for a group stage."""

    stage_id = str(stage["id"])
    groups = stage.get("groups")
    if not isinstance(groups, Mapping):
        raise TournamentValidationError("group stage groups must be a mapping")
    rounds_value = stage.get("rounds_per_pair", 1)
    if (
        isinstance(rounds_value, bool)
        or not isinstance(rounds_value, int)
        or rounds_value < 1
    ):
        raise TournamentValidationError("group rounds per pair must be a positive integer")
    fixtures: list[GroupFixtureSpec] = []
    match_ids: set[str] = set()
    for group_id in sorted(groups):
        roster = groups[group_id]
        if not isinstance(roster, Sequence) or isinstance(roster, (str, bytes)):
            raise TournamentValidationError("group roster must be a sequence")
        for first, second in combinations(sorted(str(team_id) for team_id in roster), 2):
            for round_number in range(1, rounds_value + 1):
                home, away = (first, second) if round_number % 2 else (second, first)
                match_id = group_fixture_match_id(
                    stage_id,
                    str(group_id),
                    first,
                    second,
                    round_number,
                )
                if match_id in match_ids:
                    raise TournamentValidationError(
                        "generated group fixture ids must be unique"
                    )
                match_ids.add(match_id)
                fixtures.append(
                    GroupFixtureSpec(
                        match_id=match_id,
                        group_id=str(group_id),
                        round_number=round_number,
                        home_team_id=home,
                        away_team_id=away,
                    )
                )
    return tuple(fixtures)


def group_fixture_contract(
    stage: Mapping[str, object],
) -> dict[str, tuple[frozenset[str], int]]:
    """Return exact completed-fact identity keyed by canonical match ID."""

    return {
        fixture.match_id: (
            frozenset((fixture.home_team_id, fixture.away_team_id)),
            fixture.leg,
        )
        for fixture in generate_group_fixture_specs(stage)
    }

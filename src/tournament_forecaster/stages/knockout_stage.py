"""One- and two-leg knockout-stage simulation."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ..domain import CompletedMatch, Score
from ..errors import TournamentValidationError
from ..pairing import Pairing, build_pairings
from ..probabilities import (
    DEFAULT_RATING,
    resolve_knockout_draw,
    resolve_penalty_shootout,
    simulate_score,
)
from ..qualification import QualificationState
from ..standings import TableMatch


ScoreSimulator = Callable[[float, float, random.Random], Score]


@dataclass(frozen=True, slots=True)
class KnockoutStageResult:
    stage_id: str
    pairings: tuple[Pairing, ...]
    matches: tuple[TableMatch, ...]
    winners: Mapping[str, str]
    entrant_team_ids: tuple[str, ...]


def _winner_from_draw(
    first_team_id: str,
    second_team_id: str,
    ratings: Mapping[str, float],
    rng: random.Random,
    aggregate_tiebreak: str,
) -> str:
    if aggregate_tiebreak == "penalties":
        first_wins = resolve_penalty_shootout(rng)
    else:
        first_wins = resolve_knockout_draw(
            float(ratings.get(first_team_id, DEFAULT_RATING)),
            float(ratings.get(second_team_id, DEFAULT_RATING)),
            rng,
        )
    return first_team_id if first_wins else second_team_id


def _expected_legs(pairing: Pairing, legs: int, home_away_order: str) -> tuple[tuple[str, str], ...]:
    if legs == 1:
        return ((pairing.first_team_id, pairing.second_team_id),)
    if home_away_order == "seeded_team_second_leg_home":
        return (
            (pairing.second_team_id, pairing.first_team_id),
            (pairing.first_team_id, pairing.second_team_id),
        )
    return (
        (pairing.first_team_id, pairing.second_team_id),
        (pairing.second_team_id, pairing.first_team_id),
    )


def simulate_knockout_stage(
    stage: Mapping[str, object],
    *,
    state: QualificationState,
    ratings: Mapping[str, float],
    completed_matches: Sequence[CompletedMatch],
    rng: random.Random,
    score_simulator: ScoreSimulator = simulate_score,
) -> KnockoutStageResult:
    """Resolve pairings, preserve completed legs, and advance every tie winner."""

    stage_id = str(stage["id"])
    pairing_config = stage["pairing"]
    assert isinstance(pairing_config, Mapping)
    ties = pairing_config["ties"]
    assert isinstance(ties, Sequence)
    pairings = build_pairings(
        str(pairing_config["mode"]),
        ties,  # type: ignore[arg-type]
        state,
        rng,
    )
    relevant = tuple(
        sorted(
            (match for match in completed_matches if match.stage_id == stage_id),
            key=lambda match: (match.match_id, match.leg),
        )
    )
    configured_ids = {pairing.match_id for pairing in pairings}
    if any(match.match_id not in configured_ids for match in relevant):
        raise TournamentValidationError("completed knockout match is not a configured tie")

    actual_pairings: list[Pairing] = []
    for pairing in pairings:
        locked_matches = [match for match in relevant if match.match_id == pairing.match_id]
        if locked_matches:
            locked_teams = frozenset(
                (locked_matches[0].home_team_id, locked_matches[0].away_team_id)
            )
            configured_teams = frozenset((pairing.first_team_id, pairing.second_team_id))
            if locked_teams != configured_teams:
                pairing = Pairing(
                    pairing.match_id,
                    locked_matches[0].home_team_id,
                    locked_matches[0].away_team_id,
                )
        actual_pairings.append(pairing)

    legs_value = stage["legs"]
    if isinstance(legs_value, bool) or not isinstance(legs_value, int):
        raise TournamentValidationError("knockout legs must be an integer")
    legs = legs_value
    home_away_order = str(stage.get("home_away_order", "listed_team_first_leg_home"))
    away_goals_rule = bool(stage.get("away_goals_rule", False))
    aggregate_tiebreak = str(stage.get("aggregate_tiebreak", "extra_time_then_penalties"))
    matches: list[TableMatch] = []
    winners: dict[str, str] = {}
    for pairing in actual_pairings:
        facts_by_leg = {
            match.leg: match
            for match in relevant
            if match.match_id == pairing.match_id
        }
        expected_legs = _expected_legs(pairing, legs, home_away_order)
        tie_matches: list[TableMatch] = []
        for leg, (home_team_id, away_team_id) in enumerate(expected_legs, start=1):
            fact = facts_by_leg.get(leg)
            if fact is not None:
                if frozenset((fact.home_team_id, fact.away_team_id)) != frozenset(
                    (pairing.first_team_id, pairing.second_team_id)
                ):
                    raise TournamentValidationError("completed knockout leg teams contradict its tie")
                tie_matches.append(
                    TableMatch(
                        fact.match_id,
                        fact.home_team_id,
                        fact.away_team_id,
                        fact.score,
                        leg,
                    )
                )
            else:
                tie_matches.append(
                    TableMatch(
                        pairing.match_id,
                        home_team_id,
                        away_team_id,
                        score_simulator(
                            float(ratings.get(home_team_id, DEFAULT_RATING)),
                            float(ratings.get(away_team_id, DEFAULT_RATING)),
                            rng,
                        ),
                        leg,
                    )
                )
        matches.extend(tie_matches)

        totals = {pairing.first_team_id: 0, pairing.second_team_id: 0}
        away_goals = {pairing.first_team_id: 0, pairing.second_team_id: 0}
        for match in tie_matches:
            totals[match.home_team_id] += match.score.home
            totals[match.away_team_id] += match.score.away
            away_goals[match.away_team_id] += match.score.away
        first_total = totals[pairing.first_team_id]
        second_total = totals[pairing.second_team_id]
        locked_tie_winner = (
            facts_by_leg[2].winner_team_id
            if legs == 2 and len(facts_by_leg) == 2 and 2 in facts_by_leg
            else None
        )
        if locked_tie_winner is not None:
            winner = locked_tie_winner
        elif first_total != second_total:
            winner = pairing.first_team_id if first_total > second_total else pairing.second_team_id
        elif legs == 1 and 1 in facts_by_leg and facts_by_leg[1].winner_team_id is not None:
            winner = facts_by_leg[1].winner_team_id
        elif away_goals_rule and away_goals[pairing.first_team_id] != away_goals[pairing.second_team_id]:
            winner = (
                pairing.first_team_id
                if away_goals[pairing.first_team_id] > away_goals[pairing.second_team_id]
                else pairing.second_team_id
            )
        else:
            winner = _winner_from_draw(
                pairing.first_team_id,
                pairing.second_team_id,
                ratings,
                rng,
                aggregate_tiebreak,
            )
        winners[pairing.match_id] = winner

    entrants = tuple(
        sorted(
            {
                team_id
                for pairing in actual_pairings
                for team_id in (pairing.first_team_id, pairing.second_team_id)
            }
        )
    )
    return KnockoutStageResult(
        stage_id=stage_id,
        pairings=tuple(actual_pairings),
        matches=tuple(matches),
        winners=winners,
        entrant_team_ids=entrants,
    )

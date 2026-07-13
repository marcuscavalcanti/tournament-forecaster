"""One- and two-leg knockout-stage simulation."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ..domain import CompletedMatch, Score, _completed_knockout_winner
from ..errors import TournamentValidationError
from ..pairing import Pairing, build_pairings
from ..probabilities import (
    DEFAULT_RATING,
    compose_rating,
    resolve_knockout_draw,
    resolve_penalty_shootout,
    simulate_score,
    stage_home_advantage_points,
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
    deciding_home_team_id: str,
    ratings: Mapping[str, float],
    rng: random.Random,
    aggregate_tiebreak: str,
    home_advantage: float,
) -> str:
    first_is_home = deciding_home_team_id == first_team_id
    first_advantage = home_advantage if first_is_home else 0.0
    second_advantage = home_advantage if not first_is_home else 0.0
    if aggregate_tiebreak == "penalties":
        first_wins = resolve_penalty_shootout(
            rng,
            first_team_advantage_points=first_advantage,
            second_team_advantage_points=second_advantage,
        )
    else:
        first_wins = resolve_knockout_draw(
            compose_rating(
                float(ratings.get(first_team_id, DEFAULT_RATING)),
                first_advantage,
            ),
            compose_rating(
                float(ratings.get(second_team_id, DEFAULT_RATING)),
                second_advantage,
            ),
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


def _locked_pairs(
    stage_id: str,
    legs: int,
    home_away_order: str,
    completed_matches: Sequence[CompletedMatch],
) -> tuple[tuple[CompletedMatch, ...], dict[str, tuple[str, str]]]:
    relevant = tuple(
        sorted(
            (match for match in completed_matches if match.stage_id == stage_id),
            key=lambda match: (match.match_id, match.leg),
        )
    )
    grouped: dict[str, list[CompletedMatch]] = {}
    for match in relevant:
        grouped.setdefault(match.match_id, []).append(match)
    locked: dict[str, tuple[str, str]] = {}
    reserved: set[str] = set()
    for match_id, facts in sorted(grouped.items()):
        team_pair = {facts[0].home_team_id, facts[0].away_team_id}
        if any({fact.home_team_id, fact.away_team_id} != team_pair for fact in facts):
            raise TournamentValidationError("completed match legs must use the same team pair")
        first_fact = facts[0]
        if legs == 1:
            oriented = (first_fact.home_team_id, first_fact.away_team_id)
        else:
            first_is_home = (
                home_away_order == "listed_team_first_leg_home" and first_fact.leg == 1
            ) or (
                home_away_order == "seeded_team_second_leg_home" and first_fact.leg == 2
            )
            oriented = (
                (first_fact.home_team_id, first_fact.away_team_id)
                if first_is_home
                else (first_fact.away_team_id, first_fact.home_team_id)
            )
        if any(team_id in reserved for team_id in oriented):
            raise TournamentValidationError(
                "a locked entrant cannot be reserved in more than one tie"
            )
        reserved.update(oriented)
        locked[match_id] = oriented
    return relevant, locked


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
    home_advantage = stage_home_advantage_points(stage)
    pairing_config = stage["pairing"]
    assert isinstance(pairing_config, Mapping)
    ties = pairing_config["ties"]
    assert isinstance(ties, Sequence)
    typed_ties: list[Mapping[str, object]] = []
    for tie in ties:
        assert isinstance(tie, Mapping)
        typed_ties.append(tie)
    legs_value = stage["legs"]
    if isinstance(legs_value, bool) or not isinstance(legs_value, int):
        raise TournamentValidationError("knockout legs must be an integer")
    legs = legs_value
    home_away_order = str(stage.get("home_away_order", "listed_team_first_leg_home"))
    relevant, locked = _locked_pairs(
        stage_id,
        legs,
        home_away_order,
        completed_matches,
    )
    pairings = build_pairings(
        str(pairing_config["mode"]),
        typed_ties,
        state,
        rng,
        locked_pairs=locked,
    )
    away_goals_rule = bool(stage.get("away_goals_rule", False))
    aggregate_tiebreak = str(stage.get("aggregate_tiebreak", "extra_time_then_penalties"))
    matches: list[TableMatch] = []
    winners: dict[str, str] = {}
    for pairing in pairings:
        facts_by_leg = {
            match.leg: match
            for match in relevant
            if match.match_id == pairing.match_id
        }
        completed_winner = _completed_knockout_winner(
            stage,
            tuple(facts_by_leg.values()),
        )
        expected_legs = _expected_legs(pairing, legs, home_away_order)
        tie_matches: list[TableMatch] = []
        for leg, (home_team_id, away_team_id) in enumerate(expected_legs, start=1):
            fact = facts_by_leg.get(leg)
            if fact is not None:
                if (fact.home_team_id, fact.away_team_id) != (home_team_id, away_team_id):
                    raise TournamentValidationError(
                        "completed knockout leg home-away order contradicts its tie"
                    )
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
                            compose_rating(
                                float(ratings.get(home_team_id, DEFAULT_RATING)),
                                home_advantage,
                            ),
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
        if completed_winner is not None:
            winner = completed_winner
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
                expected_legs[-1][0],
                ratings,
                rng,
                aggregate_tiebreak,
                home_advantage,
            )
        winners[pairing.match_id] = winner

    entrants = tuple(
        sorted(
            {
                team_id
                for pairing in pairings
                for team_id in (pairing.first_team_id, pairing.second_team_id)
            }
        )
    )
    return KnockoutStageResult(
        stage_id=stage_id,
        pairings=pairings,
        matches=tuple(matches),
        winners=winners,
        entrant_team_ids=entrants,
    )

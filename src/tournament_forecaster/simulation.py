"""Complete-tournament deterministic Monte Carlo simulation."""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from .domain import Forecast, MatchupProbability, SimulationOptions, Tournament
from .errors import TournamentValidationError
from .probabilities import wilson_interval
from .qualification import QualificationState
from .stages.group_stage import simulate_group_stage
from .stages.knockout_stage import KnockoutStageResult, simulate_knockout_stage
from .stages.league_stage import simulate_league_stage


def _tie_sources(stage: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    if stage.get("type") != "knockout":
        return ()
    pairing = stage.get("pairing")
    if not isinstance(pairing, Mapping):
        return ()
    ties = pairing.get("ties")
    if not isinstance(ties, Sequence):
        return ()
    sources: list[Mapping[str, object]] = []
    for tie in ties:
        if not isinstance(tie, Mapping):
            continue
        entrants = tie.get("entrants")
        if not isinstance(entrants, Sequence):
            continue
        sources.extend(source for source in entrants if isinstance(source, Mapping))
    return tuple(sources)


def _tie_owners(stages: Sequence[Mapping[str, object]]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for stage in stages:
        if stage.get("type") != "knockout":
            continue
        pairing = stage.get("pairing")
        assert isinstance(pairing, Mapping)
        ties = pairing.get("ties")
        assert isinstance(ties, Sequence)
        for tie in ties:
            assert isinstance(tie, Mapping)
            owners[str(tie["id"])] = str(stage["id"])
    return owners


def _stable_stage_order(stages: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    stages_by_id = {str(stage["id"]): stage for stage in stages}
    tie_owners = _tie_owners(stages)
    dependencies: dict[str, set[str]] = {stage_id: set() for stage_id in stages_by_id}
    for stage_id, stage in stages_by_id.items():
        for source in _tie_sources(stage):
            source_type = source.get("type")
            if source_type in {"group_rank", "best_additional", "league_rank"}:
                source_stage_id = source.get("stage_id")
                if isinstance(source_stage_id, str):
                    dependencies[stage_id].add(source_stage_id)
            elif source_type == "match_winner":
                match_id = source.get("match_id")
                if isinstance(match_id, str) and match_id in tie_owners:
                    dependencies[stage_id].add(tie_owners[match_id])
    ordered: list[Mapping[str, object]] = []
    completed: set[str] = set()
    while len(ordered) < len(stages_by_id):
        ready = sorted(
            stage_id
            for stage_id, required in dependencies.items()
            if stage_id not in completed and required <= completed
        )
        if not ready:
            raise TournamentValidationError("tournament stage dependencies must form a directed acyclic graph")
        for stage_id in ready:
            ordered.append(stages_by_id[stage_id])
            completed.add(stage_id)
    return tuple(ordered)


def _terminal_knockout_contract(
    stages: Sequence[Mapping[str, object]],
) -> tuple[str, str]:
    championship_stages = [
        stage
        for stage in stages
        if stage.get("type") == "knockout" and stage.get("terminal") == "championship"
    ]
    if len(championship_stages) != 1:
        raise TournamentValidationError(
            "tournament must define exactly one knockout championship terminal"
        )
    championship_stage = championship_stages[0]
    pairing = championship_stage.get("pairing")
    ties = pairing.get("ties") if isinstance(pairing, Mapping) else None
    if (
        not isinstance(ties, Sequence)
        or isinstance(ties, (str, bytes))
        or len(ties) != 1
        or not isinstance(ties[0], Mapping)
    ):
        raise TournamentValidationError(
            "championship terminal must contain exactly one tie"
        )
    championship_tie_id = str(ties[0]["id"])
    if any(
        source.get("type") == "match_winner"
        and source.get("match_id") == championship_tie_id
        for stage in stages
        for source in _tie_sources(stage)
    ):
        raise TournamentValidationError(
            "championship terminal must be a graph sink"
        )
    return str(championship_stage["id"]), championship_tie_id


def _canonical_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(value[key])
            for key in sorted(value, key=str)
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    return value


def _canonical_stage(stage: Mapping[str, object]) -> dict[str, object]:
    canonical = _canonical_value(stage)
    assert isinstance(canonical, dict)
    if stage["type"] == "round_robin_groups":
        groups = stage["groups"]
        assert isinstance(groups, Mapping)
        canonical["groups"] = {
            str(group_id): sorted(str(team_id) for team_id in groups[group_id])  # type: ignore[index]
            for group_id in sorted(groups)
        }
    elif stage["type"] == "league_table":
        fixtures = stage["fixtures"]
        assert isinstance(fixtures, Sequence)
        canonical["fixtures"] = [
            _canonical_value(fixture)
            for fixture in sorted(
                fixtures,
                key=lambda fixture: str(fixture["match_id"]),  # type: ignore[index]
            )
        ]
    else:
        pairing = stage["pairing"]
        assert isinstance(pairing, Mapping)
        ties = pairing["ties"]
        assert isinstance(ties, Sequence)
        canonical_pairing = canonical["pairing"]
        assert isinstance(canonical_pairing, dict)
        canonical_pairing["ties"] = [
            _canonical_value(tie)
            for tie in sorted(
                ties,
                key=lambda tie: str(tie["id"]),  # type: ignore[index]
            )
        ]
    return canonical


def _run_id(tournament: Tournament, focus_team_id: str, options: SimulationOptions) -> str:
    payload = {
        "tournament": {
            "schema_version": tournament.schema_version,
            "id": tournament.id,
            "display_name": tournament.display_name,
            "season": tournament.season,
            "metadata": _canonical_value(tournament.metadata),
            "teams": [
                {
                    "id": team.id,
                    "display_name": team.display_name,
                    "aliases": sorted(team.aliases),
                    "metadata": _canonical_value(team.metadata),
                }
                for team in sorted(tournament.teams, key=lambda item: item.id)
            ],
            "stages": [
                _canonical_stage(stage)
                for stage in sorted(tournament.stages, key=lambda item: str(item["id"]))
            ],
            "ratings": dict(sorted(tournament.ratings.items())),
            "completed_matches": [
                {
                    "match_id": match.match_id,
                    "stage_id": match.stage_id,
                    "home_team_id": match.home_team_id,
                    "away_team_id": match.away_team_id,
                    "score": {"home": match.score.home, "away": match.score.away},
                    "leg": match.leg,
                    "winner_team_id": match.winner_team_id,
                    "metadata": _canonical_value(match.metadata),
                }
                for match in sorted(
                    tournament.completed_matches,
                    key=lambda item: (item.stage_id, item.match_id, item.leg),
                )
            ],
        },
        "focus_team_id": focus_team_id,
        "options": {
            "seed": options.seed,
            "iterations": options.iterations,
            "confidence_level": options.confidence_level,
        },
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return f"run-{hashlib.sha256(encoded).hexdigest()[:16]}"


def simulate_tournament(
    tournament: Tournament,
    *,
    focus_team_id: str | None = None,
    options: SimulationOptions | None = None,
) -> Forecast:
    """Simulate every configured stage in every iteration."""

    selected_focus = focus_team_id or tournament.focus_team_id
    team_ids = {team.id for team in tournament.teams}
    if selected_focus not in team_ids:
        raise TournamentValidationError("focus team id must reference a configured team")
    simulation_options = options or SimulationOptions()
    rng = random.Random(simulation_options.seed)
    stages = _stable_stage_order(tournament.stages)
    terminal_stage_id, terminal_tie_id = _terminal_knockout_contract(stages)
    reach_counts: Counter[str] = Counter()
    matchup_counts: Counter[tuple[str, str]] = Counter()
    championship_count = 0

    for _ in range(simulation_options.iterations):
        state = QualificationState()
        knockout_results: dict[str, KnockoutStageResult] = {}
        for stage in stages:
            stage_id = str(stage["id"])
            stage_type = stage["type"]
            if stage_type == "round_robin_groups":
                group_result = simulate_group_stage(
                    stage,
                    ratings=tournament.ratings,
                    completed_matches=tournament.completed_matches,
                    rng=rng,
                )
                state.group_rankings[stage_id] = {
                    group_id: tuple(row.team_id for row in rows)
                    for group_id, rows in group_result.rankings.items()
                }
                state.best_additional[stage_id] = group_result.best_additional_team_ids
                groups = stage["groups"]
                assert isinstance(groups, Mapping)
                entrants = {
                    str(team_id)
                    for roster in groups.values()
                    for team_id in roster
                }
            elif stage_type == "league_table":
                league_result = simulate_league_stage(
                    stage,
                    ratings=tournament.ratings,
                    completed_matches=tournament.completed_matches,
                    rng=rng,
                )
                state.league_rankings[stage_id] = tuple(
                    row.team_id for row in league_result.rankings
                )
                entrants = {row.team_id for row in league_result.rankings}
            else:
                knockout = simulate_knockout_stage(
                    stage,
                    state=state,
                    ratings=tournament.ratings,
                    completed_matches=tournament.completed_matches,
                    rng=rng,
                )
                knockout_results[stage_id] = knockout
                state.match_winners.update(knockout.winners)
                entrants = set(knockout.entrant_team_ids)
                for pairing in knockout.pairings:
                    if pairing.first_team_id == selected_focus:
                        matchup_counts[(stage_id, pairing.second_team_id)] += 1
                    elif pairing.second_team_id == selected_focus:
                        matchup_counts[(stage_id, pairing.first_team_id)] += 1
            if selected_focus in entrants:
                reach_counts[stage_id] += 1

        terminal = knockout_results.get(terminal_stage_id)
        if terminal is None or set(terminal.winners) != {terminal_tie_id}:
            raise TournamentValidationError(
                "championship terminal must resolve exactly one winner"
            )
        if terminal.winners[terminal_tie_id] == selected_focus:
            championship_count += 1

    iterations = simulation_options.iterations
    stage_probabilities = {
        stage_id: reach_counts[stage_id] / iterations
        for stage_id in sorted(str(stage["id"]) for stage in stages)
    }
    matchup_probabilities = tuple(
        MatchupProbability(stage_id, opponent_team_id, count / iterations)
        for (stage_id, opponent_team_id), count in sorted(matchup_counts.items())
    )
    championship_probability = championship_count / iterations
    confidence_intervals = {
        stage_id: wilson_interval(
            reach_counts[stage_id],
            iterations,
            simulation_options.confidence_level,
        )
        for stage_id in stage_probabilities
    }
    confidence_intervals["championship_probability"] = wilson_interval(
        championship_count,
        iterations,
        simulation_options.confidence_level,
    )
    missing_ratings = sorted(team_ids - set(tournament.ratings))
    warnings = (
        (f"ratings missing for: {', '.join(missing_ratings)}",)
        if missing_ratings
        else ()
    )
    return Forecast(
        run_id=_run_id(tournament, selected_focus, simulation_options),
        generated_at=datetime.now(UTC).isoformat(),
        tournament_id=tournament.id,
        focus_team_id=selected_focus,
        stage_probabilities=stage_probabilities,
        matchup_probabilities=matchup_probabilities,
        championship_probability=championship_probability,
        confidence_intervals=confidence_intervals,
        input_provenance=({"kind": "tournament", "source_id": tournament.id},),
        warnings=warnings,
    )

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest


def _require_package() -> None:
    assert importlib.util.find_spec("tournament_forecaster") is not None, (
        "the generic tournament_forecaster package does not exist yet"
    )


def _document() -> dict[str, object]:
    return {
        "schema_version": 2,
        "tournament": {
            "id": "synthetic-cup",
            "display_name": "Synthetic Cup",
            "season": "2026",
        },
        "focus_team_id": "north-city",
        "teams": [
            {"id": "north-city", "display_name": "North City", "aliases": ["North"]},
            {"id": "south-city", "display_name": "South City"},
        ],
        "stages": [
            {
                "id": "group-stage",
                "type": "round_robin_groups",
                "groups": {"A": ["north-city", "south-city"]},
            },
            _terminal_stage(),
        ],
        "ratings": {"north-city": 1600, "south-city": 1500},
        "completed_matches": [
            {
                "match_id": "group-stage-group-41-round-1-match-6e6f7274682d63697479-736f7574682d63697479",
                "stage_id": "group-stage",
                "home_team_id": "north-city",
                "away_team_id": "south-city",
                "score": {"home": 2, "away": 1},
            }
        ],
    }


def _terminal_stage(
    stage_id: str = "final",
    *,
    terminal: str = "championship",
    entrants: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    entrant_values = entrants or [
        {
            "type": "group_rank",
            "stage_id": "group-stage",
            "group": "A",
            "rank": 1,
        },
        {
            "type": "group_rank",
            "stage_id": "group-stage",
            "group": "A",
            "rank": 2,
        },
    ]
    return {
        "id": stage_id,
        "type": "knockout",
        "pairing": {
            "mode": "fixed",
            "ties": [{"id": f"{stage_id}-1", "entrants": entrant_values}],
        },
        "legs": 1,
        "home_away_order": "listed_team_first_leg_home",
        "aggregate_tiebreak": "extra_time_then_penalties",
        "away_goals_rule": False,
        "terminal": terminal,
    }


def _append_terminal(document: dict[str, object]) -> dict[str, object]:
    stages = document["stages"]
    assert isinstance(stages, list)
    if not any(
        isinstance(stage, dict) and stage.get("terminal") == "championship"
        for stage in stages
    ):
        stages.append(_terminal_stage())
    return document


def _source_group_stage() -> dict[str, object]:
    return {
        "id": "source-groups",
        "type": "round_robin_groups",
        "groups": {"A": ["north-city", "south-city"]},
    }


def _two_leg_tie_stage(
    stage_id: str,
    *,
    terminal: str,
) -> dict[str, object]:
    return {
        "id": stage_id,
        "type": "knockout",
        "pairing": {
            "mode": "fixed",
            "ties": [
                {
                    "id": "semi-final-1" if stage_id == "semi-final" else "other-1",
                    "entrants": [
                        {
                            "type": "group_rank",
                            "stage_id": "source-groups",
                            "group": "A",
                            "rank": 1,
                        },
                        {
                            "type": "group_rank",
                            "stage_id": "source-groups",
                            "group": "A",
                            "rank": 2,
                        },
                    ],
                }
            ],
        },
        "legs": 2,
        "home_away_order": "listed_team_first_leg_home",
        "terminal": terminal,
    }


def _rank_fed_completed_tie_document(
    pairing_mode: str,
    source_kind: str,
    *,
    source_completed: bool,
) -> dict[str, object]:
    if source_kind == "league_rank":
        source_stage: dict[str, object] = {
            "id": "source-league",
            "type": "league_table",
            "fixtures": [
                {
                    "match_id": "league-1",
                    "home_team_id": "north-city",
                    "away_team_id": "south-city",
                }
            ],
            "qualification_bands": [
                {"ranks": [1, 2], "destination": "final"},
            ],
        }
        entrants = [
            {"type": "league_rank", "stage_id": "source-league", "rank": 1},
            {"type": "league_rank", "stage_id": "source-league", "rank": 2},
        ]
        source_matches = [
            {
                "match_id": "league-1",
                "stage_id": "source-league",
                "home_team_id": "north-city",
                "away_team_id": "south-city",
                "score": {"home": 1, "away": 0},
            }
        ]
    else:
        source_stage = {
            "id": "source-groups",
            "type": "round_robin_groups",
            "groups": {"A": ["north-city", "south-city"]},
            "rounds_per_pair": 1,
        }
        if source_kind == "best_additional":
            source_stage["qualification"] = {
                "direct_per_group": 1,
                "best_additional": 1,
                "additional_rank": 2,
            }
            entrants = [
                {"type": "best_additional", "stage_id": "source-groups", "rank": 1},
                {
                    "type": "group_rank",
                    "stage_id": "source-groups",
                    "group": "A",
                    "rank": 1,
                },
            ]
        else:
            entrants = [
                {
                    "type": "group_rank",
                    "stage_id": "source-groups",
                    "group": "A",
                    "rank": 1,
                },
                {
                    "type": "group_rank",
                    "stage_id": "source-groups",
                    "group": "A",
                    "rank": 2,
                },
            ]
        from tournament_forecaster.stages.group_stage import generate_group_fixtures

        fixture = generate_group_fixtures(source_stage)[0]
        source_matches = [
            {
                "match_id": fixture.match_id,
                "stage_id": "source-groups",
                "home_team_id": fixture.home_team_id,
                "away_team_id": fixture.away_team_id,
                "score": {"home": 1, "away": 0},
            }
        ]
    completed_matches = source_matches if source_completed else []
    completed_matches.append(
        {
            "match_id": "final-1",
            "stage_id": "final",
            "home_team_id": "north-city",
            "away_team_id": "south-city",
            "score": {"home": 2, "away": 0},
        }
    )
    return {
        "schema_version": 2,
        "tournament": {"id": "rank-fed-cup", "display_name": "Rank-fed Cup"},
        "focus_team_id": "north-city",
        "teams": [
            {"id": "north-city", "display_name": "North City"},
            {"id": "south-city", "display_name": "South City"},
        ],
        "stages": [
            source_stage,
            {
                "id": "final",
                "type": "knockout",
                "pairing": {
                    "mode": pairing_mode,
                    "ties": [{"id": "final-1", "entrants": entrants}],
                },
                "legs": 1,
                "home_away_order": "listed_team_first_leg_home",
                "terminal": "championship",
            },
        ],
        "ratings": {"north-city": 1600, "south-city": 1500},
        "completed_matches": completed_matches,
    }


def _resolved_rank_lock_document(
    pairing_mode: str,
    source_kind: str,
    locked_pair: tuple[str, str],
) -> dict[str, object]:
    teams = ("a", "b", "c")
    if source_kind == "league_rank":
        source_stage: dict[str, object] = {
            "id": "source-league",
            "type": "league_table",
            "fixtures": [
                {"match_id": "league-a-b", "home_team_id": "a", "away_team_id": "b"},
                {"match_id": "league-a-c", "home_team_id": "a", "away_team_id": "c"},
                {"match_id": "league-b-c", "home_team_id": "b", "away_team_id": "c"},
            ],
            "tiebreakers": ["points", "goal_difference", "goals_for", "team_id"],
            "qualification_bands": [
                {"ranks": [1, 2], "destination": "final"},
                {"ranks": [3, 3], "destination": "eliminated"},
            ],
        }
        entrants = [
            {"type": "league_rank", "stage_id": "source-league", "rank": 1},
            {"type": "league_rank", "stage_id": "source-league", "rank": 2},
        ]
        source_fixtures = cast(list[dict[str, object]], source_stage["fixtures"])
        source_matches = [
            {
                "match_id": fixture["match_id"],
                "stage_id": "source-league",
                "home_team_id": fixture["home_team_id"],
                "away_team_id": fixture["away_team_id"],
                "score": {"home": 1, "away": 0},
            }
            for fixture in source_fixtures
        ]
    else:
        source_stage = {
            "id": "source-groups",
            "type": "round_robin_groups",
            "groups": {"A": list(teams)},
            "rounds_per_pair": 1,
            "tiebreakers": ["points", "goal_difference", "goals_for", "team_id"],
        }
        if source_kind == "best_additional":
            source_stage["qualification"] = {
                "direct_per_group": 1,
                "best_additional": 1,
                "additional_rank": 2,
            }
            entrants = [
                {"type": "best_additional", "stage_id": "source-groups", "rank": 1},
                {
                    "type": "group_rank",
                    "stage_id": "source-groups",
                    "group": "A",
                    "rank": 1,
                },
            ]
        else:
            entrants = [
                {
                    "type": "group_rank",
                    "stage_id": "source-groups",
                    "group": "A",
                    "rank": 1,
                },
                {
                    "type": "group_rank",
                    "stage_id": "source-groups",
                    "group": "A",
                    "rank": 2,
                },
            ]
        from tournament_forecaster.stages.group_stage import generate_group_fixtures

        source_matches = [
            {
                "match_id": fixture.match_id,
                "stage_id": "source-groups",
                "home_team_id": fixture.home_team_id,
                "away_team_id": fixture.away_team_id,
                "score": {"home": 1, "away": 0},
            }
            for fixture in generate_group_fixtures(source_stage)
        ]
    return {
        "schema_version": 2,
        "tournament": {"id": "resolved-rank-cup", "display_name": "Resolved Rank Cup"},
        "focus_team_id": "a",
        "teams": [{"id": team_id, "display_name": team_id.upper()} for team_id in teams],
        "stages": [
            source_stage,
            {
                "id": "final",
                "type": "knockout",
                "pairing": {
                    "mode": pairing_mode,
                    "ties": [{"id": "final-1", "entrants": entrants}],
                },
                "legs": 1,
                "home_away_order": "listed_team_first_leg_home",
                "terminal": "championship",
            },
        ],
        "ratings": {"a": 1700, "b": 1600, "c": 1500},
        "completed_matches": [
            *source_matches,
            {
                "match_id": "final-1",
                "stage_id": "final",
                "home_team_id": locked_pair[0],
                "away_team_id": locked_pair[1],
                "score": {"home": 1, "away": 0},
            },
        ],
    }


def _league_band_document() -> dict[str, object]:
    return {
        "schema_version": 2,
        "tournament": {"id": "league-band-cup", "display_name": "League Band Cup"},
        "focus_team_id": "north-city",
        "teams": [
            {"id": "north-city", "display_name": "North City"},
            {"id": "south-city", "display_name": "South City"},
        ],
        "stages": [
            {
                "id": "league-stage",
                "type": "league_table",
                "fixtures": [
                    {
                        "match_id": "league-1",
                        "home_team_id": "north-city",
                        "away_team_id": "south-city",
                    }
                ],
                "qualification_bands": [
                    {"ranks": [1, 2], "destination": "final"},
                ],
            },
            {
                "id": "final",
                "type": "knockout",
                "pairing": {
                    "mode": "fixed",
                    "ties": [
                        {
                            "id": "final-1",
                            "entrants": [
                                {"type": "league_rank", "stage_id": "league-stage", "rank": 1},
                                {"type": "league_rank", "stage_id": "league-stage", "rank": 2},
                            ],
                        }
                    ],
                },
                "legs": 1,
                "home_away_order": "listed_team_first_leg_home",
                "terminal": "championship",
            },
        ],
        "ratings": {"north-city": 1600, "south-city": 1500},
        "completed_matches": [],
    }


def test_load_tournament_returns_immutable_typed_domain(tmp_path: Path) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament
    from tournament_forecaster.domain import Team, validate_tournament

    path = tmp_path / "tournament.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")

    tournament = load_tournament(path)

    assert tournament.id == "synthetic-cup"
    assert tournament.focus_team_id == "north-city"
    assert tournament.teams == (
        Team(id="north-city", display_name="North City", aliases=("North",)),
        Team(id="south-city", display_name="South City"),
    )
    assert tournament.completed_matches[0].score.home == 2
    validate_tournament(tournament)
    with pytest.raises(FrozenInstanceError):
        tournament.teams[0].id = "rewritten"  # type: ignore[misc]
    with pytest.raises(TypeError):
        tournament.ratings["north-city"] = 0  # type: ignore[index]


@pytest.mark.parametrize("championship_count", [0, 2])
def test_loader_requires_exactly_one_championship_terminal(
    championship_count: int,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    stages = document["stages"]
    assert isinstance(stages, list)
    stages[:] = [
        stage
        for stage in stages
        if not isinstance(stage, dict) or stage.get("terminal") != "championship"
    ]
    for index in range(championship_count):
        stages.append(_terminal_stage(f"final-{index + 1}"))

    with pytest.raises(
        TournamentValidationError,
        match="exactly one knockout championship terminal",
    ):
        load_tournament_document(document)


@pytest.mark.parametrize("tie_count", [0, 2])
def test_loader_requires_exactly_one_tie_in_championship_terminal(
    tie_count: int,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    championship = document["stages"][1]  # type: ignore[index]
    ties = championship["pairing"]["ties"]  # type: ignore[index]
    assert isinstance(ties, list)
    if tie_count == 0:
        ties.clear()
    else:
        ties.append({**ties[0], "id": "final-2"})

    with pytest.raises(
        TournamentValidationError,
        match="championship terminal must contain exactly one tie",
    ):
        load_tournament_document(document)


def test_championship_terminal_must_be_a_graph_sink() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    stages = document["stages"]
    assert isinstance(stages, list)
    stages.append(
        _terminal_stage(
            "placement",
            terminal="placement",
            entrants=[
                {"type": "match_winner", "match_id": "final-1"},
                {
                    "type": "group_rank",
                    "stage_id": "group-stage",
                    "group": "A",
                    "rank": 2,
                },
            ],
        )
    )

    with pytest.raises(
        TournamentValidationError,
        match="championship terminal must be a graph sink",
    ):
        load_tournament_document(document)


def test_two_leg_tie_rejects_declared_winner_until_every_leg_is_present() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    championship = document["stages"][1]  # type: ignore[index]
    championship["legs"] = 2
    completed_matches = document["completed_matches"]
    assert isinstance(completed_matches, list)
    completed_matches.append(
        {
            "match_id": "final-1",
            "stage_id": "final",
            "home_team_id": "south-city",
            "away_team_id": "north-city",
            "score": {"home": 1, "away": 1},
            "leg": 2,
            "winner_team_id": "north-city",
        }
    )

    with pytest.raises(
        TournamentValidationError,
        match="explicit winner requires every configured leg",
    ):
        load_tournament_document(document)


@pytest.mark.parametrize("pairing_mode", ["fixed", "seeded_draw", "open_draw"])
@pytest.mark.parametrize("source_kind", ["group_rank", "best_additional", "league_rank"])
def test_completed_rank_fed_tie_rejects_unfinished_source_rankings(
    pairing_mode: str,
    source_kind: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _rank_fed_completed_tie_document(
        pairing_mode,
        source_kind,
        source_completed=False,
    )
    message = (
        "requires every configured league fixture"
        if source_kind == "league_rank"
        else "requires every generated group fixture"
    )

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


@pytest.mark.parametrize("pairing_mode", ["fixed", "seeded_draw", "open_draw"])
@pytest.mark.parametrize("source_kind", ["group_rank", "best_additional", "league_rank"])
def test_completed_rank_fed_tie_accepts_fully_completed_source_rankings(
    pairing_mode: str,
    source_kind: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document

    document = _rank_fed_completed_tie_document(
        pairing_mode,
        source_kind,
        source_completed=True,
    )

    tournament = load_tournament_document(document)

    assert {match.stage_id for match in tournament.completed_matches} == {
        "final",
        "source-league" if source_kind == "league_rank" else "source-groups",
    }


def test_completed_group_facts_require_exact_generated_match_ids() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _rank_fed_completed_tie_document(
        "fixed",
        "group_rank",
        source_completed=True,
    )
    completed_matches = cast(list[dict[str, object]], document["completed_matches"])
    group_fact = next(
        match
        for match in completed_matches
        if match["stage_id"] == "source-groups"
    )
    group_fact["match_id"] = "fabricated-source-match"

    with pytest.raises(
        TournamentValidationError,
        match="generated group fixture contract",
    ):
        load_tournament_document(document)


@pytest.mark.parametrize(
    ("pairing_mode", "message"),
    [
        ("fixed", "contradicts fixed pairing sources"),
        ("seeded_draw", "contradicts configured seed pots"),
        ("open_draw", "undeclared entrant"),
    ],
)
@pytest.mark.parametrize("source_kind", ["group_rank", "best_additional", "league_rank"])
def test_completed_rank_fed_tie_rejects_lock_contradicting_resolved_ranks(
    pairing_mode: str,
    message: str,
    source_kind: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _resolved_rank_lock_document(
        pairing_mode,
        source_kind,
        ("a", "c"),
    )

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


@pytest.mark.parametrize("pairing_mode", ["fixed", "seeded_draw", "open_draw"])
@pytest.mark.parametrize("source_kind", ["group_rank", "best_additional", "league_rank"])
def test_completed_rank_fed_tie_accepts_lock_matching_resolved_ranks(
    pairing_mode: str,
    source_kind: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document

    tournament = load_tournament_document(
        _resolved_rank_lock_document(pairing_mode, source_kind, ("a", "b"))
    )

    final = next(match for match in tournament.completed_matches if match.match_id == "final-1")
    assert {final.home_team_id, final.away_team_id} == {"a", "b"}


def test_loader_accepts_one_championship_and_any_number_of_placement_terminals() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document

    document = _document()
    stages = document["stages"]
    assert isinstance(stages, list)
    stages.append(_terminal_stage("third-place", terminal="placement"))

    tournament = load_tournament_document(document)

    assert [stage.get("terminal") for stage in tournament.stages] == [
        None,
        "championship",
        "placement",
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"terminal": "winner"}, "terminal"),
        ({"home_away_order": "random_home"}, "home away order"),
        ({"legs": 1, "away_goals_rule": True}, "away goals"),
    ],
)
def test_loader_rejects_invalid_knockout_format_knobs(
    mutation: dict[str, object],
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    stages = document["stages"]
    assert isinstance(stages, list)
    stage = next(
        stage
        for stage in stages
        if isinstance(stage, dict) and stage.get("terminal") == "championship"
    )
    stage.update(mutation)

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


@pytest.mark.parametrize(
    ("direct_per_group", "best_additional", "message"),
    [(3, 0, "attainable"), (2, 1, "every group")],
)
def test_loader_rejects_unattainable_group_qualification_counts(
    direct_per_group: int,
    best_additional: int,
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _append_terminal(_document())
    group_stage = document["stages"][0]  # type: ignore[index]
    group_stage["qualification"] = {  # type: ignore[index]
        "direct_per_group": direct_per_group,
        "best_additional": best_additional,
        **({"additional_rank": direct_per_group + 1} if best_additional else {}),
    }

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


@pytest.mark.parametrize(
    ("qualification", "message"),
    [
        ({"direct_per_group": 1, "best_additional": 1}, "additional rank"),
        (
            {"direct_per_group": 2, "best_additional": 1, "additional_rank": 2},
            "overlap",
        ),
        (
            {"direct_per_group": 1, "best_additional": 1, "additional_rank": 5},
            "every group",
        ),
        (
            {"direct_per_group": 1, "best_additional": 2, "additional_rank": 2},
            "number of groups",
        ),
    ],
)
def test_loader_rejects_invalid_additional_qualification_contract(
    qualification: dict[str, int],
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    teams = document["teams"]
    ratings = document["ratings"]
    group_stage = document["stages"][0]  # type: ignore[index]
    assert isinstance(teams, list) and isinstance(ratings, dict)
    teams.extend(
        [
            {"id": "east-city", "display_name": "East City"},
            {"id": "west-city", "display_name": "West City"},
        ]
    )
    ratings.update({"east-city": 1400, "west-city": 1300})
    group_stage["groups"] = {  # type: ignore[index]
        "A": ["north-city", "south-city", "east-city", "west-city"]
    }
    group_stage["qualification"] = qualification  # type: ignore[index]

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


def test_best_additional_source_rank_must_be_attainable() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    group_stage = document["stages"][0]  # type: ignore[index]
    group_stage["qualification"] = {  # type: ignore[index]
        "direct_per_group": 1,
        "best_additional": 1,
        "additional_rank": 2,
    }
    final = document["stages"][1]  # type: ignore[index]
    final["pairing"]["ties"] = [  # type: ignore[index]
        {
            "id": "final-1",
            "entrants": [
                {"type": "best_additional", "stage_id": "group-stage", "rank": 2},
                {"type": "group_rank", "stage_id": "group-stage", "group": "A", "rank": 1},
            ],
        }
    ]

    with pytest.raises(TournamentValidationError, match="does not resolve"):
        load_tournament_document(document)


def test_league_band_destination_must_reference_an_existing_stage() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _league_band_document()
    band = document["stages"][0]["qualification_bands"][0]  # type: ignore[index]
    band["destination"] = "seeded-final"  # type: ignore[index]

    with pytest.raises(TournamentValidationError, match="destination references an unknown stage"):
        load_tournament_document(document)


def test_league_rank_sources_must_exactly_match_destination_band_ranks() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _league_band_document()
    band = document["stages"][0]["qualification_bands"][0]  # type: ignore[index]
    band["ranks"] = [1, 1]  # type: ignore[index]

    with pytest.raises(TournamentValidationError, match="bands do not align"):
        load_tournament_document(document)


def test_league_rank_sources_accept_exact_destination_band_ranks() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document

    tournament = load_tournament_document(_league_band_document())

    assert [stage["id"] for stage in tournament.stages] == ["league-stage", "final"]


def test_loader_rejects_non_ascii_stable_identifier() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    teams = document["teams"]
    assert isinstance(teams, list)
    assert isinstance(teams[0], dict)
    teams[0]["id"] = "north-city-2!"

    with pytest.raises(TournamentValidationError, match="stable ASCII identifier"):
        load_tournament_document(document)


def test_loader_keeps_team_ids_lowercase_even_when_group_labels_allow_uppercase() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    teams = document["teams"]
    assert isinstance(teams, list) and isinstance(teams[0], dict)
    teams[0]["id"] = "North-City"

    with pytest.raises(TournamentValidationError, match="stable ASCII identifier"):
        load_tournament_document(document)


@pytest.mark.parametrize("group_label", ["A", "Group_A", "A-1", "group_a", "2026"])
def test_loader_accepts_stable_ascii_group_labels(group_label: str) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.stages.group_stage import generate_group_fixtures

    document = _document()
    stages = document["stages"]
    assert isinstance(stages, list) and isinstance(stages[0], dict)
    stages[0]["groups"] = {group_label: ["north-city", "south-city"]}
    championship = stages[1]
    assert isinstance(championship, dict)
    for source in championship["pairing"]["ties"][0]["entrants"]:  # type: ignore[index]
        source["group"] = group_label
    completed_matches = document["completed_matches"]
    assert isinstance(completed_matches, list) and isinstance(completed_matches[0], dict)
    completed_matches[0]["match_id"] = generate_group_fixtures(stages[0])[0].match_id

    tournament = load_tournament_document(document)

    assert group_label in tournament.stages[0]["groups"]  # type: ignore[operator]


@pytest.mark.parametrize("group_label", ["Group A", "-A", "A_", "A__B", "Á", ""])
def test_loader_rejects_invalid_group_labels(group_label: str) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    stages = document["stages"]
    assert isinstance(stages, list) and isinstance(stages[0], dict)
    stages[0]["groups"] = {group_label: ["north-city", "south-city"]}

    with pytest.raises(TournamentValidationError, match="group label"):
        load_tournament_document(document)


def test_loader_rejects_duplicate_completed_match_and_leg() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    completed_matches = document["completed_matches"]
    assert isinstance(completed_matches, list)
    assert isinstance(completed_matches[0], dict)
    completed_matches.append(completed_matches[0].copy())

    with pytest.raises(TournamentValidationError, match="duplicate completed result"):
        load_tournament_document(document)


def test_loader_rejects_unrecognized_stage_type() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    stages = document["stages"]
    assert isinstance(stages, list)
    assert isinstance(stages[0], dict)
    stages[0]["type"] = "custom_stage"

    with pytest.raises(TournamentValidationError, match="recognized stage type"):
        load_tournament_document(document)


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ({"a": ["north-city", "unknown-city"]}, "configured teams"),
        ({"a": ["north-city", "north-city"]}, "duplicate team"),
        (
            {
                "a": ["north-city", "south-city"],
                "b": ["south-city", "north-city"],
            },
            "multiple groups",
        ),
    ],
)
def test_group_stage_rejects_invalid_roster_references(
    groups: dict[str, list[str]],
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    stages = document["stages"]
    assert isinstance(stages, list)
    assert isinstance(stages[0], dict)
    stages[0]["groups"] = groups

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


@pytest.mark.parametrize(
    ("fixtures", "message"),
    [
        (
            [
                {
                    "match_id": "league-1",
                    "home_team_id": "north-city",
                    "away_team_id": "unknown-city",
                }
            ],
            "configured teams",
        ),
        (
            [
                {
                    "match_id": "league-1",
                    "home_team_id": "north-city",
                    "away_team_id": "south-city",
                },
                {
                    "match_id": "league-1",
                    "home_team_id": "south-city",
                    "away_team_id": "north-city",
                },
            ],
            "fixture match ids must be unique",
        ),
        (
            [
                {
                    "match_id": "league-1",
                    "home_team_id": "north-city",
                    "away_team_id": "north-city",
                }
            ],
            "fixture teams must be distinct",
        ),
    ],
)
def test_league_stage_rejects_invalid_fixture_references(
    fixtures: list[dict[str, str]],
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    document["stages"] = [
        {"id": "league-stage", "type": "league_table", "fixtures": fixtures}
    ]
    document["completed_matches"] = []

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


@pytest.mark.parametrize(
    ("stage", "message"),
    [
        (
            {
                "id": "final",
                "type": "knockout",
                "pairing": {"mode": "random", "ties": []},
                "legs": 1,
                "home_away_order": "listed_team_first_leg_home",
                "terminal": "championship",
            },
            "pairing mode",
        ),
        (
            {
                "id": "final",
                "type": "knockout",
                "pairing": {"mode": "fixed", "ties": []},
                "legs": 3,
                "home_away_order": "listed_team_first_leg_home",
                "terminal": "championship",
            },
            "one or two legs",
        ),
        (
            {
                "id": "final",
                "type": "knockout",
                "pairing": {"mode": "fixed", "ties": "final-1"},
                "legs": 1,
                "home_away_order": "listed_team_first_leg_home",
                "terminal": "championship",
            },
            "ties must be a sequence",
        ),
        (
            {
                "id": "final",
                "type": "knockout",
                "pairing": {"mode": "fixed", "ties": []},
                "legs": 1,
                "home_away_order": "listed_team_first_leg_home",
                "aggregate_tiebreak": "coin_flip",
                "terminal": "championship",
            },
            "aggregate tiebreak",
        ),
    ],
)
def test_knockout_stage_rejects_invalid_pairing_contract(
    stage: dict[str, object],
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    document["stages"] = [stage]
    document["completed_matches"] = []

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


def test_loader_preserves_typed_knockout_entrants_as_data() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document

    document = _document()
    document["completed_matches"] = []
    document["stages"] = [
        {
            "id": "groups",
            "type": "round_robin_groups",
            "groups": {"A": ["north-city", "south-city"]},
        },
        {
            "id": "final",
            "type": "knockout",
            "pairing": {
                "mode": "fixed",
                "ties": [
                    {
                        "id": "final-1",
                        "entrants": [
                            {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 1},
                            {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 2},
                        ],
                    }
                ],
            },
            "legs": 1,
            "home_away_order": "listed_team_first_leg_home",
            "terminal": "championship",
        },
    ]

    tournament = load_tournament_document(document)

    entrant = tournament.stages[1]["pairing"]["ties"][0]["entrants"][0]  # type: ignore[index]
    assert entrant == {
        "type": "group_rank",
        "stage_id": "groups",
        "group": "A",
        "rank": 1,
    }


@pytest.mark.parametrize(
    ("entrants", "message"),
    [
        (["1A", {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 2}], "mapping"),
        (
            [
                {"type": "team", "team_id": "north-city"},
                {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 2},
            ],
            "entrant type",
        ),
        (
            [
                {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 0},
                {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 2},
            ],
            "greater than or equal to 1",
        ),
        ([{"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 1}], "two entrants"),
    ],
)
def test_loader_rejects_non_typed_knockout_entrant_contracts(
    entrants: list[object],
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    document["completed_matches"] = []
    document["stages"] = [
        {
            "id": "groups",
            "type": "round_robin_groups",
            "groups": {"A": ["north-city", "south-city"]},
        },
        {
            "id": "final",
            "type": "knockout",
            "pairing": {
                "mode": "fixed",
                "ties": [{"id": "final-1", "entrants": entrants}],
            },
            "legs": 1,
        },
    ]

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


@pytest.mark.parametrize(
    ("second_result", "message"),
    [
        ({"stage_id": "other-stage"}, "same stage"),
        ({"away_team_id": "east-city"}, "same team pair"),
    ],
)
def test_completed_match_legs_keep_stable_identity(
    second_result: dict[str, object],
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    teams = document["teams"]
    ratings = document["ratings"]
    assert isinstance(teams, list)
    assert isinstance(ratings, dict)
    teams.append({"id": "east-city", "display_name": "East City"})
    ratings["east-city"] = 1450
    document["stages"] = [
        _source_group_stage(),
        _two_leg_tie_stage("semi-final", terminal="championship"),
        _two_leg_tie_stage("other-stage", terminal="placement"),
    ]
    first = {
        "match_id": "semi-final-1",
        "stage_id": "semi-final",
        "home_team_id": "north-city",
        "away_team_id": "south-city",
        "score": {"home": 1, "away": 0},
        "leg": 1,
    }
    second = {**first, "leg": 2, **second_result}
    document["completed_matches"] = [first, second]

    with pytest.raises(TournamentValidationError, match=message):
        load_tournament_document(document)


def test_completed_match_allows_reversed_home_away_order_across_legs() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document

    document = _document()
    teams = document["teams"]
    ratings = document["ratings"]
    assert isinstance(teams, list) and isinstance(ratings, dict)
    teams.append({"id": "east-city", "display_name": "East City"})
    ratings["east-city"] = 1450
    source_stage = _source_group_stage()
    from tournament_forecaster.stages.group_stage import generate_group_fixtures

    source_fixture = generate_group_fixtures(source_stage)[0]
    document["stages"] = [
        source_stage,
        _two_leg_tie_stage("semi-final", terminal="championship"),
    ]
    document["completed_matches"] = [
        {
            "match_id": source_fixture.match_id,
            "stage_id": "source-groups",
            "home_team_id": source_fixture.home_team_id,
            "away_team_id": source_fixture.away_team_id,
            "score": {"home": 1, "away": 0},
        },
        {
            "match_id": "semi-final-1",
            "stage_id": "semi-final",
            "home_team_id": "north-city",
            "away_team_id": "south-city",
            "score": {"home": 1, "away": 0},
            "leg": 1,
        },
        {
            "match_id": "semi-final-1",
            "stage_id": "semi-final",
            "home_team_id": "south-city",
            "away_team_id": "north-city",
            "score": {"home": 2, "away": 0},
            "leg": 2,
        },
    ]

    tournament = load_tournament_document(document)

    assert len(tournament.completed_matches) == 3


def test_completed_match_rejects_winner_contradicted_by_score() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    completed_matches = document["completed_matches"]
    assert isinstance(completed_matches, list)
    assert isinstance(completed_matches[0], dict)
    completed_matches[0]["winner_team_id"] = "south-city"

    with pytest.raises(TournamentValidationError, match="winner contradicts score"):
        load_tournament_document(document)


@pytest.mark.parametrize(
    "stage",
    [
        {"id": "group-stage", "type": "round_robin_groups", "groups": {"a": ["north-city", "south-city"]}},
        {"id": "league-stage", "type": "league_table", "fixtures": []},
        {
            "id": "final",
            "type": "knockout",
            "pairing": {"mode": "fixed", "ties": []},
            "legs": 1,
            "home_away_order": "listed_team_first_leg_home",
            "terminal": "championship",
        },
    ],
)
def test_completed_match_leg_must_fit_stage_contract(stage: dict[str, object]) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    if stage["type"] == "knockout":
        stage["pairing"] = _terminal_stage()["pairing"]
        stage["home_away_order"] = "listed_team_first_leg_home"
        stage["terminal"] = "championship"
        document["stages"] = [document["stages"][0], stage]  # type: ignore[index]
    elif stage["type"] == "round_robin_groups":
        document["stages"] = [
            stage,
            _terminal_stage(
                entrants=[
                    {
                        "type": "group_rank",
                        "stage_id": "group-stage",
                        "group": "a",
                        "rank": 1,
                    },
                    {
                        "type": "group_rank",
                        "stage_id": "group-stage",
                        "group": "a",
                        "rank": 2,
                    },
                ]
            ),
        ]
    else:
        document["stages"] = [document["stages"][0], stage, _terminal_stage()]  # type: ignore[index]
    completed_matches = document["completed_matches"]
    assert isinstance(completed_matches, list)
    assert isinstance(completed_matches[0], dict)
    completed_matches[0]["stage_id"] = stage["id"]
    completed_matches[0]["leg"] = 2

    with pytest.raises(TournamentValidationError, match="leg exceeds stage contract"):
        load_tournament_document(document)


def test_completed_group_match_rejects_cross_group_teams() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    teams = document["teams"]
    ratings = document["ratings"]
    assert isinstance(teams, list)
    assert isinstance(ratings, dict)
    teams.extend(
        [
            {"id": "east-city", "display_name": "East City"},
            {"id": "west-city", "display_name": "West City"},
        ]
    )
    ratings.update({"east-city": 1450, "west-city": 1400})
    document["stages"] = [
        {
            "id": "group-stage",
            "type": "round_robin_groups",
            "groups": {
                "a": ["north-city", "south-city"],
                "b": ["east-city", "west-city"],
            },
        },
        _terminal_stage(
            entrants=[
                {
                    "type": "group_rank",
                    "stage_id": "group-stage",
                    "group": "a",
                    "rank": 1,
                },
                {
                    "type": "group_rank",
                    "stage_id": "group-stage",
                    "group": "a",
                    "rank": 2,
                },
            ]
        ),
    ]
    completed_matches = document["completed_matches"]
    assert isinstance(completed_matches, list)
    assert isinstance(completed_matches[0], dict)
    completed_matches[0]["away_team_id"] = "east-city"

    with pytest.raises(TournamentValidationError, match="same configured group"):
        load_tournament_document(document)


def test_completed_league_match_must_reference_configured_fixture() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    document["stages"] = [
        {
            "id": "league-stage",
            "type": "league_table",
            "fixtures": [
                {
                    "match_id": "league-1",
                    "home_team_id": "north-city",
                    "away_team_id": "south-city",
                }
            ],
            "qualification_bands": [
                {"ranks": [1, 2], "destination": "final"},
            ],
        },
        _terminal_stage(
            entrants=[
                {"type": "league_rank", "stage_id": "league-stage", "rank": 1},
                {"type": "league_rank", "stage_id": "league-stage", "rank": 2},
            ]
        ),
    ]
    completed_matches = document["completed_matches"]
    assert isinstance(completed_matches, list)
    assert isinstance(completed_matches[0], dict)
    completed_matches[0]["stage_id"] = "league-stage"
    completed_matches[0]["match_id"] = "league-2"

    with pytest.raises(TournamentValidationError, match="configured league fixture"):
        load_tournament_document(document)


@pytest.mark.parametrize("rating", [float("nan"), float("inf"), float("-inf")])
def test_loader_rejects_non_finite_rating_from_mapping(rating: float) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    ratings = document["ratings"]
    assert isinstance(ratings, dict)
    ratings["north-city"] = rating

    with pytest.raises(TournamentValidationError, match="finite"):
        load_tournament_document(document)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_json_loader_rejects_non_finite_number_syntax(tmp_path: Path, number: str) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament
    from tournament_forecaster.errors import TournamentValidationError

    path = tmp_path / "tournament.json"
    payload = json.dumps(_document()).replace("1600", number, 1)
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(TournamentValidationError, match="finite"):
        load_tournament(path)


def test_json_loader_rejects_exponent_overflow_in_nested_metadata(tmp_path: Path) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    document["metadata"] = {"nested": {"overflow": "OVERFLOW"}}
    payload = json.dumps(document).replace('"OVERFLOW"', "1e999")
    path = tmp_path / "tournament.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(TournamentValidationError, match="finite"):
        load_tournament(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document, value: document.update(metadata={"nested": [value]}),
        lambda document, value: document["teams"][0].update(  # type: ignore[index,union-attr]
            metadata={"nested": [value]}
        ),
        lambda document, value: document["stages"][0].update(  # type: ignore[index,union-attr]
            metadata={"nested": [value]}
        ),
        lambda document, value: document["completed_matches"][0].update(  # type: ignore[index,union-attr]
            metadata={"nested": [value]}
        ),
    ],
    ids=["tournament", "team", "stage", "completed-match"],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_loader_rejects_nested_non_finite_numbers_from_mapping(
    mutate: Callable[[dict[str, object], float], None],
    value: float,
) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    mutate(document, value)

    with pytest.raises(TournamentValidationError, match="finite"):
        load_tournament_document(document)


def test_team_rejects_non_finite_number_in_nested_metadata() -> None:
    _require_package()
    from tournament_forecaster.domain import Team
    from tournament_forecaster.errors import TournamentValidationError

    with pytest.raises(TournamentValidationError, match="finite"):
        Team(
            id="north-city",
            display_name="North City",
            metadata={"nested": [float("nan")]},
        )


def test_tournament_rejects_non_finite_number_in_nested_stage_metadata() -> None:
    _require_package()
    from tournament_forecaster.domain import Team, Tournament
    from tournament_forecaster.errors import TournamentValidationError

    with pytest.raises(TournamentValidationError, match="finite"):
        Tournament(
            id="synthetic-cup",
            display_name="Synthetic Cup",
            focus_team_id="north-city",
            teams=(
                Team(id="north-city", display_name="North City"),
                Team(id="south-city", display_name="South City"),
            ),
            stages=(
                {
                    "id": "group-stage",
                    "type": "round_robin_groups",
                    "groups": {"A": ["north-city", "south-city"]},
                    "metadata": {"nested": [float("inf")]},
                },
            ),
            ratings={"north-city": 1600.0, "south-city": 1500.0},
            completed_matches=(),
        )


def test_forecast_rejects_non_finite_number_in_nested_provenance() -> None:
    _require_package()
    from tournament_forecaster.domain import Forecast
    from tournament_forecaster.errors import TournamentValidationError

    with pytest.raises(TournamentValidationError, match="finite"):
        Forecast(
            run_id="run-0001",
            generated_at="2026-07-10T12:00:00+00:00",
            tournament_id="synthetic-cup",
            focus_team_id="north-city",
            stage_probabilities={"group-stage": 1.0},
            stage_order=("group-stage",),
            matchup_probabilities=(),
            championship_probability=0.18,
            confidence_intervals={"championship_probability": (0.12, 0.24)},
            input_provenance=(
                {"kind": "preset", "metadata": {"nested": [float("-inf")]}},
            ),
            warnings=(),
        )


@pytest.mark.parametrize("required_key", ["ratings", "completed_matches"])
def test_loader_requires_explicit_rating_and_result_collections(required_key: str) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    document.pop(required_key)

    with pytest.raises(TournamentValidationError, match=required_key.replace("_", " ")):
        load_tournament_document(document)


@pytest.mark.parametrize(
    "location",
    [
        "root",
        "tournament",
        "team",
        "group-stage",
        "group-points",
        "group-qualification",
        "league-fixture",
        "league-band",
        "knockout-pairing",
        "completed-match",
        "score",
    ],
)
def test_loader_rejects_schema_forbidden_properties(location: str) -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    tournament = document["tournament"]
    teams = document["teams"]
    stages = document["stages"]
    completed_matches = document["completed_matches"]
    assert isinstance(tournament, dict)
    assert isinstance(teams, list) and isinstance(teams[0], dict)
    assert isinstance(stages, list) and isinstance(stages[0], dict)
    assert isinstance(completed_matches, list) and isinstance(completed_matches[0], dict)

    if location == "root":
        document["unexpected"] = True
    elif location == "tournament":
        tournament["unexpected"] = True
    elif location == "team":
        teams[0]["unexpected"] = True
    elif location == "group-stage":
        stages[0]["unexpected"] = True
    elif location == "group-points":
        stages[0]["points"] = {"win": 3, "draw": 1, "loss": 0, "unexpected": 1}
    elif location == "group-qualification":
        stages[0]["qualification"] = {
            "direct_per_group": 1,
            "best_additional": 0,
            "unexpected": 1,
        }
    elif location == "league-fixture":
        stages[0] = {
            "id": "group-stage",
            "type": "league_table",
            "fixtures": [
                {
                    "match_id": "group-a-1",
                    "home_team_id": "north-city",
                    "away_team_id": "south-city",
                    "unexpected": True,
                }
            ],
        }
    elif location == "league-band":
        stages[0] = {
            "id": "group-stage",
            "type": "league_table",
            "fixtures": [
                {
                    "match_id": "group-a-1",
                    "home_team_id": "north-city",
                    "away_team_id": "south-city",
                }
            ],
            "qualification_bands": [
                {"ranks": [1, 2], "destination": "final", "unexpected": True}
            ],
        }
    elif location == "knockout-pairing":
        stages[0] = {
            "id": "group-stage",
            "type": "knockout",
            "pairing": {"mode": "fixed", "ties": [], "unexpected": True},
            "legs": 1,
        }
    elif location == "completed-match":
        completed_matches[0]["unexpected"] = True
    else:
        score = completed_matches[0]["score"]
        assert isinstance(score, dict)
        score["unexpected"] = 1

    with pytest.raises(TournamentValidationError, match="unknown propert"):
        load_tournament_document(document)


def test_loader_and_team_reject_duplicate_aliases() -> None:
    _require_package()
    from tournament_forecaster.config import load_tournament_document
    from tournament_forecaster.domain import Team
    from tournament_forecaster.errors import TournamentValidationError

    document = _document()
    teams = document["teams"]
    assert isinstance(teams, list) and isinstance(teams[0], dict)
    teams[0]["aliases"] = ["North", "North"]

    with pytest.raises(TournamentValidationError, match="aliases must be unique"):
        load_tournament_document(document)
    with pytest.raises(TournamentValidationError, match="aliases must be unique"):
        Team(
            id="north-city",
            display_name="North City",
            aliases=("North", "North"),
        )


@pytest.mark.parametrize(
    ("provenance", "message"),
    [
        ({}, "kind"),
        ({"kind": "preset", "unexpected": True}, "unknown propert"),
        ({"kind": ""}, "kind"),
        ({"kind": "preset", "source_id": "Source A"}, "stable ASCII identifier"),
        ({"kind": "preset", "metadata": []}, "metadata must be a mapping"),
    ],
)
def test_forecast_rejects_schema_invalid_provenance(
    provenance: dict[str, object],
    message: str,
) -> None:
    _require_package()
    from tournament_forecaster.domain import Forecast
    from tournament_forecaster.errors import TournamentValidationError

    with pytest.raises(TournamentValidationError, match=message):
        Forecast(
            run_id="run-0001",
            generated_at="2026-07-10T12:00:00+00:00",
            tournament_id="synthetic-cup",
            focus_team_id="north-city",
            stage_probabilities={"group-stage": 1.0},
            stage_order=("group-stage",),
            matchup_probabilities=(),
            championship_probability=0.18,
            confidence_intervals={"championship_probability": (0.12, 0.24)},
            input_provenance=(provenance,),
            warnings=(),
        )


def test_direct_domain_construction_rejects_non_team_values() -> None:
    _require_package()
    from tournament_forecaster.domain import Tournament
    from tournament_forecaster.errors import TournamentValidationError

    with pytest.raises(TournamentValidationError, match="teams must be Team values"):
        Tournament(
            id="synthetic-cup",
            display_name="Synthetic Cup",
            focus_team_id="north-city",
            teams=("north-city",),  # type: ignore[arg-type]
            stages=({"id": "group-stage", "type": "round_robin_groups"},),
            ratings={},
            completed_matches=(),
        )


@pytest.mark.parametrize("aliases", ["North", b"North", None])
def test_team_rejects_malformed_alias_containers(aliases: object) -> None:
    _require_package()
    from tournament_forecaster.domain import Team
    from tournament_forecaster.errors import TournamentValidationError

    with pytest.raises(TournamentValidationError, match="aliases must be a sequence"):
        Team(id="north-city", display_name="North City", aliases=aliases)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["teams", "stages", "completed_matches"])
@pytest.mark.parametrize("value", ["invalid", b"invalid", None])
def test_tournament_rejects_malformed_sequence_containers(field: str, value: object) -> None:
    _require_package()
    from tournament_forecaster.domain import Team, Tournament
    from tournament_forecaster.errors import TournamentValidationError

    values: dict[str, object] = {
        "id": "synthetic-cup",
        "display_name": "Synthetic Cup",
        "focus_team_id": "north-city",
        "teams": (
            Team(id="north-city", display_name="North City"),
            Team(id="south-city", display_name="South City"),
        ),
        "stages": (
            {
                "id": "group-stage",
                "type": "round_robin_groups",
                "groups": {"A": ["north-city", "south-city"]},
            },
        ),
        "ratings": {"north-city": 1600.0, "south-city": 1500.0},
        "completed_matches": (),
    }
    values[field] = value

    with pytest.raises(TournamentValidationError, match=f"{field.replace('_', ' ')} must be a sequence"):
        Tournament(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["matchup_probabilities", "input_provenance", "warnings"])
@pytest.mark.parametrize("value", ["invalid", b"invalid", None])
def test_forecast_rejects_malformed_sequence_containers(field: str, value: object) -> None:
    _require_package()
    from tournament_forecaster.domain import Forecast
    from tournament_forecaster.errors import TournamentValidationError

    values: dict[str, object] = {
        "run_id": "run-0001",
        "generated_at": "2026-07-10T12:00:00+00:00",
        "tournament_id": "synthetic-cup",
        "focus_team_id": "north-city",
        "stage_probabilities": {"group-stage": 1.0},
        "stage_order": ("group-stage",),
        "matchup_probabilities": (),
        "championship_probability": 0.18,
        "confidence_intervals": {"championship_probability": (0.12, 0.24)},
        "input_provenance": (),
        "warnings": (),
    }
    values[field] = value

    with pytest.raises(TournamentValidationError, match=field.replace("_", " ")):
        Forecast(**values)  # type: ignore[arg-type]


def test_forecast_serializes_the_versioned_generic_contract() -> None:
    _require_package()
    from tournament_forecaster.domain import Forecast, MatchupProbability

    forecast = Forecast(
        run_id="run-0001",
        generated_at="2026-07-10T12:00:00+00:00",
        tournament_id="synthetic-cup",
        focus_team_id="north-city",
        stage_probabilities={"group-stage": 1.0, "final": 0.25},
        stage_order=("group-stage", "final"),
        matchup_probabilities=(
            MatchupProbability(
                stage_id="final",
                opponent_team_id="south-city",
                probability=0.4,
            ),
        ),
        championship_probability=0.18,
        confidence_intervals={"championship_probability": (0.12, 0.24)},
        input_provenance=({"kind": "preset", "name": "synthetic-cup"},),
        warnings=("rating coverage is incomplete",),
        council={"enabled": False},
    )

    assert forecast.to_dict() == {
        "schema_version": 2,
        "run_id": "run-0001",
        "generated_at": "2026-07-10T12:00:00+00:00",
        "tournament_id": "synthetic-cup",
        "focus_team_id": "north-city",
        "stage_probabilities": {"group-stage": 1.0, "final": 0.25},
        "stage_order": ["group-stage", "final"],
        "matchup_probabilities": [
            {
                "stage_id": "final",
                "opponent_team_id": "south-city",
                "probability": 0.4,
            }
        ],
        "championship_probability": 0.18,
        "confidence_intervals": {"championship_probability": [0.12, 0.24]},
        "input_provenance": [{"kind": "preset", "name": "synthetic-cup"}],
        "warnings": ["rating coverage is incomplete"],
        "council": {"enabled": False},
    }

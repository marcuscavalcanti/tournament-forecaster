from __future__ import annotations

import random

from tournament_forecaster.pairing import build_pairings
from tournament_forecaster.qualification import QualificationState, resolve_entrant


def _state() -> QualificationState:
    return QualificationState(
        group_rankings={
            "groups": {
                "A": ("alpha", "bravo"),
                "B": ("charlie", "delta"),
            }
        },
        best_additional={"groups": ("echo", "foxtrot")},
        league_rankings={"league": ("golf", "hotel")},
        match_winners={"semi-1": "india", "semi-2": "juliet"},
    )


def test_resolve_entrant_supports_all_typed_sources_without_string_parsing() -> None:
    state = _state()

    assert resolve_entrant(
        {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 2},
        state,
    ) == "bravo"
    assert resolve_entrant(
        {"type": "best_additional", "stage_id": "groups", "rank": 1},
        state,
    ) == "echo"
    assert resolve_entrant(
        {"type": "league_rank", "stage_id": "league", "rank": 2},
        state,
    ) == "hotel"
    assert resolve_entrant(
        {"type": "match_winner", "match_id": "semi-1"},
        state,
    ) == "india"


def _draw_ties() -> tuple[dict[str, object], ...]:
    return (
        {
            "id": "tie-2",
            "entrants": [
                {"type": "group_rank", "stage_id": "groups", "group": "B", "rank": 1},
                {"type": "group_rank", "stage_id": "groups", "group": "B", "rank": 2},
            ],
        },
        {
            "id": "tie-1",
            "entrants": [
                {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 1},
                {"type": "group_rank", "stage_id": "groups", "group": "A", "rank": 2},
            ],
        },
    )


def test_fixed_pairing_preserves_tie_sources_in_stable_tie_order() -> None:
    pairings = build_pairings("fixed", _draw_ties(), _state(), random.Random(999))

    assert [(pairing.match_id, pairing.first_team_id, pairing.second_team_id) for pairing in pairings] == [
        ("tie-1", "alpha", "bravo"),
        ("tie-2", "charlie", "delta"),
    ]


def test_seeded_draw_is_replayable_and_keeps_seed_pots_apart() -> None:
    first = build_pairings("seeded_draw", _draw_ties(), _state(), random.Random(17))
    replay = build_pairings("seeded_draw", _draw_ties(), _state(), random.Random(17))

    assert first == replay
    assert {pairing.first_team_id for pairing in first} == {"alpha", "charlie"}
    assert {pairing.second_team_id for pairing in first} == {"bravo", "delta"}


def test_open_draw_is_replayable_and_uses_every_entrant_once() -> None:
    first = build_pairings("open_draw", _draw_ties(), _state(), random.Random(23))
    replay = build_pairings("open_draw", _draw_ties(), _state(), random.Random(23))

    assert first == replay
    assert sorted(
        team_id
        for pairing in first
        for team_id in (pairing.first_team_id, pairing.second_team_id)
    ) == ["alpha", "bravo", "charlie", "delta"]

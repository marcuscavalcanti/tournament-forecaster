from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from tournament_forecaster.config import load_tournament
from tournament_forecaster.domain import SimulationOptions
from tournament_forecaster.simulation import simulate_tournament


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIR = ROOT / "examples" / "copa-libertadores-2026-live"
EXAMPLE_CONFIG = EXAMPLE_DIR / "tournament.json"


def _stage(tournament: object, stage_id: str) -> Mapping[str, object]:
    stages = getattr(tournament, "stages")
    return next(stage for stage in stages if stage["id"] == stage_id)


def _tie_entrants(stage: Mapping[str, object], tie_id: str) -> tuple[str, str]:
    pairing = stage["pairing"]
    assert isinstance(pairing, Mapping)
    ties = pairing["ties"]
    assert isinstance(ties, Sequence)
    tie = next(tie for tie in ties if isinstance(tie, Mapping) and tie["id"] == tie_id)
    entrants = tie["entrants"]
    assert isinstance(entrants, Sequence)
    return tuple(str(entrant["team_id"]) for entrant in entrants if isinstance(entrant, Mapping))  # type: ignore[return-value]


def _tie_match_winner_sources(stage: Mapping[str, object], tie_id: str) -> tuple[str, str]:
    pairing = stage["pairing"]
    assert isinstance(pairing, Mapping)
    ties = pairing["ties"]
    assert isinstance(ties, Sequence)
    tie = next(tie for tie in ties if isinstance(tie, Mapping) and tie["id"] == tie_id)
    entrants = tie["entrants"]
    assert isinstance(entrants, Sequence)
    return tuple(str(entrant["match_id"]) for entrant in entrants if isinstance(entrant, Mapping))  # type: ignore[return-value]


def test_copa_libertadores_snapshot_has_official_r16_field_and_progression() -> None:
    tournament = load_tournament(EXAMPLE_CONFIG)

    assert tournament.focus_team_id == "palmeiras"
    assert tournament.knockout_seeds["palmeiras"] == 11
    assert len(tournament.teams) == 16
    round_of_16 = _stage(tournament, "round-of-16")
    quarter_finals = _stage(tournament, "quarter-finals")
    semi_finals = _stage(tournament, "semi-finals")
    final = _stage(tournament, "final")

    assert _tie_entrants(round_of_16, "r16-g") == ("palmeiras", "cerro-porteno")
    assert quarter_finals["pairing"]["mode"] == "fixed"
    assert {
        tie_id: _tie_match_winner_sources(quarter_finals, tie_id)
        for tie_id in (
            "quarter-final-1",
            "quarter-final-2",
            "quarter-final-3",
            "quarter-final-4",
        )
    } == {
        "quarter-final-1": ("r16-a", "r16-h"),
        "quarter-final-2": ("r16-b", "r16-g"),
        "quarter-final-3": ("r16-c", "r16-f"),
        "quarter-final-4": ("r16-d", "r16-e"),
    }
    assert semi_finals["pairing"]["mode"] == "fixed"
    assert {
        tie_id: _tie_match_winner_sources(semi_finals, tie_id)
        for tie_id in ("semi-final-1", "semi-final-2")
    } == {
        "semi-final-1": ("quarter-final-1", "quarter-final-4"),
        "semi-final-2": ("quarter-final-2", "quarter-final-3"),
    }
    assert all(
        stage["legs"] == 2
        and stage["home_away_order"] == "better_seed_second_leg_home"
        and stage["aggregate_tiebreak"] == "penalties"
        for stage in (round_of_16, quarter_finals, semi_finals)
    )
    assert final["legs"] == 1
    assert final["aggregate_tiebreak"] == "extra_time_then_penalties"


def test_copa_libertadores_snapshot_replays_deterministically() -> None:
    tournament = load_tournament(EXAMPLE_CONFIG)
    options = SimulationOptions(seed=20260713, iterations=240)

    first = simulate_tournament(tournament, options=options)
    replay = simulate_tournament(tournament, options=options)

    assert first.run_id == replay.run_id
    assert first.stage_order == (
        "round-of-16",
        "quarter-finals",
        "semi-finals",
        "final",
    )
    assert first.stage_probabilities == replay.stage_probabilities
    assert first.matchup_probabilities == replay.matchup_probabilities
    assert first.championship_probability == replay.championship_probability
    assert first.stage_probabilities["round-of-16"] == 1.0
    assert 0.0 <= first.championship_probability <= first.stage_probabilities["final"]

"""JSON tournament configuration loading and normalization."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from .domain import CompletedMatch, Score, Team, Tournament
from .errors import TournamentValidationError


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TournamentValidationError(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise TournamentValidationError(f"{label} must use string keys")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise TournamentValidationError(f"{label} must be an array")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TournamentValidationError(f"{label} must be text")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TournamentValidationError(f"{label} must be an integer")
    return value


def _optional_mapping(document: Mapping[str, object], key: str, label: str) -> Mapping[str, object]:
    value = document.get(key, {})
    return _mapping(value, label)


def _team(document: Mapping[str, object], index: int) -> Team:
    aliases = _list(document.get("aliases", []), f"teams[{index}].aliases")
    return Team(
        id=_string(document.get("id"), f"teams[{index}].id"),
        display_name=_string(document.get("display_name"), f"teams[{index}].display_name"),
        aliases=tuple(_string(alias, f"teams[{index}].aliases") for alias in aliases),
        metadata=_optional_mapping(document, "metadata", f"teams[{index}].metadata"),
    )


def _completed_match(document: Mapping[str, object], index: int) -> CompletedMatch:
    score = _mapping(document.get("score"), f"completed_matches[{index}].score")
    winner_team_id = document.get("winner_team_id")
    if winner_team_id is not None:
        winner_team_id = _string(winner_team_id, f"completed_matches[{index}].winner_team_id")
    return CompletedMatch(
        match_id=_string(document.get("match_id"), f"completed_matches[{index}].match_id"),
        stage_id=_string(document.get("stage_id"), f"completed_matches[{index}].stage_id"),
        home_team_id=_string(document.get("home_team_id"), f"completed_matches[{index}].home_team_id"),
        away_team_id=_string(document.get("away_team_id"), f"completed_matches[{index}].away_team_id"),
        score=Score(
            home=_integer(score.get("home"), f"completed_matches[{index}].score.home"),
            away=_integer(score.get("away"), f"completed_matches[{index}].score.away"),
        ),
        leg=_integer(document.get("leg", 1), f"completed_matches[{index}].leg"),
        winner_team_id=winner_team_id,
        metadata=_optional_mapping(document, "metadata", f"completed_matches[{index}].metadata"),
    )


def load_tournament_document(document: Mapping[str, object]) -> Tournament:
    """Convert a JSON-compatible tournament document into immutable domain values."""

    root = _mapping(document, "tournament document")
    tournament = _mapping(root.get("tournament"), "tournament")
    teams = tuple(_team(_mapping(value, f"teams[{index}]"), index) for index, value in enumerate(_list(root.get("teams"), "teams")))
    stages = tuple(
        dict(_mapping(value, f"stages[{index}]"))
        for index, value in enumerate(_list(root.get("stages"), "stages"))
    )
    ratings_document = _mapping(root.get("ratings", {}), "ratings")
    ratings: dict[str, float] = {}
    for team_id, rating in ratings_document.items():
        if isinstance(rating, bool) or not isinstance(rating, (int, float)):
            raise TournamentValidationError("ratings must contain numeric values")
        ratings[team_id] = float(rating)
    completed_matches = tuple(
        _completed_match(_mapping(value, f"completed_matches[{index}]"), index)
        for index, value in enumerate(_list(root.get("completed_matches", []), "completed_matches"))
    )
    season = tournament.get("season")
    if season is not None:
        season = _string(season, "tournament.season")
    schema_version = _integer(root.get("schema_version"), "schema_version")
    return Tournament(
        id=_string(tournament.get("id"), "tournament.id"),
        display_name=_string(tournament.get("display_name"), "tournament.display_name"),
        focus_team_id=_string(root.get("focus_team_id"), "focus_team_id"),
        teams=teams,
        stages=stages,
        ratings=ratings,
        completed_matches=completed_matches,
        season=season,
        metadata=_optional_mapping(root, "metadata", "metadata"),
        schema_version=schema_version,
    )


def load_tournament(path: Path) -> Tournament:
    """Load one UTF-8 JSON tournament document from ``path``."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise TournamentValidationError(f"invalid tournament JSON: {error.msg}") from error
    return load_tournament_document(_mapping(document, "tournament document"))

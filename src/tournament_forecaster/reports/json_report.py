"""Versioned forecast JSON serialization and loading."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from ..domain import Forecast, MatchupProbability
from ..errors import TournamentValidationError


_REQUIRED_PROPERTIES = frozenset(
    {
        "schema_version",
        "run_id",
        "generated_at",
        "tournament_id",
        "focus_team_id",
        "stage_probabilities",
        "matchup_probabilities",
        "championship_probability",
        "confidence_intervals",
        "input_provenance",
        "warnings",
        "council",
    }
)
_OPTIONAL_PROPERTIES = frozenset(
    {"stage_order", "tournament_display_name", "team_display_names", "simulation"}
)


def _reject_non_finite(value: str) -> object:
    raise TournamentValidationError(f"forecast JSON number {value} must be finite")


def _parse_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise TournamentValidationError(f"forecast JSON number {value} must be finite")
    return parsed


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise TournamentValidationError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TournamentValidationError(f"{label} must be an array")
    return value


def render_json_report(forecast: Forecast) -> str:
    """Return deterministic, finite, UTF-8-ready forecast JSON."""

    return json.dumps(
        forecast.to_dict(),
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def forecast_from_document(document: Mapping[str, object]) -> Forecast:
    """Build a forecast, preserving v2 insertion order when stage_order is absent."""

    version = document.get("schema_version")
    if version != Forecast.SCHEMA_VERSION:
        raise TournamentValidationError(
            f"unsupported forecast schema version: {version!r}"
        )
    missing = sorted(_REQUIRED_PROPERTIES - set(document))
    if missing:
        raise TournamentValidationError(
            f"forecast document is missing required properties: {', '.join(missing)}"
        )
    unknown = sorted(set(document) - _REQUIRED_PROPERTIES - _OPTIONAL_PROPERTIES)
    if unknown:
        raise TournamentValidationError(
            f"forecast document contains unknown properties: {', '.join(unknown)}"
        )

    matchup_documents = _sequence(
        document["matchup_probabilities"],
        "forecast matchup probabilities",
    )
    matchups: list[MatchupProbability] = []
    for index, value in enumerate(matchup_documents):
        matchup = _mapping(value, f"forecast matchup probabilities[{index}]")
        if set(matchup) != {"stage_id", "opponent_team_id", "probability"}:
            raise TournamentValidationError(
                "forecast matchup probabilities contain invalid properties"
            )
        matchups.append(
            MatchupProbability(
                stage_id=matchup["stage_id"],  # type: ignore[arg-type]
                opponent_team_id=matchup["opponent_team_id"],  # type: ignore[arg-type]
                probability=matchup["probability"],  # type: ignore[arg-type]
            )
        )

    warnings = _sequence(document["warnings"], "forecast warnings")
    provenance = _sequence(document["input_provenance"], "forecast input provenance")
    stage_probabilities = cast(
        Mapping[str, float],
        _mapping(
            document["stage_probabilities"],
            "forecast stage probabilities",
        ),
    )
    stage_order_value = document.get("stage_order")
    stage_order = (
        tuple(stage_probabilities)
        if "stage_order" not in document
        else tuple(
            cast(
                Sequence[str],
                _sequence(stage_order_value, "forecast stage order"),
            )
        )
    )
    return Forecast(
        run_id=document["run_id"],  # type: ignore[arg-type]
        generated_at=document["generated_at"],  # type: ignore[arg-type]
        tournament_id=document["tournament_id"],  # type: ignore[arg-type]
        focus_team_id=document["focus_team_id"],  # type: ignore[arg-type]
        stage_probabilities=stage_probabilities,
        stage_order=stage_order,
        matchup_probabilities=tuple(matchups),
        championship_probability=document["championship_probability"],  # type: ignore[arg-type]
        confidence_intervals=_mapping(
            document["confidence_intervals"],
            "forecast confidence intervals",
        ),  # type: ignore[arg-type]
        input_provenance=tuple(
            _mapping(item, "forecast input provenance item") for item in provenance
        ),
        warnings=tuple(warnings),  # type: ignore[arg-type]
        council=(
            None
            if document["council"] is None
            else _mapping(document["council"], "forecast council")
        ),
        tournament_display_name=document.get("tournament_display_name"),  # type: ignore[arg-type]
        team_display_names=_mapping(
            document.get("team_display_names", {}),
            "forecast team display names",
        ),  # type: ignore[arg-type]
        simulation=_mapping(
            document.get("simulation", {}),
            "forecast simulation metadata",
        ),
    )


def load_forecast(path: Path) -> Forecast:
    """Load a finite, supported forecast JSON artifact from ``path``."""

    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_non_finite,
            parse_float=_parse_float,
        )
    except json.JSONDecodeError as error:
        raise TournamentValidationError(f"invalid forecast JSON: {error.msg}") from error
    return forecast_from_document(_mapping(document, "forecast document"))

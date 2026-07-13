"""Structured, validated values exchanged by council participants."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from ..errors import TournamentValidationError


_CODE_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL | re.IGNORECASE)
_OPINION_PROPERTIES = frozenset(
    {
        "stage_probabilities",
        "championship_probability",
        "confidence",
        "summary",
        "key_factors",
    }
)


def _probability(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TournamentValidationError(f"{label} must be a probability")
    probability = float(value)
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise TournamentValidationError(f"{label} must be between 0 and 1")
    return probability


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TournamentValidationError(f"{label} must be non-empty text")
    return " ".join(value.split())


def _json_object(text: str) -> Mapping[str, object]:
    match = _CODE_FENCE.search(text)
    payload = match.group(1) if match else text[text.find("{") : text.rfind("}") + 1]
    if not payload or not payload.startswith("{") or not payload.endswith("}"):
        raise TournamentValidationError("council response must contain one JSON object")
    try:
        document = json.loads(payload, parse_constant=lambda value: (_ for _ in ()).throw(
            TournamentValidationError(f"council response number {value} must be finite")
        ))
    except json.JSONDecodeError as error:
        raise TournamentValidationError(
            f"invalid council response JSON: {error.msg}"
        ) from error
    if not isinstance(document, Mapping) or not all(
        isinstance(key, str) for key in document
    ):
        raise TournamentValidationError("council response must be a JSON object")
    return document


@dataclass(frozen=True, slots=True)
class CouncilOpinion:
    """One validated probability position from one council participant."""

    agent_id: str
    round_number: int
    stage_probabilities: Mapping[str, float]
    championship_probability: float
    confidence: float
    summary: str
    key_factors: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stage_probabilities",
            MappingProxyType(dict(self.stage_probabilities)),
        )
        object.__setattr__(self, "key_factors", tuple(self.key_factors))

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "round": self.round_number,
            "stage_probabilities": dict(self.stage_probabilities),
            "championship_probability": self.championship_probability,
            "confidence": self.confidence,
            "summary": self.summary,
            "key_factors": list(self.key_factors),
        }


def parse_opinion(
    text: str,
    *,
    agent_id: str,
    round_number: int,
    stage_order: Sequence[str],
    locked_stage_probabilities: Mapping[str, float],
) -> CouncilOpinion:
    """Parse and validate a provider's JSON-only council position."""

    document = _json_object(text)
    unknown = sorted(set(document) - _OPINION_PROPERTIES)
    if unknown:
        raise TournamentValidationError(
            f"council opinion contains unknown properties: {', '.join(unknown)}"
        )
    raw_stages = document.get("stage_probabilities")
    if not isinstance(raw_stages, Mapping) or not all(
        isinstance(key, str) for key in raw_stages
    ):
        raise TournamentValidationError(
            "council opinion must contain all stage probabilities"
        )
    expected = tuple(stage_order)
    if set(raw_stages) != set(expected):
        raise TournamentValidationError(
            "council opinion must contain all stage probabilities"
        )
    stages = {
        stage_id: _probability(raw_stages[stage_id], f"council stage {stage_id}")
        for stage_id in expected
    }
    for stage_id, locked in locked_stage_probabilities.items():
        if stage_id in stages and not math.isclose(stages[stage_id], locked, abs_tol=1e-9):
            raise TournamentValidationError(
                f"council opinion changed locked stage {stage_id}"
            )
    ordered = [stages[stage_id] for stage_id in expected]
    if any(later > earlier + 1e-9 for earlier, later in zip(ordered, ordered[1:])):
        raise TournamentValidationError(
            "council stage probabilities must be non-increasing"
        )
    championship = _probability(
        document.get("championship_probability"),
        "council championship_probability",
    )
    if ordered and championship > ordered[-1] + 1e-9:
        raise TournamentValidationError(
            "council championship probability cannot exceed final-stage reach"
        )
    raw_factors = document.get("key_factors")
    if (
        not isinstance(raw_factors, Sequence)
        or isinstance(raw_factors, (str, bytes, bytearray))
    ):
        raise TournamentValidationError("council key_factors must be an array")
    factors = tuple(
        _text(value, f"council key_factors[{index}]")
        for index, value in enumerate(raw_factors)
    )
    if len(factors) > 12:
        raise TournamentValidationError("council key_factors must contain at most 12 items")
    return CouncilOpinion(
        agent_id=agent_id,
        round_number=round_number,
        stage_probabilities=MappingProxyType(stages),
        championship_probability=championship,
        confidence=_probability(document.get("confidence"), "council confidence"),
        summary=_text(document.get("summary"), "council summary"),
        key_factors=factors,
    )

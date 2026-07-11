"""Leakage-resistant deterministic 1X2 backtest evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .errors import TournamentValidationError
from .probabilities import predict_match_outcomes


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ROOT_PROPERTIES = frozenset(
    {
        "schema_version",
        "model_version",
        "home_advantage_rating_points",
        "ratings",
        "ratings_sha256",
        "cases",
        "metadata",
    }
)
_CASE_PROPERTIES = frozenset(
    {
        "source_id",
        "captured_at",
        "kickoff_at",
        "home_team_id",
        "away_team_id",
        "result",
        "metadata",
    }
)


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """Computed metrics and explicit sample sufficiency state."""

    status: str
    ok: bool
    sample_size: int
    min_resolved: int
    metrics: Mapping[str, float | None]
    uniform_baseline: Mapping[str, float | None]
    exclusions: tuple[Mapping[str, str], ...]
    model_version: str
    ratings_sha256: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "ok": self.ok,
            "sample_size": self.sample_size,
            "min_resolved": self.min_resolved,
            "metrics": dict(self.metrics),
            "uniform_baseline": dict(self.uniform_baseline),
            "exclusions": [dict(exclusion) for exclusion in self.exclusions],
            "model_version": self.model_version,
            "ratings_sha256": self.ratings_sha256,
        }


def ratings_sha256(ratings: Mapping[str, float]) -> str:
    """Hash the canonical JSON ratings object used by a backtest."""

    payload = json.dumps(
        dict(ratings),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_backtest(path: Path) -> Mapping[str, object]:
    """Load a finite-number JSON backtest document from disk."""

    def reject_constant(value: str) -> object:
        raise TournamentValidationError(f"JSON number {value} must be finite")

    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise TournamentValidationError(f"invalid backtest JSON: {error.msg}") from error
    return _mapping(value, "backtest document")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise TournamentValidationError(f"{label} must be an object with string keys")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TournamentValidationError(f"{label} must be an array")
    return value


def _reject_unknown(value: Mapping[str, object], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise TournamentValidationError(
            f"{label} contains unknown properties: {', '.join(unknown)}"
        )


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TournamentValidationError(f"{label} must be non-empty text")
    return value


def _finite_number(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise TournamentValidationError(f"{label} must be finite numeric")
    return float(value)


def _timestamp(value: object, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise TournamentValidationError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TournamentValidationError(f"{label} must include a timezone")
    return parsed


def _null_metrics() -> dict[str, None]:
    return {
        "rps": None,
        "brier": None,
        "log_loss": None,
        "top_pick_accuracy": None,
    }


def evaluate_backtest(
    document: Mapping[str, object],
    *,
    min_resolved: int = 1,
) -> BacktestReport:
    """Evaluate exact neutral-or-explicit-site 1X2 probabilities without sampling."""

    if isinstance(min_resolved, bool) or not isinstance(min_resolved, int) or min_resolved < 1:
        raise TournamentValidationError("min_resolved must be an integer greater than or equal to 1")
    root = _mapping(document, "backtest document")
    _reject_unknown(root, _ROOT_PROPERTIES, "backtest document")
    if root.get("schema_version") != 1:
        raise TournamentValidationError("unsupported backtest schema version")
    model_version = _text(root.get("model_version"), "model_version")
    home_advantage = _finite_number(
        root.get("home_advantage_rating_points", 0.0),
        "home_advantage_rating_points",
    )
    ratings_document = _mapping(root.get("ratings"), "ratings")
    ratings: dict[str, float] = {}
    for team_id, value in ratings_document.items():
        ratings[_text(team_id, "rating team id")] = _finite_number(value, f"rating {team_id}")
    declared_hash = _text(root.get("ratings_sha256"), "ratings_sha256")
    if not _SHA256.fullmatch(declared_hash) or declared_hash != ratings_sha256(ratings):
        raise TournamentValidationError("ratings_sha256 does not match the canonical ratings object")

    metrics_total = {"rps": 0.0, "brier": 0.0, "log_loss": 0.0, "top_pick_accuracy": 0.0}
    baseline_total = {"rps": 0.0, "brier": 0.0, "log_loss": 0.0}
    exclusions: list[Mapping[str, str]] = []
    source_ids: set[str] = set()
    sample_size = 0
    for index, case_value in enumerate(_sequence(root.get("cases"), "cases")):
        case = _mapping(case_value, f"cases[{index}]")
        _reject_unknown(case, _CASE_PROPERTIES, f"cases[{index}]")
        source_id = _text(case.get("source_id"), f"cases[{index}].source_id")
        if source_id in source_ids:
            raise TournamentValidationError("backtest source_id values must be unique")
        source_ids.add(source_id)
        captured_at = _timestamp(case.get("captured_at"), f"cases[{index}].captured_at")
        kickoff_at = _timestamp(case.get("kickoff_at"), f"cases[{index}].kickoff_at")
        if captured_at >= kickoff_at:
            raise TournamentValidationError("captured_at must be before kickoff_at")
        home_team_id = _text(case.get("home_team_id"), f"cases[{index}].home_team_id")
        away_team_id = _text(case.get("away_team_id"), f"cases[{index}].away_team_id")
        if home_team_id == away_team_id:
            raise TournamentValidationError("backtest case teams must be distinct")
        if home_team_id not in ratings or away_team_id not in ratings:
            raise TournamentValidationError("backtest case references a team without a frozen rating")
        result_value = case.get("result")
        if result_value is None:
            exclusions.append({"source_id": source_id, "reason": "unresolved_result"})
            continue
        result = _mapping(result_value, f"cases[{index}].result")
        if set(result) != {"home", "away"}:
            raise TournamentValidationError("backtest result must contain only home and away")
        scores: list[int] = []
        for side in ("home", "away"):
            score = result[side]
            if isinstance(score, bool) or not isinstance(score, int) or score < 0:
                raise TournamentValidationError("backtest result scores must be non-negative integers")
            scores.append(score)

        probabilities = predict_match_outcomes(
            ratings[home_team_id],
            ratings[away_team_id],
            home_advantage_points=home_advantage,
        )
        predicted = (probabilities.home_win, probabilities.draw, probabilities.away_win)
        actual_index = 0 if scores[0] > scores[1] else 1 if scores[0] == scores[1] else 2
        actual = tuple(1.0 if index == actual_index else 0.0 for index in range(3))
        metrics_total["brier"] += sum(
            (probability - outcome) ** 2
            for probability, outcome in zip(predicted, actual, strict=True)
        )
        metrics_total["rps"] += (
            (predicted[0] - actual[0]) ** 2
            + (predicted[0] + predicted[1] - actual[0] - actual[1]) ** 2
        ) / 2.0
        metrics_total["log_loss"] += -math.log(predicted[actual_index])
        maximum = max(predicted)
        top_pick_count = sum(
            math.isclose(probability, maximum, rel_tol=0.0, abs_tol=1e-15)
            for probability in predicted
        )
        metrics_total["top_pick_accuracy"] += float(
            top_pick_count == 1
            and math.isclose(
                predicted[actual_index],
                maximum,
                rel_tol=0.0,
                abs_tol=1e-15,
            )
        )
        uniform = (1 / 3, 1 / 3, 1 / 3)
        baseline_total["brier"] += sum(
            (probability - outcome) ** 2
            for probability, outcome in zip(uniform, actual, strict=True)
        )
        baseline_total["rps"] += (
            (uniform[0] - actual[0]) ** 2
            + (uniform[0] + uniform[1] - actual[0] - actual[1]) ** 2
        ) / 2.0
        baseline_total["log_loss"] += math.log(3)
        sample_size += 1

    if sample_size == 0:
        metrics: Mapping[str, float | None] = _null_metrics()
        baseline: Mapping[str, float | None] = _null_metrics()
        status = "no_resolved"
    else:
        metrics = {key: value / sample_size for key, value in metrics_total.items()}
        baseline = {
            "rps": baseline_total["rps"] / sample_size,
            "brier": baseline_total["brier"] / sample_size,
            "log_loss": baseline_total["log_loss"] / sample_size,
            "top_pick_accuracy": 1 / 3,
        }
        status = "ok" if sample_size >= min_resolved else "insufficient"
    return BacktestReport(
        status=status,
        ok=status == "ok",
        sample_size=sample_size,
        min_resolved=min_resolved,
        metrics=metrics,
        uniform_baseline=baseline,
        exclusions=tuple(exclusions),
        model_version=model_version,
        ratings_sha256=declared_hash,
    )

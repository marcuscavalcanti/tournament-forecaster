"""Deterministic, rating-derived probability helpers."""

from __future__ import annotations

import math
import random
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from statistics import NormalDist

from .domain import Score
from .standings import DEFAULT_RATING as DEFAULT_RATING


def rating_win_probability(first_rating: float, second_rating: float) -> float:
    """Return the neutral-site Elo win strength for the first team."""

    difference = second_rating - first_rating
    if math.isinf(difference):
        return 0.0 if difference > 0.0 else 1.0
    exponent = math.log(10.0) * difference / 400.0
    if exponent >= 0.0:
        inverse = math.exp(-exponent)
        return inverse / (1.0 + inverse)
    forward = math.exp(exponent)
    return 1.0 / (1.0 + forward)


@dataclass(frozen=True, slots=True)
class OutcomeProbabilities:
    """Exact regulation-time probabilities in home, draw, away order."""

    home_win: float
    draw: float
    away_win: float

    def to_dict(self) -> dict[str, float]:
        return {
            "home_win": self.home_win,
            "draw": self.draw,
            "away_win": self.away_win,
        }


def _finite_rating(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite numeric value")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{label} must be a finite numeric value")
    return normalized


def compose_rating(rating: float, adjustment: float) -> float:
    """Add two finite rating values without producing an infinite intermediate."""

    base = _finite_rating(rating, "rating")
    delta = _finite_rating(adjustment, "rating adjustment")
    combined = base + delta
    if math.isfinite(combined):
        return combined
    return math.copysign(sys.float_info.max, combined)


def _goal_rates(
    home_rating: float,
    away_rating: float,
    home_advantage_points: float = 0.0,
) -> tuple[float, float]:
    home = _finite_rating(home_rating, "home rating")
    away = _finite_rating(away_rating, "away rating")
    advantage = _finite_rating(home_advantage_points, "home advantage points")
    home_share = rating_win_probability(compose_rating(home, advantage), away)
    expected_total = 2.6
    return (
        max(0.15, expected_total * home_share),
        max(0.15, expected_total * (1.0 - home_share)),
    )


def _poisson_probabilities(rate: float) -> tuple[float, ...]:
    probabilities = [math.exp(-rate)]
    cumulative = probabilities[0]
    goals = 0
    while 1.0 - cumulative > 1e-15:
        goals += 1
        probabilities.append(probabilities[-1] * rate / goals)
        cumulative += probabilities[-1]
    return tuple(probabilities)


def predict_match_outcomes(
    home_rating: float,
    away_rating: float,
    *,
    home_advantage_points: float = 0.0,
) -> OutcomeProbabilities:
    """Return deterministic 1X2 probabilities from the Poisson/Elo scorer."""

    home_rate, away_rate = _goal_rates(
        home_rating,
        away_rating,
        home_advantage_points,
    )
    home_goals = _poisson_probabilities(home_rate)
    away_goals = _poisson_probabilities(away_rate)
    home_win = 0.0
    draw = 0.0
    away_win = 0.0
    for home_score, home_probability in enumerate(home_goals):
        for away_score, away_probability in enumerate(away_goals):
            probability = home_probability * away_probability
            if home_score > away_score:
                home_win += probability
            elif home_score == away_score:
                draw += probability
            else:
                away_win += probability
    total = home_win + draw + away_win
    return OutcomeProbabilities(home_win / total, draw / total, away_win / total)


def stage_home_advantage_points(stage: Mapping[str, object]) -> float:
    """Read the explicit rating boost for the actual home side of a stage."""

    metadata = stage.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("stage metadata must be a mapping")
    return _finite_rating(
        metadata.get("home_advantage_rating_points", 0.0),
        "stage home_advantage_rating_points",
    )


def _poisson(rate: float, rng: random.Random) -> int:
    threshold = math.exp(-rate)
    product = 1.0
    value = 0
    while product > threshold:
        value += 1
        product *= rng.random()
    return value - 1


def simulate_score(
    home_rating: float,
    away_rating: float,
    rng: random.Random,
) -> Score:
    """Draw a regulation score from ratings using only the supplied RNG."""

    home_rate, away_rate = _goal_rates(home_rating, away_rating)
    return Score(_poisson(home_rate, rng), _poisson(away_rate, rng))


def resolve_knockout_draw(
    first_rating: float,
    second_rating: float,
    rng: random.Random,
) -> bool:
    """Resolve extra time and penalties; true means the first team advances."""

    return rng.random() < rating_win_probability(first_rating, second_rating)


def resolve_penalty_shootout(
    rng: random.Random,
    *,
    first_team_advantage_points: float = 0.0,
    second_team_advantage_points: float = 0.0,
) -> bool:
    """Resolve a shootout with only explicitly configured venue advantage."""

    return rng.random() < rating_win_probability(
        first_team_advantage_points,
        second_team_advantage_points,
    )


def wilson_interval(
    successes: int,
    iterations: int,
    confidence_level: float = 0.95,
) -> tuple[float, float]:
    """Return a deterministic Wilson score interval for a binomial rate."""

    if isinstance(successes, bool) or not isinstance(successes, int):
        raise ValueError("successes must be an integer")
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        raise ValueError("iterations must be a positive integer")
    if not 0 <= successes <= iterations:
        raise ValueError("successes must be between zero and iterations")
    if isinstance(confidence_level, bool) or not isinstance(confidence_level, (int, float)):
        raise ValueError("confidence level must be between zero and one")
    if not 0.0 < float(confidence_level) < 1.0:
        raise ValueError("confidence level must be between zero and one")

    proportion = successes / iterations
    z_score = NormalDist().inv_cdf(0.5 + float(confidence_level) / 2.0)
    z_squared = z_score * z_score
    denominator = 1.0 + z_squared / iterations
    center = (proportion + z_squared / (2.0 * iterations)) / denominator
    radius = (
        z_score
        * math.sqrt(
            proportion * (1.0 - proportion) / iterations
            + z_squared / (4.0 * iterations * iterations)
        )
        / denominator
    )
    return max(0.0, center - radius), min(1.0, center + radius)

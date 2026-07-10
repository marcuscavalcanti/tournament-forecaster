"""Deterministic, rating-derived probability helpers."""

from __future__ import annotations

import math
import random
from statistics import NormalDist

from .domain import Score
from .standings import DEFAULT_RATING as DEFAULT_RATING


def rating_win_probability(first_rating: float, second_rating: float) -> float:
    """Return the neutral-site Elo win strength for the first team."""

    return 1.0 / (1.0 + 10.0 ** ((second_rating - first_rating) / 400.0))


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

    home_share = rating_win_probability(home_rating + 65.0, away_rating)
    expected_total = 2.6
    home_rate = max(0.15, expected_total * home_share)
    away_rate = max(0.15, expected_total * (1.0 - home_share))
    return Score(_poisson(home_rate, rng), _poisson(away_rate, rng))


def resolve_knockout_draw(
    first_rating: float,
    second_rating: float,
    rng: random.Random,
) -> bool:
    """Resolve extra time and penalties; true means the first team advances."""

    return rng.random() < rating_win_probability(first_rating, second_rating)


def resolve_penalty_shootout(rng: random.Random) -> bool:
    """Resolve a neutral shootout; true means the first team advances."""

    return rng.random() < 0.5


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

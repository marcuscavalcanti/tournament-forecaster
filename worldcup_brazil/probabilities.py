from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from statistics import NormalDist
from typing import Iterable, Sequence


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _one_decimal_down(value: float) -> float:
    return float(Decimal(str(value + 1e-9)).quantize(Decimal("0.1"), rounding=ROUND_DOWN))


def _bounded_confidence_level(value: float) -> float:
    return max(0.5, min(0.999, float(value)))


def _z_for_confidence_level(confidence_level: float) -> float:
    bounded = _bounded_confidence_level(confidence_level)
    return NormalDist().inv_cdf(0.5 + bounded / 2.0)


def normalize_two_way(brazil_value: float, opponent_value: float) -> tuple[float, float]:
    total = brazil_value + opponent_value
    if total <= 0:
        raise ValueError("two-way normalization requires a positive total")

    brazil_pct = round((brazil_value / total) * 100, 1)
    opponent_pct = round(100 - brazil_pct, 1)
    return brazil_pct, opponent_pct


@dataclass(frozen=True)
class SourceSignal:
    source: str
    brazil_pct: float
    opponent_pct: float
    confidence: float = 1.0
    detail: str = ""

    def normalized(self) -> "SourceSignal":
        brazil_pct, opponent_pct = normalize_two_way(self.brazil_pct, self.opponent_pct)
        return SourceSignal(
            source=self.source,
            brazil_pct=brazil_pct,
            opponent_pct=opponent_pct,
            confidence=_clamp(self.confidence, 0.0, 1.0),
            detail=self.detail,
        )


@dataclass
class MatchEstimate:
    brazil: str
    opponent: str
    phase: str
    brazil_pct: float
    opponent_pct: float
    statistical_weight: float
    qualitative_weight: float
    rationale: str
    draw_pct: float | None = None
    match_date: str | None = None
    brazil_ci_low: float | None = None
    brazil_ci_high: float | None = None
    most_likely: bool | None = None
    venue: str | None = None
    scenario_pct: float | None = None


def _confidence_weighted_average(signals: Sequence[SourceSignal], attr: str) -> float:
    if not signals:
        raise ValueError("at least one signal is required")

    total_weight = 0.0
    weighted_sum = 0.0
    for signal in signals:
        normalized = signal.normalized()
        weight = max(normalized.confidence, 0.01)
        total_weight += weight
        weighted_sum += getattr(normalized, attr) * weight

    return weighted_sum / total_weight


def _mean_confidence(signals: Sequence[SourceSignal]) -> float:
    if not signals:
        return 0.0
    return sum(_clamp(signal.confidence, 0.0, 1.0) for signal in signals) / len(signals)


def confidence_interval(
    probability_pct: float,
    signals: Sequence[SourceSignal],
    *,
    minimum_width: float = 4.0,
    maximum_width: float = 18.0,
    confidence_level: float = 0.95,
) -> tuple[float, float]:
    if not signals:
        width = maximum_width
    else:
        normalized = [signal.normalized() for signal in signals]
        confidence = _mean_confidence(normalized)
        values = [signal.brazil_pct for signal in normalized]
        disagreement = max(values) - min(values) if len(values) > 1 else 0.0
        width = (1.0 - confidence) * 14.0 + disagreement * 0.2 + minimum_width
        width *= _z_for_confidence_level(confidence_level) / _z_for_confidence_level(0.95)
        width = _clamp(width, minimum_width, maximum_width)

    low = round(_clamp(probability_pct - width / 2, 0.0, 100.0), 1)
    high = round(_clamp(probability_pct + width / 2, 0.0, 100.0), 1)
    return low, high


def _source_names(signals: Iterable[SourceSignal]) -> str:
    names = []
    for signal in signals:
        if signal.source not in names:
            names.append(signal.source)
    return ", ".join(names)


def blend_match_estimate(
    *,
    brazil: str,
    opponent: str,
    phase: str,
    statistical: Sequence[SourceSignal],
    qualitative: Sequence[SourceSignal],
    rationale: str | None = None,
    draw_pct: float | None = None,
    match_date: str | None = None,
    most_likely: bool | None = None,
    venue: str | None = None,
    scenario_pct: float | None = None,
    statistical_weight: float = 0.5,
    qualitative_weight: float = 0.5,
    confidence_level: float = 0.95,
) -> MatchEstimate:
    if not statistical and not qualitative:
        raise ValueError("blend_match_estimate requires at least one source signal")

    statistical_weight = _clamp(statistical_weight, 0.0, 1.0)
    qualitative_weight = _clamp(qualitative_weight, 0.0, 1.0)

    if statistical and qualitative:
        stat_brazil = _confidence_weighted_average(statistical, "brazil_pct")
        qual_brazil = _confidence_weighted_average(qualitative, "brazil_pct")

        # The neutral split is only a baseline for local priors. Model-provided
        # match probabilities can overwrite this estimate after the debriefing.
        qual_effective_weight = qualitative_weight * _mean_confidence(qualitative)
        denominator = statistical_weight + qual_effective_weight
        brazil_pct = (stat_brazil * statistical_weight + qual_brazil * qual_effective_weight) / denominator
    elif statistical:
        brazil_pct = _confidence_weighted_average(statistical, "brazil_pct")
    else:
        brazil_pct = _confidence_weighted_average(qualitative, "brazil_pct")

    brazil_pct = _one_decimal_down(_clamp(brazil_pct, 1.0, 99.0))
    opponent_pct = round(100 - brazil_pct, 1)
    if rationale is None:
        stat_sources = _source_names(statistical)
        qual_sources = _source_names(qualitative)
        rationale = (
            "Estimativa composta com peso direcional configurável para sinais estatísticos "
            f"({stat_sources or 'sem sinal estatístico'}) e sinais qualitativos "
            f"({qual_sources or 'sem sinal qualitativo'}), com desconto por confiança. "
            "A sala de modelos pode mover probabilidades quando trouxer premissas melhores com número e fonte."
        )

    all_signals = [*statistical, *qualitative]
    ci_low, ci_high = confidence_interval(brazil_pct, all_signals, confidence_level=confidence_level)

    return MatchEstimate(
        brazil=brazil,
        opponent=opponent,
        phase=phase,
        brazil_pct=brazil_pct,
        opponent_pct=opponent_pct,
        statistical_weight=statistical_weight,
        qualitative_weight=qualitative_weight,
        rationale=rationale,
        draw_pct=draw_pct,
        match_date=match_date,
        brazil_ci_low=ci_low,
        brazil_ci_high=ci_high,
        most_likely=most_likely,
        venue=venue,
        scenario_pct=scenario_pct,
    )

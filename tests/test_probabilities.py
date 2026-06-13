from worldcup_brazil.probabilities import (
    MatchEstimate,
    SourceSignal,
    blend_match_estimate,
    normalize_two_way,
)
from worldcup_brazil.pipeline import _stage_probability_source, _stage_probabilities


def test_normalize_two_way_rejects_zero_or_negative_total() -> None:
    try:
        normalize_two_way(0, 0)
    except ValueError as exc:
        assert "positive total" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_normalize_two_way_returns_percentages_summing_to_one_hundred() -> None:
    home, away = normalize_two_way(1.7, 2.3)

    assert round(home + away, 8) == 100
    assert home == 42.5
    assert away == 57.5


def test_blend_match_estimate_defaults_to_neutral_directional_weights_not_hard_seventy_thirty() -> None:
    estimate = blend_match_estimate(
        brazil="Brasil",
        opponent="Argentina",
        phase="Final",
        statistical=[
            SourceSignal(source="rating", brazil_pct=60, opponent_pct=40, confidence=0.9),
            SourceSignal(source="market", brazil_pct=50, opponent_pct=50, confidence=0.6),
        ],
        qualitative=[
            SourceSignal(source="injuries", brazil_pct=40, opponent_pct=60, confidence=0.5),
        ],
    )

    assert isinstance(estimate, MatchEstimate)
    assert estimate.brazil_pct == 50.6
    assert estimate.opponent_pct == 49.4
    assert estimate.brazil_ci_low < estimate.brazil_pct < estimate.brazil_ci_high
    assert estimate.statistical_weight == 0.5
    assert estimate.qualitative_weight == 0.5
    assert "70%" not in estimate.rationale
    assert "30%" not in estimate.rationale
    assert "direcional" in estimate.rationale


def test_blend_match_estimate_preserves_exact_consensus_percentage() -> None:
    estimate = blend_match_estimate(
        brazil="Brasil",
        opponent="Marrocos",
        phase="Grupo",
        statistical=[SourceSignal(source="rating", brazil_pct=59, opponent_pct=41, confidence=0.8)],
        qualitative=[SourceSignal(source="injuries", brazil_pct=59, opponent_pct=41, confidence=0.6)],
    )

    assert estimate.brazil_pct == 59.0


def test_blend_match_estimate_confidence_level_widens_operational_interval() -> None:
    common_kwargs = {
        "brazil": "Brasil",
        "opponent": "Marrocos",
        "phase": "Grupo",
        "statistical": [SourceSignal(source="rating", brazil_pct=59, opponent_pct=41, confidence=0.8)],
        "qualitative": [SourceSignal(source="injuries", brazil_pct=56, opponent_pct=44, confidence=0.6)],
    }

    estimate_95 = blend_match_estimate(**common_kwargs, confidence_level=0.95)
    estimate_99 = blend_match_estimate(**common_kwargs, confidence_level=0.99)

    width_95 = estimate_95.brazil_ci_high - estimate_95.brazil_ci_low
    width_99 = estimate_99.brazil_ci_high - estimate_99.brazil_ci_low

    assert width_99 > width_95


def test_blend_match_estimate_preserves_exact_percentage_with_multiple_sources() -> None:
    estimate = blend_match_estimate(
        brazil="Brasil",
        opponent="Marrocos",
        phase="Grupo",
        statistical=[
            SourceSignal(source="market", brazil_pct=59, opponent_pct=41, confidence=0.64),
            SourceSignal(source="elo", brazil_pct=59, opponent_pct=41, confidence=0.82),
            SourceSignal(source="ranking", brazil_pct=59, opponent_pct=41, confidence=0.78),
        ],
        qualitative=[
            SourceSignal(source="squad", brazil_pct=59, opponent_pct=41, confidence=0.55),
            SourceSignal(source="news", brazil_pct=59, opponent_pct=41, confidence=0.64),
        ],
    )

    assert estimate.brazil_pct == 59.0


def test_stage_probabilities_use_single_monte_carlo_funnel_including_title() -> None:
    """Regressão do run de 10/jun/2026: o funil misturava fases do Monte Carlo com título
    do consenso da sala e tentou publicar titulo=11.0% > final=2.7%. Funil agora é único:
    com Monte Carlo ativo, o título também vem da simulação; a leitura da sala fica em
    metadata/palpites e só influencia o funil via sinais de contexto reconciliados."""
    probabilities = _stage_probabilities(
        11.2,
        {
            "monte_carlo": {"use_stage_probabilities": True},
            "_monte_carlo_result": {
                "enabled": True,
                "stage_probabilities": {
                    "quartas": 62.0,
                    "semifinal": 38.0,
                    "final": 21.0,
                    "titulo": 8.0,
                },
            },
        },
    )

    assert probabilities == {
        "quartas": 62.0,
        "semifinal": 38.0,
        "final": 21.0,
        "titulo": 8.0,
    }
    assert probabilities["titulo"] <= probabilities["final"]


def test_stage_probability_source_marks_partial_monte_carlo_as_partial_fallback() -> None:
    config = {
        "monte_carlo": {"use_stage_probabilities": True},
        "_monte_carlo_result": {
            "enabled": True,
            "stage_probabilities": {
                "quartas": 62.0,
                "semifinal": 38.0,
            },
        },
    }

    assert _stage_probability_source(config) == "monte_carlo_partial_agent_scaled_fallback"

from pathlib import Path

from worldcup_brazil.monte_carlo import (
    _stage_uncertainty_intervals,
    monte_carlo_compact_summary,
    run_brazil_monte_carlo,
    widen_ci_for_monte_carlo_path_uncertainty,
)
from worldcup_brazil.pipeline import load_config
from worldcup_brazil.probabilities import MatchEstimate


def _ci_width(ci: tuple[float, float] | list[float]) -> float:
    return round(float(ci[1]) - float(ci[0]), 3)


def _mc_config(iterations: int = 6000) -> dict:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    config["monte_carlo"] = {
        "enabled": True,
        "iterations": iterations,
        "seed": 26062026,
        "default_draw_pct": 24.0,
        "rating_scale": 400.0,
        "rating_source": "test priors",
        "team_ratings": {
            "Brasil": 1850,
            "Marrocos": 1660,
            "Haiti": 1320,
            "Escócia": 1540,
            "Holanda": 1860,
            "Japão": 1690,
            "Suécia": 1650,
            "Tunísia": 1480,
        },
    }
    config["group_matches"] = [
        {"opponent": "Marrocos", "brazil_pct": 95.0, "draw_pct": 3.0},
        {"opponent": "Haiti", "brazil_pct": 98.0, "draw_pct": 1.0},
        {"opponent": "Escócia", "brazil_pct": 96.0, "draw_pct": 2.0},
    ]
    return config


def test_monte_carlo_confidence_level_controls_wilson_interval_width() -> None:
    config_95 = _mc_config(iterations=5000)
    config_95["uncertainty"]["confidence_level"] = 0.95
    config_95["monte_carlo"]["confidence_level"] = 0.95
    config_99 = _mc_config(iterations=5000)
    config_99["uncertainty"]["confidence_level"] = 0.99
    config_99["monte_carlo"]["confidence_level"] = 0.99

    result_95 = run_brazil_monte_carlo(config_95)
    result_99 = run_brazil_monte_carlo(config_99)

    assert result_95["confidence_level"] == 0.95
    assert result_99["confidence_level"] == 0.99
    assert _ci_width(result_99["phases"]["Oitavas"]["reach_ci"]) > _ci_width(
        result_95["phases"]["Oitavas"]["reach_ci"]
    )


def test_monte_carlo_two_level_rating_uncertainty_exposes_epistemic_band() -> None:
    config = _mc_config(iterations=2400)
    config["monte_carlo"].update(
        {
            "confidence_level": 0.99,
            "rating_uncertainty_enabled": True,
            "rating_uncertainty_outer_samples": 24,
            "rating_uncertainty_inner_iterations": 100,
            "configured_rating_sigma": 45.0,
            "prior_rating_sigma": 180.0,
        }
    )

    result = run_brazil_monte_carlo(config)

    assert result["confidence_level"] == 0.99
    assert result["rating_uncertainty"]["enabled"] is True
    assert result["rating_uncertainty"]["outer_samples"] == 24
    assert result["rating_uncertainty"]["inner_iterations"] == 100
    assert result["rating_uncertainty"]["configured_rating_sigma"] == 45.0
    assert result["rating_uncertainty"]["prior_rating_sigma"] == 180.0
    assert set(result["stage_uncertainty_intervals"]) >= {
        "16_avos",
        "oitavas",
        "quartas",
        "semifinal",
        "final",
        "titulo",
    }
    assert _ci_width(result["stage_uncertainty_intervals"]["titulo"]) > 0.0
    assert result["stage_uncertainty_intervals"]["titulo"][0] <= result["stage_probabilities"]["titulo"]
    assert result["stage_uncertainty_intervals"]["titulo"][1] >= result["stage_probabilities"]["titulo"]
    assert result["rating_uncertainty"]["central_point_semantics"] == "posterior_mean_over_rating_scenarios"
    assert result["rating_uncertainty"]["variance_correction"] == "law_of_total_variance_subtracts_inner_binomial_noise"


def test_stage_uncertainty_interval_subtracts_inner_simulation_noise() -> None:
    intervals = _stage_uncertainty_intervals(
        {"titulo": [0.0, 10.0, 5.0, 5.0, 10.0, 0.0, 5.0, 5.0]},
        {"titulo": 5.0},
        confidence_level=0.99,
        inner_variances={"titulo": [25.0] * 8},
    )

    assert _ci_width(intervals["titulo"]) <= 1.0


def test_monte_carlo_simulates_group_f_runner_up_for_brazil_1c_instead_of_raw_allowed_list() -> None:
    result = run_brazil_monte_carlo(_mc_config())

    round_of_32 = result["phases"]["16 avos"]
    top_two = [entry["opponent"] for entry in round_of_32["opponents"][:2]]

    assert round_of_32["brazil_slot_counts"]["1C"] > round_of_32["brazil_slot_counts"].get("2C", 0)
    assert top_two[0] in {"Japão", "Suécia"}
    assert "Tunísia" not in top_two
    assert set(top_two).issubset({"Holanda", "Japão", "Suécia", "Tunísia"})


def test_monte_carlo_downstream_round_uses_winner_of_official_neighbor_match() -> None:
    config = _mc_config(iterations=6000)
    result = run_brazil_monte_carlo(config)

    groups = config["groups_config"]["groups"]
    group_e_or_i = {team["name"] for group in ("E", "I") for team in groups[group]}
    round_of_16_top_three = [
        entry["opponent"]
        for entry in result["phases"]["Oitavas"]["opponents"][:3]
    ]

    assert result["phases"]["16 avos"]["brazil_slot_counts"]["1C"] > 5500
    assert set(round_of_16_top_three).issubset(group_e_or_i)


def test_monte_carlo_uses_same_team_context_signal_families_for_candidate_opponents() -> None:
    baseline = run_brazil_monte_carlo(_mc_config(iterations=5000))
    adjusted_config = _mc_config(iterations=5000)
    adjusted_config["monte_carlo"]["probability_pct_to_rating_points"] = 10.0
    adjusted_config["monte_carlo"]["team_context"] = {
        "Suécia": [
            {
                "category": "bets_prediction_markets",
                "rating_delta": 140,
                "confidence": 0.9,
                "source_url": "https://example.com/sweden-market",
                "rationale": "Odds e prediction markets encurtaram Suécia para 2F.",
            },
            {
                "category": "specialist_press_recent_friendlies",
                "probability_delta_pct": 3.0,
                "confidence": 0.8,
                "source_query": "Sweden recent friendlies specialist press World Cup 2026",
                "rationale": "Amistosos recentes e imprensa especializada melhoraram o rating efetivo.",
            },
        ],
        "Japão": [
            {
                "category": "injuries_cuts_news",
                "rating_delta": -80,
                "confidence": 0.85,
                "source_url": "https://example.com/japan-injury",
                "rationale": "Lesão/corte reduz profundidade no setor defensivo.",
            }
        ],
    }

    adjusted = run_brazil_monte_carlo(adjusted_config)

    baseline_sweden = next(
        entry["scenario_pct"]
        for entry in baseline["phases"]["16 avos"]["opponents"]
        if entry["opponent"] == "Suécia"
    )
    adjusted_sweden = next(
        entry["scenario_pct"]
        for entry in adjusted["phases"]["16 avos"]["opponents"]
        if entry["opponent"] == "Suécia"
    )

    assert adjusted_sweden > baseline_sweden + 10.0
    assert adjusted["team_context"]["applied_signal_count"] == 3
    assert adjusted["team_context"]["teams_with_context_count"] == 2
    assert "bets_prediction_markets" in adjusted["team_context"]["source_families"]
    assert "injuries_cuts_news" in adjusted["team_context"]["source_families"]
    assert any(
        item["team"] == "Suécia" and item["rating_delta"] > 120.0
        for item in adjusted["team_context"]["team_adjustments"]
    )


def test_monte_carlo_ignores_context_signals_without_numeric_effect_or_source() -> None:
    config = _mc_config(iterations=2000)
    config["monte_carlo"]["team_context"] = {
        "Holanda": [
            {
                "category": "recent_news",
                "rating_delta": -140,
                "confidence": 1.0,
                "rationale": "Sem fonte auditável, não deve mover rating.",
            },
            {
                "category": "specialist_press",
                "source_url": "https://example.com/netherlands-press",
                "rationale": "Sem delta numérico, não deve mover rating.",
            },
        ]
    }

    result = run_brazil_monte_carlo(config)

    assert result["team_context"]["applied_signal_count"] == 0
    assert result["team_context"]["ignored_signal_count"] == 2
    assert result["team_context"]["teams_with_context_count"] == 0


def test_monte_carlo_is_deterministic_for_same_seed_and_config() -> None:
    config = _mc_config(iterations=2500)

    first = run_brazil_monte_carlo(config)
    second = run_brazil_monte_carlo(config)

    assert first["stage_probabilities"] == second["stage_probabilities"]
    assert first["phases"]["16 avos"]["opponents"][:3] == second["phases"]["16 avos"]["opponents"][:3]


def test_monte_carlo_stage_probabilities_are_monotonic() -> None:
    result = run_brazil_monte_carlo(_mc_config(iterations=3000))
    stages = result["stage_probabilities"]

    assert stages["16_avos"] >= stages["oitavas"]
    assert stages["oitavas"] >= stages["quartas"]
    assert stages["quartas"] >= stages["semifinal"]
    assert stages["semifinal"] >= stages["final"]
    assert stages["final"] >= stages["titulo"]


def test_monte_carlo_compact_summary_exposes_candidates_without_full_transcript() -> None:
    result = run_brazil_monte_carlo(_mc_config(iterations=2500))

    summary = monte_carlo_compact_summary(result)

    assert summary["enabled"] is True
    assert summary["iterations"] == 2500
    assert "16 avos" in summary["phases"]
    assert summary["phases"]["16 avos"]["top_opponents"][0]["opponent"]
    assert "rating_coverage_pct" in summary


def test_widen_ci_for_monte_carlo_path_uncertainty_expands_flat_path_distribution() -> None:
    estimate = MatchEstimate(
        brazil="Brasil",
        opponent="Adversário mais provável a definir",
        phase="16 avos",
        brazil_pct=57.0,
        opponent_pct=43.0,
        statistical_weight=0.5,
        qualitative_weight=0.5,
        rationale="base",
        brazil_ci_low=53.0,
        brazil_ci_high=61.0,
    )
    monte_carlo_result = {
        "enabled": True,
        "phases": {
            "16 avos": {
                "path_uncertainty_pct": 83.0,
            }
        },
    }

    widen_ci_for_monte_carlo_path_uncertainty(estimate, monte_carlo_result, max_widen_pct=8.0)

    assert estimate.brazil_ci_low < 53.0
    assert estimate.brazil_ci_high > 61.0


def test_monte_carlo_reliable_concentrated_path_narrows_ci() -> None:
    estimate = MatchEstimate(
        brazil="Brasil",
        opponent="Japão",
        phase="16 avos",
        brazil_pct=58.0,
        opponent_pct=42.0,
        statistical_weight=0.5,
        qualitative_weight=0.5,
        rationale="base",
        brazil_ci_low=53.0,
        brazil_ci_high=61.0,
    )
    monte_carlo_result = {
        "enabled": True,
        "iterations": 40000,
        "rating_coverage_pct": 82.0,
        "phases": {
            "16 avos": {
                "path_uncertainty_pct": 12.0,
            }
        },
    }

    widen_ci_for_monte_carlo_path_uncertainty(
        estimate,
        monte_carlo_result,
        max_widen_pct=8.0,
        min_iterations=10000,
        min_rating_coverage_pct=65.0,
        max_narrow_pct=4.0,
        narrow_uncertainty_threshold_pct=35.0,
    )

    assert estimate.brazil_ci_low > 53.0
    assert estimate.brazil_ci_high < 61.0


def test_best_third_allocation_uses_matching_where_greedy_fails() -> None:
    """Regressão: alocação gulosa falhava quando o primeiro slot consumia o único
    terceiro compatível com o slot seguinte; o jogo era pulado em silêncio e a
    cascata suprimia o funil inteiro (final não disputada em ~60% das simulações)."""
    from worldcup_brazil.monte_carlo import _allocate_best_thirds

    config = {
        "bracket_config": {
            "round_of_32": [
                {"match_id": 1, "slots": ["1A", "3A/B"]},
                {"match_id": 2, "slots": ["1B", "3A/C"]},
            ]
        }
    }
    qualified_thirds = [
        {"group": "A", "team": "Time A"},
        {"group": "B", "team": "Time B"},
    ]

    assignment, relaxed = _allocate_best_thirds(config, qualified_thirds)

    assert relaxed == 0
    assert assignment["3A/C"] == "Time A"
    assert assignment["3A/B"] == "Time B"


def test_full_simulation_never_skips_matches_with_official_configs() -> None:
    """Toda simulação deve disputar todos os jogos da chave oficial: zero jogos
    não resolvidos e zero alocações relaxadas com os configs canônicos."""
    import json
    from pathlib import Path

    from worldcup_brazil.bracket import hydrate_canonical_configs
    from worldcup_brazil.monte_carlo import run_brazil_monte_carlo

    config = json.loads(
        Path("config/worldcup_brazil.example.json").read_text(encoding="utf-8")
    )
    hydrate_canonical_configs(config, base_dir=Path("config"))
    config["monte_carlo"]["rating_uncertainty_enabled"] = False
    config["monte_carlo"]["iterations"] = 1500

    result = run_brazil_monte_carlo(config)

    diagnostics = result["simulation_diagnostics"]
    assert diagnostics["unresolved_match_count"] == 0
    assert diagnostics["third_allocation_relaxed_count"] == 0
    assert result["stage_probabilities"]["titulo"] > 2.0


def test_monte_carlo_fails_hard_on_unresolved_bracket_slot() -> None:
    """Gate de integridade (auditoria 11/jun): label de slot quebrado em
    bracket.config.json fazia jogos serem pulados em silêncio (continue) e o
    funil colapsar — título 0.0 publicado com exit 0, a mesma classe de bug do
    alocador greedy que deprimiu o funil em ~60%. Agora: falha hard, sem publicar."""
    import pytest

    from worldcup_brazil.monte_carlo import MonteCarloIntegrityError

    config = _mc_config(iterations=200)
    config["monte_carlo"]["rating_uncertainty_enabled"] = False
    config["bracket_config"]["round_of_32"][0]["slots"][0] = "2Z"

    with pytest.raises(MonteCarloIntegrityError, match="sem slot resolvido"):
        run_brazil_monte_carlo(config)


def test_monte_carlo_compact_summary_carries_simulation_diagnostics() -> None:
    """Os contadores de integridade precisam chegar aos artefatos persistidos —
    antes só existiam no result completo, invisíveis para watchdog e post-mortem."""
    result = run_brazil_monte_carlo(_mc_config(iterations=400))

    summary = monte_carlo_compact_summary(result)

    assert summary["simulation_diagnostics"] == result["simulation_diagnostics"]
    assert summary["simulation_diagnostics"]["unresolved_match_count"] == 0


def test_simulation_integrity_relaxed_thirds_prong_enforces_cap() -> None:
    """O 2º dente do gate: alocação de melhores-terceiros relaxada acima de 0,5%
    das iterações também é config degenerado e não pode publicar em silêncio."""
    import pytest

    from worldcup_brazil.monte_carlo import (
        MonteCarloIntegrityError,
        _check_simulation_integrity,
    )

    _check_simulation_integrity(
        {"unresolved_match_count": 0, "third_allocation_relaxed_count": 200},
        iterations=40000,
    )  # 0,5% exato: passa

    with pytest.raises(MonteCarloIntegrityError, match="melhores-terceiros"):
        _check_simulation_integrity(
            {"unresolved_match_count": 0, "third_allocation_relaxed_count": 201},
            iterations=40000,
        )

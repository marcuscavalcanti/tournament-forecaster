from pathlib import Path

import pytest

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
    config["completed_group_matches"] = []
    return config


def _completed_match_anchor_config(iterations: int = 2000) -> dict:
    config = _mc_config(iterations=iterations)
    config["monte_carlo"]["team_ratings"].update(
        {
            "Alemanha": 1860,
            "Curaçau": 1360,
        }
    )
    config["completed_group_matches"] = [
        {
            "group": "C",
            "team_a": "Brasil",
            "team_b": "Marrocos",
            "score_a": 1,
            "score_b": 1,
            "date": "2026-06-13",
        },
        {
            "group": "C",
            "team_a": "Escócia",
            "team_b": "Haiti",
            "score_a": 1,
            "score_b": 0,
            "date": "2026-06-13",
        },
        {
            "group": "E",
            "team_a": "Alemanha",
            "team_b": "Curaçau",
            "score_a": 7,
            "score_b": 1,
            "date": "2026-06-14",
        },
        {
            "group": "F",
            "team_a": "Holanda",
            "team_b": "Japão",
            "score_a": 2,
            "score_b": 2,
            "date": "2026-06-14",
        },
        {
            "group": "F",
            "team_a": "Suécia",
            "team_b": "Tunísia",
            "score_a": 5,
            "score_b": 1,
            "date": "2026-06-15",
        },
    ]
    return config


def _team_context_adjustment(result: dict, team: str) -> dict:
    return next(
        item for item in result["team_context"]["team_adjustments"] if item["team"] == team
    )


def test_monte_carlo_conditions_group_on_completed_results() -> None:
    baseline = run_brazil_monte_carlo(_mc_config(iterations=3000))
    config = _mc_config(iterations=3000)
    config["completed_group_matches"] = [
        {
            "group": "C",
            "team_a": "Brasil",
            "team_b": "Marrocos",
            "score_a": 1,
            "score_b": 1,
            "date": "2026-06-13",
        },
        {
            "group": "C",
            "team_a": "Escócia",
            "team_b": "Haiti",
            "score_a": 1,
            "score_b": 0,
            "date": "2026-06-13",
        },
    ]

    conditioned = run_brazil_monte_carlo(config)

    group_state = conditioned["group_state"]
    current = {row["team"]: row for row in group_state["current_table"]}
    assert conditioned["completed_group_matches"]["count"] == 2
    assert current["Escócia"]["points"] == 3
    assert current["Brasil"]["points"] == 1
    assert current["Marrocos"]["points"] == 1
    assert current["Haiti"]["points"] == 0
    assert sum(group_state["brazil_position_counts"].values()) == conditioned["iterations"]
    assert group_state["brazil_first_pct"] == group_state["brazil_position_pct"]["1"]
    assert group_state["completed_results"][0]["score"] == "Brasil 1-1 Marrocos"


def test_monte_carlo_exposes_completed_results_for_brazil_crossing_groups() -> None:
    config = _mc_config(iterations=3000)
    config["completed_group_matches"] = [
        {
            "group": "C",
            "team_a": "Brasil",
            "team_b": "Marrocos",
            "score_a": 1,
            "score_b": 1,
            "date": "2026-06-13",
        },
        {
            "group": "F",
            "team_a": "Holanda",
            "team_b": "Japão",
            "score_a": 2,
            "score_b": 2,
            "date": "2026-06-14",
        },
        {
            "group": "F",
            "team_a": "Suécia",
            "team_b": "Tunísia",
            "score_a": 5,
            "score_b": 1,
            "date": "2026-06-15",
        },
    ]

    conditioned = run_brazil_monte_carlo(config)

    assert conditioned["completed_group_matches"]["count"] == 3
    assert any(match["score"] == "Holanda 2-2 Japão" for match in conditioned["completed_group_matches"]["matches"])
    assert conditioned["phase_relevant_groups"]["16 avos"] == ["F"]
    group_f = conditioned["relevant_group_states"]["F"]
    assert "16 avos" in group_f["phases"]
    assert group_f["completed_results"][0]["score"] == "Holanda 2-2 Japão"
    assert group_f["current_table"][0]["team"] == "Suécia"
    assert group_f["current_table"][0]["points"] == 3
    assert group_f["current_table"][0]["goal_difference"] == 4

    compact = monte_carlo_compact_summary(conditioned)
    assert compact["relevant_group_states"]["F"]["current_table"][0]["team"] == "Suécia"
    assert compact["completed_group_matches"]["count"] == 3


def test_completed_group_result_overrides_extreme_pre_match_probability() -> None:
    baseline = run_brazil_monte_carlo(_mc_config(iterations=3000))
    config = _mc_config(iterations=3000)
    config["group_matches"][0]["brazil_pct"] = 99.0
    config["group_matches"][0]["draw_pct"] = 0.5
    config["completed_group_matches"] = [
        {
            "group": "C",
            "team_a": "Brasil",
            "team_b": "Marrocos",
            "score_a": 0,
            "score_b": 1,
            "date": "2026-06-13",
        },
    ]

    conditioned = run_brazil_monte_carlo(config)

    assert conditioned["group_state"]["current_table"][0]["team"] == "Marrocos"
    assert conditioned["group_state"]["brazil_first_pct"] < baseline["group_state"]["brazil_first_pct"]


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
    adjusted_config["monte_carlo"]["team_context_evidence_regression_enabled"] = False
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
    assert adjusted["team_context"]["warnings"] == [
        {
            "team": "Suécia",
            "reason": "team_context_reactive_families_without_calendar_anchor",
            "source_families": ["bets_prediction_markets", "specialized_press"],
        },
        {
            "team": "Suécia",
            "rating_delta": 132.0,
            "threshold": 40.0,
            "reason": "team_context_delta_above_warning_threshold",
        },
        {
            "team": "Japão",
            "rating_delta": -68.0,
            "threshold": 40.0,
            "reason": "team_context_delta_above_warning_threshold",
        }
    ]


def test_monte_carlo_collapses_correlated_team_context_signals_by_normalized_family() -> None:
    config = _mc_config(iterations=2000)
    config["monte_carlo"]["team_context"] = {
        "Brasil": [
            {
                "category": "lesões/cortes/notícias recentes",
                "rating_delta": -8.0,
                "confidence": 1.0,
                "source_url": "https://example.com/neymar-injury",
            },
            {
                "category": "lesões/cortes/notícias recentes",
                "rating_delta": -13.4,
                "confidence": 1.0,
                "source_url": "https://example.com/raphinha-rest",
            },
            {
                "category": "injuries_cuts_news",
                "rating_delta": -13.6,
                "confidence": 1.0,
                "source_url": "https://example.com/brazil-injury-roundup",
            },
        ]
    }

    adjusted = run_brazil_monte_carlo(config)

    brazil_adjustment = next(
        item for item in adjusted["team_context"]["team_adjustments"] if item["team"] == "Brasil"
    )
    assert brazil_adjustment["rating_delta"] == -13.4
    assert brazil_adjustment["source_families"] == ["injuries_cuts_news"]
    assert brazil_adjustment["family_adjustments"] == [
        {"source_family": "injuries_cuts_news", "rating_delta": -13.4, "signal_count": 3}
    ]


def test_monte_carlo_shrinks_cross_family_signals_for_same_correlation_group() -> None:
    config = _completed_match_anchor_config(iterations=2000)
    config["monte_carlo"]["team_context_correlation_default_rho"] = 0.7
    config["monte_carlo"]["team_context"] = {
        "Brasil": [
            {
                "category": "bets_prediction_markets",
                "rating_delta": -13.1,
                "confidence": 1.0,
                "correlation_group": "brasil_pos_marrocos",
                "source_url": "https://example.com/brazil-odds",
            },
            {
                "category": "injuries_cuts_news",
                "rating_delta": -13.6,
                "confidence": 1.0,
                "correlation_group": "brasil_pos_marrocos",
                "source_url": "https://example.com/brazil-injuries",
            },
            {
                "category": "ratings",
                "rating_delta": -13.0,
                "confidence": 1.0,
                "correlation_group": "brasil_pos_marrocos",
                "source_url": "https://example.com/brazil-ratings",
            },
            {
                "category": "performance",
                "rating_delta": -9.2,
                "confidence": 1.0,
                "correlation_group": "brasil_pos_marrocos",
                "source_url": "https://example.com/brazil-performance",
            },
            {
                "category": "elenco_talento",
                "rating_delta": 5.8,
                "confidence": 1.0,
                "correlation_group": "brasil_structural_talent",
                "source_url": "https://example.com/brazil-talent",
            },
        ]
    }

    adjusted = run_brazil_monte_carlo(config)

    brazil_adjustment = next(
        item for item in adjusted["team_context"]["team_adjustments"] if item["team"] == "Brasil"
    )
    assert -25.0 < brazil_adjustment["rating_delta"] < -12.0
    assert brazil_adjustment["rating_delta"] != -43.1
    adjustments_by_group = {
        item["correlation_group"]: item
        for item in brazil_adjustment["correlation_adjustments"]
    }
    assert adjustments_by_group["match_event:brasil:marrocos:2026-06-13"] == {
        "correlation_group": "match_event:brasil:marrocos:2026-06-13",
        "rho": 0.7,
        "rating_delta": -24.2,
        "dominant_family": "injuries_cuts_news",
        "dominant_delta": -13.6,
        "residual_delta": -35.3,
        "member_families": [
            "bets_prediction_markets",
            "injuries_cuts_news",
            "performance",
            "ratings",
        ],
        "correlation_group_source": "completed_match",
    }
    assert adjustments_by_group["brasil_structural_talent"] == {
        "correlation_group": "brasil_structural_talent",
        "rho": 0.7,
        "rating_delta": 5.8,
        "dominant_family": "elenco_talento",
        "dominant_delta": 5.8,
        "residual_delta": 0.0,
        "member_families": ["elenco_talento"],
    }


def test_monte_carlo_derives_match_shock_when_models_use_different_group_labels() -> None:
    """Regressão do run a30341: o modelo pediu o mesmo choque Brasil 1-1
    Marrocos em famílias diferentes, mas com labels diferentes de
    correlation_group. O motor antigo confiava cegamente nesses labels e somava
    o choque 4x; o motor precisa derivar um grupo determinístico de evento
    quando a evidência aponta para o mesmo jogo."""
    config = _completed_match_anchor_config(iterations=2000)
    config["monte_carlo"]["team_context_correlation_default_rho"] = 0.7
    config["monte_carlo"]["team_context"] = {
        "Brasil": [
            {
                "category": "bets_prediction_markets",
                "rating_delta": -16.0,
                "confidence": 1.0,
                "correlation_group": "br_marrocos_draw_shock",
                "rationale": "Odds do Brasil driftaram depois do empate Brasil 1-1 Marrocos em 13/06.",
                "source_url": "https://example.com/brazil-morocco-odds",
            },
            {
                "category": "ratings",
                "rating_delta": -13.0,
                "confidence": 1.0,
                "correlation_group": "br_marrocos_rating_update",
                "rationale": "Rating/Elo atualizados após Brasil x Marrocos 1-1.",
                "source_url": "https://example.com/brazil-morocco-ratings",
            },
            {
                "category": "performance",
                "rating_delta": -13.0,
                "confidence": 1.0,
                "correlation_group": "br_mar_2026_match_reaction",
                "rationale": "Performance ruim no empate com Marrocos em 2026.",
                "source_url": "https://example.com/brazil-morocco-performance",
            },
            {
                "category": "injuries_cuts_news",
                "rating_delta": -13.6,
                "confidence": 1.0,
                "correlation_group": "shock_group_c_debut",
                "rationale": "Notícias de escalação e lesões para Brasil x Marrocos na estreia.",
                "source_url": "https://example.com/brazil-morocco-injuries",
            },
            {
                "category": "squad_depth",
                "rating_delta": -16.5,
                "confidence": 1.0,
                "correlation_group": "br_squad_attrition",
                "rationale": "Atrito estrutural do elenco por ausências acumuladas de Neymar, Rodrygo e Militão.",
                "source_url": "https://example.com/brazil-squad-attrition",
            },
            {
                "category": "elenco_talento",
                "rating_delta": -6.6,
                "confidence": 1.0,
                "correlation_group": "bra_attack_structure_2026",
                "rationale": "Problema estrutural de criação ofensiva do Brasil no ciclo 2026.",
                "source_url": "https://example.com/brazil-attack-structure",
            },
        ]
    }

    adjusted = run_brazil_monte_carlo(config)

    brazil_adjustment = next(
        item for item in adjusted["team_context"]["team_adjustments"] if item["team"] == "Brasil"
    )
    assert brazil_adjustment["rating_delta"] > -55.0
    match_groups = [
        item for item in brazil_adjustment["correlation_adjustments"]
        if set(item["member_families"]) == {
            "bets_prediction_markets",
            "injuries_cuts_news",
            "performance",
            "ratings",
        }
    ]
    assert match_groups, brazil_adjustment["correlation_adjustments"]
    assert match_groups[0]["rating_delta"] > -35.0
    assert {item["correlation_group"] for item in brazil_adjustment["correlation_adjustments"]} >= {
        "match_event:brasil:marrocos:2026-06-13",
        "br_squad_attrition",
        "bra_attack_structure_2026",
    }


def test_monte_carlo_anchors_brazil_event_reactive_context_to_completed_match_not_text_opponent() -> None:
    config = _completed_match_anchor_config(iterations=2000)
    config["monte_carlo"]["team_context_correlation_default_rho"] = 0.7
    config["monte_carlo"]["team_context_correlation_rho_by_group"] = {
        "match_event:brasil:marrocos": 0.9,
    }
    config["monte_carlo"]["team_context"] = {
        "Brasil": [
            {
                "category": "performance",
                "rating_delta": -10.0,
                "confidence": 1.0,
                "date": "2026-06-13",
                "rationale": "Performance review do Brasil; contexto geral cita Marrocos em notas amplas.",
                "source_url": "https://example.com/contexto-amplo-marrocos",
            },
            {
                "category": "injuries_cuts_news",
                "rating_delta": -8.0,
                "confidence": 1.0,
                "correlation_group": "brasil_haiti_prep",
                "rationale": "Cortes e preparação citam Haiti como próximo adversário no calendário.",
                "source_url": "https://example.com/brasil-prep-haiti",
            },
        ]
    }

    adjusted = run_brazil_monte_carlo(config)

    brazil_adjustment = _team_context_adjustment(adjusted, "Brasil")
    adjustments_by_group = {
        item["correlation_group"]: item
        for item in brazil_adjustment["correlation_adjustments"]
    }
    match_group = adjustments_by_group["match_event:brasil:marrocos:2026-06-13"]
    assert match_group["correlation_group_source"] == "completed_match"
    assert match_group["rho"] == 0.9
    assert match_group["member_families"] == ["injuries_cuts_news", "performance"]
    assert match_group["residual_delta"] != 0.0
    assert "match_event:brasil:haiti" not in adjustments_by_group
    assert all(
        signal["correlation_group"] == "match_event:brasil:marrocos:2026-06-13"
        and signal["correlation_group_source"] == "completed_match"
        for signal in brazil_adjustment["signals"]
    )
    overridden_signal = next(
        signal for signal in brazil_adjustment["signals"]
        if signal.get("model_correlation_group_hint") == "brasil_haiti_prep"
    )
    assert overridden_signal["correlation_group_override_reason"] == "completed_match_overrode_model_hint"


@pytest.mark.parametrize(
    ("team", "expected_group"),
    [
        ("Alemanha", "match_event:alemanha:curacau:2026-06-14"),
        ("Holanda", "match_event:holanda:japao:2026-06-14"),
        ("Suécia", "match_event:suecia:tunisia:2026-06-15"),
    ],
)
def test_monte_carlo_anchors_non_brazil_event_context_to_completed_match_when_text_mentions_marrocos(
    team: str,
    expected_group: str,
) -> None:
    config = _completed_match_anchor_config(iterations=2000)
    config["monte_carlo"]["team_context_correlation_default_rho"] = 0.7
    config["monte_carlo"]["team_context"] = {
        team: [
            {
                "category": "performance",
                "rating_delta": 12.0,
                "confidence": 1.0,
                "date": "2026-06-16",
                "rationale": "Relatório de performance traz Marrocos apenas como contexto paralelo do torneio.",
                "source_url": "https://example.com/world-cup-marrocos-roundup",
            },
            {
                "category": "ratings",
                "rating_delta": 7.0,
                "confidence": 1.0,
                "date": "2026-06-16",
                "rationale": "Rating pós-jogo; a página de origem também linka análise de Marrocos.",
                "source_url": "https://example.com/ratings-marrocos-sidebar",
            },
        ]
    }

    adjusted = run_brazil_monte_carlo(config)

    team_adjustment = _team_context_adjustment(adjusted, team)
    adjustments_by_group = {
        item["correlation_group"]: item
        for item in team_adjustment["correlation_adjustments"]
    }
    match_group = adjustments_by_group[expected_group]
    assert match_group["correlation_group_source"] == "completed_match"
    assert match_group["member_families"] == ["performance", "ratings"]
    assert "match_event:brasil:marrocos" not in adjustments_by_group
    assert all(
        signal["correlation_group"] == expected_group
        and signal["correlation_group_source"] == "completed_match"
        for signal in team_adjustment["signals"]
    )


def test_monte_carlo_keeps_recent_news_family_and_calendar_anchors_it() -> None:
    config = _completed_match_anchor_config(iterations=2000)
    config["monte_carlo"]["team_context_correlation_default_rho"] = 0.7
    config["monte_carlo"]["team_context"] = {
        "Brasil": [
            {
                "category": "recent_news",
                "rating_delta": -9.0,
                "confidence": 1.0,
                "rationale": "Notícia recente do ciclo pós-jogo; texto lateral menciona Haiti.",
                "source_url": "https://example.com/brasil-news-haiti-sidebar",
            },
            {
                "category": "performance",
                "rating_delta": -6.0,
                "confidence": 1.0,
                "rationale": "Performance pós-jogo.",
                "source_url": "https://example.com/brasil-performance",
            },
        ]
    }

    adjusted = run_brazil_monte_carlo(config)

    brazil_adjustment = _team_context_adjustment(adjusted, "Brasil")
    match_group = next(
        item for item in brazil_adjustment["correlation_adjustments"]
        if item["correlation_group"] == "match_event:brasil:marrocos:2026-06-13"
    )
    assert match_group["member_families"] == ["performance", "recent_news"]
    assert "recent_news" in brazil_adjustment["source_families"]
    assert all(signal["correlation_group_source"] == "completed_match" for signal in brazil_adjustment["signals"])


def test_monte_carlo_anchors_result_and_path_context_categories_to_completed_match() -> None:
    config = _completed_match_anchor_config(iterations=2000)
    config["monte_carlo"]["team_context_correlation_default_rho"] = 0.7
    config["monte_carlo"]["team_context"] = {
        "Brasil": [
            {
                "category": "performance",
                "rating_delta": -6.0,
                "confidence": 1.0,
                "correlation_group": "brazil_morocco_performance",
                "rationale": "Performance no Brasil 1-1 Marrocos.",
                "source_url": "https://example.com/brazil-morocco-performance",
            },
            {
                "category": "resultado_recente",
                "rating_delta": -16.3,
                "confidence": 1.0,
                "correlation_group": "shock_c_bra_mar_2026_06_13",
                "rationale": "Resultado recente Brasil 1-1 Marrocos em 2026-06-13.",
                "source_url": "https://example.com/brazil-morocco-result",
            },
        ],
        "Holanda": [
            {
                "category": "performance",
                "rating_delta": -8.0,
                "confidence": 1.0,
                "correlation_group": "netherlands_japan_performance",
                "rationale": "Performance no Holanda 2-2 Japão.",
                "source_url": "https://example.com/netherlands-japan-performance",
            },
            {
                "category": "caminho_16_avos",
                "rating_delta": -4.8,
                "confidence": 1.0,
                "correlation_group": "shock_f_openers_2026_06_14_15",
                "rationale": "Caminho de 16 avos reprecificado depois de Holanda 2-2 Japão em 2026-06-14.",
                "source_url": "https://example.com/netherlands-japan-path",
            },
        ],
    }

    adjusted = run_brazil_monte_carlo(config)

    brazil_adjustment = _team_context_adjustment(adjusted, "Brasil")
    brazil_groups = {
        item["correlation_group"]: item
        for item in brazil_adjustment["correlation_adjustments"]
    }
    assert set(brazil_groups) == {"match_event:brasil:marrocos:2026-06-13"}
    assert brazil_groups["match_event:brasil:marrocos:2026-06-13"]["member_families"] == [
        "performance",
        "recent_results",
    ]
    assert all(signal["correlation_group_source"] == "completed_match" for signal in brazil_adjustment["signals"])

    netherlands_adjustment = _team_context_adjustment(adjusted, "Holanda")
    netherlands_groups = {
        item["correlation_group"]: item
        for item in netherlands_adjustment["correlation_adjustments"]
    }
    assert set(netherlands_groups) == {"match_event:holanda:japao:2026-06-14"}
    assert netherlands_groups["match_event:holanda:japao:2026-06-14"]["member_families"] == [
        "path_context",
        "performance",
    ]
    assert all(signal["correlation_group_source"] == "completed_match" for signal in netherlands_adjustment["signals"])


def test_monte_carlo_warns_when_model_match_shock_lacks_calendar_anchor() -> None:
    config = _completed_match_anchor_config(iterations=2000)
    config["monte_carlo"]["team_ratings"]["Inglaterra"] = 1870
    config["monte_carlo"]["team_context_correlation_default_rho"] = 0.7
    config["monte_carlo"]["team_context"] = {
        "Inglaterra": [
            {
                "category": "resultado_recente",
                "rating_delta": 9.3,
                "confidence": 1.0,
                "correlation_group": "shock_eng_cro_2026_06_18",
                "rationale": "Resultado recente Inglaterra 4-2 Croácia em 2026-06-18.",
                "source_url": "https://example.com/england-croatia-result",
            },
        ]
    }

    adjusted = run_brazil_monte_carlo(config)

    england_adjustment = _team_context_adjustment(adjusted, "Inglaterra")
    assert england_adjustment["correlation_adjustments"] == [
        {
            "correlation_group": "recent_results",
            "rho": 0.7,
            "rating_delta": 9.3,
            "dominant_family": "recent_results",
            "dominant_delta": 9.3,
            "residual_delta": 0.0,
            "member_families": ["recent_results"],
        }
    ]
    assert {
        "team": "Inglaterra",
        "reason": "team_context_model_match_shock_without_calendar_anchor",
        "source_family": "recent_results",
        "model_correlation_group_hint": "shock_eng_cro_2026_06_18",
        "derived_match_event": "match_event:inglaterra:croacia:2026-06-18",
    } in adjusted["team_context"]["warnings"]


def test_monte_carlo_does_not_collapse_structural_context_into_completed_match_event() -> None:
    config = _completed_match_anchor_config(iterations=2000)
    config["monte_carlo"]["team_context_correlation_default_rho"] = 0.7
    config["monte_carlo"]["team_context"] = {
        "Brasil": [
            {
                "category": "elenco_talento",
                "rating_delta": 15.0,
                "confidence": 1.0,
                "rationale": "Leitura estrutural de talento do elenco; texto amplo menciona Marrocos.",
                "source_url": "https://example.com/brasil-marrocos-talento",
            },
            {
                "category": "squad_depth",
                "rating_delta": 6.0,
                "confidence": 1.0,
                "correlation_group": "match_event:brasil:marrocos:2026-06-13",
                "rationale": "Profundidade estrutural de elenco no ciclo 2026, não reação ao jogo.",
                "source_url": "https://example.com/brasil-squad-depth-marrocos",
            },
        ]
    }

    adjusted = run_brazil_monte_carlo(config)

    brazil_adjustment = _team_context_adjustment(adjusted, "Brasil")
    assert all(
        not item["correlation_group"].startswith("match_event:")
        for item in brazil_adjustment["correlation_adjustments"]
    )
    assert all(signal["correlation_group_source"] == "structural" for signal in brazil_adjustment["signals"])


def test_monte_carlo_does_not_create_match_event_from_reactive_text_without_completed_match() -> None:
    config = _mc_config(iterations=2000)
    config["completed_group_matches"] = []
    config["monte_carlo"]["team_context_correlation_default_rho"] = 0.7
    config["monte_carlo"]["team_context"] = {
        "Brasil": [
            {
                "category": "performance",
                "rating_delta": -10.0,
                "confidence": 1.0,
                "correlation_group": "brasil_pos_marrocos",
                "rationale": "Texto menciona empate com Marrocos, mas o calendário concluído não foi carregado.",
                "source_url": "https://example.com/brasil-marrocos-performance",
            },
            {
                "category": "ratings",
                "rating_delta": -8.0,
                "confidence": 1.0,
                "correlation_group": "brasil_pos_marrocos",
                "rationale": "Rating pós-Marrocos sem completed_group_matches.",
                "source_url": "https://example.com/brasil-marrocos-rating",
            },
        ]
    }

    adjusted = run_brazil_monte_carlo(config)

    brazil_adjustment = _team_context_adjustment(adjusted, "Brasil")
    groups = {item["correlation_group"] for item in brazil_adjustment["correlation_adjustments"]}
    assert not any(group.startswith("match_event:") for group in groups)
    assert groups == {"performance", "ratings"}
    assert all(signal["correlation_group_source"] == "fallback_family" for signal in brazil_adjustment["signals"])
    assert {
        "team": "Brasil",
        "reason": "team_context_reactive_families_without_calendar_anchor",
        "source_families": ["performance", "ratings"],
    } in adjusted["team_context"]["warnings"]


def test_monte_carlo_keeps_cross_family_sum_when_correlation_rho_is_zero() -> None:
    config = _mc_config(iterations=2000)
    config["monte_carlo"]["team_context_correlation_default_rho"] = 0.0
    config["monte_carlo"]["team_context_evidence_regression_enabled"] = False
    config["monte_carlo"]["team_context"] = {
        "Brasil": [
            {
                "category": "bets_prediction_markets",
                "rating_delta": -13.1,
                "confidence": 1.0,
                "correlation_group": "brasil_pos_marrocos",
                "source_url": "https://example.com/brazil-odds",
            },
            {
                "category": "injuries_cuts_news",
                "rating_delta": -13.6,
                "confidence": 1.0,
                "correlation_group": "brasil_pos_marrocos",
                "source_url": "https://example.com/brazil-injuries",
            },
            {
                "category": "ratings",
                "rating_delta": -13.0,
                "confidence": 1.0,
                "correlation_group": "brasil_pos_marrocos",
                "source_url": "https://example.com/brazil-ratings",
            },
            {
                "category": "performance",
                "rating_delta": -9.2,
                "confidence": 1.0,
                "correlation_group": "brasil_pos_marrocos",
                "source_url": "https://example.com/brazil-performance",
            },
            {
                "category": "elenco_talento",
                "rating_delta": 5.8,
                "confidence": 1.0,
                "correlation_group": "brasil_structural_talent",
                "source_url": "https://example.com/brazil-talent",
            },
        ]
    }

    adjusted = run_brazil_monte_carlo(config)

    brazil_adjustment = next(
        item for item in adjusted["team_context"]["team_adjustments"] if item["team"] == "Brasil"
    )
    assert brazil_adjustment["rating_delta"] == -43.1


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


def test_monte_carlo_output_is_bit_identical_after_hot_loop_memoization() -> None:
    """Gate de regressão do contrato completo do Monte Carlo.

    O resultado COMPLETO do Monte Carlo (todas as fases, CIs, diagnostics,
    título e placares realizados condicionantes) deve permanecer byte a byte
    estável para o contrato atual. Qualquer mudança futura que altere um único
    número precisa ser deliberada e atualizar este snapshot.

    Snapshot atualizado junto da mudança deliberada de team_context: sinais
    correlacionados passam a ser agregados por mediana dentro da família antes
    de mover o rating efetivo."""
    import hashlib
    import json as json_module
    from pathlib import Path

    from worldcup_brazil.bracket import hydrate_canonical_configs

    def _config(**mc: object) -> dict:
        config = json_module.loads(
            Path("config/worldcup_brazil.example.json").read_text(encoding="utf-8")
        )
        hydrate_canonical_configs(config, base_dir=Path("config"))
        config["monte_carlo"].update(mc)
        config["monte_carlo"]["seed"] = 26062026
        return config

    def _hash(result: dict) -> str:
        canonical = json_module.dumps(
            result, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    result_off = run_brazil_monte_carlo(
        _config(rating_uncertainty_enabled=False, iterations=2000)
    )
    result_on = run_brazil_monte_carlo(
        _config(
            rating_uncertainty_enabled=True,
            rating_uncertainty_outer_samples=6,
            rating_uncertainty_inner_iterations=400,
        )
    )

    assert _hash(result_off) == "c14d860cce5a79576dbdd1c54f9d80f7d5b6a57be3e9c7e5a4bd90951e2dce20"
    assert _hash(result_on) == "9d6af7f799a18d6d05be0de7dea5b23e22a8cc0e7d435de02ec7be9ff57087e8"


def _evidence_config(team_context: dict) -> dict:
    config = _mc_config(iterations=1500)
    config["monte_carlo"]["team_ratings"].update({"Argentina": 1820})
    config["monte_carlo"]["team_context"] = team_context
    return config


def _expected_regression(capped: float, n: int, *, prior: float = 2.0, warning: float = 40.0) -> float:
    if abs(capped) <= warning:
        return capped
    factor = n / (n + prior)
    return (1.0 if capped >= 0 else -1.0) * (warning + (abs(capped) - warning) * factor)


def test_extreme_single_source_delta_regressed_by_exact_factor() -> None:
    # Argentina +123-style blow-up: one extreme rating signal from a single source.
    config = _evidence_config(
        {
            "Argentina": [
                {
                    "category": "ratings",
                    "rating_delta": 120.0,
                    "confidence": 1.0,
                    "correlation_group": "arg_single",
                    "source_url": "https://example.com/arg",
                }
            ]
        }
    )
    reg = _team_context_adjustment(run_brazil_monte_carlo(config), "Argentina")["evidence_regression"]
    assert reg["distinct_sources"] == 1
    assert reg["independent_evidence"] == 1
    assert abs(reg["capped_delta"]) > 40.0
    # exact n/(n+2) factor -- catches a silent no-op AND a hardcoded constant factor
    assert abs(reg["regressed_delta"] - _expected_regression(reg["capped_delta"], 1)) < 0.2
    assert abs(reg["regressed_delta"]) < abs(reg["capped_delta"])  # genuinely pulled in


def test_corroborated_delta_uses_n_factor_not_noop() -> None:
    signals = [
        {
            "category": cat,
            "rating_delta": 25.0,
            "confidence": 1.0,
            "correlation_group": f"g{i}",
            "source_url": f"https://example.com/s{i}",
        }
        for i, cat in enumerate(
            ["ratings", "performance", "bets_prediction_markets", "recent_news", "specialized_press"]
        )
    ]
    reg = _team_context_adjustment(
        run_brazil_monte_carlo(_evidence_config({"Suécia": signals})), "Suécia"
    )["evidence_regression"]
    assert reg["independent_evidence"] == 5
    # exact n=5 factor; fails if the feature silently no-ops (regressed would equal capped)
    assert abs(reg["regressed_delta"] - _expected_regression(reg["capped_delta"], 5)) < 0.2
    assert _expected_regression(reg["capped_delta"], 5) > _expected_regression(reg["capped_delta"], 1)


def test_split_group_labels_cannot_dodge_regression() -> None:
    # Structural families honour the model's free-text correlation_group verbatim, so the
    # model could mint many group ids from one shock. The source cap must defeat that.
    one = [
        {
            "category": "squad_depth",
            "rating_delta": 90.0,
            "confidence": 1.0,
            "correlation_group": "struct",
            "source_url": "https://example.com/x",
        }
    ]
    split = [
        {
            "category": "squad_depth",
            "rating_delta": 10.0,
            "confidence": 1.0,
            "correlation_group": f"struct_{i}",
            "source_url": "https://example.com/x",
        }
        for i in range(9)
    ]
    a = _team_context_adjustment(run_brazil_monte_carlo(_evidence_config({"Argentina": one})), "Argentina")["evidence_regression"]
    b = _team_context_adjustment(run_brazil_monte_carlo(_evidence_config({"Argentina": split})), "Argentina")["evidence_regression"]
    assert b["distinct_sources"] == 1
    assert b["independent_evidence"] == 1  # capped by the single real source, not 9 minted labels
    assert abs(a["regressed_delta"] - b["regressed_delta"]) < 0.6  # relabelling buys nothing


def test_cap_applied_before_regression() -> None:
    # raw above the 180 cap must be capped FIRST, then regressed (not regress-then-clamp).
    signals = [
        {
            "category": cat,
            "rating_delta": 120.0,
            "confidence": 1.0,
            "correlation_group": f"g{i}",
            "source_url": f"https://example.com/s{i}",
        }
        for i, cat in enumerate(["ratings", "performance", "bets_prediction_markets"])
    ]
    reg = _team_context_adjustment(
        run_brazil_monte_carlo(_evidence_config({"Argentina": signals})), "Argentina"
    )["evidence_regression"]
    assert reg["capped_delta"] == 180.0  # clamped before regression
    assert abs(reg["regressed_delta"] - _expected_regression(180.0, reg["independent_evidence"])) < 0.5
    assert abs(reg["regressed_delta"]) < 180.0  # regression survives the cap (old code clipped to 180)


def test_regression_engages_under_production_rho() -> None:
    config = _evidence_config(
        {
            "Argentina": [
                {"category": "ratings", "rating_delta": 60.0, "confidence": 1.0,
                 "correlation_group": "g", "source_url": "https://example.com/a"},
                {"category": "performance", "rating_delta": 60.0, "confidence": 1.0,
                 "correlation_group": "g", "source_url": "https://example.com/b"},
            ]
        }
    )
    config["monte_carlo"]["team_context_correlation_default_rho"] = 0.7  # production regime
    reg = _team_context_adjustment(run_brazil_monte_carlo(config), "Argentina")["evidence_regression"]
    # material_delta=1.0 means rho shrinking a group does not flip it below the count threshold
    if abs(reg["capped_delta"]) > 40.0:
        assert reg["independent_evidence"] >= 1
        assert abs(reg["regressed_delta"]) < abs(reg["capped_delta"])


def test_small_single_source_delta_below_threshold_untouched() -> None:
    config = _evidence_config(
        {
            "Tunísia": [
                {
                    "category": "ratings",
                    "rating_delta": 15.0,
                    "confidence": 1.0,
                    "correlation_group": "mini",
                    "source_url": "https://example.com/mini",
                }
            ]
        }
    )
    reg = _team_context_adjustment(run_brazil_monte_carlo(config), "Tunísia")["evidence_regression"]
    assert abs(reg["capped_delta"]) <= 40.0
    assert reg["raw_delta"] == reg["regressed_delta"]  # below warning -> untouched

from pathlib import Path

from worldcup_brazil.consensus import AgentOpinion
from worldcup_brazil.pipeline import (
    ReportCoherenceError,
    _apply_agent_team_context_to_monte_carlo_config,
    _apply_meeting_knockout_scenarios,
    _apply_meeting_match_probabilities,
    _market_title_challenge,
    _stage_exit_distribution,
    _team_context_sensitivity_summary,
    _apply_monte_carlo_knockout_scenarios,
    _stage_confidence_intervals,
    _validate_report_coherence,
    _widen_ci_for_bracket_uncertainty,
    load_config,
)
from worldcup_brazil.probabilities import MatchEstimate


def test_apply_meeting_match_probabilities_rejects_group_win_pct_that_conflicts_with_draw_pct() -> None:
    estimate = MatchEstimate(
        brazil="Brasil",
        opponent="Marrocos",
        phase="Fase de grupos",
        brazil_pct=59.0,
        opponent_pct=17.0,
        draw_pct=24.0,
        statistical_weight=0.7,
        qualitative_weight=0.3,
        rationale="base",
    )
    opinion = AgentOpinion(
        agent="Perplexity Pro",
        title_pct=11.0,
        summary="usou chance de não perder, não vitória",
        match_probabilities={"Grupo: Marrocos": 83.0},
    )

    _apply_meeting_match_probabilities([estimate], [opinion])

    assert estimate.brazil_pct == 59.0
    assert estimate.opponent_pct == 17.0


def test_stage_confidence_intervals_envelope_monte_carlo_uncertainty_and_model_dispersion() -> None:
    config = {
        "uncertainty": {
            "confidence_level": 0.99,
            "model_dispersion_method": "logit_student_t",
        },
        "_monte_carlo_result": {
            "enabled": True,
            "confidence_level": 0.99,
            "stage_uncertainty_intervals": {
                "final": (3.0, 22.0),
                "titulo": (1.5, 18.5),
            },
            "phases": {
                "Quartas": {"reach_ci": (58.0, 62.0)},
                "Semifinal": {"reach_ci": (28.0, 32.0)},
                "Final": {"reach_ci": (9.5, 14.5)},
            },
        },
    }
    intervals = _stage_confidence_intervals(
        {
            "quartas": 60.0,
            "semifinal": 30.0,
            "final": 12.0,
            "titulo": 7.5,
        },
        dispersion_pct=0.0,
        warning_count=0,
        config=config,
        model_title_pcts=[7.2, 7.4, 7.6, 7.8],
    )

    assert config["_stage_interval_metadata"]["final"]["method"] == "logit_variance_sum"
    assert config["_stage_interval_metadata"]["titulo"]["method"] == "logit_variance_sum"
    assert "monte_carlo_epistemic" in config["_stage_interval_metadata"]["titulo"]["sources"]
    assert intervals["final"] != (3.0, 22.0)
    assert intervals["final"][0] < 12.0
    assert intervals["final"][1] > 22.0
    assert intervals["titulo"] != (1.5, 18.5)
    assert intervals["titulo"][0] < 7.5
    assert intervals["titulo"][1] > 18.5


def test_stage_confidence_intervals_falls_back_to_envelope_when_centers_disagree() -> None:
    config = {
        "uncertainty": {
            "confidence_level": 0.99,
            "model_dispersion_method": "logit_student_t",
            "logit_variance_location_gap_fallback_pct": 6.0,
        },
        "_monte_carlo_result": {
            "enabled": True,
            "confidence_level": 0.99,
            "stage_uncertainty_intervals": {"titulo": (30.0, 42.0)},
        },
    }

    intervals = _stage_confidence_intervals(
        {"titulo": 8.0},
        dispersion_pct=0.0,
        warning_count=0,
        config=config,
        model_title_pcts=[7.8, 8.2, 8.0],
    )

    assert config["_stage_interval_metadata"]["titulo"]["method"] == "envelope_fallback"
    assert config["_stage_interval_metadata"]["titulo"]["fallback_reason"] == "location_gap"
    assert intervals["titulo"] == (5.0, 42.0)


def test_market_title_challenge_flags_large_gap_without_changing_model_title() -> None:
    transcript = [
        {
            "round": 3,
            "question": "O MC aponta 4.5% de titulo, mas o mercado de-vigado está perto de 8.5%. Concordam?",
            "responses": [
                {
                    "agent": "Opus 4.8",
                    "answer": "Mercado/sportsbooks para campeão Brasil ficam entre 9% e 11%, então é divergência real.",
                    "removed_from_main": False,
                }
            ],
        }
    ]

    challenge = _market_title_challenge(
        {"titulo": 4.5},
        transcript,
        config={"market_title_challenge": {"enabled": True, "absolute_gap_pct": 3.0, "relative_gap_pct": 0.40}},
    )

    assert challenge["triggered"] is True
    assert challenge["model_title_pct"] == 4.5
    assert challenge["market_low_pct"] == 9.0
    assert challenge["market_high_pct"] == 11.0
    assert challenge["market_mid_pct"] == 10.0
    assert challenge["absolute_gap_pct"] == 5.5
    assert "mantem_funil_60_40" in challenge["decision"]


def test_market_title_challenge_ignores_protagonist_questions_as_market_evidence() -> None:
    transcript = [
        {
            "round": 1,
            "question": "O mercado de título do Brasil estaria em 9%, mas o MC está em 5.1%.",
            "responses": [
                {
                    "agent": "Opus 4.8",
                    "answer": "Sem odds auditável nesta resposta; mantenho apenas o funil.",
                    "removed_from_main": False,
                }
            ],
        }
    ]

    challenge = _market_title_challenge(
        {"titulo": 5.1},
        transcript,
        config={"market_title_challenge": {"enabled": True, "absolute_gap_pct": 3.0, "relative_gap_pct": 0.40}},
    )

    assert challenge["status"] == "no_market_signal"
    assert challenge["market_low_pct"] is None


def test_market_title_challenge_filters_model_title_leaking_into_market_band() -> None:
    transcript = [
        {
            "round": 5,
            "responses": [
                {
                    "agent": "Opus 4.8",
                    "answer": (
                        "Mantenho título em 5.1%. O mercado de título com Brasil derivando para "
                        "8/1-10/1 implica faixa de 9% a 11% antes do overround."
                    ),
                    "removed_from_main": False,
                }
            ],
        }
    ]

    challenge = _market_title_challenge(
        {"titulo": 5.1},
        transcript,
        config={"market_title_challenge": {"enabled": True, "absolute_gap_pct": 3.0, "relative_gap_pct": 0.40}},
    )

    assert challenge["market_low_pct"] == 9.1
    assert challenge["market_high_pct"] == 11.0
    assert challenge["market_mid_pct"] == 10.1


def test_market_title_challenge_filters_exact_model_title_from_run_846_pattern() -> None:
    transcript = [
        {
            "round": 2,
            "responses": [
                {
                    "agent": "Opus 4.8",
                    "answer": (
                        "Aceito o título em ~5,1%. Além disso, o mercado fresco Squawka/Flashscore, "
                        "18/jun: Brasil 8/1→10/1, implica título bruto ~7-9%, acima do MC; "
                        "o sinal externo empurra o título para cima, não para baixo."
                    ),
                    "removed_from_main": False,
                }
            ],
        },
        {
            "round": 6,
            "responses": [
                {
                    "agent": "DeepSeek V4 Pro",
                    "answer": (
                        "Concordo com 5,1% do Modelo Principal; a divergência entre a chance de título "
                        "implícita no mercado (~8-11%) e os 5,1% do Monte Carlo decorre do overround."
                    ),
                    "removed_from_main": False,
                }
            ],
        },
    ]

    challenge = _market_title_challenge(
        {"titulo": 5.1},
        transcript,
        config={"market_title_challenge": {"enabled": True, "absolute_gap_pct": 3.0, "relative_gap_pct": 0.40}},
    )

    assert challenge["market_low_pct"] > 5.1
    assert challenge["market_mid_pct"] >= 7.0


def test_market_title_challenge_uses_source_planning_texts_when_meeting_omits_market() -> None:
    challenge = _market_title_challenge(
        {"titulo": 5.1},
        [{"round": 1, "responses": []}],
        config={"market_title_challenge": {"enabled": True, "absolute_gap_pct": 3.0, "relative_gap_pct": 0.40}},
        source_texts=[
            (
                "planejamento Opus 4.8",
                "Mercado de título do Brasil: odds 8/1 a 10/1 nas casas, leitura perto de 9-11%.",
            )
        ],
    )

    assert challenge["triggered"] is True
    assert challenge["market_low_pct"] >= 8.0
    assert challenge["evidence"][0]["source"] == "planejamento Opus 4.8"


def test_market_title_challenge_ignores_small_gap_and_preserves_status() -> None:
    transcript = [
        {
            "round": 1,
            "responses": [
                {
                    "agent": "DeepSeek V4 Pro",
                    "answer": "Mercado de titulo do Brasil esta em 8.9%, muito perto do MC em 8.2%.",
                    "removed_from_main": False,
                }
            ],
        }
    ]

    challenge = _market_title_challenge(
        {"titulo": 8.2},
        transcript,
        config={"market_title_challenge": {"enabled": True, "absolute_gap_pct": 3.0, "relative_gap_pct": 0.40}},
    )

    assert challenge["triggered"] is False
    assert challenge["status"] == "within_threshold"
    assert challenge["model_title_pct"] == 8.2


def test_stage_exit_distribution_derives_modal_exit_from_reach_probabilities() -> None:
    distribution = _stage_exit_distribution(
        {
            "16_avos": 98.3,
            "oitavas": 59.9,
            "quartas": 40.1,
            "semifinal": 21.8,
            "final": 10.9,
            "titulo": 5.1,
        }
    )

    assert distribution["modal_exit_stage"] == "16 avos"
    assert distribution["modal_exit_pct"] == 38.4
    assert distribution["exit_buckets"][1] == {"stage": "16 avos", "exit_pct": 38.4}
    assert distribution["exit_buckets"][3] == {"stage": "quartas", "exit_pct": 18.3}


def test_team_context_sensitivity_summary_exposes_brazil_delta_and_replay_scenarios() -> None:
    summary = _team_context_sensitivity_summary(
        {
            "stage_probabilities": {"titulo": 5.1},
            "team_context": {
                "team_adjustments": [
                    {
                        "team": "Brasil",
                        "rating_delta": -17.4,
                        "correlation_adjustments": [
                            {
                                "correlation_group": "match_event:brasil:marrocos:2026-06-13",
                                "rho": 0.7,
                                "rating_delta": -17.4,
                            }
                        ],
                    }
                ]
            },
        }
    )

    assert summary["enabled"] is True
    assert summary["brazil_rating_delta"] == -17.4
    assert summary["requires_recalc"] is True
    assert summary["recommended_scenarios"] == [
        "current",
        "rho_1_price_once",
        "rho_0_full_sum",
        "no_brazil_context",
    ]


def test_market_title_challenge_ignores_match_probability_distractors() -> None:
    transcript = [
        {
            "round": 2,
            "responses": [
                {
                    "agent": "Perplexity Pro",
                    "answer": (
                        "Mercado de título do Brasil está em 8.5%; no jogo contra Haiti, "
                        "empate aparece em 17% e vitória brasileira em 76%."
                    ),
                    "removed_from_main": False,
                }
            ],
        }
    ]

    challenge = _market_title_challenge(
        {"titulo": 4.5},
        transcript,
        config={"market_title_challenge": {"enabled": True, "absolute_gap_pct": 3.0, "relative_gap_pct": 0.40}},
    )

    assert challenge["market_low_pct"] == 8.5
    assert challenge["market_high_pct"] == 8.5
    assert "17" not in str(challenge["market_high_pct"])


def test_market_title_challenge_rejects_model_probability_when_extracting_market_band() -> None:
    transcript = [
        {
            "round": 4,
            "responses": [
                {
                    "agent": "Opus 4.8",
                    "answer": (
                        "Mercado de título desafia o 2.9% de título do Modelo Principal; "
                        "sportsbooks/outright para campeão "
                        "Brasil ficam em ~9% bruta, de-vigado perto de 7-8%, com odds 10/1."
                    ),
                    "removed_from_main": False,
                }
            ],
        }
    ]

    challenge = _market_title_challenge(
        {"titulo": 4.3},
        transcript,
        config={"market_title_challenge": {"enabled": True, "absolute_gap_pct": 3.0, "relative_gap_pct": 0.40}},
    )

    assert challenge["triggered"] is True
    assert challenge["market_low_pct"] == 7.6
    assert challenge["market_high_pct"] == 9.0
    assert challenge["market_mid_pct"] == 8.3
    assert challenge["absolute_gap_pct"] == 4.0


def test_market_title_challenge_uses_robust_market_band_when_model_value_leaks_from_debate() -> None:
    transcript = [
        {
            "round": 1,
            "question": (
                "Minha tese: o 2.9% de título do Modelo Principal está baixo demais e inconsistente "
                "com o mercado — o Brasil drifou de 8/1 para 10/1 após o 1-1 com Marrocos mas "
                "segue 5º no oddsboard (~9-10% bruto, ~7-8% sem overround), enquanto o penalty "
                "efetivo de -64.9 derruba artificialmente a chance de título; proponho revisar "
                "o título para ~6%."
            ),
        },
        {
            "round": 4,
            "responses": [
                {
                    "agent": "GPT 5.5",
                    "answer": (
                        "O problema não é escolher entre 2.9% e 6.5% por barganha, "
                        "mas corrigir o agregador. O fetch da Squawka sustenta que o mercado "
                        "moveu o Brasil de 8/1 para 10/1 após o empate, mas ainda com odds muito "
                        "acima de uma probabilidade de título de apenas 2.9%. Isso não prova 6.5%, "
                        "mas torna 2.9% difícil de defender. Minha saída central é Brasil campeão "
                        "em 6.3%, dentro da faixa 6.0-6.5%."
                    ),
                    "removed_from_main": False,
                }
            ],
        },
        {
            "round": 6,
            "question": (
                "O de-vig do board fresco converge para cima: Brasil +1000 ≈ 9.1% implícita bruta "
                "para campeão."
            ),
        },
    ]

    challenge = _market_title_challenge(
        {"titulo": 4.3},
        transcript,
        config={"market_title_challenge": {"enabled": True, "absolute_gap_pct": 3.0, "relative_gap_pct": 0.40}},
    )

    assert challenge["triggered"] is True
    assert challenge["market_low_pct"] > 2.9
    assert challenge["market_mid_pct"] >= 7.3
    assert challenge["market_band_method"] == "robust_percentile"


def test_apply_meeting_match_probabilities_accepts_valid_group_win_pct() -> None:
    estimate = MatchEstimate(
        brazil="Brasil",
        opponent="Marrocos",
        phase="Fase de grupos",
        brazil_pct=59.0,
        opponent_pct=17.0,
        draw_pct=24.0,
        statistical_weight=0.7,
        qualitative_weight=0.3,
        rationale="base",
    )
    opinion = AgentOpinion(
        agent="Perplexity Pro",
        title_pct=11.0,
        summary="vitória explícita",
        match_probabilities={"Grupo: Marrocos": 58.0},
    )

    _apply_meeting_match_probabilities([estimate], [opinion])

    assert estimate.brazil_pct == 58.0
    assert estimate.opponent_pct == 18.0


def test_validate_report_coherence_rejects_group_match_probabilities_over_one_hundred() -> None:
    group_estimates = [
        MatchEstimate(
            brazil="Brasil",
            opponent="Haiti",
            phase="Fase de grupos",
            brazil_pct=92.0,
            draw_pct=8.0,
            opponent_pct=8.0,
            statistical_weight=0.5,
            qualitative_weight=0.5,
            rationale="bundle incoerente do run de 16/jun",
        )
    ]

    try:
        _validate_report_coherence(
            stage_probabilities={"quartas": 37.4, "semifinal": 20.1, "final": 9.8, "titulo": 4.5},
            group_estimates=group_estimates,
            knockout_estimates=[],
            monte_carlo_result=None,
        )
    except ReportCoherenceError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ReportCoherenceError")

    assert "Fase de grupos vs Haiti" in message
    assert "V+E+D=108.0%" in message


def test_validate_report_coherence_rejects_knockout_match_probabilities_not_summing_to_one_hundred() -> None:
    knockout_estimates = [
        MatchEstimate(
            brazil="Brasil",
            opponent="Inglaterra",
            phase="Quartas",
            brazil_pct=40.7,
            opponent_pct=50.0,
            statistical_weight=0.5,
            qualitative_weight=0.5,
            rationale="dois caminhos não somam 100",
            scenario_pct=33.9,
        )
    ]

    try:
        _validate_report_coherence(
            stage_probabilities={"quartas": 37.4, "semifinal": 20.1, "final": 9.8, "titulo": 4.5},
            group_estimates=[],
            knockout_estimates=knockout_estimates,
            monte_carlo_result=None,
        )
    except ReportCoherenceError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ReportCoherenceError")

    assert "Quartas vs Inglaterra" in message
    assert "Brasil+adversário=90.7%" in message


def test_widen_ci_for_bracket_uncertainty_expands_placeholder_knockout_interval() -> None:
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
    match = {
        "phase": "16 avos",
        "opponent": "Adversário mais provável a definir",
        "allowed_opponents": ["Holanda", "Japão", "Suécia", "Tunísia"],
    }

    _widen_ci_for_bracket_uncertainty(estimate, match, config={"bracket_uncertainty_ci_widening": True})

    assert estimate.brazil_ci_low == 51.7
    assert estimate.brazil_ci_high == 62.3


def test_widen_ci_for_bracket_uncertainty_does_not_expand_named_knockout_interval() -> None:
    estimate = MatchEstimate(
        brazil="Brasil",
        opponent="Holanda",
        phase="16 avos",
        brazil_pct=57.0,
        opponent_pct=43.0,
        statistical_weight=0.5,
        qualitative_weight=0.5,
        rationale="base",
        brazil_ci_low=53.0,
        brazil_ci_high=61.0,
    )
    match = {
        "phase": "16 avos",
        "opponent": "Holanda",
        "allowed_opponents": ["Holanda", "Japão", "Suécia", "Tunísia"],
    }

    _widen_ci_for_bracket_uncertainty(estimate, match, config={"bracket_uncertainty_ci_widening": True})

    assert estimate.brazil_ci_low == 53.0
    assert estimate.brazil_ci_high == 61.0


def test_apply_meeting_knockout_scenarios_uses_only_official_bracket_candidates() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    estimates = [
        MatchEstimate(
            brazil="Brasil",
            opponent="Adversário mais provável a definir",
            phase="16 avos",
            brazil_pct=57.0,
            opponent_pct=43.0,
            statistical_weight=0.5,
            qualitative_weight=0.5,
            rationale="base",
            most_likely=True,
        ),
        MatchEstimate(
            brazil="Brasil",
            opponent="Segundo adversário mais provável a definir",
            phase="16 avos",
            brazil_pct=57.0,
            opponent_pct=43.0,
            statistical_weight=0.5,
            qualitative_weight=0.5,
            rationale="base",
            most_likely=False,
        ),
    ]
    opinions = [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=10.0,
            summary="cenários",
            scenario_probabilities={"16 avos: Canadá": 80.0, "16 avos: Holanda": 31.0, "16 avos: Japão": 24.0},
            match_probabilities={"16 avos: Holanda": 56.0, "16 avos: Japão": 61.0},
        ),
        AgentOpinion(
            agent="Gemini Pro",
            title_pct=10.0,
            summary="cenários",
            scenario_probabilities={"16 avos: Holanda": 33.0, "16 avos: Japão": 22.0},
            match_probabilities={"16 avos: Holanda": 54.0, "16 avos: Japão": 60.0},
        ),
    ]

    _apply_meeting_knockout_scenarios(estimates, opinions, config=config)

    assert estimates[0].opponent == "Holanda"
    assert estimates[0].scenario_pct == 32.0
    assert estimates[0].brazil_pct == 55.0
    assert estimates[0].opponent_pct == 45.0
    assert estimates[1].opponent == "Japão"
    assert estimates[1].scenario_pct == 23.0
    assert estimates[1].brazil_pct == 60.5
    assert estimates[1].opponent_pct == 39.5


def test_apply_meeting_knockout_scenarios_filters_candidates_eliminated_by_monte_carlo_path() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    estimates = [
        MatchEstimate(
            brazil="Brasil",
            opponent="Adversário mais provável a definir",
            phase="16 avos",
            brazil_pct=57.0,
            opponent_pct=43.0,
            statistical_weight=0.5,
            qualitative_weight=0.5,
            rationale="base",
            most_likely=True,
        ),
        MatchEstimate(
            brazil="Brasil",
            opponent="Segundo adversário mais provável a definir",
            phase="16 avos",
            brazil_pct=57.0,
            opponent_pct=43.0,
            statistical_weight=0.5,
            qualitative_weight=0.5,
            rationale="base",
            most_likely=False,
        ),
    ]
    opinions = [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=10.0,
            summary="Holanda tem narrativa forte, mas não aparece no caminho simulado.",
            scenario_probabilities={"16 avos: Holanda": 91.0, "16 avos: Japão": 34.0, "16 avos: Suécia": 28.0},
            match_probabilities={"16 avos: Holanda": 52.0, "16 avos: Japão": 58.0, "16 avos: Suécia": 57.0},
        )
    ]
    monte_carlo_result = {
        "enabled": True,
        "iterations": 40000,
        "rating_coverage_pct": 82.0,
        "phases": {
            "16 avos": {
                "opponents": [
                    {"opponent": "Japão", "scenario_pct": 35.0, "brazil_pct": 58.0},
                    {"opponent": "Suécia", "scenario_pct": 27.0, "brazil_pct": 57.0},
                ]
            }
        },
    }

    _apply_meeting_knockout_scenarios(
        estimates,
        opinions,
        config=config,
        monte_carlo_result=monte_carlo_result,
    )

    assert estimates[0].opponent == "Japão"
    assert estimates[1].opponent == "Suécia"


def test_apply_meeting_knockout_scenarios_keeps_model_candidate_when_monte_carlo_path_is_weak() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    estimates = [
        MatchEstimate(
            brazil="Brasil",
            opponent="Adversário mais provável a definir",
            phase="16 avos",
            brazil_pct=57.0,
            opponent_pct=43.0,
            statistical_weight=0.5,
            qualitative_weight=0.5,
            rationale="base",
            most_likely=True,
        ),
        MatchEstimate(
            brazil="Brasil",
            opponent="Segundo adversário mais provável a definir",
            phase="16 avos",
            brazil_pct=57.0,
            opponent_pct=43.0,
            statistical_weight=0.5,
            qualitative_weight=0.5,
            rationale="base",
            most_likely=False,
        ),
    ]
    opinions = [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=10.0,
            summary="Holanda tem fonte forte; Monte Carlo está fraco e não pode eliminar.",
            scenario_probabilities={"16 avos: Holanda": 91.0, "16 avos: Japão": 34.0, "16 avos: Suécia": 28.0},
            match_probabilities={"16 avos: Holanda": 52.0, "16 avos: Japão": 58.0, "16 avos: Suécia": 57.0},
        )
    ]
    monte_carlo_result = {
        "enabled": True,
        "iterations": 1200,
        "rating_coverage_pct": 28.0,
        "phases": {
            "16 avos": {
                "opponents": [
                    {"opponent": "Japão", "scenario_pct": 35.0, "brazil_pct": 58.0},
                    {"opponent": "Suécia", "scenario_pct": 27.0, "brazil_pct": 57.0},
                ]
            }
        },
    }

    _apply_meeting_knockout_scenarios(
        estimates,
        opinions,
        config=config,
        monte_carlo_result=monte_carlo_result,
    )

    assert estimates[0].opponent == "Holanda"
    assert estimates[1].opponent == "Japão"


def test_apply_meeting_knockout_scenarios_rejects_match_values_copied_from_scenario_probabilities() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    estimates = [
        MatchEstimate(
            brazil="Brasil",
            opponent="Adversário mais provável a definir",
            phase="Oitavas",
            brazil_pct=57.0,
            opponent_pct=43.0,
            statistical_weight=0.5,
            qualitative_weight=0.5,
            rationale="base",
            most_likely=True,
        ),
        MatchEstimate(
            brazil="Brasil",
            opponent="Segundo adversário mais provável a definir",
            phase="Oitavas",
            brazil_pct=57.0,
            opponent_pct=43.0,
            statistical_weight=0.5,
            qualitative_weight=0.5,
            rationale="base",
            most_likely=False,
        ),
    ]
    opinions = [
        AgentOpinion(
            agent="Gemini Pro",
            title_pct=8.0,
            summary="copiou chance de cenário no campo de chance de jogo",
            scenario_probabilities={"Oitavas: Curaçau": 14.2, "Oitavas: Senegal": 14.1},
            match_probabilities={"Oitavas: Curaçau": 14.2, "Oitavas: Senegal": 14.1},
        )
    ]
    monte_carlo_result = {
        "enabled": True,
        "iterations": 40000,
        "rating_coverage_pct": 16.7,
        "phases": {
            "Oitavas": {
                "opponents": [
                    {"opponent": "Curaçau", "scenario_pct": 14.2, "brazil_pct": 89.5},
                    {"opponent": "Senegal", "scenario_pct": 14.1, "brazil_pct": 89.2},
                ]
            }
        },
    }

    _apply_meeting_knockout_scenarios(
        estimates,
        opinions,
        config=config,
        monte_carlo_result=monte_carlo_result,
    )

    assert estimates[0].opponent == "Curaçau"
    assert estimates[0].scenario_pct == 14.2
    assert estimates[0].brazil_pct == 89.5
    assert estimates[1].opponent == "Senegal"
    assert estimates[1].scenario_pct == 14.1
    assert estimates[1].brazil_pct == 89.2


def test_apply_meeting_match_probabilities_rejects_knockout_values_copied_from_scenarios() -> None:
    estimate = MatchEstimate(
        brazil="Brasil",
        opponent="Curaçau",
        phase="Oitavas",
        brazil_pct=89.5,
        opponent_pct=10.5,
        statistical_weight=0.5,
        qualitative_weight=0.5,
        rationale="base",
        scenario_pct=14.2,
    )
    opinion = AgentOpinion(
        agent="Gemini Pro",
        title_pct=8.0,
        summary="copiou cenário para jogo",
        scenario_probabilities={"Oitavas: Curaçau": 14.2},
        match_probabilities={"Oitavas: Curaçau": 14.2},
    )

    _apply_meeting_match_probabilities([estimate], [opinion])

    assert estimate.brazil_pct == 89.5
    assert estimate.opponent_pct == 10.5


def test_apply_meeting_match_probabilities_recenters_ci_after_valid_update() -> None:
    estimate = MatchEstimate(
        brazil="Brasil",
        opponent="Curaçau",
        phase="Oitavas",
        brazil_pct=57.0,
        opponent_pct=43.0,
        statistical_weight=0.5,
        qualitative_weight=0.5,
        rationale="base",
        brazil_ci_low=50.0,
        brazil_ci_high=64.0,
        scenario_pct=14.2,
    )
    opinion = AgentOpinion(
        agent="Gemini Pro",
        title_pct=8.0,
        summary="usou probabilidade condicional correta",
        match_probabilities={"Brasil-Curaçau": 89.5},
    )

    _apply_meeting_match_probabilities([estimate], [opinion])

    assert estimate.brazil_pct == 89.5
    assert estimate.opponent_pct == 10.5
    assert estimate.brazil_ci_low == 82.5
    assert estimate.brazil_ci_high == 96.5


def test_apply_monte_carlo_knockout_scenarios_sets_top_two_before_model_room() -> None:
    estimates = [
        MatchEstimate(
            brazil="Brasil",
            opponent="Adversário mais provável a definir",
            phase="16 avos",
            brazil_pct=57.0,
            opponent_pct=43.0,
            statistical_weight=0.5,
            qualitative_weight=0.5,
            rationale="base",
            most_likely=True,
        ),
        MatchEstimate(
            brazil="Brasil",
            opponent="Segundo adversário mais provável a definir",
            phase="16 avos",
            brazil_pct=57.0,
            opponent_pct=43.0,
            statistical_weight=0.5,
            qualitative_weight=0.5,
            rationale="base",
            most_likely=False,
        ),
    ]
    monte_carlo_result = {
        "enabled": True,
        "phases": {
            "16 avos": {
                "opponents": [
                    {"opponent": "Japão", "scenario_pct": 34.0, "brazil_pct": 58.0},
                    {"opponent": "Suécia", "scenario_pct": 25.0, "brazil_pct": 57.0},
                ]
            }
        },
    }

    _apply_monte_carlo_knockout_scenarios(estimates, monte_carlo_result)

    assert estimates[0].opponent == "Japão"
    assert estimates[0].scenario_pct == 34.0
    assert estimates[0].brazil_pct == 58.0
    assert estimates[1].opponent == "Suécia"
    assert estimates[1].scenario_pct == 25.0
    assert estimates[1].brazil_pct == 57.0


def test_apply_agent_team_context_to_monte_carlo_config_uses_only_auditable_model_signals() -> None:
    config = {"monte_carlo": {"enabled": True}}
    opinions = [
        AgentOpinion(
            agent="GPT 5.5",
            title_pct=10.0,
            summary="trouxe contexto",
            source_urls=["https://example.com/sweden-market"],
            team_context_signals=[
                {
                    "team": "Suécia",
                    "category": "bets_prediction_markets",
                    "rating_delta": 90,
                    "confidence": 0.8,
                    "source_url": "https://example.com/sweden-market",
                    "rationale": "Mercado encurtou Suécia.",
                },
                {
                    "team": "Holanda",
                    "category": "recent_news",
                    "rating_delta": -70,
                    "confidence": 0.8,
                    "rationale": "Sem fonte, deve ser ignorado.",
                },
            ],
        )
    ]

    report = _apply_agent_team_context_to_monte_carlo_config(config, opinions)

    assert report["applied_signal_count"] == 1
    assert report["ignored_signal_count"] == 1
    assert config["monte_carlo"]["team_context"]["Suécia"][0]["agent"] == "GPT 5.5"
    assert config["monte_carlo"]["team_context"]["Suécia"][0]["rating_delta"] == 90
    assert "Holanda" not in config["monte_carlo"]["team_context"]


def test_validate_report_coherence_rejects_title_above_final_probability() -> None:
    try:
        _validate_report_coherence(
            stage_probabilities={
                "quartas": 60.8,
                "semifinal": 23.1,
                "final": 9.9,
                "titulo": 10.2,
            },
            knockout_estimates=[],
            monte_carlo_result={"enabled": False},
        )
    except ReportCoherenceError as exc:
        assert "titulo" in str(exc).lower()
        assert "final" in str(exc).lower()
    else:
        raise AssertionError("expected ReportCoherenceError")


def test_validate_report_coherence_rejects_knockout_scenario_pct_echo_in_match_pct() -> None:
    estimate = MatchEstimate(
        brazil="Brasil",
        opponent="Curaçau",
        phase="Oitavas",
        brazil_pct=14.2,
        opponent_pct=85.8,
        statistical_weight=0.5,
        qualitative_weight=0.5,
        rationale="base",
        scenario_pct=14.2,
    )

    try:
        _validate_report_coherence(
            stage_probabilities={
                "quartas": 60.8,
                "semifinal": 23.1,
                "final": 9.9,
                "titulo": 7.4,
            },
            knockout_estimates=[estimate],
            monte_carlo_result={
                "enabled": True,
                "phases": {
                    "Oitavas": {
                        "opponents": [
                            {"opponent": "Curaçau", "scenario_pct": 14.2, "brazil_pct": 89.5}
                        ]
                    }
                },
            },
        )
    except ReportCoherenceError as exc:
        assert "Curaçau" in str(exc)
        assert "scenario_pct" in str(exc)
    else:
        raise AssertionError("expected ReportCoherenceError")

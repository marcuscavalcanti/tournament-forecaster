from datetime import datetime, timezone

from worldcup_brazil.pipeline import (
    _rationale,
    _scenario_pct_for_match,
    _signals_for_match,
    _source_planning_prompt,
)
from worldcup_brazil.probabilities import blend_match_estimate


def _event_harness_config() -> dict:
    return {
        "recent_event_impacts": [
            {
                "id": "brasil-egito-2-1-friendly",
                "date": "2026-06-06",
                "team": "Brasil",
                "category": "statistical",
                "summary": "Amistoso: Brasil 2x1 Egito",
                "source_url": "https://example.com/brasil-egito-2-1",
                "brazil_shift_pct": 1.2,
                "scenario_shift_pct": 0.4,
                "confidence": 0.72,
            },
            {
                "id": "wesley-right-back-cut",
                "date": "2026-06-07",
                "team": "Brasil",
                "category": "qualitative",
                "summary": "Corte do lateral direito Wesley",
                "source_url": "https://example.com/wesley-corte",
                "brazil_shift_pct": -4.0,
                "scenario_shift_pct": -0.7,
                "confidence": 0.78,
            },
            {
                "id": "uruguai-attacking-form",
                "date": "2026-06-07",
                "team": "Uruguai",
                "category": "qualitative",
                "summary": "Uruguai chega com alta de performance ofensiva",
                "source_url": "https://example.com/uruguai-forma",
                "brazil_shift_pct": -2.5,
                "scenario_shift_pct": 1.5,
                "confidence": 0.66,
            },
        ],
        "group_matches": [{"opponent": "Marrocos", "brazil_pct": 59.0}],
        "knockout_matches": [
            {
                "phase": "16 avos",
                "opponent": "Uruguai",
                "brazil_pct": 57.0,
                "scenario_pct": 46.0,
                "most_likely": True,
            }
        ],
    }


def test_model_prompts_receive_recent_event_impact_pack() -> None:
    prompt = _source_planning_prompt(
        config=_event_harness_config(),
        generated_at=datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
    )
    lowered = prompt.lower()

    assert "recent_event_impacts" in prompt
    assert "Brasil 2x1 Egito" in prompt
    assert "Corte do lateral direito Wesley" in prompt
    assert "Uruguai chega com alta de performance ofensiva" in prompt
    assert "source_url" in prompt
    assert "não invente fonte" in lowered


def test_model_prompts_require_same_event_criteria_through_final_simulations() -> None:
    config = _event_harness_config()
    config["knockout_matches"] = [
        {
            "phase": "16 avos",
            "opponent": "Uruguai",
            "brazil_pct": 57.0,
            "scenario_pct": 46.0,
            "most_likely": True,
        },
        {
            "phase": "Oitavas",
            "opponent": "Espanha",
            "brazil_pct": 54.0,
            "scenario_pct": 39.0,
            "most_likely": True,
        },
        {
            "phase": "Quartas",
            "opponent": "França",
            "brazil_pct": 49.0,
            "scenario_pct": 31.0,
            "most_likely": True,
        },
        {
            "phase": "Semifinal",
            "opponent": "Argentina",
            "brazil_pct": 48.0,
            "scenario_pct": 22.0,
            "most_likely": True,
        },
        {
            "phase": "Final",
            "opponent": "Inglaterra",
            "brazil_pct": 51.0,
            "scenario_pct": 15.0,
            "most_likely": True,
        },
    ]

    prompt = _source_planning_prompt(
        config=config,
        generated_at=datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
    )
    lowered = prompt.lower()

    assert "event_impact_scenarios" in prompt
    assert "mesmos critérios da fase de grupos" in lowered
    for phase in ("16 avos", "Oitavas", "Quartas", "Semifinal", "Final"):
        assert phase in prompt
    for field in (
        "date",
        "team",
        "category",
        "summary",
        "source_url",
        "source_query",
        "brazil_shift_pct",
        "scenario_shift_pct",
        "confidence",
    ):
        assert field in prompt


def test_recent_quant_and_qual_events_move_brazil_group_estimate_and_rationale() -> None:
    config = _event_harness_config()
    match = config["group_matches"][0]

    statistical, qualitative = _signals_for_match(match, evidence=[], knockout=False, config=config)
    estimate = blend_match_estimate(
        brazil="Brasil",
        opponent="Marrocos",
        phase="Fase de grupos",
        statistical=statistical,
        qualitative=qualitative,
        rationale=_rationale(match, evidence=[], knockout=False, config=config),
    )

    assert any("Brasil 2x1 Egito" in signal.detail for signal in statistical)
    assert any("Corte do lateral direito Wesley" in signal.detail for signal in qualitative)
    assert estimate.brazil_pct < 59.0
    assert "Brasil 2x1 Egito" in estimate.rationale
    assert "Corte do lateral direito Wesley" in estimate.rationale


def test_recent_opponent_event_moves_knockout_simulation_and_scenario_probability() -> None:
    config = _event_harness_config()
    match = config["knockout_matches"][0]

    statistical, qualitative = _signals_for_match(match, evidence=[], knockout=True, config=config)
    estimate = blend_match_estimate(
        brazil="Brasil",
        opponent="Uruguai",
        phase="16 avos",
        statistical=statistical,
        qualitative=qualitative,
        rationale=_rationale(match, evidence=[], knockout=True, config=config),
        scenario_pct=_scenario_pct_for_match(match, config=config),
    )

    assert any("Uruguai chega com alta de performance ofensiva" in signal.detail for signal in qualitative)
    assert estimate.brazil_pct < 57.0
    assert estimate.scenario_pct == 47.2
    assert "Uruguai chega com alta de performance ofensiva" in estimate.rationale

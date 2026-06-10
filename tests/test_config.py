from pathlib import Path

import json
import os

from worldcup_brazil.cli import _config_watchdog_detail, _config_watchdog_extra
from worldcup_brazil.pipeline import _apply_runtime_env_overrides, load_config


def test_load_config_uses_example_when_default_config_is_missing(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "worldcup_brazil.example.json").write_text(
        '{"group_name": "GRUPO C", "baseline_title_pct": 9.5}',
        encoding="utf-8",
    )

    config = load_config(config_dir / "worldcup_brazil.json")

    assert config["group_name"] == "GRUPO C"
    assert config["baseline_title_pct"] == 9.5


def test_example_config_uses_three_agent_source_quorum_and_repair_attempts() -> None:
    config = load_config(Path("config/worldcup_brazil.example.json"))
    teams = {
        team["name"]
        for teams_in_group in config["groups_config"]["groups"].values()
        for team in teams_in_group
    }
    configured_rating_teams = set(config["monte_carlo"]["team_ratings"])

    assert config["minimum_source_ready_agents"] == 3
    assert config["model_preflight_contract_enabled"] is True
    assert config["model_preflight_timeout_seconds"] == 180
    assert config["agent_timeout_seconds"] >= 240
    assert config["agent_reentry_probe_enabled"] is True
    assert config["agent_reentry_probe_timeout_seconds"] == 180
    assert config["source_planning_repair_attempts"] >= 1
    assert config["meeting_response_repair_attempts"] >= 1
    assert config["meeting_min_participants"] == 3
    assert "meeting_min_real_agents" not in config
    assert config["meeting_require_full_path_coverage"] is True
    assert config["parallel_opponent_debriefing_enabled"] is True
    assert config["monte_carlo"]["path_gate_min_iterations"] == 10000
    assert config["monte_carlo"]["path_gate_min_rating_coverage_pct"] == 65.0
    assert config["monte_carlo"]["path_gate_reliable_prior_weight"] == 2.0
    assert config["monte_carlo"]["path_gate_unreliable_prior_weight"] == 0.35
    assert config["monte_carlo"]["path_gate_max_ci_narrow_pct"] == 3.0
    assert config["uncertainty"]["confidence_level"] == 0.99
    assert config["uncertainty"]["minimum_declared_coverage"] == 0.95
    assert config["uncertainty"]["model_dispersion_method"] == "logit_student_t"
    assert config["monte_carlo"]["confidence_level"] == 0.99
    assert config["monte_carlo"]["rating_uncertainty_enabled"] is True
    assert config["monte_carlo"]["rating_uncertainty_outer_samples"] == 200
    assert config["monte_carlo"]["rating_uncertainty_inner_iterations"] == 200
    assert config["monte_carlo"]["configured_rating_sigma"] == 50.0
    assert config["monte_carlo"]["prior_rating_sigma"] == 150.0
    assert len(teams) == 48
    assert configured_rating_teams == teams


def test_load_config_watchdog_payload_exposes_safe_operational_config() -> None:
    config = {
        "custom_hashtag": "#copaComAchismo",
        "baseline_title_pct": 11.0,
        "group_name": "GRUPO C",
        "brazil_group": "C",
        "brazil_expected_group_position": 1,
        "enforce_bracket_constraints": True,
        "bracket_uncertainty_ci_widening": True,
        "uncertainty": {"confidence_level": 0.99, "minimum_declared_coverage": 0.95},
        "monte_carlo": {
            "confidence_level": 0.99,
            "rating_uncertainty_enabled": True,
            "rating_uncertainty_outer_samples": 200,
            "rating_uncertainty_inner_iterations": 200,
        },
        "model_preflight_contract_enabled": True,
        "minimum_source_ready_agents": 3,
        "source_planning_repair_attempts": 2,
        "meeting_response_repair_attempts": 1,
        "meeting_min_rounds": 6,
        "meeting_max_rounds": 18,
        "meeting_min_participants": 3,
        "meeting_require_full_path_coverage": True,
        "agent_timeout_seconds": 90,
        "strict_agents": False,
        "agents": [
            {"slot": "GPT 5.5", "env_api_key": "OPENAI_API_KEY"},
            {"slot": "Gemini Pro", "env_api_key": "GEMINI_API_KEY"},
        ],
        "group_matches": [{"opponent": "Marrocos"}],
        "knockout_matches": [{"phase": "16 avos", "opponent": "Uruguai"}],
    }

    detail = _config_watchdog_detail(
        config,
        requested_config_path=Path("config/worldcup_brazil.json"),
        effective_config_path=Path("config/worldcup_brazil.example.json"),
        strict_agents=False,
    )
    extra = _config_watchdog_extra(
        config,
        requested_config_path=Path("config/worldcup_brazil.json"),
        effective_config_path=Path("config/worldcup_brazil.example.json"),
        strict_agents=False,
    )

    assert "top-level config keys" in detail
    assert "effective=config/worldcup_brazil.example.json" in detail
    assert "brazil_group=C" in detail
    assert "brazil_position=1" in detail
    assert "bracket_constraints=True" in detail
    assert "confidence_level=0.99" in detail
    assert "rating_uncertainty=True" in detail
    assert "quorum_min=3" in detail
    assert "repair_attempts=2" in detail
    assert "meeting_min_participants=3" in detail
    assert "meeting_quorum_rule=maioria simples" in detail
    assert "full_path_coverage=True" in detail
    assert "agents=2" in detail
    assert "group_matches=1" in detail
    assert extra["paths"]["requested_config"] == "config/worldcup_brazil.json"
    assert extra["paths"]["effective_config"] == "config/worldcup_brazil.example.json"
    assert extra["watchdog_config"]["minimum_source_ready_agents"] == 3
    assert extra["watchdog_config"]["brazil_group"] == "C"
    assert extra["watchdog_config"]["brazil_expected_group_position"] == 1
    assert extra["watchdog_config"]["enforce_bracket_constraints"] is True
    assert extra["watchdog_config"]["bracket_uncertainty_ci_widening"] is True
    assert extra["watchdog_config"]["uncertainty"]["confidence_level"] == 0.99
    assert extra["watchdog_config"]["monte_carlo_uncertainty"]["rating_uncertainty_enabled"] is True
    assert extra["watchdog_config"]["monte_carlo_uncertainty"]["rating_uncertainty_outer_samples"] == 200
    assert extra["watchdog_config"]["model_preflight_contract_enabled"] is True
    assert extra["watchdog_config"]["source_planning_repair_attempts"] == 2
    assert extra["watchdog_config"]["meeting_response_repair_attempts"] == 1
    assert extra["watchdog_config"]["meeting_min_participants"] == 3
    assert extra["watchdog_config"]["meeting_quorum_rule"] == "maioria simples dos participantes ativos da sala"
    assert extra["watchdog_config"]["meeting_require_full_path_coverage"] is True
    assert extra["agents"]["slots"] == ["GPT 5.5", "Gemini Pro"]
    assert "bracket_path" in extra["scope"]
    assert "bracket_validation_errors" in extra["scope"]
    assert "OPENAI_API_KEY" not in json.dumps(extra)


def test_apply_runtime_env_overrides_sets_retry_and_bulkhead_knobs_without_overwriting_shell(monkeypatch) -> None:
    keys = [
        "HTTP_MAX_ATTEMPTS",
        "HTTP_BACKOFF_BASE_SECONDS",
        "HTTP_BACKOFF_MAX_SECONDS",
        "HTTP_CONNECT_TIMEOUT_SECONDS",
        "AGENT_BULKHEAD_DEFAULT",
        "SOURCE_BULKHEAD_PER_HOST",
    ]
    original = {key: os.environ.get(key) for key in keys}
    try:
        monkeypatch.setenv("HTTP_MAX_ATTEMPTS", "5")
        monkeypatch.delenv("HTTP_BACKOFF_BASE_SECONDS", raising=False)
        monkeypatch.delenv("HTTP_BACKOFF_MAX_SECONDS", raising=False)
        monkeypatch.delenv("HTTP_CONNECT_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("AGENT_BULKHEAD_DEFAULT", raising=False)
        monkeypatch.delenv("SOURCE_BULKHEAD_PER_HOST", raising=False)

        _apply_runtime_env_overrides(
            {
                "http_max_attempts": 4,
                "http_backoff_base_seconds": 0.5,
                "http_backoff_max_seconds": 8.0,
                "http_connect_timeout_seconds": 4.0,
                "agent_bulkhead_default": 2,
                "source_bulkhead_per_host": 1,
            }
        )

        assert os.environ["HTTP_MAX_ATTEMPTS"] == "5"
        assert os.environ["HTTP_BACKOFF_BASE_SECONDS"] == "0.5"
        assert os.environ["HTTP_BACKOFF_MAX_SECONDS"] == "8.0"
        assert os.environ["HTTP_CONNECT_TIMEOUT_SECONDS"] == "4.0"
        assert os.environ["AGENT_BULKHEAD_DEFAULT"] == "2"
        assert os.environ["SOURCE_BULKHEAD_PER_HOST"] == "1"
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

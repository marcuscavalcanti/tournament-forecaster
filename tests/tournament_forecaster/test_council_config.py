from __future__ import annotations

import json
from pathlib import Path

import pytest

from tournament_forecaster.council.config import (
    load_council_config,
    load_council_document,
)
from tournament_forecaster.errors import TournamentValidationError


def _document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "enabled": False,
        "engine_weight": 0.55,
        "council_weight": 0.45,
        "rounds": 2,
        "minimum_valid_agents": 2,
        "timeout_seconds": 120,
        "max_attempts": 2,
        "agents": [
            {
                "id": "gpt-reviewer",
                "display_name": "GPT reviewer",
                "provider": "openai",
                "model": "gpt-example",
                "api_key_env": "OPENAI_API_KEY",
                "reasoning_effort": "high",
                "max_output_tokens": 2400,
                "temperature": 0.2,
            },
            {
                "id": "web-reviewer",
                "display_name": "Web reviewer",
                "provider": "openai-compatible",
                "model": "sonar-example",
                "api_key_env": "PERPLEXITY_API_KEY",
                "endpoint": "https://api.example.test/chat/completions",
                "enabled": True,
            },
        ],
    }


def test_loads_portable_55_45_council_configuration(tmp_path: Path) -> None:
    path = tmp_path / "council.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")

    config = load_council_config(path)

    assert config.enabled is False
    assert config.engine_weight == 0.55
    assert config.council_weight == 0.45
    assert config.rounds == 2
    assert config.minimum_valid_agents == 2
    assert config.timeout_seconds == 120
    assert config.max_attempts == 2
    assert [agent.id for agent in config.enabled_agents] == [
        "gpt-reviewer",
        "web-reviewer",
    ]
    assert config.agents[0].reasoning_effort == "high"
    assert config.agents[0].max_output_tokens == 2400
    assert config.agents[1].endpoint == "https://api.example.test/chat/completions"


def test_provider_defaults_keep_standard_endpoints_out_of_user_config() -> None:
    document = _document()
    agents = document["agents"]
    assert isinstance(agents, list)
    agents.extend(
        [
            {
                "id": "opus-reviewer",
                "display_name": "Opus reviewer",
                "provider": "anthropic",
                "model": "claude-example",
                "api_key_env": "ANTHROPIC_API_KEY",
                "thinking_budget_tokens": 4096,
            },
            {
                "id": "gemini-reviewer",
                "display_name": "Gemini reviewer",
                "provider": "google-gemini",
                "model": "gemini-example",
                "api_key_env": "GEMINI_API_KEY",
                "reasoning_effort": "high",
            },
        ]
    )

    config = load_council_document(document)

    assert config.agents[0].endpoint == "https://api.openai.com/v1/responses"
    assert config.agents[2].endpoint == "https://api.anthropic.com/v1/messages"
    assert config.agents[3].endpoint.endswith("/{model}:generateContent")
    assert config.agents[2].thinking_budget_tokens == 4096


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value.update(engine_weight=0.6),
            "engine_weight and council_weight must sum to 1",
        ),
        (
            lambda value: value.update(engine_weight=float("nan")),
            "engine_weight must be finite",
        ),
        (
            lambda value: value["agents"].append(value["agents"][0].copy()),
            "agent ids must be unique",
        ),
        (
            lambda value: value["agents"][0].update(api_key_env="sk-live-secret"),
            "api_key_env must be an environment variable name",
        ),
        (
            lambda value: value["agents"][1].pop("endpoint"),
            "openai-compatible agents must define endpoint",
        ),
        (
            lambda value: value["agents"][1].update(endpoint="http://api.example.test"),
            "endpoint must use https",
        ),
        (
            lambda value: value["agents"][0].update(reasoning_effort="maximum-ish"),
            "reasoning_effort is unsupported",
        ),
        (
            lambda value: value.update(minimum_valid_agents=3),
            "minimum_valid_agents cannot exceed enabled agents",
        ),
    ],
)
def test_rejects_unsafe_or_incoherent_council_configuration(
    mutate: object,
    message: str,
) -> None:
    document = _document()
    assert callable(mutate)
    mutate(document)

    with pytest.raises(TournamentValidationError, match=message):
        load_council_document(document)


def test_rejects_a_custom_blend_policy_even_when_weights_sum_to_one() -> None:
    document = _document()
    document["engine_weight"] = 0.6
    document["council_weight"] = 0.4

    with pytest.raises(
        TournamentValidationError,
        match="council blend policy must be exactly 0.55 engine and 0.45 council",
    ):
        load_council_document(document)


def test_rejects_unknown_properties_instead_of_silently_ignoring_typos() -> None:
    document = _document()
    document["council_weigth"] = 0.45

    with pytest.raises(TournamentValidationError, match="unknown properties"):
        load_council_document(document)

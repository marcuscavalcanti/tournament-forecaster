from __future__ import annotations

import io
import urllib.error
from collections.abc import Mapping

import pytest

from tournament_forecaster.council.config import CouncilAgentConfig
from tournament_forecaster.council.models import parse_opinion
from tournament_forecaster.council.providers import (
    CouncilProviderError,
    call_configured_agent,
    call_provider,
    post_json,
)
from tournament_forecaster.errors import TournamentValidationError


def _agent(provider: str, *, endpoint: str = "https://provider.example/v1") -> CouncilAgentConfig:
    return CouncilAgentConfig(
        id=f"{provider.replace('_', '-')}-reviewer",
        display_name=f"{provider} reviewer",
        provider=provider,
        model="model-example",
        api_key_env="PROVIDER_API_KEY",
        endpoint=endpoint,
        reasoning_effort="high",
        thinking_budget_tokens=2048,
        max_output_tokens=1800,
        temperature=0.1,
    )


@pytest.mark.parametrize(
    ("provider", "expected_path", "header_name", "payload_assertions"),
    [
        (
            "openai",
            "https://provider.example/v1",
            "Authorization",
            {"input": "review this", "reasoning": {"effort": "high"}},
        ),
        (
            "anthropic",
            "https://provider.example/v1",
            "x-api-key",
            {
                "messages": [{"role": "user", "content": "review this"}],
                "thinking": {"type": "enabled", "budget_tokens": 2048},
            },
        ),
        (
            "openai-compatible",
            "https://provider.example/v1",
            "Authorization",
            {"messages": [{"role": "user", "content": "review this"}]},
        ),
        (
            "google-gemini",
            "https://provider.example/v1",
            "X-goog-api-key",
            {
                "contents": [{"parts": [{"text": "review this"}]}],
                "generationConfig": {
                    "maxOutputTokens": 1800,
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                    "thinkingConfig": {"thinkingBudget": 2048},
                },
            },
        ),
    ],
)
def test_builds_provider_specific_requests_without_persisting_credentials(
    provider: str,
    expected_path: str,
    header_name: str,
    payload_assertions: Mapping[str, object],
) -> None:
    captured: dict[str, object] = {}

    def transport(
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: int,
    ) -> Mapping[str, object]:
        captured.update(
            url=url,
            headers=dict(headers),
            payload=dict(payload),
            timeout_seconds=timeout_seconds,
        )
        if provider == "openai":
            return {"output_text": "provider answer"}
        if provider == "anthropic":
            return {"content": [{"type": "text", "text": "provider answer"}]}
        if provider == "openai-compatible":
            return {"choices": [{"message": {"content": "provider answer"}}]}
        return {
            "candidates": [{"content": {"parts": [{"text": "provider answer"}]}}]
        }

    response = call_provider(
        _agent(provider),
        "review this",
        api_key="super-secret",
        timeout_seconds=17,
        transport=transport,
    )

    assert response.text == "provider answer"
    assert response.runtime_model == "model-example"
    assert captured["url"] == expected_path
    assert captured["timeout_seconds"] == 17
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "super-secret" in str(headers[header_name])
    payload = captured["payload"]
    assert isinstance(payload, dict)
    for key, value in payload_assertions.items():
        assert payload[key] == value
    assert "super-secret" not in str(payload)


def test_reads_api_key_only_from_the_named_environment_variable() -> None:
    called = False

    def transport(*_args: object, **_kwargs: object) -> Mapping[str, object]:
        nonlocal called
        called = True
        return {"output_text": "should not run"}

    with pytest.raises(CouncilProviderError) as captured:
        call_configured_agent(
            _agent("openai"),
            "prompt",
            timeout_seconds=10,
            max_attempts=1,
            environ={},
            transport=transport,
        )

    assert captured.value.category == "missing_credentials"
    assert "PROVIDER_API_KEY" in captured.value.detail
    assert called is False


def test_google_429_surfaces_prepayment_credit_action_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = (
        b'{"error":{"message":"Your prepayment credits are depleted. '
        b'Please go to AI Studio to manage your project and billing."}}'
    )
    error = urllib.error.HTTPError(
        "https://provider.example/v1",
        429,
        "Too Many Requests",
        {},
        io.BytesIO(body),
    )

    def fail(*_args: object, **_kwargs: object) -> object:
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fail)

    with pytest.raises(CouncilProviderError) as captured:
        post_json(
            "https://provider.example/v1",
            {"X-goog-api-key": "super-secret"},
            {"model": "example"},
            timeout_seconds=10,
        )

    assert captured.value.category == "quota"
    assert "prepayment credits are depleted" in captured.value.detail
    assert "buy or add credits" in captured.value.detail
    assert "super-secret" not in captured.value.detail


def test_parses_fenced_structured_opinion_without_accepting_missing_stages() -> None:
    opinion = parse_opinion(
        """```json
        {
          "stage_probabilities": {"group-stage": 1.0, "final": 0.2},
          "championship_probability": 0.1,
          "confidence": 0.75,
          "summary": "The baseline is directionally sound.",
          "key_factors": ["availability", "opponent path"]
        }
        ```""",
        agent_id="gpt-reviewer",
        round_number=1,
        stage_order=("group-stage", "final"),
        locked_stage_probabilities={"group-stage": 1.0},
    )

    assert dict(opinion.stage_probabilities) == {"group-stage": 1.0, "final": 0.2}
    assert opinion.championship_probability == 0.1
    assert opinion.confidence == 0.75
    assert opinion.key_factors == ("availability", "opponent path")

    with pytest.raises(TournamentValidationError, match="all stage probabilities"):
        parse_opinion(
            '{"stage_probabilities":{"final":0.2},'
            '"championship_probability":0.1,"confidence":0.8,'
            '"summary":"Missing group","key_factors":[]}',
            agent_id="gpt-reviewer",
            round_number=1,
            stage_order=("group-stage", "final"),
            locked_stage_probabilities={"group-stage": 1.0},
        )


def test_rejects_opinion_that_changes_locked_or_non_monotonic_probabilities() -> None:
    with pytest.raises(TournamentValidationError, match="locked stage group-stage"):
        parse_opinion(
            '{"stage_probabilities":{"group-stage":0.9,"final":0.2},'
            '"championship_probability":0.1,"confidence":0.8,'
            '"summary":"Bad lock","key_factors":[]}',
            agent_id="reviewer",
            round_number=1,
            stage_order=("group-stage", "final"),
            locked_stage_probabilities={"group-stage": 1.0},
        )

    with pytest.raises(TournamentValidationError, match="non-increasing"):
        parse_opinion(
            '{"stage_probabilities":{"group-stage":0.6,"final":0.7},'
            '"championship_probability":0.1,"confidence":0.8,'
            '"summary":"Bad funnel","key_factors":[]}',
            agent_id="reviewer",
            round_number=1,
            stage_order=("group-stage", "final"),
            locked_stage_probabilities={},
        )

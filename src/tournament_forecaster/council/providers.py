"""Portable HTTPS adapters for council model providers."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypeAlias

from .config import CouncilAgentConfig


JsonObject: TypeAlias = Mapping[str, object]
Transport: TypeAlias = Callable[
    [str, Mapping[str, str], Mapping[str, object], int],
    Mapping[str, object],
]
_SECRET_HEADER_NAMES = frozenset({"authorization", "x-api-key", "x-goog-api-key"})


class CouncilProviderError(RuntimeError):
    """A classified provider failure safe to persist in an audit artifact."""

    def __init__(self, category: str, detail: str, *, retryable: bool = False) -> None:
        super().__init__(detail)
        self.category = category
        self.detail = detail
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Provider text plus the runtime model that produced it."""

    text: str
    runtime_model: str


def _safe_detail(text: str, secrets: tuple[str, ...] = ()) -> str:
    normalized = " ".join(text.split())
    for secret in secrets:
        if secret:
            normalized = normalized.replace(secret, "[REDACTED]")
    return normalized[:600] or "provider returned no error detail"


def _header_secrets(headers: Mapping[str, str]) -> tuple[str, ...]:
    secrets: list[str] = []
    for name, value in headers.items():
        if name.casefold() not in _SECRET_HEADER_NAMES:
            continue
        secrets.append(value)
        scheme, separator, credential = value.partition(" ")
        if separator and scheme.casefold() == "bearer" and credential.strip():
            secrets.append(credential.strip())
    return tuple(secrets)


def _error_message(body: str) -> str:
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        return body
    if isinstance(document, Mapping):
        error = document.get("error")
        if isinstance(error, Mapping) and isinstance(error.get("message"), str):
            return str(error["message"])
        if isinstance(document.get("message"), str):
            return str(document["message"])
    return body


def post_json(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, object],
    timeout_seconds: int,
) -> Mapping[str, object]:
    """POST JSON and classify external failures without retaining credentials."""

    request = urllib.request.Request(
        url,
        data=json.dumps(payload, allow_nan=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "tournament-forecaster/0.1",
            **dict(headers),
        },
        method="POST",
    )
    secrets = _header_secrets(headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        detail = _safe_detail(_error_message(body), secrets)
        category = "request"
        retryable = False
        if error.code == 429:
            category = "quota"
            if "prepayment credits are depleted" in detail.casefold():
                detail += " You need to buy or add credits in Google AI Studio before retrying."
        elif error.code in {401, 403}:
            category = "authentication"
        elif error.code >= 500:
            category = "unavailable"
            retryable = True
        raise CouncilProviderError(category, detail, retryable=retryable) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise CouncilProviderError(
            "unavailable",
            _safe_detail(str(error), secrets),
            retryable=True,
        ) from error
    try:
        document = json.loads(body)
    except json.JSONDecodeError as error:
        raise CouncilProviderError(
            "invalid_response",
            f"provider returned invalid JSON: {error.msg}",
        ) from error
    if not isinstance(document, Mapping):
        raise CouncilProviderError("invalid_response", "provider response must be an object")
    return document


def _extract_openai(document: Mapping[str, object]) -> str:
    if isinstance(document.get("output_text"), str):
        return str(document["output_text"])
    chunks: list[str] = []
    output = document.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping) or not isinstance(item.get("content"), list):
                continue
            for content in item["content"]:
                if isinstance(content, Mapping) and isinstance(content.get("text"), str):
                    chunks.append(str(content["text"]))
    return "\n".join(chunks).strip()


def _extract_anthropic(document: Mapping[str, object]) -> str:
    chunks: list[str] = []
    content = document.get("content")
    if isinstance(content, list):
        for item in content:
            if (
                isinstance(item, Mapping)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ):
                chunks.append(str(item["text"]))
    return "\n".join(chunks).strip()


def _extract_chat(document: Mapping[str, object]) -> str:
    choices = document.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return ""
    message = choices[0].get("message")
    return str(message.get("content", "")).strip() if isinstance(message, Mapping) else ""


def _extract_gemini(document: Mapping[str, object]) -> str:
    chunks: list[str] = []
    candidates = document.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            content = candidate.get("content")
            if not isinstance(content, Mapping) or not isinstance(content.get("parts"), list):
                continue
            for part in content["parts"]:
                if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                    chunks.append(str(part["text"]))
    return "\n".join(chunks).strip()


def call_provider(
    agent: CouncilAgentConfig,
    prompt: str,
    *,
    api_key: str,
    timeout_seconds: int,
    transport: Transport = post_json,
) -> ProviderResponse:
    """Call one configured provider through an injectable JSON transport."""

    if agent.provider == "openai":
        payload: dict[str, object] = {
            "model": agent.model,
            "input": prompt,
            "max_output_tokens": agent.max_output_tokens,
            "temperature": agent.temperature,
        }
        if agent.reasoning_effort:
            payload["reasoning"] = {"effort": agent.reasoning_effort}
        document = transport(
            agent.endpoint,
            {"Authorization": f"Bearer {api_key}"},
            payload,
            timeout_seconds,
        )
        text = _extract_openai(document)
    elif agent.provider == "anthropic":
        payload = {
            "model": agent.model,
            "max_tokens": agent.max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if agent.thinking_budget_tokens is not None:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": agent.thinking_budget_tokens,
            }
        else:
            payload["temperature"] = agent.temperature
        document = transport(
            agent.endpoint,
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            payload,
            timeout_seconds,
        )
        text = _extract_anthropic(document)
    elif agent.provider == "openai-compatible":
        document = transport(
            agent.endpoint,
            {"Authorization": f"Bearer {api_key}"},
            {
                "model": agent.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": agent.max_output_tokens,
                "temperature": agent.temperature,
            },
            timeout_seconds,
        )
        text = _extract_chat(document)
    elif agent.provider == "google-gemini":
        generation: dict[str, object] = {
            "maxOutputTokens": agent.max_output_tokens,
            "temperature": agent.temperature,
            "responseMimeType": "application/json",
        }
        if agent.thinking_budget_tokens is not None:
            generation["thinkingConfig"] = {
                "thinkingBudget": agent.thinking_budget_tokens
            }
        elif agent.reasoning_effort:
            generation["thinkingConfig"] = {
                "thinkingLevel": agent.reasoning_effort.upper()
            }
        document = transport(
            agent.endpoint.replace(
                "{model}", urllib.parse.quote(agent.model, safe="")
            ),
            {"X-goog-api-key": api_key},
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": generation,
            },
            timeout_seconds,
        )
        text = _extract_gemini(document)
    else:
        raise CouncilProviderError(
            "configuration", f"unsupported council provider {agent.provider}"
        )
    if not text:
        raise CouncilProviderError("invalid_response", "provider returned no text")
    return ProviderResponse(text=text, runtime_model=agent.model)


def call_configured_agent(
    agent: CouncilAgentConfig,
    prompt: str,
    *,
    timeout_seconds: int,
    max_attempts: int,
    environ: Mapping[str, str] | None = None,
    transport: Transport = post_json,
) -> ProviderResponse:
    """Resolve one environment key and apply bounded retry to external failures."""

    environment = os.environ if environ is None else environ
    api_key = environment.get(agent.api_key_env)
    if not api_key:
        raise CouncilProviderError(
            "missing_credentials",
            f"missing environment variable {agent.api_key_env}",
        )
    last_error: CouncilProviderError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return call_provider(
                agent,
                prompt,
                api_key=api_key,
                timeout_seconds=timeout_seconds,
                transport=transport,
            )
        except CouncilProviderError as error:
            last_error = error
            if not error.retryable or attempt == max_attempts:
                raise
            time.sleep(min(0.25 * 2 ** (attempt - 1), 1.0))
    assert last_error is not None
    raise last_error

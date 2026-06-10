from __future__ import annotations

import asyncio
import json
import os
import random
import re
import signal
import shlex
import shutil
import subprocess
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from worldcup_brazil.consensus import AgentOpinion, REQUIRED_AGENT_SLOTS


DEFAULT_MAX_OUTPUT_TOKENS = 6000
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_PREFLIGHT_TIMEOUT_SECONDS = 180
DEFAULT_HTTP_MAX_ATTEMPTS = 3
DEFAULT_HTTP_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_HTTP_BACKOFF_MAX_SECONDS = 12.0
RETRYABLE_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_GEMINI_FALLBACK_MODEL = "gemini-3.1-flash-lite"


@dataclass(frozen=True)
class AgentSpec:
    slot: str
    provider: str
    model: str
    env_api_key: str | None
    endpoint: str
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    temperature: float = 0.15
    reasoning_effort: str | None = None
    thinking_budget_tokens: int | None = None
    web_fetch_url: str | None = None
    browser_command: list[str] | None = None
    browser_fallback_commands: list[list[str]] | None = None
    model_fallbacks: list[str] | None = None
    prefer_bridge: bool = False


@dataclass(frozen=True)
class AgentPreflightResult:
    slot: str
    provider: str
    configured_model: str
    runtime_model: str
    method: str
    ok: bool
    declared_name: str = ""
    declared_version: str = ""
    message: str = ""
    error: str = ""
    elapsed_ms: int = 0


def _http_max_attempts() -> int:
    return max(1, _env_int("HTTP_MAX_ATTEMPTS") or DEFAULT_HTTP_MAX_ATTEMPTS)


def _http_backoff_base_seconds() -> float:
    value = os.environ.get("HTTP_BACKOFF_BASE_SECONDS")
    if not value:
        return DEFAULT_HTTP_BACKOFF_BASE_SECONDS
    try:
        return max(0.0, float(value))
    except ValueError:
        return DEFAULT_HTTP_BACKOFF_BASE_SECONDS


def _http_backoff_max_seconds() -> float:
    value = os.environ.get("HTTP_BACKOFF_MAX_SECONDS")
    if not value:
        return DEFAULT_HTTP_BACKOFF_MAX_SECONDS
    try:
        return max(0.0, float(value))
    except ValueError:
        return DEFAULT_HTTP_BACKOFF_MAX_SECONDS


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    headers = getattr(exc, "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _retry_delay_seconds(attempt_index: int, exc: urllib.error.HTTPError) -> float:
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return min(retry_after, _http_backoff_max_seconds())
    base = _http_backoff_base_seconds()
    exponential = base * (2 ** max(0, attempt_index - 1))
    jitter = random.uniform(0.0, min(base, 1.0)) if base > 0 else 0.0
    return min(exponential + jitter, _http_backoff_max_seconds())


def _is_retryable_http_error(exc: urllib.error.HTTPError) -> bool:
    return int(getattr(exc, "code", 0) or 0) in RETRYABLE_HTTP_STATUS_CODES


def _open_url_with_retries(request: urllib.request.Request, *, timeout: int) -> Any:
    attempts = _http_max_attempts()
    for attempt in range(1, attempts + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if attempt >= attempts or not _is_retryable_http_error(exc):
                raise
            time.sleep(_retry_delay_seconds(attempt, exc))
    raise RuntimeError("unreachable retry loop")


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with _open_url_with_retries(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _env_command(name: str) -> list[str] | None:
    value = os.environ.get(name)
    if not value:
        return None
    return shlex.split(value)


def _local_cli_command(binary: str) -> list[str] | None:
    path = shutil.which(binary)
    if not path:
        return None
    return [path, "{prompt}"]


def _local_claude_cli_command() -> list[str] | None:
    path = shutil.which("claude")
    if not path:
        return None
    model = os.environ.get("CLAUDE_CLI_MODEL", "claude-opus-4-8")
    effort = os.environ.get("CLAUDE_CLI_EFFORT", "high")
    allowed_tools = os.environ.get("CLAUDE_CLI_ALLOWED_TOOLS", "WebSearch,WebFetch")
    command = [
        path,
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--model",
        model,
        "--effort",
        effort,
    ]
    if allowed_tools.strip():
        command.append(f"--allowedTools={allowed_tools.strip()}")
    command.append("{prompt}")
    return command


def _local_codex_cli_command() -> list[str] | None:
    path = shutil.which("codex")
    if not path:
        return None
    return [
        path,
        "--search",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "-s",
        "read-only",
        "{prompt}",
    ]


def _local_openai_cli_command(model: str) -> list[str] | None:
    path = shutil.which("openai")
    if not path:
        return None
    effort = os.environ.get("OPENAI_REASONING_EFFORT", "high")
    return [
        path,
        "responses",
        "create",
        "--model",
        model,
        "--input",
        "{prompt_json}",
        "--reasoning",
        json.dumps({"effort": effort}, separators=(",", ":")),
    ]


def _local_gemini_cli_command(model: str) -> list[str] | None:
    path = shutil.which("gemini")
    if not path:
        return None
    model = os.environ.get("GEMINI_CLI_MODEL") or model
    return [
        path,
        "--skip-trust",
        "-p",
        "{prompt}",
        "--output-format",
        "text",
        "--approval-mode",
        "plan",
        "-m",
        model,
    ]


def _openai_cli_command(model: str) -> list[str] | None:
    return (
        _env_command("OPENAI_BROWSER_COMMAND")
        or _env_command("OPENAI_CLI_COMMAND")
        or _env_command("CHATGPT_CLI_COMMAND")
        or _env_command("GPT_CLI_COMMAND")
        or _local_openai_cli_command(model)
        or _local_codex_cli_command()
    )


def _openai_cli_fallback_commands(primary: list[str] | None) -> list[list[str]]:
    codex = _local_codex_cli_command()
    if not primary or not codex:
        return []
    primary_name = os.path.basename(primary[0]) if primary else ""
    if primary_name == "codex" or primary == codex:
        return []
    return [codex]


def _gemini_cli_command() -> list[str] | None:
    model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    return (
        _env_command("GEMINI_BROWSER_COMMAND")
        or _env_command("GEMINI_CLI_COMMAND")
        or _local_gemini_cli_command(model)
    )


def _gemini_model_fallbacks(primary_model: str) -> list[str]:
    raw = os.environ.get("GEMINI_FALLBACK_MODELS")
    if raw:
        fallbacks = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        fallbacks = [DEFAULT_GEMINI_FALLBACK_MODEL]
    return [model for model in fallbacks if model and model != primary_model]


def _env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "sim", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "nao", "não", "off"}:
        return False
    return default


def _browser_command_timeout(timeout: int) -> int:
    configured = _env_int("BROWSER_COMMAND_TIMEOUT_SECONDS")
    if configured is not None:
        return max(1, configured)
    return max(1, timeout)


def agent_effort_profile(spec: AgentSpec) -> dict[str, str]:
    controls: list[str] = []
    native_control = False
    effort_level = "nível configurado + resposta rápida"
    if spec.provider == "openai" and spec.reasoning_effort:
        controls.append(f"reasoning_effort={spec.reasoning_effort}")
        native_control = True
        effort_level = f"reasoning_effort={spec.reasoning_effort} + resposta rápida"
    elif spec.provider == "google-gemini":
        if spec.thinking_budget_tokens is not None:
            controls.append(f"thinkingBudget={spec.thinking_budget_tokens}")
            effort_level = f"thinkingBudget={spec.thinking_budget_tokens} + resposta rápida"
        elif spec.reasoning_effort:
            controls.append(f"thinkingLevel={spec.reasoning_effort.upper()}")
            effort_level = f"thinkingLevel={spec.reasoning_effort.upper()} + resposta rápida"
        native_control = bool(controls)
    elif spec.provider == "anthropic" and spec.thinking_budget_tokens is not None:
        controls.append(f"thinking_budget_tokens={spec.thinking_budget_tokens}")
        native_control = True
        effort_level = f"thinking_budget_tokens={spec.thinking_budget_tokens} + resposta rápida"

    if spec.browser_command:
        command_name = os.path.basename(spec.browser_command[0])
        bridge_label = {
            "chatgpt": "ChatGPT CLI",
            "claude": "claude CLI",
            "codex": "codex CLI",
            "gemini": "Gemini CLI",
            "openai": "OpenAI CLI",
        }.get(command_name, command_name)
        controls.append(f"bridge={bridge_label}")
        if spec.prefer_bridge:
            controls.append("bridge_preferred=true")
        if "--effort" in spec.browser_command:
            effort_index = spec.browser_command.index("--effort") + 1
            if effort_index < len(spec.browser_command):
                bridge_effort = spec.browser_command[effort_index]
                controls.append(f"bridge_effort={bridge_effort}")
                effort_level = f"bridge effort={bridge_effort} + resposta rápida"
        if "--model" in spec.browser_command:
            model_index = spec.browser_command.index("--model") + 1
            if model_index < len(spec.browser_command):
                controls.append(f"bridge_model={spec.browser_command[model_index]}")
        elif "-m" in spec.browser_command:
            model_index = spec.browser_command.index("-m") + 1
            if model_index < len(spec.browser_command):
                controls.append(f"bridge_model={spec.browser_command[model_index]}")
        configured_bridge_timeout = _env_int("BROWSER_COMMAND_TIMEOUT_SECONDS")
        if configured_bridge_timeout is not None:
            controls.append(f"bridge_timeout={max(1, configured_bridge_timeout)}s")
        else:
            controls.append("bridge_timeout=agent_timeout_seconds")
    if spec.browser_fallback_commands:
        fallback_labels = []
        for command in spec.browser_fallback_commands:
            if not command:
                continue
            command_name = os.path.basename(command[0])
            fallback_labels.append(
                {
                    "codex": "codex CLI",
                    "openai": "OpenAI CLI",
                    "chatgpt": "ChatGPT CLI",
                }.get(command_name, command_name)
            )
        if fallback_labels:
            controls.append(f"bridge_fallback={', '.join(fallback_labels)}")
    controls.append(f"http_retries={_http_max_attempts()}")
    controls.append(f"http_backoff={_http_backoff_base_seconds():.1f}-{_http_backoff_max_seconds():.1f}s")
    controls.append(f"bulkhead={_agent_bulkhead_key(spec)}:{_agent_bulkhead_limit(spec)}")
    if not native_control:
        controls.append("prompt_effort=high-equivalent")
        controls.append("sem parâmetro nativo de esforço; usa prompt/bridge configurado")

    controls.append(f"max_output_tokens={spec.max_output_tokens}")
    controls.append(f"temperature={spec.temperature:.2f}")
    return {
        "provider": spec.provider,
        "model": spec.model,
        "effort_level": effort_level,
        "controls": "; ".join(controls),
        "latency_guard": "JSON objetivo; resposta curta o suficiente para reduzir latência; sem texto fora do formato",
    }


def agent_effort_profiles(specs: list[AgentSpec]) -> dict[str, dict[str, str]]:
    return {spec.slot: agent_effort_profile(spec) for spec in specs}


def default_agent_specs() -> list[AgentSpec]:
    claude_web_fetch = os.environ.get("CLAUDE_WEB_FETCH_URL")
    claude_browser_command = (
        _env_command("CLAUDE_BROWSER_COMMAND")
        or _env_command("CLAUDE_CLI_COMMAND")
        or _local_claude_cli_command()
    )
    claude_prefer_bridge = _env_bool(
        "CLAUDE_PREFER_BRIDGE",
        _env_bool("CLAUDE_PREFER_CLI", bool(claude_web_fetch or claude_browser_command)),
    )
    openai_web_fetch = os.environ.get("OPENAI_WEB_FETCH_URL") or os.environ.get("CHATGPT_WEB_FETCH_URL")
    openai_model = os.environ.get("OPENAI_GPT_MODEL", "gpt-5.5")
    openai_browser_command = _openai_cli_command(openai_model)
    openai_browser_fallback_commands = _openai_cli_fallback_commands(openai_browser_command)
    openai_prefer_bridge = _env_bool(
        "OPENAI_PREFER_BRIDGE",
        _env_bool("OPENAI_PREFER_CLI", bool(openai_web_fetch or openai_browser_command)),
    )
    gemini_web_fetch = os.environ.get("GEMINI_WEB_FETCH_URL")
    gemini_model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    gemini_browser_command = _gemini_cli_command()
    gemini_model_fallbacks = _gemini_model_fallbacks(gemini_model)
    gemini_prefer_bridge = _env_bool(
        "GEMINI_PREFER_BRIDGE",
        _env_bool("GEMINI_PREFER_CLI", bool(gemini_web_fetch or gemini_browser_command)),
    )
    return [
        AgentSpec(
            slot="Opus 4.8",
            provider="anthropic",
            model=os.environ.get("ANTHROPIC_OPUS_MODEL", "claude-opus-4-8"),
            env_api_key="ANTHROPIC_API_KEY",
            endpoint="https://api.anthropic.com/v1/messages",
            thinking_budget_tokens=_env_int("ANTHROPIC_THINKING_BUDGET_TOKENS"),
            web_fetch_url=claude_web_fetch,
            browser_command=claude_browser_command,
            prefer_bridge=claude_prefer_bridge,
        ),
        AgentSpec(
            slot="GPT 5.5",
            provider="openai",
            model=openai_model,
            env_api_key="OPENAI_API_KEY",
            endpoint="https://api.openai.com/v1/responses",
            reasoning_effort=os.environ.get("OPENAI_REASONING_EFFORT", "high"),
            web_fetch_url=openai_web_fetch,
            browser_command=openai_browser_command,
            browser_fallback_commands=openai_browser_fallback_commands,
            prefer_bridge=openai_prefer_bridge,
        ),
        AgentSpec(
            slot="Perplexity Pro",
            provider="openai-compatible",
            model=os.environ.get("PERPLEXITY_MODEL", "sonar-pro"),
            env_api_key="PERPLEXITY_API_KEY",
            endpoint="https://api.perplexity.ai/chat/completions",
        ),
        AgentSpec(
            slot="DeepSeek V4 Pro",
            provider="openai-compatible",
            model=os.environ.get("DEEPSEEK_V4_PRO_MODEL", "deepseek-v4-pro"),
            env_api_key="DEEPSEEK_API_KEY",
            endpoint=(
                os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
                + "/chat/completions"
            ),
            max_output_tokens=7000,
        ),
        AgentSpec(
            slot="Gemini Pro",
            provider="google-gemini",
            model=gemini_model,
            env_api_key="GEMINI_API_KEY",
            endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            max_output_tokens=7000,
            reasoning_effort=os.environ.get("GEMINI_REASONING_EFFORT", "high"),
            thinking_budget_tokens=_env_int("GEMINI_THINKING_BUDGET_TOKENS"),
            web_fetch_url=gemini_web_fetch,
            browser_command=gemini_browser_command,
            model_fallbacks=gemini_model_fallbacks,
            prefer_bridge=gemini_prefer_bridge,
        ),
    ]


DEFAULT_AGENT_SPECS = default_agent_specs()


def _extract_openai_response(data: dict[str, Any]) -> str:
    if "output_text" in data:
        return str(data["output_text"])
    chunks = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                chunks.append(str(content.get("text", "")))
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def _extract_chat_completion(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message", {})
    return str(message.get("content", "")).strip()


def _extract_anthropic_response(data: dict[str, Any]) -> str:
    chunks = []
    for item in data.get("content", []):
        if item.get("type") == "text":
            chunks.append(str(item.get("text", "")))
    return "\n".join(chunks).strip()


def _extract_gemini_response(data: dict[str, Any]) -> str:
    chunks = []
    for candidate in data.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            if "text" in part:
                chunks.append(str(part.get("text", "")))
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def _has_api_key(spec: AgentSpec) -> bool:
    return bool(spec.env_api_key and os.environ.get(spec.env_api_key))


def _browser_command_method_label(command: list[str]) -> str:
    command_name = os.path.basename(command[0]) if command else "unknown"
    return f"cli:{command_name}"


def _call_bridge_agent(spec: AgentSpec, prompt: str, *, timeout: int) -> str:
    text, _, _ = _call_bridge_agent_runtime(spec, prompt, timeout=timeout)
    return text


def _call_bridge_agent_runtime(spec: AgentSpec, prompt: str, *, timeout: int) -> tuple[str, str, str]:
    if spec.web_fetch_url:
        return _call_web_fetch_agent(spec, prompt, timeout=timeout), "web_fetch", spec.model
    if spec.browser_command:
        return _call_browser_command_agent_runtime(spec, prompt, timeout=_browser_command_timeout(timeout))
    raise RuntimeError(f"{spec.slot} has no configured web_fetch_url or browser_command")


def _call_remote_agent(spec: AgentSpec, prompt: str, *, timeout: int) -> str:
    text, _, _ = _call_remote_agent_runtime(spec, prompt, timeout=timeout)
    return text


def _call_remote_agent_runtime(spec: AgentSpec, prompt: str, *, timeout: int) -> tuple[str, str, str]:
    if spec.prefer_bridge and (spec.web_fetch_url or spec.browser_command):
        try:
            return _call_bridge_agent_runtime(spec, prompt, timeout=timeout)
        except (RuntimeError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            if spec.provider != "google-gemini" or not _has_api_key(spec):
                raise

    if not _has_api_key(spec) and (spec.web_fetch_url or spec.browser_command):
        return _call_bridge_agent_runtime(spec, prompt, timeout=timeout)

    if not spec.env_api_key:
        raise RuntimeError(f"{spec.slot} has no configured API key environment variable")
    api_key = os.environ.get(spec.env_api_key)
    if not api_key:
        raise RuntimeError(f"{spec.slot} missing {spec.env_api_key}")

    return _call_api_agent_runtime(spec, prompt, api_key=api_key, timeout=timeout)


def _call_api_agent_runtime(
    spec: AgentSpec,
    prompt: str,
    *,
    api_key: str,
    timeout: int,
) -> tuple[str, str, str]:
    if spec.provider == "openai":
        payload = {
            "model": spec.model,
            "input": prompt,
            "max_output_tokens": spec.max_output_tokens,
            "temperature": spec.temperature,
        }
        if spec.reasoning_effort:
            payload["reasoning"] = {"effort": spec.reasoning_effort}
        data = _post_json(
            spec.endpoint,
            {"Authorization": f"Bearer {api_key}"},
            payload,
            timeout=timeout,
        )
        return _extract_openai_response(data), f"api:{spec.provider}", spec.model

    if spec.provider == "anthropic":
        payload = {
            "model": spec.model,
            "max_tokens": spec.max_output_tokens,
            "temperature": spec.temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if spec.thinking_budget_tokens is not None:
            payload["thinking"] = {"type": "enabled", "budget_tokens": spec.thinking_budget_tokens}
        data = _post_json(
            spec.endpoint,
            {"Authorization": f"Bearer {api_key}", "anthropic-version": "2023-06-01"},
            payload,
            timeout=timeout,
        )
        return _extract_anthropic_response(data), f"api:{spec.provider}", spec.model

    if spec.provider == "openai-compatible":
        data = _post_json(
            spec.endpoint,
            {"Authorization": f"Bearer {api_key}"},
            {
                "model": spec.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": spec.max_output_tokens,
                "temperature": spec.temperature,
            },
            timeout=timeout,
        )
        return _extract_chat_completion(data), f"api:{spec.provider}", spec.model

    if spec.provider == "google-gemini":
        failures: list[str] = []
        for model in [spec.model, *(spec.model_fallbacks or [])]:
            endpoint = spec.endpoint.replace("{model}", urllib.parse.quote(model, safe=""))
            generation_config: dict[str, Any] = {
                "maxOutputTokens": spec.max_output_tokens,
                "temperature": spec.temperature,
                "responseMimeType": "application/json",
            }
            if spec.thinking_budget_tokens is not None:
                generation_config["thinkingConfig"] = {"thinkingBudget": spec.thinking_budget_tokens}
            elif spec.reasoning_effort:
                generation_config["thinkingConfig"] = {"thinkingLevel": spec.reasoning_effort.upper()}
            try:
                data = _post_json(
                    endpoint,
                    {"X-goog-api-key": api_key},
                    {
                        "contents": [
                            {
                                "parts": [
                                    {
                                        "text": prompt,
                                    }
                                ]
                            }
                        ],
                        "generationConfig": generation_config,
                    },
                    timeout=timeout,
                )
                return _annotate_runtime_model_fallback(
                    _extract_gemini_response(data),
                    primary_model=spec.model,
                    used_model=model,
                ), f"api:{spec.provider}", model
            except Exception as exc:  # Gemini model fallback is intentionally broad before removing the slot.
                failures.append(f"{model}: {exc}")
        raise RuntimeError("Gemini API model fallback chain failed: " + " | ".join(failures))

    raise RuntimeError(f"unsupported provider {spec.provider}")


def _call_web_fetch_agent(spec: AgentSpec, prompt: str, *, timeout: int) -> str:
    if not spec.web_fetch_url:
        raise RuntimeError(f"{spec.slot} has no configured web_fetch_url")

    if "{prompt}" in spec.web_fetch_url:
        url = spec.web_fetch_url.replace("{prompt}", urllib.parse.quote(prompt))
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "worldcup2026-brazil-radar/0.1"},
            method="GET",
        )
    else:
        request = urllib.request.Request(
            spec.web_fetch_url,
            data=json.dumps({"slot": spec.slot, "model": spec.model, "prompt": prompt}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "worldcup2026-brazil-radar/0.1",
            },
            method="POST",
        )

    with _open_url_with_retries(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _render_command_arg(arg: str, *, prompt: str, primary_model: str, model: str) -> str:
    prompt_json = json.dumps(prompt, ensure_ascii=False)
    rendered = arg.replace("{prompt_json}", prompt_json).replace("{prompt}", prompt).replace("{model}", model)
    if rendered == primary_model:
        return model
    return rendered


def _extract_browser_command_output(command: list[str], stdout: str) -> str:
    command_name = os.path.basename(command[0]) if command else ""
    if command_name == "claude":
        return _extract_claude_stream_json_response(stdout) or stdout
    if command_name != "openai":
        return stdout
    try:
        extracted = _extract_openai_response(json.loads(stdout))
    except json.JSONDecodeError:
        return stdout
    return extracted or stdout


def _browser_command_failure_detail(slot: str, command: list[str], detail: str) -> str:
    command_name = os.path.basename(command[0]) if command else ""
    normalized = detail.lower()
    if command_name == "claude" and "not logged in" in normalized:
        return (
            f"{slot} browser_command failed: Claude CLI auth unavailable in this runner process. "
            "Evidence: `claude auth status` reports loggedIn=false here, even if another terminal session works. "
            "Remediation: run `claude auth login` or `claude setup-token` for this non-interactive runner, "
            "or export ANTHROPIC_API_KEY. Original CLI output: "
            f"{detail}"
        )
    return f"{slot} browser_command failed: {detail}"


def _extract_claude_stream_json_response(stdout: str) -> str:
    assistant_chunks: list[str] = []
    final_result = ""
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "result" and isinstance(payload.get("result"), str):
            final_result = str(payload["result"]).strip()
            continue
        if payload.get("type") != "assistant":
            continue
        message = payload.get("message")
        if not isinstance(message, dict):
            continue
        for item in message.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    assistant_chunks.append(text)
    return final_result or "\n".join(assistant_chunks).strip()


def _run_browser_command(
    slot: str,
    raw_command: list[str],
    prompt: str,
    *,
    timeout: int,
    primary_model: str,
    model: str,
) -> str:
    uses_placeholder = any("{prompt" in arg for arg in raw_command)
    command = [
        _render_command_arg(arg, prompt=prompt, primary_model=primary_model, model=model)
        for arg in raw_command
    ]
    env = _browser_command_env(command)
    result = _run_browser_subprocess(
        command,
        input_text=None if uses_placeholder else prompt,
        env=env,
        timeout=timeout,
        slot=slot,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(_browser_command_failure_detail(slot, command, detail))
    return _extract_browser_command_output(command, result.stdout.strip())


def _run_browser_subprocess(
    command: list[str],
    *,
    input_text: str | None,
    env: dict[str, str],
    timeout: int,
    slot: str,
) -> subprocess.CompletedProcess[str]:
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                process.kill()
            process.communicate()
        raise RuntimeError(f"{slot} browser_command timed out after {timeout}s") from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _call_browser_command_agent(spec: AgentSpec, prompt: str, *, timeout: int) -> str:
    text, _, _ = _call_browser_command_agent_runtime(spec, prompt, timeout=timeout)
    return text


def _call_browser_command_agent_runtime(spec: AgentSpec, prompt: str, *, timeout: int) -> tuple[str, str, str]:
    if not spec.browser_command:
        raise RuntimeError(f"{spec.slot} has no configured browser_command")

    commands = [spec.browser_command, *(spec.browser_fallback_commands or [])]
    failures: list[str] = []
    for raw_command in commands:
        models = [spec.model]
        if spec.provider == "google-gemini":
            models.extend(spec.model_fallbacks or [])
        for model in models:
            try:
                text = _run_browser_command(
                    spec.slot,
                    raw_command,
                    prompt,
                    timeout=timeout,
                    primary_model=spec.model,
                    model=model,
                )
                return (
                    _annotate_runtime_model_fallback(text, primary_model=spec.model, used_model=model),
                    _browser_command_method_label(raw_command),
                    model,
                )
            except RuntimeError as exc:
                failures.append(str(exc))
    if not failures:
        raise RuntimeError(f"{spec.slot} has no configured browser_command")
    if len(failures) == 1:
        raise RuntimeError(failures[0])
    raise RuntimeError(f"{spec.slot} browser_command chain failed: " + " | ".join(failures))


def _browser_command_env(command: list[str]) -> dict[str, str]:
    env = os.environ.copy()
    command_name = os.path.basename(command[0]) if command else ""
    if command_name in {"codex", "gemini"}:
        for key in list(env):
            if key.startswith("CODEX_") and key != "CODEX_HOME":
                env.pop(key, None)
    return env


def _extract_json_object(text: str) -> dict[str, Any] | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None


def _annotate_runtime_model_fallback(text: str, *, primary_model: str, used_model: str) -> str:
    if used_model == primary_model:
        return text
    payload = _extract_json_object(text)
    if not payload:
        return text
    note = f"Modelo runtime mudou de {primary_model} para {used_model} por fallback."
    payload["runtime_model_fallback"] = {
        "from": primary_model,
        "to": used_model,
    }
    summary = str(payload.get("summary") or "").strip()
    payload["summary"] = f"{summary} {note}".strip()
    return json.dumps(payload, ensure_ascii=False)


def _coerce_pct(value: Any, fallback: float) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().rstrip("%"))
        except ValueError:
            return fallback
    return fallback


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = value.strip().strip("\"'").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _extract_loose_source_urls(text: str) -> list[str]:
    urls = []
    for raw_url in re.findall(r"https?://[^\s\]\)\}\"']+", text):
        urls.append(raw_url.rstrip(".,;:"))
    return _unique_strings(urls)


def _is_source_query_header(line: str) -> bool:
    normalized = line.strip().strip("\"'").lower()
    return bool(
        re.match(
            r"^(?:source_queries|source queries|queries|buscas|consultas)(?:\s+audit[aá]veis)?\s*[:=\[]",
            normalized,
        )
    )


def _is_non_query_source_header(line: str) -> bool:
    normalized = line.strip().strip("\"'").lower()
    return bool(
        re.match(
            r"^(?:source_urls|source urls|urls|fontes|summary|title_pct|opening_argument|critique|adjustment)\s*[:=]",
            normalized,
        )
    )


def _extract_inline_query_items(value: str) -> list[str]:
    quoted = re.findall(r"[\"']([^\"']{12,180})[\"']", value)
    if quoted:
        return [item for item in quoted if not item.startswith("http")]
    bracket_items = []
    for item in value.strip().strip("[]").split(","):
        clean = item.strip().strip("\"'")
        if len(clean) >= 12 and not clean.startswith("http"):
            bracket_items.append(clean)
    return bracket_items


def _extract_loose_source_queries(text: str) -> list[str]:
    queries: list[str] = []
    collecting = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if collecting and queries:
                collecting = False
            continue
        if _is_source_query_header(stripped):
            collecting = True
            _, _, rest = stripped.partition(":")
            if not rest and "=" in stripped:
                _, _, rest = stripped.partition("=")
            queries.extend(_extract_inline_query_items(rest))
            continue
        if not collecting:
            continue
        if _is_non_query_source_header(stripped):
            collecting = False
            continue
        item = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", stripped).strip().strip("\"',")
        if item.startswith("http") or len(item) < 12:
            continue
        queries.append(item)
    return _unique_strings(queries)


def _coerce_probability_map(value: Any) -> dict[str, float]:
    probabilities: dict[str, float] = {}
    if not isinstance(value, dict):
        return probabilities
    for key, raw_pct in value.items():
        pct = _coerce_pct(raw_pct, -1)
        if pct >= 0:
            probabilities[str(key)] = round(pct, 1)
    return probabilities


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "sim", "yes", "y", "1", "concordo", "aceito"}:
            return True
        if normalized in {"false", "nao", "não", "no", "n", "0", "discordo", "contesto"}:
            return False
    return None


def _extract_loose_match_probabilities(text: str) -> dict[str, float]:
    probabilities: dict[str, float] = {}
    pattern = re.compile(
        r"[\"']([^\"']*(?:grupo|16\s*avos|oitavas|quartas|semi(?:final)?|final)[^\"']*)[\"']\s*:\s*"
        r"[\"']?(\d+(?:\.\d+)?)\s*%?[\"']?",
        flags=re.I,
    )
    for key, raw_pct in pattern.findall(text):
        probabilities[key] = round(float(raw_pct), 1)
    return probabilities


def _looks_like_json_fragment(text: str) -> bool:
    stripped = text.lstrip()
    lowered = stripped.lower()
    if stripped.startswith("{") or stripped.startswith("```") or lowered.startswith("```json"):
        return True
    if stripped.startswith('" }') or stripped.startswith('"}') or stripped.startswith("'}"):
        return True
    return "```json" in lowered[:160]


def _self_declared_identity(payload: dict[str, Any]) -> tuple[str, str]:
    raw_identity = payload.get("self_identification") or payload.get("identity") or payload.get("model_identity")
    if isinstance(raw_identity, dict):
        name = (
            raw_identity.get("name")
            or raw_identity.get("model_name")
            or raw_identity.get("declared_name")
            or raw_identity.get("system_name")
            or ""
        )
        version = (
            raw_identity.get("version")
            or raw_identity.get("model_version")
            or raw_identity.get("declared_version")
            or raw_identity.get("release")
            or ""
        )
        return str(name).strip(), str(version).strip()
    return (
        str(payload.get("self_declared_name") or payload.get("model_name") or payload.get("name") or "").strip(),
        str(payload.get("self_declared_version") or payload.get("model_version") or payload.get("version") or "").strip(),
    )


def _agent_preflight_prompt(slot: str, *, contract: bool = False) -> str:
    if not contract:
        return (
            "Teste rápido de conectividade antes do run diário da Copa 2026. "
            "Não pesquise na web, não debata e não calcule probabilidades. "
            "Responda apenas JSON estrito com: "
            '{"ok":true,"message":"funcionando","self_identification":{"name":"<seu nome declarado>","version":"<sua versão declarada ou não declarado>"}}. '
            f"Slot configurado: {slot}."
        )
    return (
        "Teste de contrato mínimo antes do run diário da Copa 2026. "
        "Não faça análise longa, mas responda no mesmo formato estrutural esperado do planejamento de fontes. "
        "Use JSON estrito com: ok, message, self_identification{name,version}, title_pct, summary, "
        "source_urls, source_queries. Inclua ao menos uma source_url ou source_query não-Opta que você usaria "
        "para checar Brasil, Marrocos, odds/ratings/Sofascore/lesões e chaveamento 16 avos. "
        "Dados da Opta não contam e não devem aparecer em source_urls/source_queries. "
        "title_pct deve ser número simples. "
        f"Slot configurado: {slot}."
    )


def _preflight_message(payload: dict[str, Any], raw_text: str) -> str:
    message = str(payload.get("message") or payload.get("summary") or payload.get("answer") or "").strip()
    if message:
        return re.sub(r"\s+", " ", message)[:180]
    compact = re.sub(r"\s+", " ", raw_text).strip()
    return compact[:180] if compact else "sem mensagem"


def _preflight_contract_error(payload: dict[str, Any]) -> str:
    source_urls = [
        str(url).strip()
        for url in (payload.get("source_urls") or [])
        if str(url).strip() and "opta" not in str(url).lower()
    ]
    source_queries = [
        str(query).strip()
        for query in (payload.get("source_queries") or [])
        if str(query).strip() and "opta" not in str(query).lower()
    ]
    if not source_urls and not source_queries:
        return "contrato mínimo incompleto: sem source_urls/source_queries não-Opta"
    try:
        float(payload.get("title_pct"))
    except (TypeError, ValueError):
        return "contrato mínimo incompleto: title_pct ausente ou não numérico"
    summary = str(payload.get("summary") or payload.get("answer") or payload.get("message") or "").strip()
    if not summary:
        return "contrato mínimo incompleto: summary/message ausente"
    return ""


def run_agent_preflight(
    spec: AgentSpec,
    *,
    timeout: int = DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
    contract: bool = False,
) -> AgentPreflightResult:
    started = time.monotonic()
    method = "api" if _has_api_key(spec) else "unavailable"
    runtime_model = spec.model
    try:
        text, method, runtime_model = _call_remote_agent_runtime(
            spec,
            _agent_preflight_prompt(spec.slot, contract=contract),
            timeout=timeout,
        )
        payload = _extract_json_object(text) or {}
        declared_name, declared_version = _self_declared_identity(payload)
        ok_value = _coerce_optional_bool(payload.get("ok"))
        contract_error = _preflight_contract_error(payload) if contract else ""
        return AgentPreflightResult(
            slot=spec.slot,
            provider=spec.provider,
            configured_model=spec.model,
            runtime_model=runtime_model,
            method=method,
            ok=(True if ok_value is None else bool(ok_value)) and not contract_error,
            declared_name=declared_name,
            declared_version=declared_version,
            message=_preflight_message(payload, text),
            error=contract_error,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:
        return AgentPreflightResult(
            slot=spec.slot,
            provider=spec.provider,
            configured_model=spec.model,
            runtime_model=runtime_model,
            method=method,
            ok=False,
            message="falhou",
            error=str(exc),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


async def run_agent_preflights(
    specs: list[AgentSpec],
    *,
    timeout: int = DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
    contract: bool = False,
) -> list[AgentPreflightResult]:
    tasks = [asyncio.to_thread(run_agent_preflight, spec, timeout=timeout, contract=contract) for spec in specs]
    return list(await asyncio.gather(*tasks))


def run_agent_preflights_sync(
    specs: list[AgentSpec],
    *,
    timeout: int = DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
    contract: bool = False,
) -> list[AgentPreflightResult]:
    return asyncio.run(run_agent_preflights(specs, timeout=timeout, contract=contract))


def render_agent_preflight_stdout(results: list[AgentPreflightResult]) -> str:
    width = 92
    border = "=" * width
    lines = [
        "",
        border,
        "MODEL PREFLIGHT | teste rápido dos modelos antes do run".center(width),
        border,
    ]
    if not results:
        lines.append("[WARN] nenhum agente configurado para testar")
    for result in results:
        status = "OK" if result.ok else "FAIL"
        identity = " / ".join(
            item
            for item in [
                result.declared_name or "nome não declarado",
                result.declared_version or "versão não declarada",
            ]
            if item
        )
        message = result.message or result.error or "sem mensagem"
        line = (
            f"[{status}] {result.slot} | método={result.method} | "
            f"modelo_config={result.configured_model} | runtime={result.runtime_model} | "
            f"declara={identity} | {result.elapsed_ms}ms | msg={message}"
        )
        lines.append(line)
        if result.error:
            lines.append(f"      erro={result.error[:240]}")
    ok_count = sum(1 for result in results if result.ok)
    lines.extend(
        [
            border,
            f"Resumo: {ok_count}/{len(results)} modelo(s) responderam ao smoke test.",
            border,
            "",
        ]
    )
    return "\n".join(lines)


def _coerce_team_context_signals(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (
        payload.get("team_context_signals")
        or payload.get("team_signal_adjustments")
        or payload.get("team_context")
        or []
    )
    if isinstance(raw, dict):
        expanded = []
        for team, signals in raw.items():
            if isinstance(signals, dict):
                signals = signals.get("signals", [signals])
            if not isinstance(signals, list):
                continue
            for signal in signals:
                if isinstance(signal, dict):
                    expanded.append({"team": team, **signal})
        raw = expanded
    if not isinstance(raw, list):
        return []
    signals: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        signal = dict(item)
        team = str(signal.get("team") or signal.get("selection") or signal.get("country") or "").strip()
        category = str(signal.get("category") or signal.get("family") or signal.get("source_family") or "").strip()
        if not team or not category:
            continue
        signal["team"] = team
        signal["category"] = category
        signals.append(signal)
    return signals


def parse_agent_opinion(slot: str, text: str, *, fallback_title_pct: float) -> AgentOpinion:
    payload = _extract_json_object(text) or {}
    json_like_partial = not payload and _looks_like_json_fragment(text)
    match_probabilities = {}

    title_pct = payload.get("title_pct")
    if isinstance(title_pct, dict):
        match_probabilities.update(_coerce_probability_map(title_pct))
        title_pct = fallback_title_pct
    if title_pct is None:
        percent = re.search(
            r"(?:t[ií]tulo|campe[aã]o|champion|title)[^0-9]{0,40}(\d+(?:\.\d+)?)\s*%",
            text,
            flags=re.I,
        )
        title_pct = float(percent.group(1)) if percent else fallback_title_pct
    title_pct = _coerce_pct(title_pct, fallback_title_pct)

    summary = payload.get("summary")
    if not summary:
        loose_probabilities = _extract_loose_match_probabilities(text)
        if json_like_partial and loose_probabilities:
            summary = (
                "Resposta em JSON parcial; aproveitei as probabilidades jogo a jogo "
                "e mantive a chance de título no prior até o modelo declarar title_pct numérico."
            )
        elif json_like_partial:
            summary = "Resposta em JSON parcial; mantive leitura conservadora até o modelo devolver campos auditáveis."
        else:
            compact = re.sub(r"\s+", " ", text).strip()
            summary = compact[:260] if compact else "Sem resposta parseável; fallback conservador aplicado."

    source_urls = _unique_strings([
        *_string_list(payload.get("source_urls", [])),
        *_extract_loose_source_urls(text),
    ])
    source_queries = _unique_strings([
        *_string_list(payload.get("source_queries", [])),
        *_extract_loose_source_queries(text),
    ])
    match_probabilities.update(_coerce_probability_map(payload.get("match_probabilities")))
    match_probabilities.update(_extract_loose_match_probabilities(text))
    scenario_probabilities = _coerce_probability_map(payload.get("scenario_probabilities"))
    team_context_signals = _coerce_team_context_signals(payload)
    self_declared_name, self_declared_version = _self_declared_identity(payload)

    raw_answer = payload.get("answer") or payload.get("response")
    if raw_answer is None or (json_like_partial and str(raw_answer).lstrip().startswith("{")):
        answer = str(summary)
    elif isinstance(raw_answer, dict):
        answer = str(payload.get("summary") or summary)
    else:
        answer = str(raw_answer)

    return AgentOpinion(
        agent=slot,
        title_pct=round(title_pct, 1),
        summary=str(summary),
        opening_argument=str(payload.get("opening_argument") or payload.get("argument") or summary),
        question=str(payload.get("question") or ""),
        answer=answer,
        critique=str(payload.get("critique") or payload.get("counterargument") or ""),
        adjustment=str(payload.get("adjustment") or payload.get("consensus_adjustment") or ""),
        source_urls=source_urls,
        source_queries=source_queries,
        match_probabilities=match_probabilities,
        scenario_probabilities=scenario_probabilities,
        team_context_signals=team_context_signals,
        agrees_with_protagonist=_coerce_optional_bool(
            payload.get("agrees_with_protagonist")
            if "agrees_with_protagonist" in payload
            else payload.get("accepts_protagonist_rationale")
        ),
        leadership_bid=bool(
            _coerce_optional_bool(
                payload.get("leadership_bid")
                if "leadership_bid" in payload
                else payload.get("wants_protagonism")
            )
        ),
        proposed_next_question=str(payload.get("proposed_next_question") or payload.get("next_question") or ""),
        leadership_rationale=str(payload.get("leadership_rationale") or payload.get("leadership_reason") or ""),
        consensus_check_question=str(
            payload.get("consensus_check_question")
            or payload.get("consensus_question")
            or payload.get("consensus_confirmation_question")
            or ""
        ),
        self_declared_name=self_declared_name,
        self_declared_version=self_declared_version,
        raw_text=text,
        used_fallback=False,
        removed_from_main=bool(_coerce_optional_bool(payload.get("removed_from_main")) or False),
        removal_reason=str(payload.get("removal_reason") or ""),
    )


def _local_fallback_opinion(spec: AgentSpec, reason: str, *, baseline_title_pct: float) -> AgentOpinion:
    return AgentOpinion(
        agent=spec.slot,
        title_pct=round(baseline_title_pct, 1),
        summary=(
            f"Modelo sem resposta externa verificável porque {reason}. "
            "O slot não participa do consenso até trazer plano de fontes próprio."
        ),
        opening_argument=(
            f"{spec.slot} não trouxe dados próprios neste run; a resposta deve ser tratada como falha operacional, "
            "não como voto conservador."
        ),
        question="Qual premissa ainda precisa de evidência externa antes de mover o consenso?",
        answer="Modelo não participa do consenso enquanto não trouxer fontes escolhidas e verificáveis para este run.",
        critique="A ausência de resposta real deste slot reduz o quórum da sala e deve aparecer como exclusão operacional.",
        adjustment="Excluir o slot da reunião principal até haver resposta externa auditável.",
        source_urls=[],
        source_queries=[],
        match_probabilities={},
        scenario_probabilities={},
        agrees_with_protagonist=False,
        raw_text="",
        used_fallback=True,
        removed_from_main=True,
        removal_reason=f"fallback operacional: {reason}",
    )


async def call_agent(
    spec: AgentSpec,
    prompt: str,
    *,
    baseline_title_pct: float,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    allow_local_fallback: bool = True,
) -> AgentOpinion:
    try:
        text = await asyncio.to_thread(_call_remote_agent, spec, prompt, timeout=timeout)
        return parse_agent_opinion(spec.slot, text, fallback_title_pct=baseline_title_pct)
    except (RuntimeError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        if not spec.prefer_bridge and _has_api_key(spec) and (spec.web_fetch_url or spec.browser_command):
            try:
                text = await asyncio.to_thread(_call_bridge_agent, spec, prompt, timeout=timeout)
                return parse_agent_opinion(spec.slot, text, fallback_title_pct=baseline_title_pct)
            except (RuntimeError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
                pass
        if not allow_local_fallback:
            raise
        return _local_fallback_opinion(spec, str(exc), baseline_title_pct=baseline_title_pct)


def _bulkhead_env_suffix(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")


def _agent_bulkhead_key(spec: AgentSpec) -> str:
    if spec.env_api_key:
        return f"{spec.provider}:{spec.env_api_key}"
    host = urllib.parse.urlparse(spec.endpoint).netloc
    return f"{spec.provider}:{host or spec.endpoint}"


def _agent_bulkhead_limit(spec: AgentSpec) -> int:
    provider_suffix = _bulkhead_env_suffix(spec.provider)
    configured = _env_int(f"AGENT_BULKHEAD_{provider_suffix}") or _env_int("AGENT_BULKHEAD_DEFAULT")
    if configured is None:
        configured = 3
    return max(1, int(configured))


async def call_all_agents(
    prompt: str,
    *,
    specs: list[AgentSpec] | None = None,
    baseline_title_pct: float,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    allow_local_fallback: bool = True,
) -> list[AgentOpinion]:
    specs = specs or default_agent_specs()
    by_slot = {spec.slot: spec for spec in specs}
    slots = [spec.slot for spec in specs]
    if len(slots) != len(set(slots)):
        raise ValueError("duplicate agent slots configured")

    semaphores: dict[str, asyncio.Semaphore] = {}

    async def run_with_bulkhead(spec: AgentSpec) -> AgentOpinion:
        key = _agent_bulkhead_key(spec)
        semaphore = semaphores.setdefault(key, asyncio.Semaphore(_agent_bulkhead_limit(spec)))
        async with semaphore:
            return await call_agent(
                spec,
                prompt,
                baseline_title_pct=baseline_title_pct,
                timeout=timeout,
                allow_local_fallback=allow_local_fallback,
            )

    tasks = [
        run_with_bulkhead(by_slot[slot])
        for slot in slots
    ]
    return list(await asyncio.gather(*tasks))


def load_agent_specs_from_config(config: dict[str, Any]) -> list[AgentSpec]:
    configured = config.get("agents")
    if not configured:
        return default_agent_specs()

    defaults_by_slot = {spec.slot: spec for spec in default_agent_specs()}
    specs = []
    for item in configured:
        default = defaults_by_slot.get(item["slot"])
        browser_command = (
            _coerce_browser_command(item["browser_command"])
            if item.get("browser_command") is not None
            else (default.browser_command if default else None)
        )
        browser_fallback_commands = (
            _coerce_browser_fallback_commands(item["browser_fallback_commands"])
            if item.get("browser_fallback_commands") is not None
            else (default.browser_fallback_commands if default else None)
        )
        thinking_budget_tokens = (
            int(item["thinking_budget_tokens"])
            if item.get("thinking_budget_tokens") is not None
            else (default.thinking_budget_tokens if default else None)
        )
        specs.append(
            AgentSpec(
                slot=item["slot"],
                provider=_configured_value(item, "provider", default.provider if default else "openai-compatible"),
                model=_configured_value(item, "model", default.model if default else ""),
                env_api_key=_configured_value(item, "env_api_key", default.env_api_key if default else None),
                endpoint=_configured_value(item, "endpoint", default.endpoint if default else ""),
                max_output_tokens=int(
                    _configured_value(
                        item,
                        "max_output_tokens",
                        default.max_output_tokens if default else DEFAULT_MAX_OUTPUT_TOKENS,
                    )
                ),
                temperature=float(_configured_value(item, "temperature", default.temperature if default else 0.15)),
                reasoning_effort=_configured_value(
                    item, "reasoning_effort", default.reasoning_effort if default else None
                ),
                thinking_budget_tokens=thinking_budget_tokens,
                web_fetch_url=_configured_value(item, "web_fetch_url", default.web_fetch_url if default else None),
                browser_command=browser_command,
                browser_fallback_commands=browser_fallback_commands,
                model_fallbacks=_configured_value(
                    item, "model_fallbacks", default.model_fallbacks if default else None
                ),
                prefer_bridge=bool(_configured_value(item, "prefer_bridge", default.prefer_bridge if default else False)),
            )
        )
    return specs


def _configured_value(item: dict[str, Any], key: str, default: Any) -> Any:
    value = item.get(key)
    return default if value is None else value


def _coerce_browser_command(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, list):
        return [str(item) for item in value]
    raise TypeError("browser_command must be a string, list, or null")


def _coerce_browser_fallback_commands(value: Any) -> list[list[str]] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [shlex.split(value)]
    if isinstance(value, list):
        if not value:
            return []
        if all(isinstance(item, str) for item in value):
            return [[str(item) for item in value]]
        commands = []
        for command in value:
            if isinstance(command, str):
                commands.append(shlex.split(command))
            elif isinstance(command, list):
                commands.append([str(item) for item in command])
            else:
                raise TypeError("browser_fallback_commands entries must be strings or lists")
        return commands
    raise TypeError("browser_fallback_commands must be a string, list, or null")

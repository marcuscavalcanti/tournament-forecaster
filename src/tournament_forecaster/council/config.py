"""Validated, credential-free configuration for the optional LLM council."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ..errors import TournamentValidationError


_STABLE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_ENVIRONMENT_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_PROVIDERS = frozenset(
    {"openai", "anthropic", "openai-compatible", "google-gemini"}
)
_REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh"}
)
_DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/responses",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "google-gemini": (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "{model}:generateContent"
    ),
}
ENGINE_WEIGHT = 0.55
COUNCIL_WEIGHT = 0.45
_ROOT_PROPERTIES = frozenset(
    {
        "schema_version",
        "enabled",
        "engine_weight",
        "council_weight",
        "rounds",
        "minimum_valid_agents",
        "timeout_seconds",
        "max_attempts",
        "agents",
    }
)
_AGENT_PROPERTIES = frozenset(
    {
        "id",
        "display_name",
        "provider",
        "model",
        "api_key_env",
        "endpoint",
        "enabled",
        "reasoning_effort",
        "thinking_budget_tokens",
        "max_output_tokens",
        "temperature",
    }
)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise TournamentValidationError(f"{label} must be an object with string keys")
    return value


def _reject_unknown(
    value: Mapping[str, object],
    allowed: frozenset[str],
    label: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise TournamentValidationError(
            f"{label} contains unknown properties: {', '.join(unknown)}"
        )


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TournamentValidationError(f"{label} must be non-empty text")
    return value.strip()


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise TournamentValidationError(f"{label} must be a boolean")
    return value


def _integer(value: object, label: str, *, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise TournamentValidationError(
            f"{label} must be an integer between {minimum} and {maximum}"
        )
    return value


def _finite_number(
    value: object,
    label: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TournamentValidationError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise TournamentValidationError(f"{label} must be finite")
    if not minimum <= result <= maximum:
        raise TournamentValidationError(
            f"{label} must be between {minimum} and {maximum}"
        )
    return result


def _endpoint(value: object, provider: str) -> str:
    if value is None:
        default = _DEFAULT_ENDPOINTS.get(provider)
        if default is None:
            raise TournamentValidationError(
                "openai-compatible agents must define endpoint"
            )
        return default
    endpoint = _text(value, "council agent endpoint")
    parsed = urlsplit(endpoint.replace("{model}", "model"))
    if parsed.scheme != "https" or not parsed.hostname:
        raise TournamentValidationError("council agent endpoint must use https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TournamentValidationError(
            "council agent endpoint must not contain credentials, query, or fragment"
        )
    return endpoint


@dataclass(frozen=True, slots=True)
class CouncilAgentConfig:
    """One provider/model slot in the debriefing council."""

    id: str
    display_name: str
    provider: str
    model: str
    api_key_env: str
    endpoint: str
    enabled: bool = True
    reasoning_effort: str | None = None
    thinking_budget_tokens: int | None = None
    max_output_tokens: int = 2_400
    temperature: float = 0.2


@dataclass(frozen=True, slots=True)
class CouncilConfig:
    """Runtime policy for an optional multi-model debriefing."""

    enabled: bool
    engine_weight: float
    council_weight: float
    rounds: int
    minimum_valid_agents: int
    timeout_seconds: int
    max_attempts: int
    agents: tuple[CouncilAgentConfig, ...]
    schema_version: int = 1

    @property
    def enabled_agents(self) -> tuple[CouncilAgentConfig, ...]:
        return tuple(agent for agent in self.agents if agent.enabled)


def _agent(value: object, index: int) -> CouncilAgentConfig:
    label = f"council agents[{index}]"
    document = _mapping(value, label)
    _reject_unknown(document, _AGENT_PROPERTIES, label)
    agent_id = _text(document.get("id"), f"{label}.id")
    if not _STABLE_ID.fullmatch(agent_id):
        raise TournamentValidationError(f"{label}.id must be a stable ASCII identifier")
    provider = _text(document.get("provider"), f"{label}.provider")
    if provider not in _PROVIDERS:
        raise TournamentValidationError(f"{label}.provider is unsupported")
    api_key_env = _text(document.get("api_key_env"), f"{label}.api_key_env")
    if not _ENVIRONMENT_NAME.fullmatch(api_key_env):
        raise TournamentValidationError(
            f"{label}.api_key_env must be an environment variable name"
        )
    effort_value = document.get("reasoning_effort")
    reasoning_effort = None
    if effort_value is not None:
        reasoning_effort = _text(effort_value, f"{label}.reasoning_effort")
        if reasoning_effort not in _REASONING_EFFORTS:
            raise TournamentValidationError(f"{label}.reasoning_effort is unsupported")
    thinking_value = document.get("thinking_budget_tokens")
    thinking_budget_tokens = None
    if thinking_value is not None:
        thinking_budget_tokens = _integer(
            thinking_value,
            f"{label}.thinking_budget_tokens",
            minimum=1,
            maximum=1_000_000,
        )
    return CouncilAgentConfig(
        id=agent_id,
        display_name=_text(document.get("display_name"), f"{label}.display_name"),
        provider=provider,
        model=_text(document.get("model"), f"{label}.model"),
        api_key_env=api_key_env,
        endpoint=_endpoint(document.get("endpoint"), provider),
        enabled=_boolean(document.get("enabled", True), f"{label}.enabled"),
        reasoning_effort=reasoning_effort,
        thinking_budget_tokens=thinking_budget_tokens,
        max_output_tokens=_integer(
            document.get("max_output_tokens", 2_400),
            f"{label}.max_output_tokens",
            minimum=128,
            maximum=1_000_000,
        ),
        temperature=_finite_number(
            document.get("temperature", 0.2),
            f"{label}.temperature",
            minimum=0.0,
            maximum=2.0,
        ),
    )


def load_council_document(value: object) -> CouncilConfig:
    """Validate one parsed council document."""

    document = _mapping(value, "council document")
    _reject_unknown(document, _ROOT_PROPERTIES, "council document")
    schema_version = _integer(
        document.get("schema_version"),
        "council schema_version",
        minimum=1,
        maximum=1,
    )
    engine_weight = _finite_number(
        document.get("engine_weight", ENGINE_WEIGHT),
        "council engine_weight",
        minimum=0.0,
        maximum=1.0,
    )
    council_weight = _finite_number(
        document.get("council_weight", COUNCIL_WEIGHT),
        "council council_weight",
        minimum=0.0,
        maximum=1.0,
    )
    if not math.isclose(engine_weight + council_weight, 1.0, abs_tol=1e-9):
        raise TournamentValidationError(
            "council engine_weight and council_weight must sum to 1"
        )
    if not math.isclose(engine_weight, ENGINE_WEIGHT, abs_tol=1e-9) or not math.isclose(
        council_weight,
        COUNCIL_WEIGHT,
        abs_tol=1e-9,
    ):
        raise TournamentValidationError(
            "council blend policy must be exactly 0.55 engine and 0.45 council"
        )
    raw_agents = document.get("agents")
    if not isinstance(raw_agents, list):
        raise TournamentValidationError("council agents must be an array")
    agents = tuple(_agent(value, index) for index, value in enumerate(raw_agents))
    ids = tuple(agent.id for agent in agents)
    if len(ids) != len(set(ids)):
        raise TournamentValidationError("council agent ids must be unique")
    minimum_valid_agents = _integer(
        document.get("minimum_valid_agents", 2),
        "council minimum_valid_agents",
        minimum=1,
        maximum=100,
    )
    if minimum_valid_agents > sum(agent.enabled for agent in agents):
        raise TournamentValidationError(
            "council minimum_valid_agents cannot exceed enabled agents"
        )
    return CouncilConfig(
        schema_version=schema_version,
        enabled=_boolean(document.get("enabled", False), "council enabled"),
        engine_weight=engine_weight,
        council_weight=council_weight,
        rounds=_integer(
            document.get("rounds", 2),
            "council rounds",
            minimum=1,
            maximum=3,
        ),
        minimum_valid_agents=minimum_valid_agents,
        timeout_seconds=_integer(
            document.get("timeout_seconds", 120),
            "council timeout_seconds",
            minimum=1,
            maximum=3_600,
        ),
        max_attempts=_integer(
            document.get("max_attempts", 2),
            "council max_attempts",
            minimum=1,
            maximum=5,
        ),
        agents=agents,
    )


def _reject_json_constant(value: str) -> object:
    raise TournamentValidationError(f"council JSON number {value} must be finite")


def load_council_config(path: Path) -> CouncilConfig:
    """Load and validate a UTF-8 council JSON document."""

    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as error:
        raise TournamentValidationError(f"invalid council JSON: {error.msg}") from error
    return load_council_document(document)

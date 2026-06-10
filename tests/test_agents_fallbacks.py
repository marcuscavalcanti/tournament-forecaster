import asyncio
import io
import json
import subprocess
import sys
import urllib.error

from worldcup_brazil.agents import (
    AgentSpec,
    _browser_command_env,
    _browser_command_timeout,
    _call_browser_command_agent,
    _call_remote_agent,
    _post_json,
    agent_effort_profile,
    call_agent,
    call_all_agents,
    load_agent_specs_from_config,
    render_agent_preflight_stdout,
    run_agent_preflights_sync,
)


def test_load_agent_specs_accepts_single_deepseek_api_slot_and_gemini() -> None:
    specs = load_agent_specs_from_config(
        {
            "agents": [
                {
                    "slot": "Opus 4.8",
                    "provider": "anthropic",
                    "model": "claude-opus-4-8",
                    "env_api_key": "ANTHROPIC_API_KEY",
                    "endpoint": "https://api.anthropic.com/v1/messages",
                },
                {
                    "slot": "GPT 5.5",
                    "provider": "openai",
                    "model": "gpt-5.5",
                    "env_api_key": "OPENAI_API_KEY",
                    "endpoint": "https://api.openai.com/v1/responses",
                },
                {
                    "slot": "Perplexity Pro",
                    "provider": "openai-compatible",
                    "model": "sonar-pro",
                    "env_api_key": "PERPLEXITY_API_KEY",
                    "endpoint": "https://api.perplexity.ai/chat/completions",
                },
                {
                    "slot": "DeepSeek V4 Pro",
                    "provider": "openai-compatible",
                    "model": "deepseek-v4-pro",
                    "env_api_key": "DEEPSEEK_API_KEY",
                    "endpoint": "https://api.deepseek.com/chat/completions",
                    "max_output_tokens": 7000,
                },
                {
                    "slot": "Gemini Pro",
                    "provider": "google-gemini",
                    "model": "gemini-flash-latest",
                    "env_api_key": "GEMINI_API_KEY",
                    "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    "reasoning_effort": "high",
                    "max_output_tokens": 7000,
                },
            ]
        }
    )

    by_slot = {spec.slot: spec for spec in specs}
    assert "DeepSeek Latest Free" not in by_slot
    assert by_slot["DeepSeek V4 Pro"].model == "deepseek-v4-pro"
    assert by_slot["DeepSeek V4 Pro"].endpoint == "https://api.deepseek.com/chat/completions"
    assert by_slot["DeepSeek V4 Pro"].max_output_tokens == 7000
    assert by_slot["Gemini Pro"].provider == "google-gemini"
    assert by_slot["Gemini Pro"].reasoning_effort == "high"


def test_load_agent_specs_merges_operational_defaults_for_effort_and_local_cli(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    monkeypatch.delenv("OPENAI_BROWSER_COMMAND", raising=False)
    monkeypatch.delenv("OPENAI_CLI_COMMAND", raising=False)
    monkeypatch.delenv("CHATGPT_CLI_COMMAND", raising=False)
    monkeypatch.delenv("GPT_CLI_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_BROWSER_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_COMMAND", raising=False)
    monkeypatch.setattr(
        "worldcup_brazil.agents._local_openai_cli_command",
        lambda model: [
            "openai",
            "responses",
            "create",
            "--model",
            model,
            "--input",
            "{prompt_json}",
            "--reasoning",
            '{"effort":"high"}',
        ],
        raising=False,
    )
    monkeypatch.setattr(
        "worldcup_brazil.agents._local_codex_cli_command",
        lambda: ["codex", "--search", "exec", "--ignore-user-config", "{prompt}"],
    )
    monkeypatch.setattr(
        "worldcup_brazil.agents._local_claude_cli_command",
        lambda: [
            "claude",
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--model",
            "claude-opus-4-8",
            "--effort",
            "high",
            "{prompt}",
        ],
    )

    specs = load_agent_specs_from_config(
        {
            "agents": [
                {
                    "slot": "Opus 4.8",
                    "provider": "anthropic",
                    "model": "claude-opus-4-8",
                    "env_api_key": "ANTHROPIC_API_KEY",
                    "endpoint": "https://api.anthropic.com/v1/messages",
                    "browser_command": None,
                },
                {
                    "slot": "GPT 5.5",
                    "provider": "openai",
                    "model": "gpt-5.5",
                    "env_api_key": "OPENAI_API_KEY",
                    "endpoint": "https://api.openai.com/v1/responses",
                },
            ]
        }
    )

    by_slot = {spec.slot: spec for spec in specs}
    assert by_slot["Opus 4.8"].browser_command == [
        "claude",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--model",
        "claude-opus-4-8",
        "--effort",
        "high",
        "{prompt}",
    ]
    assert by_slot["GPT 5.5"].reasoning_effort == "high"
    assert by_slot["GPT 5.5"].browser_command == [
        "openai",
        "responses",
        "create",
        "--model",
        "gpt-5.5",
        "--input",
        "{prompt_json}",
        "--reasoning",
        '{"effort":"high"}',
    ]
    assert by_slot["GPT 5.5"].browser_fallback_commands == [
        ["codex", "--search", "exec", "--ignore-user-config", "{prompt}"]
    ]


def test_call_remote_agent_uses_browser_command_when_openai_compatible_key_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    payload = {
        "title_pct": 7.5,
        "summary": "browser fallback respondeu via sessão web",
        "answer": "Bridge externo trouxe dados próprios sem chave de API.",
    }
    command = [
        sys.executable,
        "-c",
        "import json; print(json.dumps(%r))" % payload,
    ]
    spec = AgentSpec(
        slot="Perplexity Pro",
        provider="openai-compatible",
        model="sonar-pro",
        env_api_key="PERPLEXITY_API_KEY",
        endpoint="https://api.perplexity.ai/chat/completions",
        browser_command=command,
    )

    text = _call_remote_agent(spec, "pergunta longa", timeout=10)

    assert json.loads(text)["title_pct"] == 7.5
    assert "browser fallback" in json.loads(text)["summary"]


def test_call_remote_agent_uses_local_claude_cli_when_anthropic_key_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    payload = {
        "title_pct": 10.8,
        "summary": "Claude CLI respondeu como participante da sala.",
        "answer": "Opus contestou a hipótese inicial.",
    }
    command = [
        sys.executable,
        "-c",
        "import json, sys; print(json.dumps(%r))" % payload,
        "{prompt}",
    ]
    spec = AgentSpec(
        slot="Opus 4.8",
        provider="anthropic",
        model="claude-opus-4-8",
        env_api_key="ANTHROPIC_API_KEY",
        endpoint="https://api.anthropic.com/v1/messages",
        browser_command=command,
    )

    text = _call_remote_agent(spec, "pergunta para o claude", timeout=10)

    assert json.loads(text)["title_pct"] == 10.8
    assert "Claude CLI" in json.loads(text)["summary"]


def test_browser_command_timeout_inherits_call_budget_unless_operator_overrides(monkeypatch) -> None:
    monkeypatch.delenv("BROWSER_COMMAND_TIMEOUT_SECONDS", raising=False)

    assert _browser_command_timeout(240) == 240
    assert _browser_command_timeout(120) == 120
    assert _browser_command_timeout(90) == 90
    assert _browser_command_timeout(30) == 30

    monkeypatch.setenv("BROWSER_COMMAND_TIMEOUT_SECONDS", "300")
    assert _browser_command_timeout(240) == 300


def test_call_remote_agent_uses_full_agent_timeout_for_claude_cli(monkeypatch) -> None:
    monkeypatch.delenv("BROWSER_COMMAND_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    captured = {}

    class Result:
        returncode = 0
        stdout = '{"title_pct": 8.0, "summary": "claude ok"}'
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["timeout"] = kwargs.get("timeout")
        return Result()

    monkeypatch.setattr("worldcup_brazil.agents.subprocess.run", fake_run)
    spec = AgentSpec(
        slot="Opus 4.8",
        provider="anthropic",
        model="claude-opus-4-8",
        env_api_key="ANTHROPIC_API_KEY",
        endpoint="https://api.anthropic.com/v1/messages",
        browser_command=["claude", "--print", "--verbose", "--model", "claude-opus-4-8", "{prompt}"],
    )

    text = _call_remote_agent(spec, "pergunta para o claude", timeout=240)

    assert json.loads(text)["summary"] == "claude ok"
    assert captured["command"][-1] == "pergunta para o claude"
    assert captured["timeout"] == 240


def test_call_remote_agent_uses_local_chatgpt_cli_when_openai_key_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    payload = {
        "title_pct": 11.2,
        "summary": "ChatGPT CLI respondeu como participante da sala.",
        "answer": "GPT contestou a hipótese com fontes próprias.",
    }
    command = [
        sys.executable,
        "-c",
        "import json, sys; print(json.dumps(%r))" % payload,
        "{prompt}",
    ]
    spec = AgentSpec(
        slot="GPT 5.5",
        provider="openai",
        model="gpt-5.5",
        env_api_key="OPENAI_API_KEY",
        endpoint="https://api.openai.com/v1/responses",
        reasoning_effort="high",
        browser_command=command,
    )

    text = _call_remote_agent(spec, "pergunta para o chatgpt", timeout=10)

    assert json.loads(text)["title_pct"] == 11.2
    assert "ChatGPT CLI" in json.loads(text)["summary"]


def test_call_remote_agent_prefers_local_chatgpt_cli_when_configured_even_with_openai_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "api-key-present")
    payload = {
        "title_pct": 12.4,
        "summary": "GPT usou CLI local apesar da chave de API existir.",
        "answer": "A sala recebeu a resposta pelo bridge local.",
    }
    command = [
        sys.executable,
        "-c",
        "import json, sys; print(json.dumps(%r))" % payload,
        "{prompt}",
    ]

    def fail_if_api_is_called(*args, **kwargs):
        raise AssertionError("OpenAI API should not be called when local GPT bridge is preferred")

    monkeypatch.setattr("worldcup_brazil.agents._post_json", fail_if_api_is_called)
    spec = AgentSpec(
        slot="GPT 5.5",
        provider="openai",
        model="gpt-5.5",
        env_api_key="OPENAI_API_KEY",
        endpoint="https://api.openai.com/v1/responses",
        reasoning_effort="high",
        browser_command=command,
        prefer_bridge=True,
    )

    text = _call_remote_agent(spec, "pergunta para o chatgpt", timeout=10)

    assert json.loads(text)["title_pct"] == 12.4
    assert "CLI local" in json.loads(text)["summary"]


def test_call_remote_agent_prefers_local_claude_cli_when_configured_even_with_anthropic_key(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key-present")
    payload = {
        "title_pct": 12.4,
        "summary": "Claude usou CLI local apesar da chave de API existir.",
        "answer": "A sala recebeu a resposta pelo Claude CLI.",
    }
    command = [
        sys.executable,
        "-c",
        "import json, sys; print(json.dumps(%r))" % payload,
        "{prompt}",
    ]

    def fail_if_api_is_called(*args, **kwargs):
        raise AssertionError("Anthropic API should not be called when local Claude bridge is preferred")

    monkeypatch.setattr("worldcup_brazil.agents._post_json", fail_if_api_is_called)
    spec = AgentSpec(
        slot="Opus 4.8",
        provider="anthropic",
        model="claude-opus-4-8",
        env_api_key="ANTHROPIC_API_KEY",
        endpoint="https://api.anthropic.com/v1/messages",
        browser_command=command,
        prefer_bridge=True,
    )

    text = _call_remote_agent(spec, "pergunta para o claude", timeout=240)

    assert json.loads(text)["title_pct"] == 12.4
    assert "CLI local" in json.loads(text)["summary"]


def test_call_remote_agent_does_not_fall_back_to_openai_api_when_bridge_is_preferred(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "api-key-present")

    def bridge_fails(*args, **kwargs):
        raise RuntimeError("GPT 5.5 browser_command timed out after 90s")

    def fail_if_api_is_called(*args, **kwargs):
        raise AssertionError("OpenAI API should not be called after preferred bridge failure")

    monkeypatch.setattr("worldcup_brazil.agents._call_bridge_agent_runtime", bridge_fails)
    monkeypatch.setattr("worldcup_brazil.agents._post_json", fail_if_api_is_called)
    spec = AgentSpec(
        slot="GPT 5.5",
        provider="openai",
        model="gpt-5.5",
        env_api_key="OPENAI_API_KEY",
        endpoint="https://api.openai.com/v1/responses",
        reasoning_effort="high",
        browser_command=["codex", "--search", "exec", "-"],
        prefer_bridge=True,
    )

    try:
        _call_remote_agent(spec, "prompt", timeout=90)
    except RuntimeError as exc:
        assert "browser_command timed out" in str(exc)
    else:
        raise AssertionError("expected preferred bridge failure")


def test_codex_browser_command_gets_fresh_process_environment(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-thread")
    monkeypatch.setenv("CODEX_SANDBOX_NETWORK_DISABLED", "1")
    monkeypatch.setenv("CODEX_CI", "1")
    monkeypatch.setenv("CODEX_HOME", "/Users/marcus/.codex")
    monkeypatch.setenv("OPENAI_API_KEY", "keep-openai-key")

    env = _browser_command_env(["/opt/homebrew/bin/codex", "--search", "exec"])

    assert "CODEX_THREAD_ID" not in env
    assert "CODEX_SANDBOX_NETWORK_DISABLED" not in env
    assert "CODEX_CI" not in env
    assert env["CODEX_HOME"] == "/Users/marcus/.codex"
    assert env["OPENAI_API_KEY"] == "keep-openai-key"


def test_gemini_browser_command_also_gets_fresh_process_environment(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-thread")
    monkeypatch.setenv("CODEX_SANDBOX_NETWORK_DISABLED", "1")
    monkeypatch.setenv("CODEX_HOME", "/Users/marcus/.codex")
    monkeypatch.setenv("GEMINI_API_KEY", "keep-gemini-key")

    env = _browser_command_env(["/opt/homebrew/bin/gemini", "--skip-trust", "-p", "prompt"])

    assert "CODEX_THREAD_ID" not in env
    assert "CODEX_SANDBOX_NETWORK_DISABLED" not in env
    assert env["CODEX_HOME"] == "/Users/marcus/.codex"
    assert env["GEMINI_API_KEY"] == "keep-gemini-key"


def test_call_browser_command_agent_passes_prompt_as_argument_for_codex_bridge(monkeypatch) -> None:
    captured = {}

    class Result:
        returncode = 0
        stdout = '{"title_pct": 8.0, "summary": "ok"}'
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input")
        captured["env"] = kwargs.get("env")
        return Result()

    monkeypatch.setenv("CODEX_THREAD_ID", "parent-thread")
    monkeypatch.setattr("worldcup_brazil.agents.subprocess.run", fake_run)
    spec = AgentSpec(
        slot="GPT 5.5",
        provider="openai",
        model="gpt-5.5",
        env_api_key="OPENAI_API_KEY",
        endpoint="https://api.openai.com/v1/responses",
        browser_command=["codex", "--search", "exec", "--ignore-user-config", "{prompt}"],
    )

    text = _call_browser_command_agent(spec, "prompt real", timeout=90)

    assert json.loads(text)["summary"] == "ok"
    assert captured["command"][-1] == "prompt real"
    assert captured["input"] is None
    assert "CODEX_THREAD_ID" not in captured["env"]


def test_call_browser_command_agent_json_encodes_prompt_and_extracts_openai_cli_output(monkeypatch) -> None:
    captured = {}
    model_payload = {"title_pct": 8.0, "summary": "openai cli ok"}
    response_payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(model_payload),
                    }
                ],
            }
        ]
    }

    class Result:
        returncode = 0
        stdout = json.dumps(response_payload)
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input")
        return Result()

    monkeypatch.setattr("worldcup_brazil.agents.subprocess.run", fake_run)
    spec = AgentSpec(
        slot="GPT 5.5",
        provider="openai",
        model="gpt-5.5",
        env_api_key="OPENAI_API_KEY",
        endpoint="https://api.openai.com/v1/responses",
        browser_command=[
            "openai",
            "responses",
            "create",
            "--model",
            "gpt-5.5",
            "--input",
            "{prompt_json}",
            "--reasoning",
            '{"effort":"high"}',
        ],
    )

    text = _call_browser_command_agent(spec, "linha 1\nlinha 2", timeout=90)

    assert json.loads(text) == model_payload
    assert captured["command"][6] == json.dumps("linha 1\nlinha 2", ensure_ascii=False)
    assert captured["command"][8] == '{"effort":"high"}'
    assert captured["input"] is None


def test_call_browser_command_agent_extracts_claude_stream_json_result(monkeypatch) -> None:
    stream = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "hook_response", "output": "ignore hook"}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "texto parcial"}]}}),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": json.dumps({"title_pct": 8.7, "summary": "claude stream ok"}),
                    "modelUsage": {"claude-opus-4-8": {}},
                }
            ),
        ]
    )

    class Result:
        returncode = 0
        stdout = stream
        stderr = ""

    monkeypatch.setattr("worldcup_brazil.agents.subprocess.run", lambda *args, **kwargs: Result())
    spec = AgentSpec(
        slot="Opus 4.8",
        provider="anthropic",
        model="claude-opus-4-8",
        env_api_key="ANTHROPIC_API_KEY",
        endpoint="https://api.anthropic.com/v1/messages",
        browser_command=[
            "claude",
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--model",
            "claude-opus-4-8",
            "{prompt}",
        ],
    )

    text = _call_browser_command_agent(spec, "prompt", timeout=20)

    assert json.loads(text)["summary"] == "claude stream ok"


def test_call_browser_command_agent_falls_back_from_openai_cli_to_codex_bridge(monkeypatch) -> None:
    calls = []

    class Result:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "openai":
            return Result(1, stderr="openai cli unavailable")
        return Result(0, stdout='{"title_pct": 8.0, "summary": "codex fallback ok"}')

    monkeypatch.setattr("worldcup_brazil.agents.subprocess.run", fake_run)
    spec = AgentSpec(
        slot="GPT 5.5",
        provider="openai",
        model="gpt-5.5",
        env_api_key="OPENAI_API_KEY",
        endpoint="https://api.openai.com/v1/responses",
        browser_command=[
            "openai",
            "responses",
            "create",
            "--model",
            "gpt-5.5",
            "--input",
            "{prompt_json}",
            "--reasoning",
            '{"effort":"high"}',
        ],
        browser_fallback_commands=[["codex", "--search", "exec", "--ignore-user-config", "{prompt}"]],
        prefer_bridge=True,
    )

    text = _call_browser_command_agent(spec, "prompt real", timeout=90)

    assert json.loads(text)["summary"] == "codex fallback ok"
    assert calls[0] == [
        "openai",
        "responses",
        "create",
        "--model",
        "gpt-5.5",
        "--input",
        json.dumps("prompt real", ensure_ascii=False),
        "--reasoning",
        '{"effort":"high"}',
    ]
    assert calls[1] == ["codex", "--search", "exec", "--ignore-user-config", "prompt real"]


def test_agent_preflight_reports_status_method_and_self_declared_version(monkeypatch) -> None:
    payload = {
        "ok": True,
        "message": "funcionando",
        "self_identification": {"name": "Modelo Teste", "version": "v-test"},
    }

    class Result:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input")
        return Result()

    monkeypatch.setattr("worldcup_brazil.agents.subprocess.run", fake_run)
    spec = AgentSpec(
        slot="GPT 5.5",
        provider="openai",
        model="gpt-5.5",
        env_api_key="OPENAI_API_KEY",
        endpoint="https://api.openai.com/v1/responses",
        browser_command=["openai", "responses", "create", "--model", "gpt-5.5", "--input", "{prompt_json}"],
        prefer_bridge=True,
    )

    results = run_agent_preflights_sync([spec], timeout=15)

    assert len(results) == 1
    result = results[0]
    assert result.ok is True
    assert result.slot == "GPT 5.5"
    assert result.method == "cli:openai"
    assert result.configured_model == "gpt-5.5"
    assert result.runtime_model == "gpt-5.5"
    assert result.declared_name == "Modelo Teste"
    assert result.declared_version == "v-test"
    assert result.message == "funcionando"
    assert captured["input"] is None
    assert json.loads(captured["command"][6]).startswith("Teste rápido de conectividade")


def test_agent_preflight_contract_rejects_ping_without_sources(monkeypatch) -> None:
    payload = {
        "ok": True,
        "message": "funcionando",
        "self_identification": {"name": "Modelo Teste", "version": "v-test"},
    }

    class Result:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr("worldcup_brazil.agents.subprocess.run", lambda *_args, **_kwargs: Result())
    spec = AgentSpec(
        slot="GPT 5.5",
        provider="openai",
        model="gpt-5.5",
        env_api_key="OPENAI_API_KEY",
        endpoint="https://api.openai.com/v1/responses",
        browser_command=["openai", "responses", "create", "--model", "gpt-5.5", "--input", "{prompt_json}"],
        prefer_bridge=True,
    )

    result = run_agent_preflights_sync([spec], timeout=15, contract=True)[0]

    assert result.ok is False
    assert "source_urls/source_queries" in result.error
    assert result.message == "funcionando"


def test_agent_preflight_contract_accepts_structured_source_plan(monkeypatch) -> None:
    payload = {
        "ok": True,
        "message": "contrato mínimo ok",
        "self_identification": {"name": "Modelo Teste", "version": "v-test"},
        "title_pct": 8.5,
        "summary": "Brasil e Marrocos cobertos com odds e ratings.",
        "source_queries": ["Brazil Morocco World Cup 2026 odds Elo Sofascore injuries"],
    }
    captured = {}

    class Result:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    def fake_run(command, **kwargs):
        captured["prompt"] = json.loads(command[6])
        return Result()

    monkeypatch.setattr("worldcup_brazil.agents.subprocess.run", fake_run)
    spec = AgentSpec(
        slot="GPT 5.5",
        provider="openai",
        model="gpt-5.5",
        env_api_key="OPENAI_API_KEY",
        endpoint="https://api.openai.com/v1/responses",
        browser_command=["openai", "responses", "create", "--model", "gpt-5.5", "--input", "{prompt_json}"],
        prefer_bridge=True,
    )

    result = run_agent_preflights_sync([spec], timeout=15, contract=True)[0]

    assert result.ok is True
    assert result.error == ""
    assert "Teste de contrato mínimo" in captured["prompt"]
    assert "source_urls/source_queries" in captured["prompt"]


def test_render_agent_preflight_stdout_is_highlighted() -> None:
    spec = AgentSpec(
        slot="Gemini Pro",
        provider="google-gemini",
        model="gemini-3.5-flash",
        env_api_key="GEMINI_API_KEY",
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
    )
    result = run_agent_preflights_sync([spec], timeout=1)[0]
    rendered = render_agent_preflight_stdout([result])

    assert "MODEL PREFLIGHT" in rendered
    assert "[FAIL]" in rendered
    assert "Gemini Pro" in rendered
    assert "método=" in rendered
    assert "modelo_config=gemini-3.5-flash" in rendered


def test_post_json_retries_http_429_with_exponential_backoff(monkeypatch) -> None:
    calls = {"count": 0}
    sleeps = []
    monkeypatch.delenv("HTTP_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("HTTP_BACKOFF_BASE_SECONDS", raising=False)
    monkeypatch.delenv("HTTP_BACKOFF_MAX_SECONDS", raising=False)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.HTTPError(
                url="https://api.example.test",
                code=429,
                msg="Too Many Requests",
                hdrs=None,
                fp=io.BytesIO(b"rate limited"),
            )
        return Response()

    monkeypatch.setattr("worldcup_brazil.agents.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("worldcup_brazil.agents.time.sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr("worldcup_brazil.agents.random.uniform", lambda _low, _high: 0.0)

    assert _post_json("https://api.example.test", {}, {"x": 1}, timeout=3) == {"ok": True}
    assert calls["count"] == 2
    assert sleeps == [1.0]


def test_post_json_does_not_retry_http_404(monkeypatch) -> None:
    calls = {"count": 0}
    monkeypatch.delenv("HTTP_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("HTTP_BACKOFF_BASE_SECONDS", raising=False)
    monkeypatch.delenv("HTTP_BACKOFF_MAX_SECONDS", raising=False)

    def fake_urlopen(request, timeout):
        calls["count"] += 1
        raise urllib.error.HTTPError(
            url="https://api.example.test",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(b"missing"),
        )

    monkeypatch.setattr("worldcup_brazil.agents.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("worldcup_brazil.agents.time.sleep", lambda _seconds: None)

    try:
        _post_json("https://api.example.test", {}, {"x": 1}, timeout=3)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:
        raise AssertionError("expected HTTPError")
    assert calls["count"] == 1


def test_call_all_agents_uses_provider_bulkheads_without_serializing_other_providers(monkeypatch) -> None:
    running_by_provider = {}
    max_running_by_provider = {}
    order = []

    async def fake_call_agent(spec, prompt, *, baseline_title_pct, timeout, allow_local_fallback):
        provider_key = f"{spec.provider}:{spec.env_api_key or spec.endpoint}"
        running_by_provider[provider_key] = running_by_provider.get(provider_key, 0) + 1
        max_running_by_provider[provider_key] = max(
            max_running_by_provider.get(provider_key, 0),
            running_by_provider[provider_key],
        )
        order.append(("start", spec.slot))
        await asyncio.sleep(0)
        running_by_provider[provider_key] -= 1
        order.append(("finish", spec.slot))
        return __import__("worldcup_brazil.consensus").consensus.AgentOpinion(
            agent=spec.slot,
            title_pct=8.0,
            summary="ok",
        )

    monkeypatch.setattr("worldcup_brazil.agents.call_agent", fake_call_agent)
    monkeypatch.setenv("AGENT_BULKHEAD_OPENAI_COMPATIBLE", "1")
    specs = [
        AgentSpec(
            slot="Perplexity Pro",
            provider="openai-compatible",
            model="sonar-pro",
            env_api_key="PERPLEXITY_API_KEY",
            endpoint="https://api.perplexity.ai/chat/completions",
        ),
        AgentSpec(
            slot="Perplexity Audit",
            provider="openai-compatible",
            model="sonar-pro",
            env_api_key="PERPLEXITY_API_KEY",
            endpoint="https://api.perplexity.ai/chat/completions",
        ),
        AgentSpec(
            slot="Gemini Pro",
            provider="google-gemini",
            model="gemini-flash-latest",
            env_api_key="GEMINI_API_KEY",
            endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        ),
    ]

    opinions = asyncio.run(
        call_all_agents(
            "prompt",
            specs=specs,
            baseline_title_pct=8.0,
            timeout=10,
        )
    )

    perplexity_key = "openai-compatible:PERPLEXITY_API_KEY"
    gemini_key = "google-gemini:GEMINI_API_KEY"
    assert [opinion.agent for opinion in opinions] == ["Perplexity Pro", "Perplexity Audit", "Gemini Pro"]
    assert max_running_by_provider[perplexity_key] == 1
    assert max_running_by_provider[gemini_key] == 1
    assert ("start", "Gemini Pro") in order[:2]


def test_call_agent_converts_browser_command_timeout_to_local_fallback(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    calls = {}

    def fake_run(*args, **kwargs):
        calls["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))

    monkeypatch.setattr("worldcup_brazil.agents.subprocess.run", fake_run)
    spec = AgentSpec(
        slot="Opus 4.8",
        provider="anthropic",
        model="claude-opus-4-8",
        env_api_key="ANTHROPIC_API_KEY",
        endpoint="https://api.anthropic.com/v1/messages",
        browser_command=["claude", "{prompt}"],
    )

    opinion = asyncio.run(call_agent(spec, "pergunta para o claude", baseline_title_pct=10.0, timeout=90))

    assert opinion.used_fallback is True
    assert opinion.title_pct == 10.0
    assert calls["timeout"] == 90
    assert "não participa do consenso" in opinion.answer
    assert "mantenho a leitura conservadora" not in opinion.answer


def test_call_agent_reports_claude_cli_auth_failure_as_runner_auth_issue(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    class Result:
        returncode = 1
        stdout = ""
        stderr = "Not logged in · Please run /login"

    monkeypatch.setattr("worldcup_brazil.agents.subprocess.run", lambda *_args, **_kwargs: Result())
    spec = AgentSpec(
        slot="Opus 4.8",
        provider="anthropic",
        model="claude-opus-4-8",
        env_api_key="ANTHROPIC_API_KEY",
        endpoint="https://api.anthropic.com/v1/messages",
        browser_command=["claude", "--print", "--verbose", "{prompt}"],
    )

    opinion = asyncio.run(call_agent(spec, "pergunta para o claude", baseline_title_pct=10.0, timeout=240))

    assert opinion.used_fallback is True
    assert "Claude CLI auth unavailable in this runner process" in opinion.summary
    assert "claude auth status" in opinion.summary
    assert "ANTHROPIC_API_KEY" in opinion.summary


def test_call_agent_does_not_retry_preferred_bridge_after_timeout(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "api-key-present")
    calls = {"bridge": 0}

    def bridge_fails(*args, **kwargs):
        calls["bridge"] += 1
        raise RuntimeError("GPT 5.5 browser_command timed out after 90s")

    monkeypatch.setattr("worldcup_brazil.agents._call_bridge_agent_runtime", bridge_fails)
    spec = AgentSpec(
        slot="GPT 5.5",
        provider="openai",
        model="gpt-5.5",
        env_api_key="OPENAI_API_KEY",
        endpoint="https://api.openai.com/v1/responses",
        browser_command=["codex", "--search", "exec", "-"],
        prefer_bridge=True,
    )

    opinion = asyncio.run(call_agent(spec, "prompt", baseline_title_pct=10.0, timeout=90))

    assert calls["bridge"] == 1
    assert opinion.used_fallback is True
    assert "browser_command timed out" in opinion.summary


def test_call_remote_agent_posts_to_gemini_generate_content(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    calls = {}

    def fake_post_json(url, headers, payload, *, timeout):
        calls["url"] = url
        calls["headers"] = headers
        calls["payload"] = payload
        calls["timeout"] = timeout
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "title_pct": 9.8,
                                        "summary": "Gemini trouxe ratings e performance individual.",
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr("worldcup_brazil.agents._post_json", fake_post_json)
    spec = AgentSpec(
        slot="Gemini Pro",
        provider="google-gemini",
        model="gemini-flash-latest",
        env_api_key="GEMINI_API_KEY",
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        max_output_tokens=7000,
        reasoning_effort="high",
    )

    text = _call_remote_agent(spec, "Monte uma tese sem Opta.", timeout=12)

    assert calls["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    assert calls["headers"]["X-goog-api-key"] == "gemini-secret"
    assert calls["timeout"] == 12
    assert calls["payload"]["contents"][0]["parts"][0]["text"] == "Monte uma tese sem Opta."
    assert calls["payload"]["generationConfig"]["maxOutputTokens"] == 7000
    assert calls["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    assert calls["payload"]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "HIGH"
    assert json.loads(text)["title_pct"] == 9.8


def test_call_remote_agent_tries_gemini_lite_model_before_removing_from_room(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    calls = {"bridge_models": [], "http_urls": []}

    def fake_run(command, **_kwargs):
        calls["bridge_models"].append(command[-1])
        class Result:
            returncode = 1
            stdout = ""
            stderr = "cli failed"
        return Result()

    def fake_post_json(url, headers, payload, *, timeout):
        calls["http_urls"].append(url)
        assert headers["X-goog-api-key"] == "gemini-secret"
        assert payload["contents"][0]["parts"][0]["text"] == "Monte uma tese sem Opta."
        if "gemini-3.5-flash" in url:
            raise urllib.error.HTTPError(url=url, code=429, msg="Too Many Requests", hdrs=None, fp=None)
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "title_pct": 9.6,
                                        "summary": "Gemini lite assumiu depois da falha primária.",
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr("worldcup_brazil.agents.subprocess.run", fake_run)
    monkeypatch.setattr("worldcup_brazil.agents._post_json", fake_post_json)
    spec = AgentSpec(
        slot="Gemini Pro",
        provider="google-gemini",
        model="gemini-3.5-flash",
        env_api_key="GEMINI_API_KEY",
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        max_output_tokens=7000,
        reasoning_effort="high",
        browser_command=["gemini", "--skip-trust", "-p", "{prompt}", "-m", "gemini-3.5-flash"],
        prefer_bridge=True,
        model_fallbacks=["gemini-3.1-flash-lite"],
    )

    text = _call_remote_agent(spec, "Monte uma tese sem Opta.", timeout=12)

    assert calls["bridge_models"] == ["gemini-3.5-flash", "gemini-3.1-flash-lite"]
    assert calls["http_urls"] == [
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent",
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent",
    ]
    payload = json.loads(text)
    assert payload["summary"].startswith("Gemini lite assumiu depois da falha primária.")
    assert payload["runtime_model_fallback"] == {
        "from": "gemini-3.5-flash",
        "to": "gemini-3.1-flash-lite",
    }


def test_call_remote_agent_falls_back_to_gemini_http_api_after_cli_failure(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    calls = {"bridge": 0}

    def bridge_fails(spec, prompt, *, timeout):
        calls["bridge"] += 1
        assert spec.slot == "Gemini Pro"
        assert prompt == "Monte uma tese sem Opta."
        assert timeout == 12
        raise RuntimeError("Gemini Pro browser_command failed: cli crashed")

    def fake_post_json(url, headers, payload, *, timeout):
        calls["url"] = url
        calls["headers"] = headers
        calls["payload"] = payload
        calls["timeout"] = timeout
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "title_pct": 9.6,
                                        "summary": "Gemini HTTP assumiu depois da falha do CLI.",
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr("worldcup_brazil.agents._call_bridge_agent_runtime", bridge_fails)
    monkeypatch.setattr("worldcup_brazil.agents._post_json", fake_post_json)
    spec = AgentSpec(
        slot="Gemini Pro",
        provider="google-gemini",
        model="gemini-flash-latest",
        env_api_key="GEMINI_API_KEY",
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        max_output_tokens=7000,
        reasoning_effort="high",
        browser_command=["gemini", "--skip-trust", "-p", "{prompt}"],
        prefer_bridge=True,
        model_fallbacks=[],
    )

    text = _call_remote_agent(spec, "Monte uma tese sem Opta.", timeout=12)

    assert calls["bridge"] == 1
    assert calls["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    assert calls["headers"]["X-goog-api-key"] == "gemini-secret"
    assert calls["payload"]["contents"][0]["parts"][0]["text"] == "Monte uma tese sem Opta."
    assert json.loads(text)["summary"] == "Gemini HTTP assumiu depois da falha do CLI."


def test_agent_effort_profile_reports_native_controls_and_fast_latency_guard() -> None:
    openai = AgentSpec(
        slot="GPT 5.5",
        provider="openai",
        model="gpt-5.5",
        env_api_key="OPENAI_API_KEY",
        endpoint="https://api.openai.com/v1/responses",
        reasoning_effort="high",
        max_output_tokens=6000,
    )
    gemini = AgentSpec(
        slot="Gemini Pro",
        provider="google-gemini",
        model="gemini-flash-latest",
        env_api_key="GEMINI_API_KEY",
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        reasoning_effort="high",
        max_output_tokens=7000,
    )
    gemini_cli = AgentSpec(
        slot="Gemini Pro",
        provider="google-gemini",
        model="gemini-flash-latest",
        env_api_key="GEMINI_API_KEY",
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        browser_command=["gemini", "--skip-trust", "-p", "{prompt}"],
        prefer_bridge=True,
    )
    claude_cli = AgentSpec(
        slot="Opus 4.8",
        provider="anthropic",
        model="claude-opus-4-8",
        env_api_key="ANTHROPIC_API_KEY",
        endpoint="https://api.anthropic.com/v1/messages",
        browser_command=[
            "claude",
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--model",
            "claude-opus-4-8",
            "--effort",
            "high",
            "{prompt}",
        ],
    )
    gpt_cli = AgentSpec(
        slot="GPT 5.5",
        provider="openai",
        model="gpt-5.5",
        env_api_key="OPENAI_API_KEY",
        endpoint="https://api.openai.com/v1/responses",
        reasoning_effort="high",
        browser_command=["codex", "{prompt}"],
    )

    openai_profile = agent_effort_profile(openai)
    gemini_profile = agent_effort_profile(gemini)
    claude_profile = agent_effort_profile(claude_cli)
    gpt_cli_profile = agent_effort_profile(gpt_cli)
    gemini_cli_profile = agent_effort_profile(gemini_cli)

    assert openai_profile["effort_level"] == "reasoning_effort=high + resposta rápida"
    assert "reasoning_effort=high" in openai_profile["controls"]
    assert "thinkingLevel=HIGH" in gemini_profile["controls"]
    assert claude_profile["effort_level"] == "bridge effort=high + resposta rápida"
    assert "bridge=claude CLI" in claude_profile["controls"]
    assert "bridge_effort=high" in claude_profile["controls"]
    assert "bridge_model=claude-opus-4-8" in claude_profile["controls"]
    assert "bridge=codex CLI" in gpt_cli_profile["controls"]
    assert "bridge=Gemini CLI" in gemini_cli_profile["controls"]
    assert "bridge_preferred=true" in gemini_cli_profile["controls"]
    assert "reasoning_effort=high" in gpt_cli_profile["controls"]
    assert "bridge_timeout=agent_timeout_seconds" in claude_profile["controls"]
    assert "sem parâmetro nativo" in claude_profile["controls"]
    assert "JSON objetivo" in openai_profile["latency_guard"]

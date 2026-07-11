import os
from pathlib import Path

import pytest

from scripts.run_agent_source_harness import build_parser as build_harness_parser
from worldcup_brazil.agents import default_agent_specs, load_agent_specs_from_config
from worldcup_brazil.cli import (
    _bridges_enabled,
    build_parser as build_legacy_parser,
    load_env_file,
)


def test_local_executable_and_browser_bridges_are_disabled_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for binary in ("claude", "openai", "codex", "gemini"):
        path = bin_dir / binary
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("CLAUDE_BROWSER_COMMAND", "claude {prompt}")
    monkeypatch.setenv("OPENAI_WEB_FETCH_URL", "https://bridge.example.test")

    specs = default_agent_specs()

    assert all(spec.browser_command is None for spec in specs)
    assert all(
        spec.browser_fallback_commands is None or spec.browser_fallback_commands == []
        for spec in specs
    )
    assert all(spec.web_fetch_url is None for spec in specs)
    assert all(spec.prefer_bridge is False for spec in specs)


def test_configured_bridge_requires_enable_and_no_bridges_override_is_absolute(
    monkeypatch,
) -> None:
    config = {
        "bridges_enabled": True,
        "agents": [
            {
                "slot": "Opus 4.8",
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "env_api_key": "ANTHROPIC_API_KEY",
                "endpoint": "https://api.anthropic.com/v1/messages",
                "browser_command": ["claude", "--print", "{prompt}"],
                "prefer_bridge": True,
            }
        ],
    }
    monkeypatch.setenv("WORLDCUP_ENABLE_BRIDGES", "1")

    disabled = load_agent_specs_from_config(config, bridges_enabled=False)
    enabled = load_agent_specs_from_config(config, bridges_enabled=True)

    assert disabled[0].browser_command is None
    assert disabled[0].prefer_bridge is False
    assert enabled[0].browser_command == ["claude", "--print", "{prompt}"]
    assert enabled[0].prefer_bridge is True
    assert _bridges_enabled(config, cli_override=False) is False
    assert _bridges_enabled(config, cli_override=None) is True
    assert _bridges_enabled({"bridges_enabled": "false"}, cli_override=None) is False


@pytest.mark.parametrize("command", ["claude --print {prompt}", [], [""]])
def test_bridge_commands_in_config_must_be_nonempty_argument_arrays(
    command: object,
) -> None:
    config = {
        "agents": [
            {
                "slot": "Opus 4.8",
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "env_api_key": "ANTHROPIC_API_KEY",
                "endpoint": "https://api.anthropic.com/v1/messages",
                "browser_command": command,
            }
        ]
    }

    with pytest.raises(TypeError, match="argument array"):
        load_agent_specs_from_config(config, bridges_enabled=True)


def test_legacy_cli_and_harness_never_default_to_a_shell_profile() -> None:
    legacy = build_legacy_parser().parse_args([])
    harness = build_harness_parser().parse_args([])

    assert legacy.shell_env_file is None
    assert harness.shell_env_file is None
    assert legacy.bridges is None
    assert harness.bridges is None


def test_load_env_file_sets_missing_keys_without_overwriting_existing_values(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANTHROPIC_API_KEY=from-file\n"
        "OPENAI_API_KEY=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "already-set")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    load_env_file(env_file)

    assert os.environ["ANTHROPIC_API_KEY"] == "from-file"
    assert os.environ["OPENAI_API_KEY"] == "already-set"


def test_default_agent_specs_read_browser_commands_after_env_file_load(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLAUDE_CLI_COMMAND=claude-fetch {prompt}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DEEPSEEK_BROWSER_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_BROWSER_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_COMMAND", raising=False)

    load_env_file(env_file)
    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert "DeepSeek Latest Free" not in specs
    assert specs["DeepSeek V4 Pro"].env_api_key == "DEEPSEEK_API_KEY"
    assert specs["Opus 4.8"].browser_command == ["claude-fetch", "{prompt}"]


def test_default_agent_specs_include_deepseek_v4_pro_from_base_url(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_V4_PRO_MODEL", "deepseek-v4-pro")

    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert specs["DeepSeek V4 Pro"].provider == "openai-compatible"
    assert specs["DeepSeek V4 Pro"].model == "deepseek-v4-pro"
    assert specs["DeepSeek V4 Pro"].env_api_key == "DEEPSEEK_API_KEY"
    assert specs["DeepSeek V4 Pro"].endpoint == "https://api.deepseek.com/chat/completions"
    assert specs["DeepSeek V4 Pro"].max_output_tokens == 7000


def test_default_agent_specs_use_local_claude_cli_when_anthropic_api_key_is_missing(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\"\n", encoding="utf-8")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_BROWSER_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_EFFORT", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_ALLOWED_TOOLS", raising=False)

    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert specs["Opus 4.8"].browser_command == [
        str(claude),
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--model",
        "claude-opus-4-8",
        "--effort",
        "high",
        "--allowedTools=WebSearch,WebFetch",
        "{prompt}",
    ]
    assert specs["Opus 4.8"].prefer_bridge is True


def test_default_agent_specs_prefers_claude_cli_even_when_anthropic_key_exists(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\"\n", encoding="utf-8")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key-present")
    monkeypatch.delenv("CLAUDE_BROWSER_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_PREFER_BRIDGE", raising=False)
    monkeypatch.delenv("CLAUDE_PREFER_CLI", raising=False)

    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert specs["Opus 4.8"].browser_command[0] == str(claude)
    assert specs["Opus 4.8"].prefer_bridge is True


def test_default_agent_specs_can_explicitly_disable_claude_cli_preference(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\"\n", encoding="utf-8")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key-present")
    monkeypatch.setenv("CLAUDE_PREFER_BRIDGE", "false")
    monkeypatch.delenv("CLAUDE_BROWSER_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_PREFER_CLI", raising=False)

    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert specs["Opus 4.8"].browser_command[0] == str(claude)
    assert specs["Opus 4.8"].prefer_bridge is False


def test_default_agent_specs_allow_claude_cli_fast_mode_overrides(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\"\n", encoding="utf-8")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("CLAUDE_CLI_EFFORT", "medium")
    monkeypatch.setenv("CLAUDE_CLI_MODEL", "sonnet")
    monkeypatch.delenv("CLAUDE_BROWSER_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_COMMAND", raising=False)

    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert "--model" in specs["Opus 4.8"].browser_command
    assert "sonnet" in specs["Opus 4.8"].browser_command
    assert "--effort" in specs["Opus 4.8"].browser_command
    assert "medium" in specs["Opus 4.8"].browser_command
    assert "--verbose" in specs["Opus 4.8"].browser_command
    assert "stream-json" in specs["Opus 4.8"].browser_command
    assert specs["Opus 4.8"].browser_command[-1] == "{prompt}"


def test_default_agent_specs_use_openai_fast_reasoning_by_default_with_override(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)

    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert specs["GPT 5.5"].reasoning_effort == "high"

    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "medium")
    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert specs["GPT 5.5"].reasoning_effort == "medium"


def test_default_agent_specs_use_openai_cli_for_gpt_with_codex_fallback(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    openai = bin_dir / "openai"
    openai.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\"\n", encoding="utf-8")
    openai.chmod(0o755)
    codex = bin_dir / "codex"
    codex.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\"\n", encoding="utf-8")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("OPENAI_GPT_MODEL", "gpt-5.5")
    monkeypatch.delenv("OPENAI_BROWSER_COMMAND", raising=False)
    monkeypatch.delenv("OPENAI_CLI_COMMAND", raising=False)
    monkeypatch.delenv("CHATGPT_CLI_COMMAND", raising=False)
    monkeypatch.delenv("GPT_CLI_COMMAND", raising=False)

    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert specs["GPT 5.5"].browser_command == [
        str(openai),
        "responses",
        "create",
        "--model",
        "gpt-5.5",
        "--input",
        "{prompt_json}",
        "--reasoning",
        '{"effort":"high"}',
    ]
    assert specs["GPT 5.5"].browser_fallback_commands == [
        [
            str(codex),
            "--search",
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "-s",
            "read-only",
            "{prompt}",
        ]
    ]


def test_default_agent_specs_use_local_codex_cli_for_gpt_when_openai_api_key_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\"\n", encoding="utf-8")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BROWSER_COMMAND", raising=False)
    monkeypatch.delenv("OPENAI_CLI_COMMAND", raising=False)
    monkeypatch.delenv("CHATGPT_CLI_COMMAND", raising=False)
    monkeypatch.delenv("GPT_CLI_COMMAND", raising=False)

    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert specs["GPT 5.5"].browser_command == [
        str(codex),
        "--search",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "-s",
        "read-only",
        "{prompt}",
    ]


def test_default_agent_specs_use_local_gemini_cli_as_preferred_bridge(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gemini = bin_dir / "gemini"
    gemini.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\"\n", encoding="utf-8")
    gemini.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_CLI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_BROWSER_COMMAND", raising=False)
    monkeypatch.delenv("GEMINI_CLI_COMMAND", raising=False)
    monkeypatch.delenv("GEMINI_PREFER_BRIDGE", raising=False)
    monkeypatch.delenv("GEMINI_PREFER_CLI", raising=False)

    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert specs["Gemini Pro"].browser_command == [
        str(gemini),
        "--skip-trust",
        "-p",
        "{prompt}",
        "--output-format",
        "text",
        "--approval-mode",
        "plan",
        "-m",
        "gemini-3.5-flash",
    ]
    assert specs["Gemini Pro"].model == "gemini-3.5-flash"
    assert specs["Gemini Pro"].model_fallbacks == ["gemini-3.1-flash-lite"]
    assert specs["Gemini Pro"].prefer_bridge is True


def test_default_agent_specs_prefers_explicit_chatgpt_cli_env_for_gpt(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CHATGPT_CLI_COMMAND=chatgpt --model gpt-5.5 {prompt}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_BROWSER_COMMAND", raising=False)
    monkeypatch.delenv("OPENAI_CLI_COMMAND", raising=False)
    monkeypatch.delenv("CHATGPT_CLI_COMMAND", raising=False)
    monkeypatch.delenv("GPT_CLI_COMMAND", raising=False)

    load_env_file(env_file)
    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert specs["GPT 5.5"].browser_command == ["chatgpt", "--model", "gpt-5.5", "{prompt}"]


def test_load_env_file_accepts_export_lines_for_gemini_key(tmp_path: Path, monkeypatch) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        "export GEMINI_API_KEY='from-zshrc'\n"
        "export GEMINI_MODEL=gemini-flash-latest\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    load_env_file(zshrc)
    specs = {spec.slot: spec for spec in default_agent_specs(bridges_enabled=True)}

    assert os.environ["GEMINI_API_KEY"] == "from-zshrc"
    assert specs["Gemini Pro"].env_api_key == "GEMINI_API_KEY"
    assert specs["Gemini Pro"].model == "gemini-flash-latest"

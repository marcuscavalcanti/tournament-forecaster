import subprocess
import sys
from pathlib import Path
import argparse
import asyncio
import json

from worldcup_brazil import cli
from worldcup_brazil.agents import AgentPreflightResult
from worldcup_brazil.consensus import AgentOpinion
from worldcup_brazil.pipeline import ReportCoherenceError, SourcePlanningQuorumError
import scripts.run_agent_source_harness as source_harness
from worldcup_brazil.probabilities import MatchEstimate


def test_daily_script_entrypoint_can_be_invoked_directly() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/run_daily_worldcup_brazil.py", "--help"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Generate the Brazil World Cup 2026 LinkedIn forecast post" in result.stdout


def test_agent_source_harness_entrypoint_can_be_invoked_directly() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/run_agent_source_harness.py", "--help"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Diagnose agent source-planning quorum" in result.stdout


def test_opponent_room_contract_validator_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/validate_opponent_room_contract.py"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True


def test_cli_reports_source_quorum_failure_without_python_stacktrace(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"baseline_title_pct": 8.0}', encoding="utf-8")

    def fail_quorum(**kwargs):
        raise SourcePlanningQuorumError("Quórum insuficiente para debriefing: 2 modelo(s); mínimo exigido: 3.")

    monkeypatch.setattr(cli, "build_report_bundle_sync", fail_quorum)

    result = cli.main(
        [
            "--config",
            str(config),
            "--state",
            str(tmp_path / "state.json"),
            "--source-memory",
            str(tmp_path / "source_memory.json"),
            "--output-dir",
            str(tmp_path / "outputs"),
            "--watchdog-log",
            str(tmp_path / "watchdog.jsonl"),
            "--lock-file",
            str(tmp_path / ".run.lock"),
            "--force",
            "--no-watchdog",
            "--no-model-preflight",
        ]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert "fail: Quórum insuficiente" in captured.err
    assert "Traceback" not in captured.err


def test_cli_reports_report_coherence_failure_without_python_stacktrace(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"baseline_title_pct": 8.0}', encoding="utf-8")

    def fail_coherence(**kwargs):
        raise ReportCoherenceError("Gate de coerência pré-render falhou: titulo=10.2 > final=9.9")

    monkeypatch.setattr(cli, "build_report_bundle_sync", fail_coherence)

    result = cli.main(
        [
            "--config",
            str(config),
            "--state",
            str(tmp_path / "state.json"),
            "--source-memory",
            str(tmp_path / "source_memory.json"),
            "--output-dir",
            str(tmp_path / "outputs"),
            "--watchdog-log",
            str(tmp_path / "watchdog.jsonl"),
            "--lock-file",
            str(tmp_path / ".run.lock"),
            "--force",
            "--no-watchdog",
            "--no-model-preflight",
        ]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert "fail: Gate de coerência" in captured.err
    assert "Traceback" not in captured.err


def test_cli_prints_model_preflight_before_report_outputs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"baseline_title_pct": 8.0, "agents": []}', encoding="utf-8")

    class FakePreflight:
        slot = "GPT 5.5"
        ok = True
        method = "cli:openai"
        configured_model = "gpt-5.5"
        runtime_model = "gpt-5.5-2026-04-23"
        declared_name = "GPT"
        declared_version = "5.5"
        message = "funcionando"
        error = ""
        elapsed_ms = 12

    class FakeBundle:
        generated_at_iso = "2026-06-09T12:00:00+00:00"
        group_matches = []
        knockout_matches = []
        stage_probabilities = {}
        stage_confidence_intervals = {}
        sources = []
        warnings = []
        debate_transcript = []
        meeting_transcript = []
        source_plan_by_model = {}
        model_influence_pct = {}
        model_participation = {}
        model_token_costs = {}
        agent_effort_profiles = {}
        model_predictions_no_opta = {}
        opta_benchmark = {}
        model_vs_opta = {}
        metadata = {}

    class FakeArtifacts:
        post = "# post"
        bundle = FakeBundle()
        raw_evidence = []

    preflight_call = {}

    def fake_preflight(specs, *, timeout, contract):
        preflight_call["timeout"] = timeout
        preflight_call["contract"] = contract
        return [FakePreflight()]

    monkeypatch.setattr(cli, "run_agent_preflights_sync", fake_preflight)
    monkeypatch.setattr(cli, "render_agent_preflight_stdout", lambda results: "===== MODEL PREFLIGHT =====\n[OK] GPT 5.5 | método=cli:openai | versão=5.5\n===========================\n")
    monkeypatch.setattr(cli, "build_report_bundle_sync", lambda **kwargs: FakeArtifacts())

    result = cli.main(
        [
            "--config",
            str(config),
            "--state",
            str(tmp_path / "state.json"),
            "--source-memory",
            str(tmp_path / "source_memory.json"),
            "--output-dir",
            str(tmp_path / "outputs"),
            "--watchdog-log",
            str(tmp_path / "watchdog.jsonl"),
            "--lock-file",
            str(tmp_path / ".run.lock"),
            "--force",
            "--no-watchdog",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "MODEL PREFLIGHT" in captured.out
    assert "[OK] GPT 5.5" in captured.out
    assert captured.out.index("MODEL PREFLIGHT") < captured.out.index("post:")
    assert preflight_call == {"timeout": 180, "contract": True}


def test_match_estimate_json_exposes_debate_relevant_match_fields() -> None:
    match = MatchEstimate(
        brazil="Brasil",
        opponent="Japao",
        phase="16 avos",
        brazil_pct=71.8,
        opponent_pct=28.2,
        statistical_weight=0.7,
        qualitative_weight=0.3,
        rationale="Monte Carlo + sala adversarios.",
        match_date="2026-06-29",
        brazil_ci_low=65.0,
        brazil_ci_high=78.0,
        most_likely=True,
        venue="Houston Stadium",
        scenario_pct=36.7,
    )

    payload = cli._match_estimate_to_json(match)

    assert payload["phase"] == "16 avos"
    assert payload["opponent"] == "Japao"
    assert payload["scenario_pct"] == 36.7
    assert payload["brazil_pct"] == 71.8
    assert payload["most_likely"] is True
    assert payload["venue"] == "Houston Stadium"


def test_agent_source_harness_uses_source_planning_sanitizer(tmp_path: Path, monkeypatch) -> None:
    async def fake_call_all_agents(*args, **kwargs):
        assert kwargs["timeout"] == 77
        return [
            AgentOpinion(
                agent="GPT 5.5",
                title_pct=8.0,
                summary="Concordo que odds e Elo devem abrir o plano.",
                source_queries=["Brazil World Cup 2026 odds Elo Sofascore injuries"],
            ),
            AgentOpinion(
                agent="DeepSeek V4 Pro",
                title_pct=8.0,
                summary="Concordo e adiciono Transfermarkt e arbitragem.",
                source_queries=["Brazil Morocco Scotland Haiti Transfermarkt referee World Cup 2026"],
            ),
            AgentOpinion(
                agent="Gemini Pro",
                title_pct=8.0,
                summary="Concordo com pesquisa simétrica por adversário.",
                source_queries=["Brazil Group C Morocco Haiti Scotland World Cup 2026 ratings"],
            ),
        ]

    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "baseline_title_pct": 8.0,
                "model_preflight_enabled": False,
                "doctor_agent_timeout_seconds": 77,
                "minimum_source_ready_agents": 3,
                "require_agent_source_plan": True,
                "agents": [
                    {"slot": "GPT 5.5", "provider": "openai", "model": "gpt-5.5", "env_api_key": None, "endpoint": "x"},
                    {
                        "slot": "DeepSeek V4 Pro",
                        "provider": "openai-compatible",
                        "model": "deepseek-v4-pro",
                        "env_api_key": None,
                        "endpoint": "x",
                    },
                    {
                        "slot": "Gemini Pro",
                        "provider": "google-gemini",
                        "model": "gemini-flash-latest",
                        "env_api_key": None,
                        "endpoint": "x/{model}",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(source_harness, "call_all_agents", fake_call_all_agents)
    args = argparse.Namespace(
        config=config,
        env_file=tmp_path / ".env",
        shell_env_file=tmp_path / ".zshrc",
        output=tmp_path / "report.json",
        strict_agents=False,
        now="2026-06-08T10:00:00+00:00",
        json=False,
    )

    result = asyncio.run(source_harness.run_harness(args))
    report = json.loads(args.output.read_text(encoding="utf-8"))

    assert result == 0
    assert report["ready_count"] == 3
    assert report["active_agents"] == ["GPT 5.5", "DeepSeek V4 Pro", "Gemini Pro"]


def test_agent_source_harness_excludes_failed_preflight_slots_before_planning(tmp_path: Path, monkeypatch) -> None:
    async def fake_preflights(specs, *, timeout, contract):
        assert timeout == 30
        assert contract is True
        return [
            AgentPreflightResult(
                slot=spec.slot,
                provider=spec.provider,
                configured_model=spec.model,
                runtime_model=spec.model,
                method="mock",
                ok=spec.slot != "Gemini Pro",
                error="" if spec.slot != "Gemini Pro" else "HTTP Error 429: Too Many Requests",
            )
            for spec in specs
        ]

    async def fake_call_all_agents(prompt, *, specs, baseline_title_pct, timeout, allow_local_fallback, progress_callback=None):
        assert [spec.slot for spec in specs] == ["GPT 5.5", "DeepSeek V4 Pro", "Perplexity Pro"]
        return [
            AgentOpinion(
                agent=spec.slot,
                title_pct=8.0,
                summary="Plano com fonte verificável.",
                source_urls=[f"https://example.com/{spec.slot.lower().split()[0]}"],
            )
            for spec in specs
        ]

    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "baseline_title_pct": 8.0,
                "model_preflight_enabled": True,
                "model_preflight_contract_enabled": True,
                "doctor_preflight_timeout_seconds": 30,
                "exclude_slots_failing_preflight": True,
                "minimum_source_ready_agents": 3,
                "require_agent_source_plan": True,
                "agents": [
                    {"slot": "GPT 5.5", "provider": "openai", "model": "gpt-5.5", "env_api_key": None, "endpoint": "x"},
                    {
                        "slot": "DeepSeek V4 Pro",
                        "provider": "openai-compatible",
                        "model": "deepseek-v4-pro",
                        "env_api_key": None,
                        "endpoint": "x",
                    },
                    {
                        "slot": "Perplexity Pro",
                        "provider": "openai-compatible",
                        "model": "sonar-pro",
                        "env_api_key": None,
                        "endpoint": "x",
                    },
                    {
                        "slot": "Gemini Pro",
                        "provider": "google-gemini",
                        "model": "gemini-flash-latest",
                        "env_api_key": None,
                        "endpoint": "x/{model}",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(source_harness, "run_agent_preflights", fake_preflights)
    monkeypatch.setattr(source_harness, "call_all_agents", fake_call_all_agents)
    args = argparse.Namespace(
        config=config,
        env_file=tmp_path / ".env",
        shell_env_file=tmp_path / ".zshrc",
        output=tmp_path / "report.json",
        strict_agents=False,
        no_model_preflight=False,
        now="2026-06-08T10:00:00+00:00",
        json=False,
    )

    result = asyncio.run(source_harness.run_harness(args))
    report = json.loads(args.output.read_text(encoding="utf-8"))

    assert result == 0
    assert report["preflight_failed_slots"] == ["Gemini Pro"]
    assert report["active_agent_slots_after_preflight"] == ["GPT 5.5", "DeepSeek V4 Pro", "Perplexity Pro"]
    assert report["ready_count"] == 3

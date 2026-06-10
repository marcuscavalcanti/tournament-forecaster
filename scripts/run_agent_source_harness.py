#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldcup_brazil.agents import call_all_agents, load_agent_specs_from_config
from worldcup_brazil.cli import load_env_file
from worldcup_brazil.pipeline import (
    _apply_runtime_env_overrides,
    _sanitize_source_planning_opinions,
    _source_planning_prompt,
    _source_planning_readiness_report,
    load_config,
)


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnose agent source-planning quorum without rendering the full LinkedIn report."
    )
    parser.add_argument("--config", type=Path, default=Path("config/worldcup_brazil.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--shell-env-file", type=Path, default=Path.home() / ".zshrc")
    parser.add_argument("--output", type=Path, default=Path("outputs/agent_source_harness_latest.json"))
    parser.add_argument("--strict-agents", action="store_true")
    parser.add_argument("--now", help="ISO timestamp override for deterministic harness runs.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report to stdout.")
    return parser


async def run_harness(args: argparse.Namespace) -> int:
    load_env_file(args.env_file)
    load_env_file(args.shell_env_file)
    generated_at = _parse_datetime(args.now)
    config = load_config(args.config)
    _apply_runtime_env_overrides(config)
    baseline_title_pct = float(config.get("baseline_title_pct", 11.0))
    agent_specs = load_agent_specs_from_config(config)
    prompt = _source_planning_prompt(config=config, generated_at=generated_at)
    raw_opinions = await call_all_agents(
        prompt,
        specs=agent_specs,
        baseline_title_pct=baseline_title_pct,
        timeout=int(config.get("agent_timeout_seconds", 90)),
        allow_local_fallback=not args.strict_agents,
    )
    planning_opinions = _sanitize_source_planning_opinions(
        raw_opinions,
        baseline_title_pct=baseline_title_pct,
        config=config,
    )
    report = _source_planning_readiness_report(planning_opinions, config)
    report = {
        **report,
        "generated_at_iso": generated_at.isoformat(),
        "config": str(args.config),
        "agent_slots": [spec.slot for spec in agent_specs],
        "prompt_chars": len(prompt),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        active = ", ".join(report["active_agents"]) or "nenhum"
        print(f"quorum: {report['ready_count']}/{report['required_count']} ready")
        print(f"active: {active}")
        print(f"report: {args.output}")
        for entry in report["removed_agents"]:
            print(f"removed: {entry['agent']} - {entry['reason']}")

    return 0 if report["quorum_met"] else 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run_harness(args))


if __name__ == "__main__":
    raise SystemExit(main())

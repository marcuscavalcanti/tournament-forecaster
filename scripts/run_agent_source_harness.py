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

from worldcup_brazil.agents import (  # noqa: E402 - repository path bootstrap above
    call_all_agents,
    load_agent_specs_from_config,
    preflight_exclusion_slots,
    preflight_warning_slots,
    run_agent_preflights,
)
from worldcup_brazil.cli import _bridges_enabled, load_env_file  # noqa: E402
from worldcup_brazil.pipeline import (  # noqa: E402 - repository path bootstrap above
    _apply_runtime_env_overrides,
    _sanitize_source_planning_opinions,
    _specs_after_preflight_exclusion,
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
    parser.add_argument("--env-file", type=Path, help="Trusted dotenv file loaded only when provided.")
    parser.add_argument("--shell-env-file", type=Path, help="Trusted shell-style env file loaded only when provided.")
    bridge_group = parser.add_mutually_exclusive_group()
    bridge_group.add_argument("--bridges", dest="bridges", action="store_true")
    bridge_group.add_argument("--no-bridges", dest="bridges", action="store_false")
    parser.set_defaults(bridges=None)
    parser.add_argument("--output", type=Path, default=Path("outputs/agent_source_harness_latest.json"))
    parser.add_argument("--strict-agents", action="store_true")
    parser.add_argument("--no-model-preflight", action="store_true", help="Skip preflight exclusion in the harness.")
    parser.add_argument("--now", help="ISO timestamp override for deterministic harness runs.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report to stdout.")
    return parser


def _doctor_source_planning_prompt(config: dict, generated_at: datetime) -> str:
    group_matches = [
        f"{match.get('opponent')} ({match.get('venue', 'local indefinido')})"
        for match in config.get("group_matches", [])
    ]
    knockout_phases = [
        f"{match.get('phase')}: {match.get('opponent')}"
        for match in config.get("knockout_matches", [])[:10]
    ]
    return (
        "Doctor rápido do pipeline World Cup 2026. Não feche consenso; apenas prove que você consegue "
        "chegar munido de fontes próprias e frescas para a sala. Responda SOMENTE JSON válido com campos: "
        "self_identification{name,version}, title_pct, summary, opening_argument, critique, adjustment, "
        "source_urls, source_queries, team_context_signals. Regras: sem cache; o mediador não busca dados; "
        "cada modelo escolhe as próprias fontes; dados da Opta não contam e não devem aparecer em "
        "source_urls/source_queries; não invente URL, ranking, lesão, odd, score ou método. "
        "Aqui 'fontes/source' significa fonte de INFORMAÇÃO esportiva verificável; não significa fonte tipográfica, "
        "camisa, uniforme, design, Instagram/Reels, YouTube visual ou identidade visual. Use fontes "
        "quantitativas e qualitativas como achar melhor, sem percentual fixo entre elas. Cubra Brasil e "
        "adversários/cenários com pelo menos 3 famílias entre odds/mercados, Elo/FIFA/ratings, "
        "Sofascore/performance, lesões/cortes/cartões, arbitragem/VAR, descanso/logística, imprensa "
        "especializada e Transfermarkt. Inclua pelo menos 2 source_urls HTTP ou 2 source_queries específicas "
        "não-Opta. "
        f"Data do run: {generated_at.isoformat()}. "
        f"Grupo: {config.get('group_name', 'Grupo não informado')} contra {', '.join(group_matches) or 'não informado'}. "
        f"Cenários de mata-mata configurados: {', '.join(knockout_phases) or 'não informado'}."
    )


async def run_harness(args: argparse.Namespace) -> int:
    load_env_file(args.env_file)
    load_env_file(args.shell_env_file)
    generated_at = _parse_datetime(args.now)
    config = load_config(args.config)
    config["_bridges_enabled"] = _bridges_enabled(
        config,
        cli_override=getattr(args, "bridges", None),
    )
    _apply_runtime_env_overrides(config)
    baseline_title_pct = float(config.get("baseline_title_pct", 11.0))
    agent_specs = load_agent_specs_from_config(
        config,
        bridges_enabled=bool(config["_bridges_enabled"]),
    )
    original_agent_slots = [spec.slot for spec in agent_specs]
    preflight_results = []
    preflight_failed_slots: list[str] = []
    preflight_warning_slots_list: list[str] = []
    if not getattr(args, "no_model_preflight", False) and bool(config.get("model_preflight_enabled", True)):
        timeout = int(config.get("doctor_preflight_timeout_seconds", config.get("model_preflight_timeout_seconds", 180)))
        contract_preflight = bool(config.get("model_preflight_contract_enabled", True))
        preflight_results = await run_agent_preflights(
            agent_specs,
            timeout=timeout,
            contract=contract_preflight,
        )
        preflight_failed_slots = preflight_exclusion_slots(preflight_results)
        preflight_warning_slots_list = preflight_warning_slots(preflight_results)
        if (
            preflight_failed_slots
            and not args.strict_agents
            and bool(config.get("exclude_slots_failing_preflight", True))
        ):
            config["_preflight_failed_slots"] = preflight_failed_slots
            agent_specs = _specs_after_preflight_exclusion(agent_specs, config)
    prompt = _doctor_source_planning_prompt(config=config, generated_at=generated_at)
    raw_opinions = await call_all_agents(
        prompt,
        specs=agent_specs,
        baseline_title_pct=baseline_title_pct,
        timeout=int(config.get("doctor_agent_timeout_seconds", config.get("agent_timeout_seconds", 90))),
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
        "agent_slots": original_agent_slots,
        "active_agent_slots_after_preflight": [spec.slot for spec in agent_specs],
        "preflight_failed_slots": preflight_failed_slots,
        "preflight_warning_slots": preflight_warning_slots_list,
        "preflight_results": [
            {
                "slot": result.slot,
                "provider": result.provider,
                "configured_model": result.configured_model,
                "runtime_model": result.runtime_model,
                "method": result.method,
                "ok": result.ok,
                "declared_name": result.declared_name,
                "declared_version": result.declared_version,
                "message": result.message,
                "error": result.error,
                "elapsed_ms": result.elapsed_ms,
            }
            for result in preflight_results
        ],
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
        if preflight_results:
            ok_count = sum(1 for result in preflight_results if result.ok)
            print(f"preflight: {ok_count}/{len(preflight_results)} ok")
            if preflight_failed_slots:
                print(f"preflight_removed: {', '.join(preflight_failed_slots)}")
        print(f"report: {args.output}")
        for entry in report["removed_agents"]:
            print(f"removed: {entry['agent']} - {entry['reason']}")

    return 0 if report["quorum_met"] else 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run_harness(args))


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from worldcup_brazil.agents import (
    load_agent_specs_from_config,
    render_agent_preflight_stdout,
    run_agent_preflights_sync,
)
from worldcup_brazil.bracket import brazil_bracket_path, invalid_configured_knockout_opponents
from worldcup_brazil.calibration import append_prediction_log, prediction_records_from_bundle
from worldcup_brazil.pipeline import (
    MeetingConsensusError,
    ReportCoherenceError,
    SourcePlanningQuorumError,
    build_report_bundle_sync,
    load_config,
)
from worldcup_brazil.renderer import render_audit_report, render_decision_flow_svg
from worldcup_brazil.scheduler import RunState, should_run
from worldcup_brazil.source_memory import SourceMemory
from worldcup_brazil.watchdog import RunWatchdog


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        cleaned = value.strip().strip('"').strip("'")
        os.environ[key] = cleaned


def _effective_config_path(path: Path) -> Path:
    if path.exists():
        return path
    if path.name == "worldcup_brazil.json":
        example_path = path.with_name("worldcup_brazil.example.json")
        if example_path.exists():
            return example_path
    return path


def _config_agent_slots(config: dict) -> list[str]:
    agents = config.get("agents")
    if not isinstance(agents, list):
        return []
    return [
        str(agent.get("slot", "")).strip()
        for agent in agents
        if isinstance(agent, dict) and str(agent.get("slot", "")).strip()
    ]


def _config_watchdog_extra(
    config: dict,
    *,
    requested_config_path: Path,
    effective_config_path: Path,
    strict_agents: bool,
) -> dict:
    group_matches = config.get("group_matches") if isinstance(config.get("group_matches"), list) else []
    knockout_matches = config.get("knockout_matches") if isinstance(config.get("knockout_matches"), list) else []
    bracket_path = brazil_bracket_path(config)
    bracket_errors = invalid_configured_knockout_opponents(config)
    return {
        "paths": {
            "requested_config": str(requested_config_path),
            "effective_config": str(effective_config_path),
            "used_example_fallback": requested_config_path != effective_config_path,
            "groups_config": str(config.get("_groups_config_path") or config.get("groups_config_path") or ""),
            "bracket_config": str(config.get("_bracket_config_path") or config.get("bracket_config_path") or ""),
        },
        "watchdog_config": {
            "top_level_keys": len(config),
            "custom_hashtag": config.get("custom_hashtag"),
            "baseline_title_pct": config.get("baseline_title_pct"),
            "group_name": config.get("group_name"),
            "brazil_group": config.get("brazil_group"),
            "brazil_expected_group_position": config.get("brazil_expected_group_position"),
            "enforce_bracket_constraints": config.get("enforce_bracket_constraints", True),
            "bracket_uncertainty_ci_widening": config.get("bracket_uncertainty_ci_widening", True),
            "uncertainty": config.get("uncertainty", {}),
            "monte_carlo_uncertainty": {
                "confidence_level": (config.get("monte_carlo") or {}).get("confidence_level"),
                "rating_uncertainty_enabled": (config.get("monte_carlo") or {}).get(
                    "rating_uncertainty_enabled",
                    False,
                ),
                "rating_uncertainty_outer_samples": (config.get("monte_carlo") or {}).get(
                    "rating_uncertainty_outer_samples"
                ),
                "rating_uncertainty_inner_iterations": (config.get("monte_carlo") or {}).get(
                    "rating_uncertainty_inner_iterations"
                ),
            },
            "model_preflight_contract_enabled": config.get("model_preflight_contract_enabled", True),
            "model_preflight_timeout_seconds": config.get("model_preflight_timeout_seconds", 180),
            "minimum_source_ready_agents": config.get("minimum_source_ready_agents", 3),
            "source_planning_repair_attempts": config.get("source_planning_repair_attempts", 2),
            "repair_format_removals_with_quorum": config.get("repair_format_removals_with_quorum", True),
            "source_planning_format_repair_timeout_seconds": config.get(
                "source_planning_format_repair_timeout_seconds",
                min(90, int(config.get("agent_timeout_seconds", 90))),
            ),
            "meeting_response_repair_attempts": config.get("meeting_response_repair_attempts", 1),
            "meeting_min_rounds": config.get("meeting_min_rounds"),
            "meeting_max_rounds": config.get("meeting_max_rounds"),
            "meeting_min_participants": config.get(
                "meeting_min_participants",
                config.get("meeting_min_real_agents", 3),
            ),
            "meeting_quorum_rule": "maioria simples dos participantes ativos da sala",
            "meeting_require_full_path_coverage": config.get("meeting_require_full_path_coverage", True),
            "parallel_opponent_debriefing_enabled": config.get("parallel_opponent_debriefing_enabled", False),
            "agent_timeout_seconds": config.get("agent_timeout_seconds", 90),
            "agent_reentry_probe_enabled": config.get("agent_reentry_probe_enabled", False),
            "agent_reentry_probe_timeout_seconds": config.get("agent_reentry_probe_timeout_seconds", 180),
            "strict_agents": strict_agents,
            "require_agent_source_plan": config.get("require_agent_source_plan", True),
            "require_auditable_source_urls_for_meeting_votes": config.get(
                "require_auditable_source_urls_for_meeting_votes",
                True,
            ),
        },
        "agents": {
            "count": len(_config_agent_slots(config)),
            "slots": _config_agent_slots(config),
        },
        "scope": {
            "group_matches_count": len(group_matches),
            "knockout_matches_count": len(knockout_matches),
            "group_opponents": [str(match.get("opponent", "")) for match in group_matches if isinstance(match, dict)],
            "knockout_phases": [
                str(match.get("phase", "Mata-mata"))
                for match in knockout_matches
                if isinstance(match, dict)
            ],
            "bracket_path": [
                {
                    "phase": entry.get("phase"),
                    "match_id": entry.get("match_id"),
                    "brazil_slot": entry.get("brazil_slot"),
                    "opponent_slots": entry.get("opponent_slots"),
                    "allowed_opponent_groups": entry.get("allowed_opponent_groups"),
                    "allowed_opponents": entry.get("allowed_opponents"),
                }
                for entry in bracket_path
            ],
            "bracket_validation_errors": bracket_errors,
        },
    }


def _config_watchdog_detail(
    config: dict,
    *,
    requested_config_path: Path,
    effective_config_path: Path,
    strict_agents: bool,
) -> str:
    extra = _config_watchdog_extra(
        config,
        requested_config_path=requested_config_path,
        effective_config_path=effective_config_path,
        strict_agents=strict_agents,
    )
    cfg = extra["watchdog_config"]
    agents = extra["agents"]
    scope = extra["scope"]
    return (
        f"{cfg['top_level_keys']} top-level config keys; "
        f"effective={extra['paths']['effective_config']}; "
        f"hashtag={cfg['custom_hashtag']}; "
        f"baseline_title_pct={cfg['baseline_title_pct']}; "
        f"brazil_group={cfg['brazil_group']}; "
        f"brazil_position={cfg['brazil_expected_group_position']}; "
        f"bracket_constraints={cfg['enforce_bracket_constraints']}; "
        f"confidence_level={(cfg['uncertainty'] or {}).get('confidence_level', 's/d')}; "
        f"rating_uncertainty={cfg['monte_carlo_uncertainty'].get('rating_uncertainty_enabled')}; "
        f"preflight_timeout_s={cfg['model_preflight_timeout_seconds']}; "
        f"quorum_min={cfg['minimum_source_ready_agents']}; "
        f"repair_attempts={cfg['source_planning_repair_attempts']}; "
        f"meeting_min_participants={cfg['meeting_min_participants']}; "
        f"meeting_quorum_rule={cfg['meeting_quorum_rule']}; "
        f"full_path_coverage={cfg['meeting_require_full_path_coverage']}; "
        f"parallel_opponent_room={cfg['parallel_opponent_debriefing_enabled']}; "
        f"reentry_probe={cfg['agent_reentry_probe_enabled']}; "
        f"reentry_timeout_s={cfg['agent_reentry_probe_timeout_seconds']}; "
        f"agents={agents['count']}; "
        f"group_matches={scope['group_matches_count']}; "
        f"knockout_scenarios={scope['knockout_matches_count']}; "
        f"strict_agents={strict_agents}"
    )


def _match_estimate_to_json(match) -> dict:
    return {
        "brazil": getattr(match, "brazil", ""),
        "opponent": getattr(match, "opponent", ""),
        "phase": getattr(match, "phase", ""),
        "brazil_pct": getattr(match, "brazil_pct", None),
        "opponent_pct": getattr(match, "opponent_pct", None),
        "draw_pct": getattr(match, "draw_pct", None),
        "match_date": getattr(match, "match_date", None),
        "brazil_ci_low": getattr(match, "brazil_ci_low", None),
        "brazil_ci_high": getattr(match, "brazil_ci_high", None),
        "most_likely": getattr(match, "most_likely", None),
        "venue": getattr(match, "venue", None),
        "scenario_pct": getattr(match, "scenario_pct", None),
        "rationale": getattr(match, "rationale", ""),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the Brazil World Cup 2026 LinkedIn forecast post."
    )
    parser.add_argument("--config", type=Path, default=Path("config/worldcup_brazil.json"))
    parser.add_argument("--state", type=Path, default=Path("data/run_state.json"))
    parser.add_argument("--source-memory", type=Path, default=Path("data/source_memory.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--watchdog-log", type=Path, default=Path("data/watchdog.jsonl"))
    parser.add_argument("--calibration-log", type=Path, default=Path("data/calibration_predictions.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Optional dotenv file loaded before agent setup.")
    parser.add_argument(
        "--shell-env-file",
        type=Path,
        default=Path.home() / ".zshrc",
        help="Optional shell env file loaded after .env; supports simple export KEY=value lines.",
    )
    parser.add_argument("--no-watchdog", action="store_true", help="Disable watchdog JSONL events.")
    parser.add_argument("--quiet-watchdog", action="store_true", help="Write watchdog JSONL without stderr progress lines.")
    parser.add_argument("--force", action="store_true", help="Run even if the three-day interval has not elapsed.")
    parser.add_argument("--strict-agents", action="store_true", help="Fail instead of using local fallback for missing agent APIs.")
    parser.add_argument("--no-model-preflight", action="store_true", help="Skip the startup model smoke test.")
    parser.add_argument("--now", help="ISO timestamp override for deterministic tests/runs.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = _parse_datetime(args.now)
    state = RunState(args.state)
    watchdog = None if args.no_watchdog else RunWatchdog(path=args.watchdog_log, verbose=not args.quiet_watchdog)
    if watchdog:
        watchdog.start("run", detail="starting World Cup Brazil report job")
    interval = timedelta(days=3)
    if not args.force and not should_run(state, now=now, interval=interval):
        last = state.last_success_at()
        if watchdog:
            watchdog.finish("run", detail=f"skipped; last successful run at {last.isoformat() if last else 'never'}")
        print(f"skip: last successful run at {last.isoformat() if last else 'never'}")
        return 0

    try:
        load_env_file(args.env_file)
        load_env_file(args.shell_env_file)
        if watchdog:
            watchdog.start("load_config", detail=str(args.config))
        config = load_config(args.config)
        if watchdog:
            effective_config_path = _effective_config_path(args.config)
            watchdog.finish(
                "load_config",
                detail=_config_watchdog_detail(
                    config,
                    requested_config_path=args.config,
                    effective_config_path=effective_config_path,
                    strict_agents=args.strict_agents,
                ),
                extra=_config_watchdog_extra(
                    config,
                    requested_config_path=args.config,
                    effective_config_path=effective_config_path,
                    strict_agents=args.strict_agents,
                ),
            )
        if not args.no_model_preflight and bool(config.get("model_preflight_enabled", True)):
            timeout = int(config.get("model_preflight_timeout_seconds", 180))
            contract_preflight = bool(config.get("model_preflight_contract_enabled", True))
            if watchdog:
                watchdog.start(
                    "model_preflight",
                    detail=f"testing configured model slots; timeout_s={timeout}; contract={contract_preflight}",
                )
            preflight_results = run_agent_preflights_sync(
                load_agent_specs_from_config(config),
                timeout=timeout,
                contract=contract_preflight,
            )
            print(render_agent_preflight_stdout(preflight_results), flush=True)
            if watchdog:
                watchdog.finish(
                    "model_preflight",
                    detail=(
                        f"{sum(1 for result in preflight_results if result.ok)}/"
                        f"{len(preflight_results)} model smoke test(s) ok"
                    ),
                    extra={
                        "results": [
                            {
                                "slot": result.slot,
                                "provider": result.provider,
                                "method": result.method,
                                "configured_model": result.configured_model,
                                "runtime_model": result.runtime_model,
                                "ok": result.ok,
                                "declared_name": result.declared_name,
                                "declared_version": result.declared_version,
                                "message": result.message,
                                "error": result.error,
                                "elapsed_ms": result.elapsed_ms,
                            }
                            for result in preflight_results
                        ]
                    },
                )
            failed_preflight_slots = [result.slot for result in preflight_results if not result.ok]
            if (
                failed_preflight_slots
                and not args.strict_agents
                and bool(config.get("exclude_slots_failing_preflight", True))
            ):
                config["_preflight_failed_slots"] = failed_preflight_slots
        memory = SourceMemory(args.source_memory)
        artifacts = build_report_bundle_sync(
            config=config,
            source_memory=memory,
            generated_at=now,
            allow_agent_fallback=not args.strict_agents,
            watchdog=watchdog,
        )

        if watchdog:
            watchdog.start("write_outputs", detail=str(args.output_dir))
        args.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
        post_path = args.output_dir / f"linkedin_brazil_{stamp}.md"
        json_path = args.output_dir / f"linkedin_brazil_{stamp}.json"
        audit_path = args.output_dir / f"audit_brazil_{stamp}.md"
        graph_path = args.output_dir / f"decision_flow_brazil_{stamp}.svg"
        post_path.write_text(artifacts.post, encoding="utf-8")
        audit_path.write_text(render_audit_report(artifacts.bundle), encoding="utf-8")
        graph_path.write_text(render_decision_flow_svg(artifacts.bundle), encoding="utf-8")
        json_path.write_text(
            json.dumps(
                {
                    "bundle": {
                        "generated_at_iso": artifacts.bundle.generated_at_iso,
                        "group_matches": [
                            _match_estimate_to_json(match)
                            for match in artifacts.bundle.group_matches
                        ],
                        "knockout_matches": [
                            _match_estimate_to_json(match)
                            for match in artifacts.bundle.knockout_matches
                        ],
                        "stage_probabilities": artifacts.bundle.stage_probabilities,
                        "stage_confidence_intervals": artifacts.bundle.stage_confidence_intervals,
                        "sources": artifacts.bundle.sources,
                        "warnings": artifacts.bundle.warnings,
                        "debate_transcript": artifacts.bundle.debate_transcript,
                        "meeting_transcript": artifacts.bundle.meeting_transcript,
                        "source_plan_by_model": artifacts.bundle.source_plan_by_model,
                        "model_influence_pct": artifacts.bundle.model_influence_pct,
                        "model_participation": artifacts.bundle.model_participation,
                        "model_token_costs": artifacts.bundle.model_token_costs,
                        "agent_effort_profiles": artifacts.bundle.agent_effort_profiles,
                        "model_predictions_no_opta": artifacts.bundle.model_predictions_no_opta,
                        "opta_benchmark": artifacts.bundle.opta_benchmark,
                        "model_vs_opta": artifacts.bundle.model_vs_opta,
                        "metadata": artifacts.bundle.metadata,
                    },
                    "evidence": [
                        {
                            "source": result.source.name,
                            "ok": result.ok,
                            "error": result.error,
                        }
                        for result in artifacts.raw_evidence
                    ],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        calibration_records = prediction_records_from_bundle(
            artifacts.bundle,
            run_id=watchdog.run_id if watchdog else now.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
            artifact_path=str(json_path),
        )
        append_prediction_log(args.calibration_log, calibration_records)
        state.mark_success(now)
        if watchdog:
            watchdog.finish(
                "write_outputs",
                detail=(
                    f"wrote {post_path.name}, {json_path.name}, {audit_path.name}, {graph_path.name}; "
                    f"calibration_log={args.calibration_log}"
                ),
                extra={"calibration_records": len(calibration_records), "calibration_log": str(args.calibration_log)},
            )
            watchdog.finish("run", detail="completed successfully")
        print(f"post: {post_path}")
        print(f"json: {json_path}")
        print(f"audit: {audit_path}")
        print(f"graph: {graph_path}")
    except (SourcePlanningQuorumError, ReportCoherenceError, MeetingConsensusError) as exc:
        if watchdog:
            watchdog.fail("run", detail=str(exc))
        print(f"fail: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if watchdog:
            watchdog.fail("run", detail=str(exc))
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

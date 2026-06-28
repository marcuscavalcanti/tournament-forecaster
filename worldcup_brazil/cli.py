from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl  # POSIX-only; ausente no Windows.
except ImportError:  # pragma: no cover - plataforma sem fcntl
    fcntl = None

from worldcup_brazil.agents import (
    load_agent_specs_from_config,
    preflight_exclusion_slots,
    preflight_warning_slots,
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
from worldcup_brazil.post_template import (
    _parse_template_match_date,
    apply_editor_append,
    bundle_from_json,
    render_template_post,
    validate_template_post,
)
from worldcup_brazil.renderer import render_audit_report, render_decision_flow_svg
from worldcup_brazil.scheduler import RunState, should_run
from worldcup_brazil.source_memory import SourceMemory
from worldcup_brazil.watchdog import RunWatchdog


def _run_post_editor_append(config: dict, base_text: str, *, watchdog: RunWatchdog | None) -> str:
    """Editor opcional do post de template: só pode fazer APPEND (emojis, insights
    pontuais). Qualquer mutação do esqueleto é descartada pelo apply_editor_append."""
    import asyncio

    from worldcup_brazil.agents import call_agent

    slot = str(config.get("post_editor_agent_slot", "Opus 4.8"))
    specs = [spec for spec in load_agent_specs_from_config(config) if spec.slot == slot]
    if not specs:
        return base_text
    prompt = (
        "Você é o editor final de um post de LinkedIn gerado por template fixo. REGRAS INVIOLÁVEIS: "
        "(1) devolva o texto ORIGINAL na íntegra, byte a byte, sem alterar uma vírgula; "
        "(2) você só pode ACRESCENTAR, ao final, 1 a 3 linhas curtas com emojis/insights pontuais sobre os jogos, "
        "coerentes com os números do próprio post; (3) o total não pode passar de 3000 caracteres; "
        "(4) se não tiver o que acrescentar, devolva o texto original puro. Não use markdown de bloco, não comente, "
        "não explique: devolva apenas o post.\n\n---\n" + base_text
    )
    try:
        opinion = asyncio.run(
            call_agent(
                specs[0],
                prompt,
                baseline_title_pct=float(config.get("baseline_title_pct", 11.0)),
                timeout=int(config.get("post_editor_timeout_seconds", 120)),
                allow_local_fallback=False,
            )
        )
        edited = str(getattr(opinion, "raw_text", "") or "")
    except Exception as exc:  # noqa: BLE001 - editor é opcional; falha não derruba o run
        if watchdog:
            watchdog.event("post_editor", "fail", detail=str(exc)[:200])
        return base_text
    final_text = apply_editor_append(base_text, edited)
    if watchdog:
        watchdog.event(
            "post_editor",
            "finish",
            detail="append aceito" if final_text != base_text else "sem append (texto original mantido)",
        )
    return final_text


def _comparison_matchday_stamp(current_bundle: object | None) -> str | None:
    if current_bundle is None:
        return None
    raw = str(getattr(current_bundle, "generated_at_iso", "") or "").strip()
    try:
        run_date = datetime.fromisoformat(raw).date()
    except ValueError:
        return None
    year = run_date.year
    dated: list[date] = []
    for match in list(getattr(current_bundle, "group_matches", []) or []) + list(
        getattr(current_bundle, "knockout_matches", []) or []
    ):
        parsed = _parse_template_match_date(getattr(match, "match_date", ""), year=year)
        if parsed is not None and parsed < run_date:
            dated.append(parsed)
    if not dated:
        return None
    return max(dated).isoformat()


def _previous_template_bundle(
    output_dir: Path,
    current_json_path: Path,
    *,
    current_bundle: object | None = None,
) -> object | None:
    matchday_stamp = _comparison_matchday_stamp(current_bundle)
    if matchday_stamp:
        preferred = output_dir / f"linkedin_brazil_{matchday_stamp}.json"
        if preferred.exists() and preferred.name != current_json_path.name:
            try:
                return bundle_from_json(preferred)
            except Exception:  # noqa: BLE001 - comparação histórica não pode derrubar o post
                pass
    candidates = [
        path
        for path in sorted(output_dir.glob("linkedin_brazil_*.json"))
        if path.name != current_json_path.name
    ]
    if not candidates:
        return None
    try:
        return bundle_from_json(candidates[-1])
    except Exception:  # noqa: BLE001 - comparação histórica não pode derrubar o post
        return None


def _acquire_run_lock(path: Path):
    """Adquire lock exclusivo não-bloqueante em ``path`` e devolve o fd aberto.

    Retorna ``None`` se outro run já segura o lock (BlockingIOError/OSError) ou se
    fcntl não estiver disponível (Windows). O chamador DEVE manter o fd vivo
    durante todo o run — fechar/coletar libera o lock. Sem isso, 3 runs no mesmo
    dia gastam o dobro, sobrescrevem artefatos date-stamped e rasgam o
    read-modify-write da calibração.
    """
    if fcntl is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        os.close(fd)
        return None
    return fd


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _bundle_output_stamp(bundle, fallback: datetime) -> str:
    raw = str(getattr(bundle, "generated_at_iso", "") or "").strip()
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        parsed = fallback
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d")


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
            "repair_reentry_eligible_removals_before_meeting": config.get(
                "repair_reentry_eligible_removals_before_meeting",
                config.get("repair_reentry_eligible_removals_at_quorum_floor", True),
            ),
            "source_planning_floor_repair_timeout_seconds": config.get(
                "source_planning_floor_repair_timeout_seconds",
                config.get("agent_timeout_seconds", 90),
            ),
            "blind_peer_review_enabled": config.get("blind_peer_review_enabled", False),
            "blind_peer_review_shadow_only": config.get("blind_peer_review_shadow_only", True),
            "blind_peer_review_on_consensus_exit": config.get("blind_peer_review_on_consensus_exit", True),
            "blind_peer_review_timeout_seconds": config.get("blind_peer_review_timeout_seconds", 90),
            "blind_peer_review_acceptance_threshold": config.get("blind_peer_review_acceptance_threshold", 0.72),
            "blind_peer_review_max_self_preference_leakage": config.get(
                "blind_peer_review_max_self_preference_leakage",
                0.20,
            ),
            "numeric_chairman_enabled": config.get("numeric_chairman_enabled", True),
            "llm_council_fast_path_enabled": config.get("llm_council_fast_path_enabled", False),
            "llm_council_fast_path_shadow_only": config.get("llm_council_fast_path_shadow_only", True),
            "llm_council_fast_path_min_participants": config.get("llm_council_fast_path_min_participants", 3),
            "meeting_response_repair_attempts": config.get("meeting_response_repair_attempts", 1),
            "max_agent_title_shift_pct": config.get("max_agent_title_shift_pct", 5.0),
            "max_agent_title_shift_with_sources_pct": config.get("max_agent_title_shift_with_sources_pct", 8.0),
            "max_agent_title_pct_abs_cap": config.get("max_agent_title_pct_abs_cap", 25.0),
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
        f"pre_meeting_repair={cfg['repair_reentry_eligible_removals_before_meeting']}; "
        f"meeting_min_participants={cfg['meeting_min_participants']}; "
        f"meeting_quorum_rule={cfg['meeting_quorum_rule']}; "
        f"numeric_chairman={cfg['numeric_chairman_enabled']}; "
        f"fast_path={cfg['llm_council_fast_path_enabled']}; "
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
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path("data/.run.lock"),
        help="Exclusive run lock path used to prevent concurrent daily runs.",
    )
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
    run_lock_fd = _acquire_run_lock(args.lock_file)
    if run_lock_fd is None and fcntl is not None:
        print(f"skip: outro run ja esta em andamento (lock {args.lock_file})", file=sys.stderr)
        return 0
    try:
        return _run(args)
    finally:
        # Libera o lock só no fim do run (ou da falha). Mantê-lo durante todo o
        # processamento é o que bloqueia runs concorrentes; fechar aqui evita
        # vazar o fd entre invocações no mesmo processo.
        if run_lock_fd is not None:
            os.close(run_lock_fd)


def _run(args: argparse.Namespace) -> int:
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
            failed_preflight_slots = preflight_exclusion_slots(preflight_results)
            warning_preflight_slots = preflight_warning_slots(preflight_results)
            if warning_preflight_slots:
                config["_preflight_warning_slots"] = warning_preflight_slots
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
        stamp = _bundle_output_stamp(artifacts.bundle, now)
        post_path = args.output_dir / f"linkedin_brazil_{stamp}.md"
        json_path = args.output_dir / f"linkedin_brazil_{stamp}.json"
        audit_path = args.output_dir / f"audit_brazil_{stamp}.md"
        graph_path = args.output_dir / f"decision_flow_brazil_{stamp}.svg"
        # Resiliência: o JSON é o ÚNICO registro em disco de meeting_transcript,
        # model_influence_pct e model_token_costs (~US$6,43 de debate). É escrito
        # PRIMEIRO; se qualquer render (post/audit/svg/template) estourar depois, o
        # registro já está salvo. Caminho feliz idêntico; só muda a ORDEM.
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
                        "group_name": getattr(artifacts.bundle, "group_name", ""),
                        "group_summary": getattr(artifacts.bundle, "group_summary", ""),
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
        post_path.write_text(artifacts.post, encoding="utf-8")
        audit_path.write_text(render_audit_report(artifacts.bundle), encoding="utf-8")
        graph_path.write_text(render_decision_flow_svg(artifacts.bundle), encoding="utf-8")
        template_post_path = args.output_dir / f"linkedin_post_brazil_{stamp}.md"
        try:
            post_index = (
                len(
                    [
                        p
                        for p in args.output_dir.glob("linkedin_post_brazil_*.md")
                        if p.name != template_post_path.name
                    ]
                )
                + 1
            )
            template_post = render_template_post(
                artifacts.bundle,
                post_index=post_index,
                previous_bundle=_previous_template_bundle(
                    args.output_dir,
                    json_path,
                    current_bundle=artifacts.bundle,
                ),
            )
            if bool(config.get("post_editor_enabled", False)):
                template_post = _run_post_editor_append(config, template_post, watchdog=watchdog)
            validate_template_post(template_post, artifacts.bundle)
            template_post_path.write_text(template_post, encoding="utf-8")
        except ValueError as exc:
            template_post_path = None
            if watchdog:
                watchdog.event("post_template", "fail", detail=str(exc)[:200])
            print(f"aviso: post de template não gerado ({exc})", file=sys.stderr)
        # Calibração é best-effort: uma falha aqui (ex.: log corrompido, disco
        # cheio) NÃO pode impedir state.mark_success — senão o run de US$6,43 é
        # marcado como "não-feito" e roda de novo no próximo agendamento.
        calibration_records: list = []
        try:
            calibration_records = prediction_records_from_bundle(
                artifacts.bundle,
                run_id=watchdog.run_id if watchdog else now.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
                artifact_path=str(json_path),
            )
            append_prediction_log(args.calibration_log, calibration_records)
        except Exception as exc:  # noqa: BLE001 - calibração não derruba o run
            calibration_records = []
            if watchdog:
                watchdog.event("calibration", "fail", detail=str(exc)[:200])
            print(f"aviso: calibração não registrada ({exc})", file=sys.stderr)
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
        if template_post_path is not None:
            print(f"linkedin: {template_post_path}")
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

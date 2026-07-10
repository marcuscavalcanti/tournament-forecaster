from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PHASE_ORDER = ("16 avos", "Oitavas", "Quartas", "Semifinal", "Final")


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "s/d"


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _response_status(response: dict[str, Any]) -> str:
    if response.get("used_fallback"):
        return "removida/fallback"
    if response.get("disagreed"):
        return "discorda"
    if response.get("accepted", float(response.get("support_score", 0.0) or 0.0) >= 0.72):
        return "aceita"
    return "contestada"


def _phase_rank(phase: str) -> int:
    try:
        return PHASE_ORDER.index(phase)
    except ValueError:
        return len(PHASE_ORDER)


def _split_phase_key(key: str) -> tuple[str, str]:
    if ":" not in key:
        return "Fase nao informada", key.strip()
    phase, opponent = key.split(":", 1)
    return phase.strip(), opponent.strip()


def find_latest_run_json(output_dir: Path) -> Path:
    candidates = sorted(output_dir.glob("linkedin_brazil_*.json"))
    if not candidates:
        raise FileNotFoundError(f"Nenhum linkedin_brazil_*.json encontrado em {output_dir}")
    return candidates[-1]


def _maybe_find_latest_run_json(output_dir: Path) -> Path | None:
    try:
        return find_latest_run_json(output_dir)
    except FileNotFoundError:
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _payload_generated_at(path: Path | None) -> datetime | None:
    if path is None:
        return None
    try:
        payload = load_run_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    bundle = _bundle(payload)
    return _parse_timestamp(bundle.get("generated_at_iso"))


def load_watchdog_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def latest_watchdog_run(events: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]] | None:
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        run_id = str(event.get("run_id", "") or "").strip()
        if run_id:
            by_run[run_id].append(event)
    if not by_run:
        return None
    return sorted(by_run.items(), key=lambda item: str(item[1][-1].get("timestamp", "")))[-1]


def _run_last_timestamp(run: list[dict[str, Any]]) -> datetime | None:
    if not run:
        return None
    return _parse_timestamp(run[-1].get("timestamp"))


def _run_failed(run: list[dict[str, Any]]) -> bool:
    return any(event.get("step") == "run" and event.get("status") == "fail" for event in run)


def latest_failed_watchdog_run_after_json(
    *,
    watchdog_log: Path,
    latest_json: Path | None,
) -> tuple[str, list[dict[str, Any]]] | None:
    latest_run = latest_watchdog_run(load_watchdog_events(watchdog_log))
    if latest_run is None:
        return None
    _run_id, run = latest_run
    if not _run_failed(run):
        return None
    run_ts = _run_last_timestamp(run)
    json_ts = _payload_generated_at(latest_json)
    if run_ts is None:
        return None
    if json_ts is None or run_ts > json_ts:
        return latest_run
    return None


def load_run_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _bundle(payload: dict[str, Any]) -> dict[str, Any]:
    bundle = payload.get("bundle", payload)
    return bundle if isinstance(bundle, dict) else {}


def _participants_from_transcript(transcript: list[dict[str, Any]]) -> list[str]:
    participants: list[str] = []
    for turn in transcript:
        protagonist = str(turn.get("protagonist", "")).strip()
        if protagonist and protagonist not in participants:
            participants.append(protagonist)
        for response in turn.get("responses", []) or []:
            agent = str(response.get("agent", "")).strip()
            if agent and agent not in participants:
                participants.append(agent)
    return participants


def _probability_rows_from_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        phase = str(match.get("phase", "") or "Fase nao informada").strip()
        if phase not in PHASE_ORDER:
            continue
        opponent = str(match.get("opponent", "") or "Adversario a definir").strip()
        if opponent.lower() in {"outros", "other", "others"}:
            continue
        rows.append(
            {
                "phase": phase,
                "opponent": opponent,
                "scenario_pct": match.get("scenario_pct"),
                "brazil_pct": match.get("brazil_pct"),
                "most_likely": match.get("most_likely"),
                "venue": match.get("venue", ""),
                "source": "knockout_matches serializado",
            }
        )
    return rows


def _probability_rows_from_transcript(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for turn in transcript:
        round_index = int(turn.get("round", 0) or 0)
        for response in turn.get("responses", []) or []:
            agent = str(response.get("agent", "") or "")
            scenario_probabilities = response.get("scenario_probabilities") or {}
            match_probabilities = response.get("match_probabilities") or {}
            keys = set()
            if isinstance(scenario_probabilities, dict):
                keys.update(str(key) for key in scenario_probabilities)
            if isinstance(match_probabilities, dict):
                keys.update(str(key) for key in match_probabilities)
            for raw_key in keys:
                phase, opponent = _split_phase_key(raw_key)
                if phase not in PHASE_ORDER:
                    continue
                if opponent.lower() in {"outros", "other", "others"}:
                    continue
                current = rows_by_key.get((phase, opponent), {})
                rows_by_key[(phase, opponent)] = {
                    "phase": phase,
                    "opponent": opponent,
                    "scenario_pct": (
                        scenario_probabilities.get(raw_key)
                        if isinstance(scenario_probabilities, dict) and raw_key in scenario_probabilities
                        else current.get("scenario_pct")
                    ),
                    "brazil_pct": (
                        match_probabilities.get(raw_key)
                        if isinstance(match_probabilities, dict) and raw_key in match_probabilities
                        else current.get("brazil_pct")
                    ),
                    "most_likely": current.get("most_likely"),
                    "venue": current.get("venue", ""),
                    "source": f"fala de {agent} na rodada {round_index}",
                }
    return list(rows_by_key.values())


def _top_two_by_phase(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("phase", "Fase nao informada")), []).append(row)
    selected: list[dict[str, Any]] = []
    for phase in sorted(grouped, key=_phase_rank):
        ranked = sorted(
            grouped[phase],
            key=lambda row: (
                float(row.get("scenario_pct") or -1.0),
                float(row.get("brazil_pct") or -1.0),
                str(row.get("opponent", "")),
            ),
            reverse=True,
        )
        for rank, row in enumerate(ranked[:2], start=1):
            ranked_row = dict(row)
            ranked_row["_rank_in_phase"] = rank
            selected.append(ranked_row)
    return selected


def _render_probability_rows(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return [
            "- Top-2 por fase nao esta serializado neste JSON; veja as falas das salas abaixo para auditoria."
        ]
    lines: list[str] = []
    for row in _top_two_by_phase(rows):
        phase = str(row.get("phase", "Fase nao informada"))
        opponent = str(row.get("opponent", "Adversario a definir"))
        scenario = _fmt_pct(row.get("scenario_pct"))
        brazil = _fmt_pct(row.get("brazil_pct"))
        venue = str(row.get("venue", "") or "").strip()
        venue_text = f"; local {venue}" if venue else ""
        if row.get("most_likely") is True:
            label = "mais provavel"
        elif row.get("most_likely") is False:
            label = "segunda opcao"
        else:
            label = "mais provavel" if int(row.get("_rank_in_phase", 1) or 1) == 1 else "segunda opcao"
        warning = ""
        try:
            scenario_value = float(row.get("scenario_pct"))
            brazil_value = float(row.get("brazil_pct"))
            if abs(scenario_value - brazil_value) < 0.05:
                warning = "; ATENCAO: brazil_pct igual ao scenario_pct, possivel campo contaminado no run"
        except (TypeError, ValueError):
            warning = ""
        source = str(row.get("source", "fonte nao informada"))
        lines.append(
            f"- {phase}: {opponent} ({label}); chance do confronto {scenario}; "
            f"Brasil passa {brazil}{venue_text}{warning}; origem: {source}."
        )
    return lines


def _opponent_room_operational_lines(opponent_room: dict[str, Any]) -> list[str]:
    if not opponent_room.get("enabled"):
        return ["- Status da sala adversarios: desligada; top-2 veio do JSON/Monte Carlo configurado."]

    exit_status = str(opponent_room.get("exit_status", "") or "nao informado")
    usable = bool(opponent_room.get("usable_for_main_room", False))
    rounds = int(opponent_room.get("rounds", 0) or 0)
    lines: list[str] = []

    if opponent_room.get("timed_out"):
        timeout_s = opponent_room.get("timeout_seconds", "s/d")
        pending = opponent_room.get("pending_round") if isinstance(opponent_room.get("pending_round"), dict) else {}
        question = _compact_text(pending.get("question", "")) if pending else ""
        round_label = f"rodada {pending.get('round', '?')}" if pending else "rodada nao registrada"
        checkpoint = "timeout preservado" if opponent_room.get("partial_progress_available") else "timeout sem checkpoint util"
        detail = f"- Status da sala adversarios: {checkpoint} em {round_label} ({timeout_s}s); "
        if question:
            detail += f"pergunta pendente: {question}; "
        detail += "usou Monte Carlo/bracket como fallback para a sala Brasil."
        lines.append(detail)
        return lines

    if opponent_room.get("degraded"):
        decision = opponent_room.get("degraded_decision") if isinstance(opponent_room.get("degraded_decision"), dict) else {}
        lines.append(
            "- Status da sala adversarios: quase-consenso degradado entrou na sala Brasil; "
            f"rodadas={rounds}; exit_status={exit_status}; "
            f"valid_participants={decision.get('valid_participants', 's/d')}."
        )
        return lines

    if opponent_room.get("degraded_would_be_usable") and opponent_room.get("degraded_shadow_only"):
        decision = opponent_room.get("degraded_decision") if isinstance(opponent_room.get("degraded_decision"), dict) else {}
        lines.append(
            "- Status da sala adversarios: quase-consenso degradado medido em shadow; "
            "não reescreveu a sala Brasil; "
            f"rodadas={rounds}; exit_status={exit_status}; "
            f"valid_participants={decision.get('valid_participants', 's/d')}; "
            f"coverage_complete={decision.get('coverage_complete', 's/d')}."
        )
        return lines

    if usable:
        lines.append(
            "- Status da sala adversarios: consenso utilizavel alimentou o top-2 da sala Brasil; "
            f"rodadas={rounds}; exit_status={exit_status}."
        )
        return lines

    if opponent_room.get("failed"):
        lines.append(
            "- Status da sala adversarios: falhou e nao alimentou a sala Brasil; "
            f"rodadas={rounds}; exit_status={exit_status}; "
            f"motivo={_compact_text(opponent_room.get('error', 'sem detalhe estruturado'))}."
        )
        return lines

    lines.append(
        "- Status da sala adversarios: nao utilizavel para a sala Brasil; "
        f"rodadas={rounds}; exit_status={exit_status}; fallback=Monte Carlo/bracket."
    )
    return lines


def _render_transcript(title: str, transcript: list[dict[str, Any]]) -> list[str]:
    lines = [title]
    if not transcript:
        lines.append("- Sem transcricao registrada neste JSON.")
        return lines
    for turn in transcript:
        lines.append("")
        lines.append(f"Rodada {turn.get('round', '?')} | Protagonista: {turn.get('protagonist', 'nao informado')}")
        lines.append(f"Pergunta: {_compact_text(turn.get('question', ''))}")
        invalidation = turn.get("invalidated_protagonist_question")
        if isinstance(invalidation, dict):
            lines.append(
                "[Facilitador] fala invalidada: "
                f"{_compact_text(invalidation.get('reason', 'fora do escopo'))}; "
                f"acao: {_compact_text(invalidation.get('action', 'fala excluida da influencia'))}"
            )
        for response in turn.get("responses", []) or []:
            lines.append("")
            lines.append(f"[{response.get('agent', 'Modelo')}]")
            lines.append(
                "Status: "
                f"{_response_status(response)} | titulo {_fmt_pct(response.get('title_pct'))} | "
                f"aceitacao {float(response.get('support_score', 0.0) or 0.0):.2f} | "
                f"fontes {int(response.get('source_count', 0) or 0)}"
            )
            lines.append(f"Resposta: {_compact_text(response.get('answer', ''))}")
            proposed = _compact_text(response.get("proposed_next_question", ""))
            rationale = _compact_text(response.get("leadership_rationale", ""))
            if proposed:
                lines.append(f"Proxima pergunta proposta: {proposed}")
            if rationale:
                lines.append(f"Racional para protagonismo: {rationale}")
        lines.append("")
        lines.append(
            f"Proximo protagonista: {turn.get('next_protagonist', 'nao informado')} | "
            f"consenso da rodada {_fmt_pct(turn.get('consensus_title_pct'))} | "
            f"dispersao {float(turn.get('consensus_spread_pct', 0.0) or 0.0):.1f} p.p."
        )
    return lines


def render_debate_report(payload: dict[str, Any], *, source_path: Path | None = None) -> str:
    bundle = _bundle(payload)
    metadata = bundle.get("metadata") if isinstance(bundle.get("metadata"), dict) else {}
    opponent_room = metadata.get("parallel_opponent_debriefing") if isinstance(metadata, dict) else {}
    if not isinstance(opponent_room, dict):
        opponent_room = {}
    opponent_transcript = list(opponent_room.get("meeting_transcript", []) or [])
    brazil_transcript = list(bundle.get("meeting_transcript", []) or [])
    serialized_matches = list(bundle.get("knockout_matches", []) or [])
    probability_rows = _probability_rows_from_matches(serialized_matches)
    if not probability_rows:
        probability_rows = _probability_rows_from_transcript(opponent_transcript)

    stage_probabilities = bundle.get("stage_probabilities") if isinstance(bundle.get("stage_probabilities"), dict) else {}
    opponent_status = "desligada"
    if opponent_room.get("enabled"):
        opponent_status = "falhou" if opponent_room.get("failed") else "concluida"
    opponent_participants = opponent_room.get("participants") or _participants_from_transcript(opponent_transcript)
    brazil_participants = _participants_from_transcript(brazil_transcript)

    lines: list[str] = [
        "# Debate das salas - Brasil e adversarios",
        "",
        f"Run: {bundle.get('generated_at_iso', 'nao informado')}",
    ]
    if source_path is not None:
        lines.append(f"Arquivo fonte: {source_path}")
    lines.extend(
        [
            "",
            "## Visao geral",
            f"- Sala adversarios do Brasil: {opponent_status}; rodadas={int(opponent_room.get('rounds', len(opponent_transcript)) or 0)}; participantes={', '.join(opponent_participants) if opponent_participants else 'nao informado'}.",
            f"- Sala Brasil: rodadas={len(brazil_transcript)}; participantes={', '.join(brazil_participants) if brazil_participants else 'nao informado'}.",
            f"- Funil final: quartas {_fmt_pct(stage_probabilities.get('quartas'))}; semifinal {_fmt_pct(stage_probabilities.get('semifinal'))}; final {_fmt_pct(stage_probabilities.get('final'))}; titulo {_fmt_pct(stage_probabilities.get('titulo'))}.",
            *_opponent_room_operational_lines(opponent_room),
            "",
            "## Retroalimentacao",
            "- Ordem operacional: Monte Carlo/bracket oficial -> sala adversarios -> top-2 por fase -> JSON da sala Brasil -> sala Brasil decide chance do Brasil contra esses cenarios.",
            "- A sala adversarios nao decide o titulo do Brasil; ela reduz o espaco de adversarios possiveis dentro do bracket oficial.",
            "- A sala Brasil recebe esses candidatos e decide as probabilidades condicionais do Brasil, ate final e titulo.",
            "",
            "### Top-2/reconciliacao por fase",
        ]
    )
    lines.extend(_render_probability_rows(probability_rows))
    lines.append("")
    lines.extend(_render_transcript("## SALA 1 - Adversarios do Brasil", opponent_transcript))
    lines.append("")
    lines.extend(_render_transcript("## SALA 2 - Brasil", brazil_transcript))
    return "\n".join(lines).rstrip() + "\n"


def _count_control_events(run: list[dict[str, Any]]) -> dict[str, int]:
    counters: dict[str, int] = defaultdict(int)
    for event in run:
        if event.get("step") not in {"model_room", "opponent_model_room"}:
            continue
        status = str(event.get("status", "") or "")
        if status:
            counters[status] += 1
    return dict(counters)


def _failure_events(run: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in run if event.get("status") == "fail"]


def _chat_events(run: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in run
        if event.get("step") in {"model_room", "opponent_model_room"}
        and event.get("status") in {"question", "response", "chat", "invalidation", "repair"}
    ]


def render_failed_watchdog_run_report(
    run_id: str,
    run: list[dict[str, Any]],
    *,
    latest_json: Path | None,
) -> str:
    first_ts = str(run[0].get("timestamp", "nao informado")) if run else "nao informado"
    last_ts = str(run[-1].get("timestamp", "nao informado")) if run else "nao informado"
    failures = _failure_events(run)
    controls = _count_control_events(run)
    latest_failure = failures[-1] if failures else {}
    latest_json_text = str(latest_json) if latest_json else "nenhum JSON renderizado encontrado"
    lines = [
        "# Debate das salas - run mais recente falhou",
        "",
        f"Run watchdog: {run_id}",
        f"Janela: {first_ts} -> {last_ts}",
        f"Ultimo JSON de post: {latest_json_text}",
        "",
        "## Diagnostico",
        "- O run mais recente terminou antes de renderizar um novo JSON de post.",
        "- Este comando nao esta mostrando o debate antigo para nao mascarar a falha atual.",
        f"- Falha final: {_compact_text(latest_failure.get('detail', 'sem detalhe estruturado'))}",
    ]
    if controls:
        lines.append(
            "- Eventos da sala: "
            + ", ".join(f"{key}={value}" for key, value in sorted(controls.items()))
            + "."
        )
    if failures:
        lines.extend(["", "## Falhas Registradas"])
        for event in failures[-8:]:
            lines.append(
                f"- {event.get('timestamp', '')} | {event.get('step', '')}: "
                f"{_compact_text(event.get('detail', ''))}"
            )
    room_events = _chat_events(run)
    if room_events:
        lines.extend(["", "## Ultimos eventos da sala"])
        for event in room_events[-16:]:
            extra = event.get("extra") if isinstance(event.get("extra"), dict) else {}
            round_text = f"rodada {extra.get('round')}" if extra.get("round") else "rodada ?"
            actor = extra.get("agent") or extra.get("protagonist") or ""
            actor_text = f" | {actor}" if actor else ""
            lines.append(
                f"- {event.get('timestamp', '')} | {event.get('step', '')}/{event.get('status', '')} "
                f"| {round_text}{actor_text}: {_compact_text(event.get('detail', ''))}"
            )
    lines.extend(
        [
            "",
            "## Como ler",
            "- Rode `make profile` para medir este run falho; por padrao ele agora usa o run mais recente.",
            "- Rode `make profile PROFILE_ARGS=\"--successful\"` se quiser comparar com o ultimo run bem-sucedido.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a structured debate report for the Brazil and opponent debriefing rooms."
    )
    parser.add_argument("--input", type=Path, help="Specific linkedin_brazil_YYYY-MM-DD.json file.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"), help="Directory containing run JSON files.")
    parser.add_argument(
        "--watchdog-log",
        type=Path,
        default=Path("data/watchdog.jsonl"),
        help="Watchdog JSONL used to detect a newer failed run than the latest rendered JSON.",
    )
    parser.add_argument("--output", type=Path, help="Optional markdown file to write. Defaults to stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = args.input
    if input_path is None:
        input_path = _maybe_find_latest_run_json(args.output_dir)
        failed_run = latest_failed_watchdog_run_after_json(
            watchdog_log=args.watchdog_log,
            latest_json=input_path,
        )
        if failed_run is not None:
            run_id, run = failed_run
            report = render_failed_watchdog_run_report(run_id, run, latest_json=input_path)
        elif input_path is not None:
            report = render_debate_report(load_run_json(input_path), source_path=input_path)
        else:
            raise FileNotFoundError(f"Nenhum linkedin_brazil_*.json encontrado em {args.output_dir}")
    else:
        report = render_debate_report(load_run_json(input_path), source_path=input_path)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"debate: {args.output}")
    else:
        print(report, end="")
    return 0

from __future__ import annotations

import argparse
import json
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a structured debate report for the Brazil and opponent debriefing rooms."
    )
    parser.add_argument("--input", type=Path, help="Specific linkedin_brazil_YYYY-MM-DD.json file.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"), help="Directory containing run JSON files.")
    parser.add_argument("--output", type=Path, help="Optional markdown file to write. Defaults to stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = args.input or find_latest_run_json(args.output_dir)
    report = render_debate_report(load_run_json(input_path), source_path=input_path)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"debate: {args.output}")
    else:
        print(report, end="")
    return 0

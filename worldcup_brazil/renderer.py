from __future__ import annotations

import html

from worldcup_brazil.models import ReportBundle
from worldcup_brazil.probabilities import MatchEstimate


PHASE_ORDER = ("16 avos", "Oitavas", "Quartas", "Semifinal", "Final")


def _fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def _fmt_ci(low: float | None, high: float | None) -> str | None:
    if low is None or high is None:
        return None
    return f"{_fmt_pct(low)}-{_fmt_pct(high)}"


def _fmt_int(value: float | int) -> str:
    return f"{int(round(float(value))):,}".replace(",", ".")


def _fmt_money(value: float, *, currency: str) -> str:
    if currency == "BRL":
        return f"R$ {value:.2f}"
    return f"US$ {value:.4f}"


def _fmt_confidence_level(value: float | int | str | None) -> str:
    try:
        pct = float(value) * 100.0
    except (TypeError, ValueError):
        pct = 95.0
    if abs(pct - round(pct)) < 0.05:
        return f"{int(round(pct))}%"
    return f"{pct:.1f}%"


def _loss_pct(match: MatchEstimate) -> float:
    if match.draw_pct is None:
        return match.opponent_pct
    return round(max(0.0, 100 - match.brazil_pct - match.draw_pct), 1)


def _venue(match: MatchEstimate) -> str:
    return match.venue or "A definir"


def _date_prefix(match: MatchEstimate) -> str:
    if not match.match_date or match.match_date.lower() in {"a definir", "tbd"}:
        return ""
    return f"{match.match_date} "


def _render_match(match: MatchEstimate, *, include_venue: bool = False) -> list[str]:
    lines = [
        (
            f"-> {match.brazil} x {match.opponent} "
            f"({_fmt_pct(match.brazil_pct)} de chances para o brasil "
            f"{_fmt_pct(match.opponent_pct)} para {match.opponent})"
        )
    ]
    ci = _fmt_ci(match.brazil_ci_low, match.brazil_ci_high)
    if ci:
        lines.append(f"Intervalo de confiança: {ci} Brasil")
    if include_venue:
        if match.most_likely is False:
            label = "Local do jogo o segundo adversário mais provável"
        else:
            label = "Local do jogo adversário mais provável"
        lines.append(f"{label}: {match.venue or 'A definir'}")
    lines.append(f"Conclusão e racional: {match.rationale}")
    return lines


def _render_group_block(bundle: ReportBundle) -> list[str]:
    lines = [f"{bundle.group_name} — probabilidade de vitória do Brasil por jogo:"]
    for match in bundle.group_matches:
        if match.draw_pct is None:
            lines.append(
                f"• {_date_prefix(match)}vs {match.opponent} ({_venue(match)}): "
                f"{_fmt_pct(match.brazil_pct)} Brasil | {_fmt_pct(match.opponent_pct)} {match.opponent}"
            )
        else:
            line = (
                f"• {_date_prefix(match)}vs {match.opponent} ({_venue(match)}): "
                f"{_fmt_pct(match.brazil_pct)} V | {_fmt_pct(match.draw_pct)} E"
            )
            loss_pct = _loss_pct(match)
            if loss_pct > 0.05:
                line += f" | {_fmt_pct(loss_pct)} D"
            lines.append(line)
    if bundle.group_summary:
        lines.append(f"→ {bundle.group_summary}")
    lines.append("")
    lines.append(
        "Leitura: V/E/D significa vitória, empate e derrota no tempo regulamentar. "
        "O número final combina força estatística, mercado e contexto de elenco."
    )
    return lines


def _scenario_label(match: MatchEstimate) -> str:
    return "Adversário mais provável" if match.most_likely is not False else "Adversário menos provável"


def _scenario_probability_label(match: MatchEstimate) -> str:
    if match.scenario_pct is None:
        return ""
    return f" (chance do confronto: {_fmt_pct(match.scenario_pct)})"


def _render_knockout_phase_block(phase: str, matches: list[MatchEstimate]) -> list[str]:
    lines = [f"{phase.upper()} — caminhos mais prováveis:"]
    for match in matches:
        ci = _fmt_ci(match.brazil_ci_low, match.brazil_ci_high)
        ci_text = f" | IC Brasil {ci}" if ci else ""
        lines.append(
            f"• {_scenario_label(match)}{_scenario_probability_label(match)}: "
            f"{_date_prefix(match)}vs {match.opponent} ({_venue(match)}): "
            f"{_fmt_pct(match.brazil_pct)} Brasil passa | {_fmt_pct(match.opponent_pct)} {match.opponent} passa"
            f"{ci_text}"
        )
        if ci:
            lines.append(f"  Intervalo de confiança: {ci} Brasil")
        lines.append(f"  Racional: {match.rationale}")
    if matches:
        most = next((match for match in matches if match.most_likely is not False), matches[0])
        second = next((match for match in matches if match.most_likely is False), None)
        if second:
            lines.append(f"→ Cenário-base: {most.opponent}. Caminho menos provável: {second.opponent}.")
        else:
            lines.append(f"→ Cenário-base: {most.opponent}.")
    return lines


def _phase_rank(phase: str) -> int:
    try:
        return PHASE_ORDER.index(phase)
    except ValueError:
        return len(PHASE_ORDER)


def _group_knockout_matches(matches: list[MatchEstimate]) -> list[tuple[str, list[MatchEstimate]]]:
    grouped: dict[str, list[MatchEstimate]] = {}
    for match in matches:
        grouped.setdefault(match.phase, []).append(match)
    return [
        (phase, grouped[phase])
        for phase in sorted(grouped, key=_phase_rank)
    ]


def _compact_chat_text(text: str, *, limit: int = 900) -> str:
    _ = limit
    return str(text).strip()


def _post_chat_text(text: str, *, limit: int = 420) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 36)].rstrip() + " [resumo; íntegra na auditoria]"


def _response_status(response: dict) -> str:
    if response.get("used_fallback"):
        return "fallback"
    if response.get("disagreed"):
        return "discorda"
    if response.get("accepted", response.get("support_score", 0) >= 0.72):
        return "aceita"
    return "contestada"


def _join_names(items: list[str]) -> str:
    clean = [str(item).strip() for item in items if str(item).strip()]
    return ", ".join(clean) if clean else "não informado"


def _stamp_from_iso(value: str) -> str:
    return value[:10] if len(value) >= 10 else "YYYY-MM-DD"


def _stage_ci(bundle: ReportBundle, key: str) -> str:
    ci = bundle.stage_confidence_intervals.get(key)
    if not ci:
        return ""
    return f" Intervalo de confiança: {_fmt_pct(ci[0])}-{_fmt_pct(ci[1])}."


def _render_model_predictions_no_opta(bundle: ReportBundle) -> list[str]:
    if not bundle.model_predictions_no_opta:
        return []
    lines = [
        "Palpites por modelo:",
        "Regra: cada agente usa suas fontes próprias e informa o racional que trouxe para a sala.",
    ]
    for agent, prediction in bundle.model_predictions_no_opta.items():
        fallback = " | fallback" if prediction.get("used_fallback") else ""
        lines.append(
            f"- {agent}: título {_fmt_pct(float(prediction.get('title_pct', 0.0)))}{fallback}. "
            f"{prediction.get('summary', '')}"
        )
    lines.append("")
    return lines


def _render_monte_carlo_summary(bundle: ReportBundle) -> list[str]:
    monte_carlo = bundle.metadata.get("monte_carlo", {})
    if not isinstance(monte_carlo, dict) or not monte_carlo.get("enabled"):
        return []
    stages = monte_carlo.get("stage_probabilities") or {}
    phases = monte_carlo.get("phases") or {}
    lines = [
        "Simulação Monte Carlo:",
        (
            f"- Rodadas: {_fmt_int(monte_carlo.get('iterations', 0))}; "
            f"seed: {monte_carlo.get('seed', 's/d')}; "
            f"cobertura de rating explícito: {_fmt_pct(float(monte_carlo.get('rating_coverage_pct', 0.0)))}."
        ),
    ]
    rating_uncertainty = monte_carlo.get("rating_uncertainty") if isinstance(monte_carlo, dict) else {}
    if isinstance(rating_uncertainty, dict) and rating_uncertainty.get("enabled"):
        lines.append(
            "- Nível de IC Monte Carlo: "
            f"{_fmt_confidence_level(monte_carlo.get('confidence_level'))}; "
            "incerteza de rating: "
            f"{_fmt_int(rating_uncertainty.get('outer_samples', 0))} cenários x "
            f"{_fmt_int(rating_uncertainty.get('inner_iterations', 0))} torneios; "
            f"sigma rating explícito {float(rating_uncertainty.get('configured_rating_sigma', 0.0)):.1f} Elo; "
            f"sigma prior {float(rating_uncertainty.get('prior_rating_sigma', 0.0)):.1f} Elo."
        )
        lines.append(
            "- Leitura estatística: ponto central = média posterior sobre cenários de rating; "
            "variância total desconta ruído interno dos torneios simulados antes de publicar a banda epistêmica."
        )
        stage_uncertainty = monte_carlo.get("stage_uncertainty_intervals") or {}
        title_ci = stage_uncertainty.get("titulo") if isinstance(stage_uncertainty, dict) else None
        if title_ci:
            lines.append(f"- Banda epistêmica MC: título {_fmt_pct(float(title_ci[0]))}-{_fmt_pct(float(title_ci[1]))}.")
    path_gate = monte_carlo.get("path_gate") if isinstance(monte_carlo, dict) else {}
    if isinstance(path_gate, dict) and path_gate:
        mode = "hard gate de caminho" if path_gate.get("reliable") else "prior fraco de caminho"
        lines.append(
            "- Gate do caminho: "
            f"{mode}; mínimo {_fmt_int(path_gate.get('min_iterations', 0))} simulações e "
            f"{_fmt_pct(float(path_gate.get('min_rating_coverage_pct', 0.0)))} de cobertura."
        )
    team_context = monte_carlo.get("team_context") if isinstance(monte_carlo, dict) else {}
    if isinstance(team_context, dict) and int(team_context.get("applied_signal_count") or 0) > 0:
        families = ", ".join(str(item) for item in team_context.get("source_families", [])[:5])
        lines.append(
            "- Contexto por seleção: "
            f"{int(team_context.get('applied_signal_count') or 0)} sinais em "
            f"{int(team_context.get('teams_with_context_count') or 0)} seleções"
            + (f"; famílias: {families}." if families else ".")
        )
    lines.append(
        "- Como pesa: simula grupos, melhores terceiros e chave oficial. "
        "Os modelos recebem isso como insumo quantitativo e podem aceitar ou contestar com fonte melhor."
    )
    if stages:
        lines.append(
            "- Funil MC: "
            f"quartas {_fmt_pct(float(stages.get('quartas', 0.0)))} | "
            f"semi {_fmt_pct(float(stages.get('semifinal', 0.0)))} | "
            f"final {_fmt_pct(float(stages.get('final', 0.0)))} | "
            f"título simulado {_fmt_pct(float(stages.get('titulo', 0.0)))}."
        )
    round_of_32 = phases.get("16 avos") if isinstance(phases, dict) else None
    if isinstance(round_of_32, dict):
        top = round_of_32.get("top_opponents") or []
        if top:
            rendered = ", ".join(
                f"{item.get('opponent')} {_fmt_pct(float(item.get('scenario_pct', 0.0)))}"
                for item in top[:3]
                if isinstance(item, dict)
            )
            if rendered:
                lines.append(f"- 16 avos, adversários por simulação: {rendered}.")
    lines.append("")
    return lines


def _fmt_eur_millions(value: float | int | None) -> str:
    if value is None:
        return "s/d"
    return f"€{float(value) / 1_000_000:.1f}M"


def _render_market_value_momentum(bundle: ReportBundle) -> list[str]:
    momentum = bundle.metadata.get("market_value_momentum", {})
    if not isinstance(momentum, dict) or not momentum.get("available"):
        return []

    source = str(momentum.get("source") or "Transfermarkt")
    teams = momentum.get("teams") or {}
    if not isinstance(teams, dict) or not teams:
        return []

    lines = [
        f"Destaques de valorização ({source}):",
        (
            "- Como pesa: delta nominal em euros pesa mais que percentual isolado; "
            "50M->55M pesa mais que 10M->13M porque dinheiro absoluto também carrega força de elenco."
        ),
    ]
    for team, raw_summary in teams.items():
        if not isinstance(raw_summary, dict):
            continue
        tracked = int(raw_summary.get("players_tracked", 0))
        if tracked <= 0:
            continue
        positive = int(raw_summary.get("positive_players", 0))
        nominal = _fmt_eur_millions(raw_summary.get("nominal_delta_eur"))
        weighted = _fmt_eur_millions(raw_summary.get("weighted_delta_eur"))
        top_players = [
            str(player.get("display") or player.get("player"))
            for player in raw_summary.get("top_players", [])
            if isinstance(player, dict) and (player.get("display") or player.get("player"))
        ]
        detail = f" Destaques: {', '.join(top_players[:3])}." if top_players else ""
        lines.append(
            f"- {team}: {positive}/{tracked} jogadores em alta; "
            f"delta nominal {nominal}; score ponderado {weighted}.{detail}"
        )
    lines.append("")
    return lines


def _render_linkedin_comment_suggestion(bundle: ReportBundle) -> list[str]:
    rounds = len(bundle.meeting_transcript)
    total_messages = int(bundle.model_participation.get("total_messages", 0)) if bundle.model_participation else 0
    rounds_text = f"{rounds} rodada(s)" if rounds else "rodadas configuradas"
    messages_text = f" e {total_messages} mensagem(ns)" if total_messages else ""
    return [
        "Comentário sugerido para postar nos comentários:",
        "",
        "Fatos curiosos sobre a sala de modelos:",
        f"- Cada modelo chega com fontes próprias antes de opinar; neste run foram {rounds_text}{messages_text} na sala.",
        "- O protagonista não é fixo: ele muda por discordância útil ou por mérito quando alguém aceita a tese e propõe uma pergunta melhor.",
        "- Concordar não é ficar passivo: um modelo pode aceitar o racional e ainda disputar a próxima pergunta com novo teste auditável.",
        "- Discordar só conta quando vem com hipótese, número e fonte; discordância vazia não deveria ganhar liderança.",
        "- Fallback não é voto forte: quando uma API/CLI falha, a resposta conservadora fica marcada e perde peso na decisão.",
        "- Quanti e quali entram juntos: nenhum modelo recebe quota fixa; só premissas auditáveis movem probabilidades.",
        "",
    ]


def _render_format_explainer(bundle: ReportBundle) -> list[str]:
    stamp = _stamp_from_iso(bundle.generated_at_iso)
    uncertainty = bundle.metadata.get("uncertainty") if isinstance(bundle.metadata, dict) else {}
    confidence_label = _fmt_confidence_level(
        uncertainty.get("confidence_level") if isinstance(uncertainty, dict) else None
    )
    minimum_label = _fmt_confidence_level(
        uncertainty.get("minimum_declared_coverage") if isinstance(uncertainty, dict) else 0.95
    )
    return [
        "Como ler este post:",
        "1. Primeiro vem o funil jogo a jogo: grupo, 16 avos, oitavas, quartas, semi e final.",
        "2. Depois vem a conclusão probabilística: quartas, semifinal, final e título com intervalo de confiança.",
        "3. A sala de modelos aparece resumida aqui; a conversa completa fica no arquivo de auditoria da rodada.",
        (
            f"IC = intervalo de confiança operacional (nível declarado: {confidence_label}; "
            f"mínimo operacional: {minimum_label}): a largura aumenta quando cai a confiança das fontes, "
            "quando os sinais/modelos discordam mais, quando o adversário ainda é um conjunto de candidatos oficiais "
            "ou quando há warning operacional no run."
        ),
        (
            "A Copa real é um único funil: o IC é condicional à simulação da chave, aos ratings/contextos "
            "e ao consenso dos modelos daquele run; ele não promete que uma Copa isolada vai se comportar "
            "como repetição perfeita do experimento."
        ),
        "",
        "Gráfico do racional:",
        f"![Fluxo de decisão](decision_flow_brazil_{stamp}.svg)",
        "",
    ]


def render_linkedin_post(bundle: ReportBundle) -> str:
    lines: list[str] = []
    lines.append("Fase de grupos (todos os jogos):")
    lines.append("")
    lines.extend(_render_format_explainer(bundle))
    lines.extend(_render_group_block(bundle))
    lines.append("")

    lines.append("Fase de mata-mata (todos os jogos):")
    lines.append("")
    for phase, matches in _group_knockout_matches(bundle.knockout_matches):
        lines.append(f"{phase}:")
        lines.append("")
        lines.extend(_render_knockout_phase_block(phase, matches))
        lines.append("")

    lines.append("---------")
    lines.append("")
    lines.append("Conclusão final:")
    lines.append("")
    lines.append("Até onde o Brasil deve chegar?")
    lines.append(bundle.final_rationale)
    lines.append(
        "Em português claro: o modelo não está tentando cravar placar. Ele pergunta: "
        "se esse jogo fosse repetido muitas vezes com elenco, forma, mercado e chave parecidos, "
        "em quantas o Brasil passaria?"
    )
    lines.append("")
    lines.append(
        "Probabilidade de Brasil chegar às quartas: "
        f"{_fmt_pct(bundle.stage_probabilities.get('quartas', 0.0))} "
        "- chance de sobreviver ao grupo, aos 16 avos e às oitavas. "
        "Método: odds/mercados, ratings e simulação de chave."
        f"{_stage_ci(bundle, 'quartas')}"
    )
    lines.append("")
    lines.append(
        "Probabilidade de Brasil chegar à semifinal: "
        f"{_fmt_pct(bundle.stage_probabilities.get('semifinal', 0.0))} "
        "- daqui em diante o peso do adversário elite começa a morder. "
        "Método: força relativa, descanso, cartões e risco de lesão."
        f"{_stage_ci(bundle, 'semifinal')}"
    )
    lines.append("")
    lines.append(
        "Probabilidade de Brasil chegar à final: "
        f"{_fmt_pct(bundle.stage_probabilities.get('final', 0.0))} "
        "- aqui entra a parte cruel do mata-mata: um jogo ruim derruba um projeto bom. "
        "Método: rating + mercado + cenário de chave."
        f"{_stage_ci(bundle, 'final')}"
    )
    lines.append("")
    lines.append(
        "Probabilidade de título: "
        f"{_fmt_pct(bundle.stage_probabilities.get('titulo', 0.0))} "
        "- funil único da simulação de chave, reconciliado com os sinais auditáveis da sala; "
        "a leitura direta dos modelos aparece em 'Palpites por modelo'."
        f"{_stage_ci(bundle, 'titulo')}"
    )
    lines.append("")
    lines.extend(_render_monte_carlo_summary(bundle))
    lines.extend(_render_model_predictions_no_opta(bundle))
    lines.extend(_render_market_value_momentum(bundle))

    if bundle.source_plan_by_model or bundle.meeting_transcript or bundle.debate_transcript:
        lines.append("Sala de decisão dos modelos (debriefing):")
        lines.append(
            "Como ler: cada modelo traz suas próprias fontes, o protagonista faz uma pergunta, "
            "os outros respondem se concordam ou discordam, e a liderança muda por discordância útil "
            "ou por mérito quando alguém aceita a tese e propõe uma pergunta melhor."
        )
        if bundle.source_plan_by_model:
            lines.append("")
            lines.append("Fontes escolhidas por modelo:")
            for agent, plan in bundle.source_plan_by_model.items():
                urls = plan.get("source_urls", [])
                queries = plan.get("source_queries", [])
                source_text = ", ".join(urls[:3] or queries[:3] or ["modelo sem plano de fontes; não deve participar da sala"])
                lines.append(f"- {agent}: {source_text}")
        if bundle.meeting_transcript:
            lines.append("")
            lines.append("Chat da sala:")
            for turn in bundle.meeting_transcript:
                lines.append(f"Rodada {turn['round']} | Protagonista: {turn['protagonist']}")
                lines.append(
                    f"[Rodada {turn['round']}] {turn['protagonist']} (protagonista): "
                    f"{_post_chat_text(turn['question'], limit=260)}"
                )
                invalidation = turn.get("invalidated_protagonist_question")
                if invalidation:
                    lines.append(
                        f"[Rodada {turn['round']}] Facilitador: Fala invalidada - "
                        f"{_post_chat_text(str(invalidation.get('reason', 'fora do escopo')), limit=180)}. "
                        "A fala não entra na influência; a sala continua com os pares."
                    )
                for response in turn.get("responses", []):
                    lines.append(f"[Rodada {turn['round']}] {response['agent']}: {_post_chat_text(response['answer'])}")
                    lines.append(
                        f"  Status: {_response_status(response)} | título: {_fmt_pct(response['title_pct'])} | "
                        f"aceitação: {response['support_score']:.2f}"
                    )
                lines.append(
                    f"Próximo protagonista: {turn['next_protagonist']} "
                    f"(consenso da rodada: {_fmt_pct(turn['consensus_title_pct'])}; "
                    f"dispersão: {turn['consensus_spread_pct']:.1f} p.p.)"
                )
            parallel_room = bundle.metadata.get("parallel_opponent_debriefing", {})
            if parallel_room.get("enabled"):
                status = "falhou" if parallel_room.get("failed") else "rodou"
                participants = ", ".join(parallel_room.get("participants", []) or [])
                lines.append(
                    "Sala paralela de adversários prováveis: "
                    f"{status}; rodadas={int(parallel_room.get('rounds', 0) or 0)}; "
                    f"participantes={participants or 'não informado'}. "
                    "Ela usa a mesma dinâmica da sala principal e só altera cenários dentro do bracket oficial."
                )
        elif bundle.debate_transcript:
            for line in bundle.debate_transcript:
                lines.append(f"- {line}")
        lines.append("")

    if bundle.model_influence_pct:
        lines.append("Influência dos modelos na decisão:")
        for agent, pct in bundle.model_influence_pct.items():
            lines.append(f"- {agent}: {_fmt_pct(pct)}")
        lines.append("")

    if bundle.model_participation:
        total_messages = int(bundle.model_participation.get("total_messages", 0))
        total_questions = int(bundle.model_participation.get("total_questions", 0))
        total_responses = int(bundle.model_participation.get("total_responses", 0))
        total_rounds = int(bundle.model_participation.get("total_rounds", len(bundle.meeting_transcript)))
        protagonist_counts = bundle.model_participation.get("protagonist_counts", {})
        round_rows = bundle.model_participation.get("rounds", [])
        last_consensus_round = int(bundle.model_participation.get("last_consensus_round", 0))
        last_consensus_protagonist = str(bundle.model_participation.get("last_consensus_protagonist", "")).strip()
        last_consensus_question = str(bundle.model_participation.get("last_consensus_question", "")).strip()
        last_consensus_participants = [
            str(item).strip()
            for item in bundle.model_participation.get("last_consensus_participants", [])
            if str(item).strip()
        ]
        lines.append("Participação na sala:")
        if total_rounds:
            lines.append(f"- Total de rodadas: {total_rounds}.")
        if isinstance(protagonist_counts, dict) and protagonist_counts:
            protagonist_text = "; ".join(
                f"{agent} {int(count)}x"
                for agent, count in protagonist_counts.items()
            )
            lines.append(f"- Protagonismo por modelo: {protagonist_text}.")
        if isinstance(round_rows, list) and round_rows:
            round_text = "; ".join(
                (
                    f"R{int(row.get('round', 0))} {row.get('protagonist', 'não informado')} "
                    f"({int(row.get('protagonist_count', 0))}x no run)"
                )
                for row in round_rows
                if isinstance(row, dict)
            )
            if round_text:
                lines.append(f"- Protagonismo por rodada: {round_text}.")
        consensus_questions_by_phase = bundle.model_participation.get("consensus_questions_by_phase", [])
        if isinstance(consensus_questions_by_phase, list) and consensus_questions_by_phase:
            lines.append("- Perguntas que fecharam consenso por fase:")
            for item in consensus_questions_by_phase:
                if not isinstance(item, dict):
                    continue
                phase = str(item.get("phase", "Fase não informada")).strip() or "Fase não informada"
                round_number = int(item.get("round", 0))
                protagonist = str(item.get("protagonist", "não informado")).strip() or "não informado"
                question = _post_chat_text(str(item.get("question", "")), limit=180)
                lines.append(f"- {phase}: rodada {round_number}, {protagonist}. Pergunta: {question}")
        if last_consensus_round and last_consensus_protagonist:
            question_text = _post_chat_text(last_consensus_question, limit=180)
            lines.append(
                f"- Última pergunta que virou consenso: rodada {last_consensus_round}, "
                f"{last_consensus_protagonist}. Pergunta: {question_text}"
            )
        if last_consensus_participants:
            lines.append(f"- Participantes da rodada de consenso: {_join_names(last_consensus_participants)}.")
        lines.append(
            f"- Total de mensagens trocadas: {total_messages} "
            f"({total_questions} perguntas de protagonista + {total_responses} respostas dos modelos)."
        )
        for agent, stats in bundle.model_participation.get("by_model", {}).items():
            lines.append(
                f"- {agent}: {int(stats.get('messages', 0))} mensagens "
                f"({int(stats.get('questions', 0))} perguntas, {int(stats.get('responses', 0))} respostas)."
            )
        lines.append("")

    if bundle.model_token_costs:
        total = bundle.model_token_costs.get("total", {})
        usd_to_brl = float(bundle.model_token_costs.get("usd_to_brl", 0.0))
        pricing_basis = bundle.model_token_costs.get("pricing_basis", "estimativa local")
        lines.append("Custo estimado da rodada:")
        lines.append(
            "- Consolidado: "
            f"{_fmt_int(total.get('total_tokens', 0))} tokens aprox. | "
            f"{_fmt_money(float(total.get('cost_usd', 0.0)), currency='USD')} | "
            f"{_fmt_money(float(total.get('cost_brl', 0.0)), currency='BRL')} "
            f"(câmbio usado: {usd_to_brl:.2f} BRL/USD)."
        )
        for agent, stats in bundle.model_token_costs.get("by_model", {}).items():
            removed = " | removido da decisão" if stats.get("removed_from_decision") else ""
            fallback = (
                f" | {int(stats.get('fallback_calls', 0))} fallback(s)"
                if int(stats.get("fallback_calls", 0))
                else ""
            )
            lines.append(
                f"- {agent}: {int(stats.get('calls', 0))} chamada(s); "
                f"{_fmt_int(stats.get('total_tokens', 0))} tokens aprox.; "
                f"{_fmt_money(float(stats.get('cost_usd', 0.0)), currency='USD')} / "
                f"{_fmt_money(float(stats.get('cost_brl', 0.0)), currency='BRL')}"
                f"{fallback}{removed}."
            )
        lines.append(f"- Base: {pricing_basis}.")
        lines.append("")

    if bundle.model_self_identification:
        lines.append("Identidade declarada pelos modelos:")
        lines.append(
            "Regra: nome e versão abaixo vêm da resposta JSON do próprio modelo no run, "
            "não do nome canônico configurado para o slot."
        )
        for agent, identity in bundle.model_self_identification.items():
            name = str(identity.get("name") or "não declarado pelo modelo")
            version = str(identity.get("version") or "não declarado pelo modelo")
            lines.append(f"- {agent}: nome {name} | versão {version}")
        lines.append("")

    if bundle.agent_effort_profiles:
        lines.append("Esforço usado por modelo:")
        for agent, profile in bundle.agent_effort_profiles.items():
            controls = profile.get("controls", "controles não informados")
            latency_guard = profile.get("latency_guard", "resposta objetiva")
            lines.append(
                f"- {agent}: {profile.get('effort_level', 'não informado')} "
                f"({profile.get('provider', 'provider?')} / {profile.get('model', 'modelo?')}). "
                f"Controles: {controls}. Latência: {latency_guard}."
            )
        lines.append("")

    if bundle.agent_summaries:
        lines.append("Consenso multi-agent:")
        for agent, summary in bundle.agent_summaries.items():
            lines.append(f"- {agent}: {summary}")
        lines.append("")

    if bundle.warnings:
        lines.append("Observações de incerteza operacional:")
        for warning in bundle.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("-----------")
    lines.append("")
    lines.append("Fontes usadas:")
    lines.append("")
    for source in bundle.sources:
        lines.append(f"- {source}")
    lines.append("")
    lines.append("--------")
    lines.append("")
    lines.extend(_render_linkedin_comment_suggestion(bundle))
    lines.append("--------")
    lines.append("")
    lines.append("#CopaDoMundo2026 #SelecaoBrasileira #Futebol #DataScience #SportsAnalytics")
    lines.append(bundle.custom_hashtag)

    post = "\n".join(lines).strip() + "\n"
    if len(post.splitlines()) > 2500:
        raise ValueError("LinkedIn post exceeded 2500 lines")
    return post


def render_audit_report(bundle: ReportBundle) -> str:
    lines: list[str] = [
        "Auditoria completa da sala de decisão",
        "",
        f"Run: {bundle.generated_at_iso}",
        "",
    ]
    if bundle.source_plan_by_model:
        lines.append("Fontes escolhidas por modelo:")
        for agent, plan in bundle.source_plan_by_model.items():
            urls = plan.get("source_urls", [])
            queries = plan.get("source_queries", [])
            excluded = plan.get("excluded_opta_items", [])
            lines.append(f"- {agent}")
            lines.append(f"  URLs: {', '.join(urls) if urls else 'nenhuma'}")
            lines.append(f"  Buscas: {', '.join(queries) if queries else 'nenhuma'}")
            if excluded:
                lines.append(f"  Benchmark reservado excluído: {', '.join(excluded)}")
        lines.append("")

    if bundle.meeting_transcript:
        consensus_questions_by_phase = bundle.model_participation.get("consensus_questions_by_phase", {})
        if isinstance(consensus_questions_by_phase, list) and consensus_questions_by_phase:
            lines.append("Perguntas que fecharam consenso por fase:")
            for item in consensus_questions_by_phase:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"- {item.get('phase', 'Fase não informada')}: rodada {int(item.get('round', 0))}, "
                    f"{item.get('protagonist', 'não informado')}. "
                    f"Pergunta: {_compact_chat_text(str(item.get('question', '')))}"
                )
            lines.append("")
        lines.append("Chat completo:")
        for turn in bundle.meeting_transcript:
            lines.append("")
            lines.append(f"Rodada {turn['round']} | Protagonista: {turn['protagonist']}")
            lines.append(f"Pergunta do protagonista: {_compact_chat_text(turn['question'])}")
            invalidation = turn.get("invalidated_protagonist_question")
            if invalidation:
                lines.append("")
                lines.append("[Facilitador] Fala invalidada")
                lines.append(f"Agente: {invalidation.get('agent', turn['protagonist'])}")
                lines.append(f"Motivo: {_compact_chat_text(str(invalidation.get('reason', 'fora do escopo')))}")
                lines.append(f"Ação: {_compact_chat_text(str(invalidation.get('action', 'fala excluída da influência')))}")
            for response in turn.get("responses", []):
                lines.append("")
                lines.append(f"[{response['agent']}]")
                lines.append(f"Status: {_response_status(response)}")
                lines.append(f"Título: {_fmt_pct(response['title_pct'])}; aceitação: {response['support_score']:.2f}")
                lines.append(f"Mensagem: {_compact_chat_text(response['answer'])}")
                if response.get("leadership_bid"):
                    proposed = str(response.get("proposed_next_question", "") or "").strip()
                    rationale = str(response.get("leadership_rationale", "") or "").strip()
                    if proposed:
                        lines.append(f"Proposta de próxima pergunta: {_compact_chat_text(proposed)}")
                    if rationale:
                        lines.append(f"Racional de protagonismo: {_compact_chat_text(rationale)}")
            lines.append("")
            lines.append(
                f"Próximo protagonista: {turn['next_protagonist']} | "
                f"consenso: {_fmt_pct(turn['consensus_title_pct'])} | "
                f"dispersão: {turn['consensus_spread_pct']:.1f} p.p."
            )
    return "\n".join(lines).strip() + "\n"


def render_decision_flow_svg(bundle: ReportBundle) -> str:
    model_title = float(bundle.stage_probabilities.get("titulo", 0.0))
    rounds = int(bundle.metadata.get("meeting_rounds", len(bundle.meeting_transcript) or 0))
    influence = sorted(bundle.model_influence_pct.items(), key=lambda item: item[1], reverse=True)[:3]
    influence_text = " | ".join(f"{agent}: {pct:.1f}%" for agent, pct in influence) or "influência em cálculo"

    boxes = [
        ("1", "Fontes + eventos", "fontes próprias e eventos recentes com fonte, data e efeito"),
        ("2", "Sala multi-modelo", f"{rounds} rodadas; protagonismo por discordância ou mérito"),
        ("3", "Funil final (simulação + sala)", f"Brasil título {_fmt_pct(model_title)}"),
        ("4", "Auditoria", "fontes, custos, influência, participação e nome/versão declarados"),
    ]
    box_svg = []
    x_positions = [50, 330, 610, 890]
    for (number, title, subtitle), x in zip(boxes, x_positions, strict=True):
        box_svg.append(
            f"""
  <g>
    <rect x="{x}" y="190" width="230" height="170" rx="8" fill="#ffffff" stroke="#233142" stroke-width="2"/>
    <circle cx="{x + 32}" cy="226" r="18" fill="#1f7a8c"/>
    <text x="{x + 32}" y="232" text-anchor="middle" font-size="18" fill="#ffffff" font-weight="700">{html.escape(number)}</text>
    <text x="{x + 24}" y="275" font-size="23" fill="#17202a" font-weight="700">{html.escape(title)}</text>
    <foreignObject x="{x + 24}" y="292" width="184" height="70">
      <div xmlns="http://www.w3.org/1999/xhtml" style="font: 16px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; color:#34495e; line-height:1.25;">{html.escape(subtitle)}</div>
    </foreignObject>
  </g>"""
        )
    arrows = """
  <path d="M 282 275 L 322 275" stroke="#1f7a8c" stroke-width="4" fill="none" marker-end="url(#arrow)"/>
  <path d="M 562 275 L 602 275" stroke="#1f7a8c" stroke-width="4" fill="none" marker-end="url(#arrow)"/>
  <path d="M 842 275 L 882 275" stroke="#1f7a8c" stroke-width="4" fill="none" marker-end="url(#arrow)"/>"""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675" viewBox="0 0 1200 675" role="img" aria-labelledby="title desc">
  <title id="title">Fluxo de decisão Brasil Copa 2026</title>
  <desc id="desc">Fluxo limpo com fontes próprias, sala multi-modelo, consenso principal e auditoria do run.</desc>
  <defs>
    <marker id="arrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto">
      <path d="M2,2 L10,6 L2,10 Z" fill="#1f7a8c"/>
    </marker>
  </defs>
  <rect width="1200" height="675" fill="#f7f9fb"/>
  <text x="60" y="78" font-size="38" fill="#17202a" font-weight="800">Fluxo de decisão</text>
  <text x="60" y="118" font-size="21" fill="#34495e">Cada modelo escolhe fontes frescas, recebe eventos recentes e declara nome/versão no JSON.</text>
  {''.join(box_svg)}
  {arrows}
  <rect x="60" y="430" width="1080" height="88" rx="8" fill="#e8f3f5" stroke="#c7dde2"/>
  <text x="86" y="466" font-size="22" fill="#17202a" font-weight="700">Influência no consenso</text>
  <text x="86" y="497" font-size="19" fill="#34495e">{html.escape(influence_text)}</text>
  <text x="60" y="596" font-size="18" fill="#596875">Leitura: o número final sai do debate entre modelos; o mediador apenas distribui regras e registra a sala.</text>
</svg>
"""

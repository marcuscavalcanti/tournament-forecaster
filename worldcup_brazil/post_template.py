from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

MAX_POST_CHARS = 3000

ORDINAIS = [
    "PRIMEIRO", "SEGUNDO", "TERCEIRO", "QUARTO", "QUINTO", "SEXTO", "SÉTIMO", "OITAVO",
]

WEEKDAYS_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MONTHS_PT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

PHASE_ORDER = ["16 avos", "Oitavas", "Quartas", "Semifinal", "Final"]
PHASE_HEADERS = {
    "16 avos": "16 AVOS",
    "Oitavas": "OITAVAS",
    "Quartas": "QUARTAS",
    "Semifinal": "SEMIFINAL",
    "Final": "FINAL",
}

# Template fixo da série (origem: Marcus, 11/jun/2026). As partes fora de chaves
# de formatação são contrato: o pipeline preenche os campos dinâmicos e NÃO pode
# alterar o esqueleto. O editor opcional só pode fazer append ao final.
TEMPLATE = """{round_header}

{title}

{model_intro}

👉 {next_game_header}

{next_game_line}

{rest_group_line}

O CAMINHO ATÉ O HEXA, adversário por adversário (no mata-mata não tem empate: ou passa, ou volta pra casa):

{path_blocks}RESUMO DA CAMINHADA: o Brasil chega nos 16 avos em {r16_pct} dos cenários, oitavas em {r8_pct}, quartas em {qf_pct}, na semifinal em {sf_pct}, na final em {final_pct}... e levanta a taça em {title_pct} 🏆.

{change_section}
{backstage_section}📊 NÚMEROS DA RODADA:
{round_stats}

{run_note}
⚠️ Propositalmente, o modelo da OPTA, que fez 350K simulações para chegar nos resultados e favoritos da Copa, é a única fonte não permitida dos modelos consultarem.

Chegou agora? O post #1 explica tudo:
https://www.linkedin.com/posts/marcuscavalcanti_copacomachismo-brasil-brazil-share-7470889508763344896-6dqG/?utm_source=share&utm_medium=member_desktop&rcm=ACoAAAAiNX0BNM7cvCA_laP0QxrgOSoYAp3D9ko

Próximo post: véspera/dia de Brasil x {next_post_game}, com o mapa recalculado.

Galera do bolão: {palpite_bolao}. Usem com moderação.

#CopaComAchismo #Brasil #Brazil #WorldCup2026 #Futebol #Football #Soccer #Hexa #WorldCup #CopaDoMundo
"""

PHASE_BLOCK = """➡️ {header} ({phase_date}){phase_venue}
• Mais provável: {ml_opp} ({ml_scn} de chance desse cruzamento) → {ml_label}: {ml_br} | {ml_opp}: {ml_opp_pct}
• Alternativa: {alt_opp} ({alt_scn}) → {alt_label}: {alt_br} | {alt_opp}: {alt_opp_pct}

"""


def _pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(number - round(number)) < 0.05:
        return f"{round(number)}%"
    return f"{number:.1f}".replace(".", ",") + "%"


def _pct_int(value: Any) -> str:
    """Percentual arredondado para inteiro — linguagem de arquibancada do template."""
    try:
        return f"{round(float(value))}%"
    except (TypeError, ValueError):
        return "—"


def _short_date(raw: Any) -> str:
    text = str(raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        parsed = date.fromisoformat(text)
        return f"{parsed.day}/{MONTHS_PT[parsed.month - 1]}"
    return text or "data a definir"


def _post_game_label(match: Any) -> str:
    return f"{getattr(match, 'opponent', '')} ({_short_date(getattr(match, 'match_date', ''))})"


def _group_state(bundle: Any) -> dict[str, Any]:
    metadata = getattr(bundle, "metadata", {}) or {}
    direct = metadata.get("group_state")
    if isinstance(direct, dict) and direct:
        return direct
    monte_carlo = metadata.get("monte_carlo") if isinstance(metadata.get("monte_carlo"), dict) else {}
    state = monte_carlo.get("group_state") if isinstance(monte_carlo, dict) else {}
    return state if isinstance(state, dict) else {}


def _first_place_pct(bundle: Any) -> str:
    state = _group_state(bundle)
    if state.get("brazil_first_pct") is not None:
        return _pct_int(state.get("brazil_first_pct"))
    group_summary = str(getattr(bundle, "group_summary", "") or "")
    first_place = re.search(r"1º:\s*~?(\d+(?:[,.]\d+)?)%", group_summary)
    if first_place:
        return _pct_int(first_place.group(1).replace(",", "."))
    return "—"


def _completed_group_context(bundle: Any) -> str:
    state = _group_state(bundle)
    results = state.get("completed_results") or []
    if not isinstance(results, list):
        return ""
    scores = [
        str(item.get("score") or "").strip()
        for item in results
        if isinstance(item, dict) and str(item.get("score") or "").strip()
    ]
    if not scores:
        return ""
    if len(scores) == 1:
        return f"Com {scores[0]}, "
    return f"Com {' e '.join(scores[:2])}, "


def _group_loss_pct(match: Any) -> float | None:
    try:
        brazil_pct = float(getattr(match, "brazil_pct", 0.0) or 0.0)
        draw_pct = getattr(match, "draw_pct", None)
        if draw_pct is None:
            opponent_pct = getattr(match, "opponent_pct", None)
            return None if opponent_pct is None else max(0.0, float(opponent_pct))
        return round(max(0.0, 100.0 - brazil_pct - float(draw_pct)), 1)
    except (TypeError, ValueError):
        return getattr(match, "opponent_pct", None)


def _model_intro(bundle: Any) -> str:
    metadata = getattr(bundle, "metadata", {}) or {}
    removed = list(metadata.get("removed_agent_slots") or [])
    influence = getattr(bundle, "model_influence_pct", {}) or {}
    participation = getattr(bundle, "model_participation", {}) or {}
    protagonists = participation.get("protagonist_counts") if isinstance(participation, dict) else {}
    active_count = len(influence) or (len(protagonists) if isinstance(protagonists, dict) else 0)
    if active_count and removed:
        return (
            f"Como prometi: neste run, {active_count} modelos ativos pesquisaram odds, rankings e notícias, "
            f"debateram e fecharam uma decisão em grupo; {len(removed)} removidos no planejamento."
        )
    if active_count:
        return (
            f"Como prometi: neste run, {active_count} modelos ativos pesquisaram odds, rankings e notícias, "
            "debateram e fecharam uma decisão em grupo."
        )
    return (
        "Como prometi: na véspera de cada jogo, os modelos pesquisam odds, rankings e notícias, "
        "debatem e fecham uma decisão em grupo."
    )


def _run_note(bundle: Any) -> str:
    metadata = getattr(bundle, "metadata", {}) or {}
    opponent_room = metadata.get("parallel_opponent_debriefing") or {}
    if opponent_room.get("enabled") and not bool(opponent_room.get("usable_for_main_room", True)):
        return "⚠️ Nota do run: cruzamentos sem consenso lateral; usei Monte Carlo/bracket oficial.\n\n"
    return ""


def _analysis_short_date(bundle: Any) -> str:
    raw = str(getattr(bundle, "generated_at_iso", "") or "")
    try:
        parsed = datetime.fromisoformat(raw).date()
        return _short_date(parsed.isoformat()).upper()
    except ValueError:
        return "ÚLTIMA ANÁLISE"


def _parse_group_date(raw: Any, *, year: int) -> date | None:
    text = str(raw or "").strip().lower()
    match = re.fullmatch(r"(\d{1,2})/([a-zç]{3})", text)
    if not match:
        return None
    day = int(match.group(1))
    month_token = unicodedata.normalize("NFKD", match.group(2)).encode("ascii", "ignore").decode("ascii")
    months_ascii = [unicodedata.normalize("NFKD", m).encode("ascii", "ignore").decode("ascii") for m in MONTHS_PT]
    if month_token not in months_ascii:
        return None
    return date(year, months_ascii.index(month_token) + 1, day)


def _venue_suffix(value: Any) -> str:
    text = str(value or "").strip()
    return f" - {text}" if text else ""


def _normalize_beat(text: str) -> str:
    return unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").strip().lower()


def _truncate_words(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    window = clean[:limit]
    sentence_end = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if sentence_end >= int(limit * 0.45):
        return window[: sentence_end + 1]
    clause_end = max(window.rfind("; "), window.rfind(" — "), window.rfind(", mas "))
    if clause_end >= int(limit * 0.45):
        return window[:clause_end].rstrip(",;") + "."
    and_end = window.rfind(" e ")
    if and_end >= int(limit * 0.6):
        return window[:and_end].rstrip(",;") + "."
    cut = window.rsplit(" ", 1)[0]
    return cut.rstrip(",;:.") + "…"


JARGON_GLOSSARY = [
    ("modelo principal", "modelo"),
    ("sinal configurado", "sinal"),
    ("title_pct", "chance de título"),
    ("team_context", "contexto"),
    ("rating_delta", "ajuste de nota"),
    ("ratings", "notas de força"),
    ("rating", "nota"),
    ("player-prop", "aposta de desempenho individual"),
    ("output", "resultado"),
    ("source_urls", "fontes"),
    ("scenario_pct", "chance do cruzamento"),
    ("brazil_pct", "chance do Brasil"),
    ("monte carlo", "simulação"),
    ("weak_prior", "modo cauteloso"),
    ("prior", "ponto de partida"),
    ("de-vigado", "sem margem das casas"),
    ("de-vig", "sem margem das casas"),
    ("overround", "margem total das casas"),
    ("prediction market", "mercado de previsão"),
    ("prior_rating_sigma", "incerteza da nota"),
    ("ci", "intervalo"),
]

# Comparados em forma normalizada (sem acento, minúsculas) — ver _normalize_beat.
EVENT_HINTS = (
    "lesao", "lesion", "fora da copa", "fora do ano", "corte", "cortado", "suspens",
    "stale", "fantasma", "errado", "errada", "apto", "duvida", "desfalque",
    "mercado", "odds", "cotac", "titular",
)

BEHAVIOR_HINTS = (
    "erro de fonte", "fonte nova", "verificacao fresca", "nao sustenta", "grupo c",
    "nao titulo", "disputo a lideranca", "assumo o protagonismo", "rejeito",
)

ABSTRACT_HINTS = (
    "convergencia auditavel", "simulacao configurad", "premissa", "central de consenso",
)


def _plain_language(sentence: str) -> str:
    result = re.sub(r"\s*\([^)]*\)", "", sentence)
    for jargon, plain in JARGON_GLOSSARY:
        result = re.sub(rf"\b{re.escape(jargon)}\b", plain, result, flags=re.IGNORECASE)
    return " ".join(result.split())


def _sentence_score(sentence: str) -> int:
    lowered = _normalize_beat(sentence)
    score = 0
    if any(hint in lowered for hint in BEHAVIOR_HINTS):
        score += 4
    if any(hint in lowered for hint in EVENT_HINTS):
        score += 2
    if re.search(r"\d", sentence):
        score += 1
    if any(hint in lowered for hint in ABSTRACT_HINTS) and not any(hint in lowered for hint in BEHAVIOR_HINTS):
        score -= 2
    if lowered.startswith(("concordo", "discordo", "sim,")):
        score -= 1
    return score


def _best_sentence(answer: str) -> tuple[int, str]:
    """Sentença com mais substância: evento concreto vale 2, número vale 1."""
    best_score, best = 0, ""
    for sentence in re.split(r"(?<=[.!?])\s+|;\s+", " ".join(str(answer or "").split())):
        if len(sentence) < 25:
            continue
        score = _sentence_score(sentence)
        if score > best_score:
            best_score, best = score, sentence
    if best:
        best = re.sub(r"^\s*(?:\d+[\)\.]|[-–•])\s*", "", best)
        head, separator, tail = best.partition(":")
        if separator and len(head) <= 35 and _sentence_score(tail) >= best_score:
            best = tail.strip()
    return best_score, best


def _valid_room_response(response: Any) -> bool:
    return not (response.get("removed_from_main") or response.get("used_fallback"))


def _source_correction_beat(bundle: Any) -> str | None:
    for turn in getattr(bundle, "meeting_transcript", []) or []:
        if not isinstance(turn, dict):
            continue
        round_index = turn.get("round")
        for response in turn.get("responses", []) or []:
            if not _valid_room_response(response):
                continue
            answer = str(response.get("answer") or "")
            normalized = _normalize_beat(answer)
            if "polymarket" in normalized and "grupo c" in normalized and "titulo" in normalized:
                return (
                    f"Rodada {round_index} — {response.get('agent')} travou o consenso: "
                    "Polymarket 72% era Grupo C, não título; a sala rejeitou usar essa leitura para derrubar o Hexa."
                )
            if "erro de fonte" in normalized and ("nao sustenta" in normalized or "seleção de âncora" in answer.lower()):
                score, sentence = _best_sentence(answer)
                if score >= 3 and sentence:
                    fact = _truncate_words(_plain_language(sentence), 125)
                    if not fact.endswith((".", "!", "?", "…")):
                        fact += "."
                    return f"Rodada {round_index} — {response.get('agent')} travou o consenso: {fact}"
    return None


def _protagonist_behavior_beat(bundle: Any) -> str | None:
    participation = getattr(bundle, "model_participation", {}) or {}
    protagonist_counts = participation.get("protagonist_counts") or {}
    if not protagonist_counts:
        return None
    agent, count = max(protagonist_counts.items(), key=lambda kv: kv[1])
    try:
        count_int = int(count)
    except (TypeError, ValueError):
        return None
    if count_int < 2:
        return None
    unit = "vez" if count_int == 1 else "vezes"
    final_agent = participation.get("last_consensus_protagonist")
    if final_agent == agent:
        return (
            f"{agent} virou protagonista {count_int} {unit}; "
            "a pergunta final combinou simulação, apostas e mercado."
        )
    return (
        f"{agent} virou protagonista {count_int} {unit}: a sala trocou liderança por mérito, "
        "não por ordem fixa."
    )


def _curated_behavior_beats(bundle: Any) -> list[str]:
    beats = [_source_correction_beat(bundle), _protagonist_behavior_beat(bundle)]
    return [beat for beat in beats if beat]


def _extract_beats(bundle: Any) -> list[str]:
    """Bastidores com a substância minerada das falas válidas da sala.

    Cada bastidor carrega o fato concreto (jogador, evento, número) da sentença
    mais rica da fala, com jargão traduzido. Regra do Marcus (11/jun): bastidor
    sem substância não vale a tinta — sem 2 lances fortes, a seção sai do post."""
    curated = _curated_behavior_beats(bundle)
    scored: list[tuple[int, str, str]] = []
    for turn in getattr(bundle, "meeting_transcript", []) or []:
        if not isinstance(turn, dict):
            continue
        round_index = turn.get("round")
        for response in turn.get("responses", []) or []:
            if not _valid_room_response(response):
                continue
            if not response.get("disagreed"):
                continue
            score, sentence = _best_sentence(response.get("answer", ""))
            if score < 2:
                continue
            fact = _truncate_words(_plain_language(sentence), 130)
            if not fact.endswith((".", "!", "?", "…")):
                fact += "."
            agent = str(response.get("agent") or "")
            if _normalize_beat(response.get("answer", "")).startswith("concordo"):
                scored.append((score, agent, f"Rodada {round_index} — {agent} foi conferir antes: {fact}"))
            else:
                scored.append((score + 1, agent, f"Rodada {round_index} — {agent} bateu de frente: {fact}"))
        invalidated = turn.get("invalidated_protagonist_question")
        if invalidated:
            scored.append(
                (
                    2,
                    str(invalidated.get("agent") or ""),
                    f"A mesa anulou uma fala do {invalidated.get('agent')}: citou adversário "
                    "que nem pode cruzar com o Brasil naquela fase. Fora da chave, não vale.",
                )
            )
    scored.sort(key=lambda item: -item[0])
    if not scored:
        return curated[:2]
    fallback: list[str] = []
    top_score, top_agent, top_beat = scored[0]
    second = next((beat for _, agent, beat in scored[1:] if agent != top_agent), None)
    if second is None and len(scored) > 1:
        second = scored[1][2]
    fallback = [top_beat, second] if second else [top_beat]

    beats: list[str] = []
    seen: set[str] = set()
    for beat in curated + fallback:
        if not beat:
            continue
        key = _normalize_beat(beat)
        if key in seen:
            continue
        seen.add(key)
        beats.append(beat)
        if len(beats) >= 2:
            break
    return beats


def _backstage_section(beats: list[str]) -> str:
    if len(beats) < 2:
        return ""
    return (
        "DOIS BASTIDORES DA REUNIÃO DE HOJE:\n\n"
        f"1️⃣ {beats[0]}\n\n"
        f"2️⃣ {beats[1]}\n\n"
    )


QUANTI_HINTS = ("rating", "odds", "bet", "mercado", "sofascore", "desempenho", "performance", "elo")


def _build_round_stats(bundle: Any, *, slots: int = 3) -> str:
    """Números da rodada: pool ponderado, bullets densos, sem dado repetido."""
    candidates: list[tuple[float, str, str]] = []
    metadata = getattr(bundle, "metadata", {}) or {}

    influence = getattr(bundle, "model_influence_pct", {}) or {}
    valid_influence = {k: float(v) for k, v in influence.items() if v is not None}
    participation = getattr(bundle, "model_participation", {}) or {}
    messages = participation.get("total_messages")
    rounds = participation.get("total_rounds")
    if messages and rounds and len(valid_influence) >= 3:
        top_agent, top_value = max(valid_influence.items(), key=lambda kv: kv[1])
        low_agent, low_value = min(valid_influence.items(), key=lambda kv: kv[1])
        tied = [k for k, v in valid_influence.items() if k != top_agent and abs(v - top_value) < 0.05]
        if tied:
            influence_text = (
                f"{top_agent.split()[0]} e {tied[0].split()[0]} lideraram ({_pct(top_value)}); "
                f"{low_agent.split()[0]} quase não moveu ({_pct(low_value)})"
            )
        else:
            influence_text = (
                f"{top_agent.split()[0]} liderou ({_pct(top_value)}); "
                f"{low_agent.split()[0]} quase não moveu ({_pct(low_value)})"
            )
        candidates.append((110, "perfil_sala", f"💬 {messages} mensagens em {rounds} rodadas; {influence_text}"))

    costs = (getattr(bundle, "model_token_costs", {}) or {}).get("total") or {}
    cost_usd = costs.get("cost_usd")
    calls = costs.get("calls")
    tokens_k = int(costs.get("total_tokens") or 0) // 1000
    if cost_usd and calls and tokens_k:
        usd = f"{float(cost_usd):.2f}".replace(".", ",")
        candidates.append(
            (55, "custo", f"💰 US$ {usd} a reunião — {calls} chamadas, {tokens_k} mil tokens")
        )
    elif cost_usd:
        usd = f"{float(cost_usd):.2f}".replace(".", ",")
        candidates.append((50, "custo", f"💰 reunião inteira: US$ {usd} de IA"))
    if tokens_k >= 350:
        candidates.append(
            (45, "custo", f"📚 {tokens_k} mil tokens lidos e escritos — um 'Senhor dos Anéis' por reunião")
        )

    if len(valid_influence) >= 3:
        top_agent, top_value = max(valid_influence.items(), key=lambda kv: kv[1])
        low_agent, low_value = min(valid_influence.items(), key=lambda kv: kv[1])
        gap_weight = 60 + min(30.0, top_value - low_value)
        tied = [k for k, v in valid_influence.items() if k != top_agent and abs(v - top_value) < 0.05]
        if tied:
            line = (
                f"🧭 {top_agent.split()[0]} e {tied[0].split()[0]} empataram como voz mais forte "
                f"({_pct(top_value)}); {low_agent.split()[0]} quase não pesou ({_pct(low_value)})"
            )
        else:
            line = (
                f"🧭 {top_agent.split()[0]} mandou no número final ({_pct(top_value)}); "
                f"{low_agent.split()[0]} quase não pesou ({_pct(low_value)})"
            )
        candidates.append((gap_weight, "influencia", line))

    sources = getattr(bundle, "sources", None) or []
    if messages and rounds and sources:
        candidates.append(
            (70, "debate", f"💬 {messages} mensagens, {rounds} rodadas e {len(sources)} fontes até bater o martelo")
        )
    elif messages and rounds:
        candidates.append((68, "debate", f"💬 {messages} mensagens em {rounds} rodadas de debate"))
    elif sources:
        candidates.append((55, "debate", f"🔎 {len(sources)} fontes consultadas"))

    mc = metadata.get("monte_carlo") or {}
    iterations = mc.get("iterations")
    if iterations:
        total = int(iterations) * 2
        candidates.append((50, "simulacao", f"🎲 {total // 1000} mil copas simuladas no dia"))

    team_context = mc.get("team_context") or {}
    signals = [
        signal
        for adjustment in team_context.get("team_adjustments", []) or []
        for signal in adjustment.get("signals", []) or []
    ]
    if len(signals) >= 5:
        quanti = sum(
            1
            for signal in signals
            if any(hint in _normalize_beat(signal.get("category", "")) for hint in QUANTI_HINTS)
        )
        quanti_pct = round(quanti / len(signals) * 100)
        candidates.append(
            (
                65,
                "contexto",
                f"⚖️ contexto: {quanti_pct}% números, {100 - quanti_pct}% fatos (lesões, notícias)",
            )
        )

    opponent_room = metadata.get("parallel_opponent_debriefing") or {}
    if opponent_room.get("enabled") and not int(opponent_room.get("rounds") or 0):
        candidates.append(
            (75, "sala", "🕐 sala dos adversários estourou o tempo; a principal seguiu sem travar")
        )

    chosen: list[str] = []
    used_keys: set[str] = set()
    for _, key, line in sorted(candidates, key=lambda item: -item[0]):
        if key in used_keys:
            continue
        used_keys.add(key)
        chosen.append(line)
        if len(chosen) >= slots:
            break
    return "\n".join(f"• {item}" for item in chosen)


def _knockout_pairs(bundle: Any) -> dict[str, dict[str, Any]]:
    pairs: dict[str, dict[str, Any]] = {}
    for match in getattr(bundle, "knockout_matches", []) or []:
        phase = str(getattr(match, "phase", "")).strip()
        if phase not in PHASE_HEADERS:
            continue
        slot = "ml" if bool(getattr(match, "most_likely", False)) else "alt"
        pairs.setdefault(phase, {})[slot] = match
    return pairs


def _stage_number(bundle: Any, key: str) -> float | None:
    try:
        return float((getattr(bundle, "stage_probabilities", {}) or {}).get(key))
    except (TypeError, ValueError):
        return None


def _most_likely_phase_match(bundle: Any, phase: str) -> Any | None:
    for match in getattr(bundle, "knockout_matches", []) or []:
        if str(getattr(match, "phase", "")).strip() == phase and bool(getattr(match, "most_likely", False)):
            return match
    return None


def _changed_stage_bullet(previous_bundle: Any, bundle: Any) -> str | None:
    prev_title, title = _stage_number(previous_bundle, "titulo"), _stage_number(bundle, "titulo")
    prev_final, final = _stage_number(previous_bundle, "final"), _stage_number(bundle, "final")
    if prev_title is None or title is None or prev_final is None or final is None:
        return None
    return f"• Hexa {_pct(prev_title)}→{_pct(title)}; final {_pct_int(prev_final)}→{_pct_int(final)}."


def _changed_quarter_bullet(previous_bundle: Any, bundle: Any) -> str | None:
    previous = _most_likely_phase_match(previous_bundle, "Quartas")
    current = _most_likely_phase_match(bundle, "Quartas")
    if previous is None or current is None:
        return None
    prev_opp = str(getattr(previous, "opponent", "") or "adversário")
    curr_opp = str(getattr(current, "opponent", "") or "adversário")
    prev_scn, curr_scn = getattr(previous, "scenario_pct", None), getattr(current, "scenario_pct", None)
    prev_br, curr_br = getattr(previous, "brazil_pct", None), getattr(current, "brazil_pct", None)
    if prev_opp == curr_opp:
        return (
            f"• Quartas: {curr_opp} {_pct_int(prev_scn)}→{_pct_int(curr_scn)}; "
            f"Brasil {_pct_int(prev_br)}→{_pct_int(curr_br)}."
        )
    return (
        f"• Quartas: {prev_opp} ({_pct_int(prev_scn)}) → {curr_opp} ({_pct_int(curr_scn)}); "
        f"Brasil {_pct_int(prev_br)}→{_pct_int(curr_br)}."
    )


def _changed_reason_bullet(bundle: Any) -> str:
    correction = _source_correction_beat(bundle)
    if correction and "Polymarket 72%" in correction:
        return "• Por quê: Polymarket era Grupo C; caminho recalculado."
    return "• Por quê: cruzamentos, notícias e mercados foram reponderados."


def _change_section(bundle: Any, previous_bundle: Any | None) -> str:
    if previous_bundle is None:
        return (
            "Mapa muda: recalculo grupos, cruzamentos e mercados antes de cada post.\n\n"
        )
    bullets = [
        bullet
        for bullet in (
            _changed_stage_bullet(previous_bundle, bundle),
            _changed_quarter_bullet(previous_bundle, bundle),
            _changed_reason_bullet(bundle),
        )
        if bullet
    ]
    if not bullets:
        return (
            "O QUE MUDOU DESDE A ÚLTIMA ANÁLISE:\n"
            "• O mapa foi recalculado, mas sem mudança material suficiente para virar headline.\n\n"
        )
    return f"O QUE MUDOU DESDE {_analysis_short_date(previous_bundle)}:\n" + "\n".join(bullets[:3]) + "\n\n"


def render_template_post(
    bundle: Any,
    *,
    post_index: int,
    run_date: date | None = None,
    previous_bundle: Any | None = None,
) -> str:
    if run_date is None:
        run_date = datetime.fromisoformat(str(bundle.generated_at_iso)).date()

    group_matches = list(getattr(bundle, "group_matches", []) or [])
    if not group_matches:
        raise ValueError("template post requer group_matches no bundle")

    dated = [(m, _parse_group_date(getattr(m, "match_date", ""), year=run_date.year)) for m in group_matches]
    upcoming = [(m, d) for m, d in dated if d is not None and d >= run_date]
    featured, featured_date = (upcoming[0] if upcoming else dated[0])
    featured_is_first = featured is group_matches[0]

    ordinal = ORDINAIS[post_index - 1] if 1 <= post_index <= len(ORDINAIS) else f"{post_index}º"
    title = f"{ordinal} PALPITE DA SÉRIE: Brasil x {getattr(featured, 'opponent', '')}"

    weekday = WEEKDAYS_PT[featured_date.weekday()] if featured_date else "em breve"
    header_label = "A ESTREIA" if featured_is_first else "O PRÓXIMO JOGO"
    next_game_header = f"{header_label} ({weekday}, {getattr(featured, 'venue', '') or 'local a definir'}):"

    win = _pct_int(getattr(featured, "brazil_pct", None))
    draw_value = getattr(featured, "draw_pct", None)
    loss_value = _group_loss_pct(featured)
    parts = [f"{win} vitória"]
    if draw_value:
        parts.append(f"{_pct_int(draw_value)} empate")
    if loss_value:
        parts.append(f"{_pct_int(loss_value)} derrota")
    next_game_line = f"BRASIL x {str(getattr(featured, 'opponent', '')).upper()} — " + " | ".join(parts)

    remaining = [m for m, d in dated if m is not featured and d is not None and d > (featured_date or run_date)]
    next_post_match = remaining[0] if remaining else featured
    first_place_pct = _first_place_pct(bundle)
    completed_context = _completed_group_context(bundle)
    if remaining:
        listed = " e ".join(
            f"{getattr(m, 'opponent', '')} ({_pct_int(getattr(m, 'brazil_pct', None))} de vitória)" for m in remaining
        )
        lead = f"{completed_context}depois" if completed_context else "Depois"
        rest_group_line = (
            f"{lead} vêm {listed}. Brasil termina em 1º do grupo em {first_place_pct} dos cenários."
        )
    else:
        lead = f"{completed_context}fase" if completed_context else "Fase"
        rest_group_line = (
            f"{lead} de grupos encerrada. Brasil terminou o grupo com "
            f"1º lugar projetado em {first_place_pct} dos cenários."
        )

    pairs = _knockout_pairs(bundle)
    blocks: list[str] = []
    for phase in PHASE_ORDER:
        pair = pairs.get(phase, {})
        ml, alt = pair.get("ml"), pair.get("alt")
        if ml is None or alt is None:
            raise ValueError(f"template post requer cenário mais provável e alternativa para {phase}")
        is_final = phase == "Final"
        blocks.append(
            PHASE_BLOCK.format(
                header=PHASE_HEADERS[phase],
                phase_date=_short_date(getattr(ml, "match_date", "")),
                phase_venue=_venue_suffix(getattr(ml, "venue", "")),
                ml_opp=getattr(ml, "opponent", ""),
                ml_scn=_pct_int(getattr(ml, "scenario_pct", None)),
                ml_label="Brasil HEXA" if is_final else "Brasil passa",
                ml_br=_pct_int(getattr(ml, "brazil_pct", None)),
                ml_opp_pct=_pct_int(getattr(ml, "opponent_pct", None)),
                alt_opp=getattr(alt, "opponent", ""),
                alt_scn=_pct_int(getattr(alt, "scenario_pct", None)),
                alt_label="Brasil HEXA" if is_final else "Brasil",
                alt_br=_pct_int(getattr(alt, "brazil_pct", None)),
                alt_opp_pct=_pct_int(getattr(alt, "opponent_pct", None)),
            )
        )

    mc_stages = ((getattr(bundle, "metadata", {}) or {}).get("monte_carlo") or {}).get("stage_probabilities") or {}
    stage = dict(getattr(bundle, "stage_probabilities", {}) or {})
    backstage = _backstage_section(_extract_beats(bundle))

    bolao = [win]
    if draw_value:
        bolao.append(_pct_int(draw_value))
    if loss_value is not None:
        bolao.append(_pct_int(loss_value))
    palpite = " / ".join(value.rstrip("%") for value in bolao)

    title_pct_text = _pct(stage.get("titulo"))
    round_header = (
        f"⚽ {_short_date(getattr(featured, 'match_date', ''))} · Brasil x {getattr(featured, 'opponent', '')} · "
        f"{win.rstrip('%')}/{_pct_int(draw_value).rstrip('%') if draw_value else '0'}/"
        f"{_pct_int(loss_value).rstrip('%') if loss_value else '0'} · Hexa: {title_pct_text}"
    )
    round_stats = _build_round_stats(bundle, slots=3 if backstage else 4)
    text = TEMPLATE.format(
        round_header=round_header,
        round_stats=round_stats,
        run_note=_run_note(bundle),
        model_intro=_model_intro(bundle),
        title=title,
        next_game_header=next_game_header,
        next_game_line=next_game_line,
        rest_group_line=rest_group_line,
        path_blocks="".join(blocks),
        r16_pct=_pct_int(mc_stages.get("16_avos")),
        r8_pct=_pct_int(mc_stages.get("oitavas")),
        qf_pct=_pct_int(stage.get("quartas")),
        sf_pct=_pct_int(stage.get("semifinal")),
        final_pct=_pct_int(stage.get("final")),
        title_pct=title_pct_text,
        change_section=_change_section(bundle, previous_bundle),
        backstage_section=backstage,
        next_post_game=_post_game_label(next_post_match),
        palpite_bolao=palpite,
    )

    text = re.sub(r"(?<=\S)  +(?=\S)", " ", text)
    return _trim_to_limit(text, bundle)


def _trim_to_limit(text: str, bundle: Any) -> str:
    """Escada de corte: números primeiro, depois encurta bastidores; estádios são o último recurso."""
    trimmed = text
    for _ in range(3):
        if len(trimmed) <= MAX_POST_CHARS:
            return trimmed
        section = re.search(r"NÚMEROS DA RODADA:\n(?:• [^\n]+\n)*• [^\n]+", trimmed)
        if not section or section.group(0).count("• ") <= 1:
            break
        trimmed_section = section.group(0).rsplit("\n• ", 1)[0]
        trimmed = trimmed[: section.start()] + trimmed_section + trimmed[section.end():]
    for limit in (155, 135, 120, 95):
        if len(trimmed) <= MAX_POST_CHARS:
            return trimmed
        trimmed = re.sub(
            r"(?m)^(1️⃣|2️⃣) (.+)$",
            lambda match: f"{match.group(1)} {_truncate_words(match.group(2), limit)}",
            trimmed,
        )
    if len(trimmed) <= MAX_POST_CHARS:
        return trimmed
    no_venues = re.sub(r"(➡️ [^\n(]+\([^)]*\)) - [^\n]+", r"\1", trimmed)
    if len(no_venues) <= MAX_POST_CHARS:
        return no_venues
    raise ValueError(
        f"post de template excede {MAX_POST_CHARS} caracteres mesmo após cortes ({len(no_venues)}); revisar conteúdo dinâmico"
    )


def validate_template_post(text: str, bundle: Any) -> None:
    """Gate executável: estrutura intacta, dados consistentes com o bundle, limite 3K."""
    errors: list[str] = []
    if len(text) > MAX_POST_CHARS:
        errors.append(f"{len(text)} caracteres (máximo {MAX_POST_CHARS})")
    if re.search(r"[{}]", text):
        errors.append("placeholder não resolvido (chaves remanescentes)")
    for sentinel in (
        "O CAMINHO ATÉ O HEXA",
        "RESUMO DA CAMINHADA",
        "Propositalmente, o modelo da OPTA",
        "NÚMEROS DA RODADA:",
        "#CopaComAchismo #Brasil #Brazil #WorldCup2026 #Futebol #Football #Soccer #Hexa",
    ):
        if sentinel not in text:
            errors.append(f"trecho fixo ausente: {sentinel[:40]}")
    stage = dict(getattr(bundle, "stage_probabilities", {}) or {})
    if stage and _pct(stage.get("titulo")) not in text:
        errors.append("percentual de título não bate com o bundle")
    for phase in PHASE_ORDER:
        if PHASE_HEADERS[phase] not in text:
            errors.append(f"fase ausente do caminho: {phase}")
    if "1️⃣" in text and "DOIS BASTIDORES DA REUNIÃO DE HOJE:" not in text:
        errors.append("bastidores presentes sem o cabeçalho fixo da seção")
    if errors:
        raise ValueError("post de template inválido: " + "; ".join(errors))


def apply_editor_append(base_text: str, edited_text: str) -> str:
    """Editor (LLM) só pode fazer append. Qualquer mutação do esqueleto é descartada."""
    candidate = str(edited_text or "")
    base = base_text.rstrip("\n")
    if not candidate.rstrip("\n").startswith(base):
        return base_text
    if len(candidate) > MAX_POST_CHARS:
        return base_text
    return candidate


def bundle_from_json(path: Path | str) -> Any:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    payload = raw.get("bundle", raw)

    def _ns(item: Any) -> Any:
        return SimpleNamespace(**item) if isinstance(item, dict) else item

    return SimpleNamespace(
        generated_at_iso=payload.get("generated_at_iso", ""),
        group_matches=[_ns(m) for m in payload.get("group_matches", [])],
        knockout_matches=[_ns(m) for m in payload.get("knockout_matches", [])],
        stage_probabilities=payload.get("stage_probabilities", {}),
        group_summary=payload.get("group_summary", ""),
        metadata=payload.get("metadata", {}),
        meeting_transcript=payload.get("meeting_transcript", []),
        model_participation=payload.get("model_participation", {}),
        model_influence_pct=payload.get("model_influence_pct", {}),
        model_token_costs=payload.get("model_token_costs", {}),
        sources=payload.get("sources", []),
    )

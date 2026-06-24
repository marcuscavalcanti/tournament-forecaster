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


def _completed_group_context(bundle: Any, *, before_date: date | None = None) -> str:
    scores = _completed_group_scores(bundle, before_date=before_date)
    if not scores:
        return ""
    if len(scores) == 1:
        return f"Com {scores[0]}, "
    return f"Com {_join_pt(scores)}, "


def _completed_group_scores(bundle: Any, *, before_date: date | None = None) -> list[str]:
    metadata = getattr(bundle, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    completed_matches = metadata.get("completed_group_matches") or []
    if isinstance(completed_matches, list) and completed_matches:
        return _scores_from_completed_group_matches(bundle, completed_matches, before_date=before_date)

    state = _group_state(bundle)
    results = state.get("completed_results") or []
    if not isinstance(results, list):
        return []
    scores = [
        str(item.get("score") or "").strip()
        for item in results
        if isinstance(item, dict) and str(item.get("score") or "").strip()
    ]
    return scores


def _scores_from_completed_group_matches(
    bundle: Any,
    completed_matches: list[Any],
    *,
    before_date: date | None = None,
) -> list[str]:
    group_matches = list(getattr(bundle, "group_matches", []) or [])
    group_teams = {"brasil"}
    group_teams.update(_normalize_beat(getattr(match, "opponent", "")) for match in group_matches)
    state = _group_state(bundle)
    brazil_group = str(state.get("brazil_group") or "").strip()
    for item in completed_matches:
        if not isinstance(item, dict):
            continue
        if "brasil" in {
            _normalize_beat(item.get("team_a", "")),
            _normalize_beat(item.get("team_b", "")),
        }:
            brazil_group = brazil_group or str(item.get("group") or "").strip()
            break

    dated_scores: list[tuple[date, int, str]] = []
    undated_scores: list[tuple[int, str]] = []
    year = _bundle_year(bundle)
    for index, item in enumerate(completed_matches):
        if not isinstance(item, dict):
            continue
        item_group = str(item.get("group") or "").strip()
        if brazil_group and item_group and item_group != brazil_group:
            continue
        team_a = str(item.get("team_a") or "").strip()
        team_b = str(item.get("team_b") or "").strip()
        if group_teams and not ({_normalize_beat(team_a), _normalize_beat(team_b)} <= group_teams):
            continue
        score = _completed_match_score(item)
        if not score:
            continue
        played_at = _parse_completed_match_date(item.get("date"), year=year)
        if before_date is not None and played_at is not None and played_at >= before_date:
            continue
        if played_at is None:
            undated_scores.append((index, score))
        else:
            dated_scores.append((played_at, index, score))
    ordered = [score for _played_at, _index, score in sorted(dated_scores)] + [
        score for _index, score in sorted(undated_scores)
    ]
    deduped: list[str] = []
    for score in ordered:
        if score not in deduped:
            deduped.append(score)
    return deduped


def _completed_match_score(item: dict[str, Any]) -> str:
    score = str(item.get("score") or "").strip()
    if score:
        return score
    team_a = str(item.get("team_a") or "").strip()
    team_b = str(item.get("team_b") or "").strip()
    if not team_a or not team_b:
        return ""
    try:
        score_a = int(item.get("score_a"))
        score_b = int(item.get("score_b"))
    except (TypeError, ValueError):
        return ""
    return f"{team_a} {score_a}-{score_b} {team_b}"


def _parse_completed_match_date(raw: Any, *, year: int) -> date | None:
    text = str(raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    return _parse_group_date(text, year=year)


def _group_match_has_completed_score(bundle: Any, match: Any) -> bool:
    opponent = _normalize_beat(getattr(match, "opponent", ""))
    if not opponent:
        return False
    for score in _completed_group_scores(bundle):
        normalized = _normalize_beat(score)
        if "brasil" in normalized and opponent in normalized:
            return True
    return False


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


def _join_pt(items: list[str]) -> str:
    clean = [item.strip() for item in items if item and item.strip()]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    return ", ".join(clean[:-1]) + f" e {clean[-1]}"


def _active_model_names(bundle: Any, removed: list[str]) -> list[str]:
    removed_set = {name.strip() for name in removed if name and name.strip()}
    source_plans = getattr(bundle, "source_plan_by_model", None)
    if isinstance(source_plans, dict) and source_plans:
        return [str(name) for name in source_plans if str(name).strip() and str(name) not in removed_set]

    participation = getattr(bundle, "model_participation", {}) or {}
    if not isinstance(participation, dict):
        return []
    consensus_participants = participation.get("last_consensus_participants")
    if isinstance(consensus_participants, list) and consensus_participants:
        return [
            str(name)
            for name in consensus_participants
            if str(name).strip() and str(name).strip() not in removed_set
        ]
    protagonists = participation.get("protagonist_counts")
    if isinstance(protagonists, dict):
        return [str(name) for name in protagonists if str(name).strip() and str(name) not in removed_set]
    return []


def _compact_removed_reason(reason: str) -> str:
    normalized = _normalize_beat(reason)
    if "fora do escopo" in normalized and "futebol competitivo" in normalized:
        return "fontes fora do escopo competitivo"
    if "falha operacional" in normalized and "sem resposta externa verificavel" in normalized:
        return "falha operacional sem fonte verificável"
    if "sem fonte" in normalized or "fonte verificavel" in normalized:
        return "sem fonte verificável"
    if "quota" in normalized or "429" in normalized or "credit" in normalized:
        return "limite/quota de API"
    text = str(reason or "").strip()
    return _truncate_words(text, 90) if text else "motivo operacional"


def _removed_models_clause(removed: list[str], reasons: dict[str, Any]) -> str:
    if not removed:
        return ""
    removed_names = _join_pt([str(name) for name in removed])
    verb = "saiu" if len(removed) == 1 else "saíram"
    reason_values = []
    for name in removed:
        reason = _compact_removed_reason(str(reasons.get(name, "") if isinstance(reasons, dict) else ""))
        if reason and reason not in reason_values:
            reason_values.append(reason)
    reason_text = f" por {'; '.join(reason_values[:2])}" if reason_values else ""
    return f"; {removed_names} {verb} no planejamento{reason_text}"


def _model_intro(bundle: Any) -> str:
    metadata = getattr(bundle, "metadata", {}) or {}
    removed = list(metadata.get("removed_agent_slots") or [])
    reasons = metadata.get("removed_agent_reasons") if isinstance(metadata, dict) else {}
    active_names = _active_model_names(bundle, [str(item) for item in removed])
    active_count = len(active_names)
    if active_count:
        names = f" ({_join_pt(active_names)})" if active_names else ""
        return (
            f"Como prometi: {active_count} modelos ativos{names} pesquisaram odds, rankings e notícias"
            f"{_removed_models_clause([str(item) for item in removed], reasons)}."
        )
    return (
        "Como prometi: na véspera de cada jogo, os modelos pesquisam odds, rankings e notícias, "
        "debatem e fecham uma decisão em grupo."
    )


def _run_note(bundle: Any) -> str:
    metadata = getattr(bundle, "metadata", {}) or {}
    notes: list[str] = []
    opponent_room = metadata.get("parallel_opponent_debriefing") or {}
    if opponent_room.get("enabled") and not bool(opponent_room.get("usable_for_main_room", True)):
        notes.append("cruzamentos sem consenso lateral; usei Monte Carlo/bracket oficial")
    if (
        opponent_room.get("enabled")
        and bool(opponent_room.get("usable_for_main_room", False))
        and opponent_room.get("phase_coverage_sufficient") is False
    ):
        notes.append("sala lateral validou o mapa, mas o ranking de adversários por fase segue ancorado no Monte Carlo")
    market_challenge = metadata.get("market_title_challenge") or {}
    if isinstance(market_challenge, dict) and bool(market_challenge.get("triggered")):
        low = market_challenge.get("market_low_pct")
        high = market_challenge.get("market_high_pct", low)
        if low is not None and high is not None and abs(float(high) - float(low)) >= 0.05:
            market = f"{_pct(low)}-{_pct(high)}"
        else:
            market = _pct(low)
        notes.append(
            "Mercado desafia o Hexa: "
            f"funil 60/40 {_pct(market_challenge.get('model_title_pct'))}; mercado {market}. Mantive funil"
        )
    context_warning_teams: list[str] = []
    for warning in getattr(bundle, "warnings", []) or []:
        warning_text = str(warning or "")
        if not (
            "Ajuste contextual fora da faixa de revisão" in warning_text
            or "Ajuste contextual pode estar subagrupado" in warning_text
            or "team_context_model_match_shock_without_calendar_anchor" in warning_text
        ):
            continue
        team_match = re.search(r":\s*([^:;]+?)\s+(?:teve|ficou|com|sem|gerou)", warning_text)
        if not team_match:
            team_match = re.search(r"\bpara\s+([^:;]+):", warning_text)
        team = team_match.group(1).strip() if team_match else ""
        if team and team not in context_warning_teams:
            context_warning_teams.append(team)
    if context_warning_teams:
        teams = ", ".join(context_warning_teams[:3])
        extra = " e outros" if len(context_warning_teams) > 3 else ""
        notes.append(f"ajustes contextuais em revisão ({teams}{extra})")
    if not notes:
        return ""
    return "⚠️ Nota do run: " + "; ".join(notes) + ".\n\n"


def _analysis_short_date(bundle: Any) -> str:
    raw = str(getattr(bundle, "generated_at_iso", "") or "")
    try:
        parsed = datetime.fromisoformat(raw).date()
        return _short_date(parsed.isoformat()).upper()
    except ValueError:
        return "ÚLTIMA ANÁLISE"


def _bundle_date(bundle: Any) -> date | None:
    raw = str(getattr(bundle, "generated_at_iso", "") or "")
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def _bundle_year(bundle: Any) -> int:
    parsed = _bundle_date(bundle)
    return parsed.year if parsed is not None else date.today().year


def _match_reference_label(match: Any, index: int, played_at: date) -> str:
    when = f"{played_at.day:02d}/{played_at.month:02d}"
    if index == 0:
        return f"A ESTREIA ({when})"
    opponent = str(getattr(match, "opponent", "") or "adversário").strip()
    return f"BRASIL x {opponent.upper()} ({when})"


def _change_reference_label(bundle: Any) -> str:
    reference_date = _bundle_date(bundle)
    group_matches = list(getattr(bundle, "group_matches", []) or [])
    if reference_date is not None and group_matches:
        year = _bundle_year(bundle)
        played = [
            (index, match, parsed)
            for index, match in enumerate(group_matches)
            if (parsed := _parse_group_date(getattr(match, "match_date", ""), year=year)) is not None
            and parsed <= reference_date
        ]
        if played:
            index, match, parsed = played[-1]
            return _match_reference_label(match, index, parsed)
    return _analysis_short_date(bundle)


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


def _last_sentence_boundary(text: str) -> int:
    matches = list(re.finditer(r"(?<!\d)[.!?]\s+", text))
    if not matches:
        return -1
    return matches[-1].start()


def _truncate_words(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    window = clean[:limit]
    sentence_end = _last_sentence_boundary(window)
    if sentence_end >= int(limit * 0.45):
        return window[: sentence_end + 1]
    clause_end = max(window.rfind("; "), window.rfind(" — "), window.rfind(", mas "))
    if clause_end >= int(limit * 0.45):
        return window[:clause_end].rstrip(",;") + "."
    and_end = window.rfind(" e ")
    if and_end >= int(limit * 0.6):
        before_and = window[:and_end].rstrip(",; ")
        if not re.search(r"\d\.?$", before_and):
            return before_and + "."
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
    "mercado", "odds", "cotac", "titular", "upset", "mata-mata", "tatico",
    "tatica", "coesao", "resiliencia", "superestim", "subestim", "japao",
    "holanda", "cauda", "risco ponderado",
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
    for sentence in re.split(r"(?<=[!?])\s+|(?<!\d\.)(?<=[.])\s+|;\s+", " ".join(str(answer or "").split())):
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


def _display_pct_token(raw: str) -> str:
    return f"{raw.replace('.', ',')}%"


def _matchup_label(answer: str) -> str:
    match = re.search(
        r"\bBrasil\s+x\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÀ-ÿ' -]{1,28}?)(?=[\s,.;:!?]|$)",
        answer,
        flags=re.IGNORECASE,
    )
    if not match:
        return "A chance debatida"
    opponent = " ".join(match.group(1).split()).strip(" ,.;:!?")
    return f"Brasil x {opponent}"


TEAM_BEAT_PATTERN = r"Holanda|Japão|Japao|Suécia|Suecia|Inglaterra|França|Franca|Argentina|Portugal"


def _display_team_token(raw: str) -> str:
    return raw.replace("Japao", "Japão").replace("Suecia", "Suécia").replace("Franca", "França")


def _tactical_reason_bits(answer: str) -> list[str]:
    normalized = _normalize_beat(answer)
    bits: list[str] = []
    if "neymar" in normalized and "raphinha" in normalized:
        bits.append("Neymar/Raphinha reduziram a criação")
    elif "neymar" in normalized:
        bits.append("Neymar pesou na criação")
    elif "raphinha" in normalized:
        bits.append("Raphinha pesou no corredor direito")
    if "coesao" in normalized or "criacao lenta" in normalized:
        bits.append("criação lenta entrou na conta")
    if "upset plaus" in normalized:
        bits.append("Japão virou upset plausível")
    elif "resiliencia" in normalized and "japao" in normalized:
        bits.append("Japão mostrou resiliência")
    return bits


def _weighted_pair_branch(compact: str) -> tuple[str, str] | None:
    pairs: list[tuple[str, float, str]] = []
    for match in re.finditer(
        rf"\b({TEAM_BEAT_PATTERN})\b\s+(\d{{1,3}}(?:[,.]\d+)?)\s*%\s*[x×]\s*(\d{{1,3}}(?:[,.]\d+)?)\s*%",
        compact,
        flags=re.IGNORECASE,
    ):
        win_raw = match.group(3)
        try:
            win_value = float(win_raw.replace(",", "."))
        except ValueError:
            continue
        pairs.append((_display_team_token(match.group(1)), win_value, win_raw))
    if not pairs:
        return None
    team, _value, raw = min(pairs, key=lambda item: item[1])
    return team, raw


def _branch_probability(compact: str) -> tuple[str, str] | None:
    pair = _weighted_pair_branch(compact)
    if pair:
        return pair
    cauda = re.search(
        rf"cauda[^.?!]{{0,90}}?\b({TEAM_BEAT_PATTERN})\b[^%]{{0,55}}?(\d{{1,3}}(?:[,.]\d+)?)\s*%",
        compact,
        flags=re.IGNORECASE,
    )
    if cauda:
        return _display_team_token(cauda.group(1)), cauda.group(2)
    direct = re.search(
        rf"\b({TEAM_BEAT_PATTERN})\b\s*(?:\(|em\s+)?(\d{{1,3}}(?:[,.]\d+)?)\s*%",
        compact,
        flags=re.IGNORECASE,
    )
    if direct:
        return _display_team_token(direct.group(1)), direct.group(2)
    return None


def _weighted_path_beat(round_index: Any, agent: str, answer: str) -> tuple[int, str] | None:
    compact = " ".join(str(answer or "").split())
    normalized = _normalize_beat(compact)
    weighted_frame = any(
        hint in normalized
        for hint in (
            "risco ponderado", "avanco ponderado", "bloco 2f", "essa media",
            "conta ponderada", "calculo ponderado",
        )
    )
    headline_frame = "headline" in normalized
    if not (weighted_frame or headline_frame) or "cauda" not in normalized:
        return None
    weighted = re.search(
        r"(?:risco\s+ponderad\w*|avan[cç]o\s+ponderad\w*|m[eé]dia|conta\s+ponderad\w*)"
        r"[^%]{0,60}?(\d{1,3}(?:[,.]\d+)?)\s*%",
        compact,
        flags=re.IGNORECASE,
    )
    if weighted is None:
        weighted = re.search(
            r"result\w*\s+em\s+(\d{1,3}(?:[,.]\d+)?)\s*%",
            compact,
            flags=re.IGNORECASE,
        )
    branch = _branch_probability(compact)
    if weighted_frame and weighted and branch:
        team, branch_pct = branch
        return (
            12,
            f"Rodada {round_index} — {agent}: 16 avos virou risco ponderado de "
            f"{_display_pct_token(weighted.group(1))}; cauda {team} a {_display_pct_token(branch_pct)}.",
        )
    headline = re.search(
        r"\bBrasil\s+(\d{1,3}(?:[,.]\d+)?)\s*%\s+vs\s+"
        rf"({TEAM_BEAT_PATTERN})\b",
        compact,
        flags=re.IGNORECASE,
    )
    if headline_frame and headline and branch:
        modal = _display_team_token(headline.group(2))
        team, branch_pct = branch
        return (
            11,
            f"Rodada {round_index} — {agent}: {modal} a {_display_pct_token(headline.group(1))} era headline; "
            f"cauda {team} a {_display_pct_token(branch_pct)}.",
        )
    return None


def _tactical_adjustment_beat(round_index: Any, agent: str, answer: str) -> tuple[int, str] | None:
    compact = " ".join(str(answer or "").split())
    normalized = _normalize_beat(compact)
    if not any(hint in normalized for hint in ("ajust", "reduz", "baix", "cai", "superestim", "subestim", "comprim")):
        return None
    percentages = re.findall(r"\b(\d{1,3}(?:[.,]\d+)?)\s*%", compact)
    if len(percentages) < 2:
        return None
    matchup = _matchup_label(compact)
    first, second = (_display_pct_token(percentages[0]), _display_pct_token(percentages[1]))
    direction = "caiu" if any(hint in normalized for hint in ("reduz", "baix", "cai", "superestim", "comprim")) else "foi ajustada"
    bits = _tactical_reason_bits(compact)
    reason = ""
    if bits:
        reason = "; " + "; ".join(bits[:3]) + "."
    else:
        score, sentence = _best_sentence(compact)
        if score >= 2 and sentence:
            reason = ": " + _truncate_words(_plain_language(sentence), 95)
            if not reason.endswith((".", "!", "?", "…")):
                reason += "."
    return (
        10 + len(bits),
        f"Rodada {round_index} — {agent}: {matchup} {direction} de {first} para {second}{reason}",
    )


def _valid_room_response(response: Any) -> bool:
    return not (response.get("removed_from_main") or response.get("used_fallback"))


def _beat_semantic_key(beat: str) -> str:
    normalized = _normalize_beat(beat)
    cauda = re.search(r"cauda\s+([a-z ]+?)\s+a\s+(\d+(?:,\d+)?)%", normalized)
    if cauda:
        return f"cauda:{cauda.group(1).strip()}:{cauda.group(2)}"
    ajuste = re.search(r"(brasil x [a-z ]+?)\s+(?:caiu|foi ajustada)\s+de\s+(\d+(?:,\d+)?)%\s+para\s+(\d+(?:,\d+)?)%", normalized)
    if ajuste:
        return f"ajuste:{ajuste.group(1).strip()}:{ajuste.group(2)}:{ajuste.group(3)}"
    return normalized


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
    source_correction = _source_correction_beat(bundle)
    behavior = _protagonist_behavior_beat(bundle)
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
            answer = str(response.get("answer", ""))
            normalized_answer = _normalize_beat(answer)
            if source_correction and (
                ("polymarket" in normalized_answer and "grupo c" in normalized_answer and "titulo" in normalized_answer)
                or ("erro de fonte" in normalized_answer and "nao sustenta" in normalized_answer)
            ):
                continue
            agent = str(response.get("agent") or "")
            weighted_path = _weighted_path_beat(round_index, agent, answer)
            if weighted_path:
                scored.append((weighted_path[0], agent, weighted_path[1]))
                continue
            tactical = _tactical_adjustment_beat(round_index, agent, answer)
            if tactical:
                scored.append((tactical[0], agent, tactical[1]))
                continue
            score, sentence = _best_sentence(answer)
            if score < 2:
                continue
            fact = _truncate_words(_plain_language(sentence), 130)
            if not fact.endswith((".", "!", "?", "…")):
                fact += "."
            if normalized_answer.startswith("concordo"):
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
        return [beat for beat in (source_correction, behavior) if beat][:2]
    fallback: list[str] = []
    fallback_keys: set[str] = set()
    fallback_agents: set[str] = set()
    for _score, agent, beat in scored:
        key = _beat_semantic_key(beat)
        if key in fallback_keys:
            continue
        if fallback and agent in fallback_agents and any(other_agent != agent for _s, other_agent, _b in scored):
            continue
        fallback.append(beat)
        fallback_keys.add(key)
        fallback_agents.add(agent)
        if len(fallback) >= 2:
            break

    beats: list[str] = []
    seen: set[str] = set()
    preferred = ([source_correction] if source_correction else []) + fallback
    if len([beat for beat in preferred if beat]) < 2 and behavior:
        preferred.append(behavior)
    for beat in preferred:
        if not beat:
            continue
        key = _beat_semantic_key(beat)
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


def _numeric_decision_label(bundle: Any) -> str:
    metadata = getattr(bundle, "metadata", {}) or {}
    monte_carlo = metadata.get("monte_carlo") if isinstance(metadata.get("monte_carlo"), dict) else {}
    mc_title = ((monte_carlo.get("stage_probabilities") or {}) if isinstance(monte_carlo, dict) else {}).get("titulo")
    model_title = metadata.get("agent_title_consensus_pct")
    blend = metadata.get("numeric_chairman", {}).get("stage_probability_blend", {}) if isinstance(metadata, dict) else {}
    mc_weight = float(blend.get("monte_carlo_weight", 0.6) or 0.6)
    model_weight = float(blend.get("model_weight", 0.4) or 0.4)
    rule = f"{round(mc_weight * 100):.0f}% Monte Carlo, {round(model_weight * 100):.0f}% modelos"
    if mc_title is None or model_title is None:
        return f"🧮 regra numérica: {rule}"
    try:
        mc_value = float(mc_title)
        model_value = float(model_title)
    except (TypeError, ValueError):
        return f"🧮 regra numérica: {rule}"
    if abs(mc_value - model_value) <= 0.15:
        return f"🧮 regra numérica: {rule}; hoje os modelos ratificaram o Monte Carlo"
    return f"🧮 regra numérica: {rule}; hoje os modelos moveram o funil antes da publicação"


def _build_round_stats(bundle: Any, *, slots: int = 3) -> str:
    """Números da rodada: pool ponderado, bullets densos, sem dado repetido."""
    candidates: list[tuple[float, str, str]] = []
    metadata = getattr(bundle, "metadata", {}) or {}
    candidates.append((109, "regra_numerica", _numeric_decision_label(bundle)))

    influence = getattr(bundle, "model_influence_pct", {}) or {}
    valid_influence = {k: float(v) for k, v in influence.items() if v is not None}
    participation = getattr(bundle, "model_participation", {}) or {}
    messages = participation.get("total_messages")
    valid_messages = participation.get("valid_messages")
    invalid_responses = participation.get("invalid_responses")
    rounds = participation.get("total_rounds")
    if messages and rounds and len(valid_influence) >= 3:
        top_agent, top_value = max(valid_influence.items(), key=lambda kv: kv[1])
        low_agent, low_value = min(valid_influence.items(), key=lambda kv: kv[1])
        tied = [k for k, v in valid_influence.items() if k != top_agent and abs(v - top_value) < 0.05]
        message_text = f"{messages} mensagens"
        if invalid_responses:
            message_text += f" ({valid_messages or messages} válidas, {invalid_responses} removidas)"
        low_fragment = (
            f"{low_agent.split()[0]} quase não moveu ({_pct(low_value)})"
            if low_value < 5.0
            else f"{low_agent.split()[0]} teve menor influência ({_pct(low_value)})"
        )
        if tied:
            influence_text = (
                f"{top_agent.split()[0]} e {tied[0].split()[0]} lideraram ({_pct(top_value)}); "
                f"{low_fragment}"
            )
        else:
            influence_text = (
                f"{top_agent.split()[0]} liderou ({_pct(top_value)}); "
                f"{low_fragment}"
            )
        candidates.append((110, "perfil_sala", f"💬 {message_text} em {rounds} rodadas; {influence_text}"))

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
        low_fragment = (
            f"{low_agent.split()[0]} quase não pesou ({_pct(low_value)})"
            if low_value < 5.0
            else f"{low_agent.split()[0]} teve menor influência ({_pct(low_value)})"
        )
        if tied:
            line = (
                f"🧭 {top_agent.split()[0]} e {tied[0].split()[0]} empataram como voz mais forte "
                f"({_pct(top_value)}); {low_fragment}"
            )
        else:
            line = (
                f"🧭 {top_agent.split()[0]} mandou no número final ({_pct(top_value)}); "
                f"{low_fragment}"
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
    return f"O QUE MUDOU DESDE {_change_reference_label(bundle)}:\n" + "\n".join(bullets[:3]) + "\n\n"


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
    completed_context = _completed_group_context(bundle, before_date=featured_date)
    if remaining:
        listed = " e ".join(
            f"{getattr(m, 'opponent', '')} ({_pct_int(getattr(m, 'brazil_pct', None))} de vitória)" for m in remaining
        )
        lead = f"{completed_context}depois" if completed_context else "Depois"
        rest_group_line = (
            f"{lead} vêm {listed}. Brasil termina em 1º do grupo em {first_place_pct} dos cenários."
        )
    elif featured_date is not None and featured_date >= run_date and not _group_match_has_completed_score(bundle, featured):
        lead = f"{completed_context}ainda" if completed_context else "Ainda"
        rest_group_line = (
            f"{lead} falta Brasil x {getattr(featured, 'opponent', '')}. "
            f"Brasil termina em 1º do grupo em {first_place_pct} dos cenários."
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
        source_plan_by_model=payload.get("source_plan_by_model", {}),
        model_participation=payload.get("model_participation", {}),
        model_influence_pct=payload.get("model_influence_pct", {}),
        model_token_costs=payload.get("model_token_costs", {}),
        agent_effort_profiles=payload.get("agent_effort_profiles", {}),
        sources=payload.get("sources", []),
    )

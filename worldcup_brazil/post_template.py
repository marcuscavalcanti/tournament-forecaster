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
TEMPLATE = """{title}

Como prometi: na véspera de cada jogo do Brasil, os 5 modelos de IA (Opus, GPT, Gemini, DeepSeek e Perplexity) se reúnem, pesquisam casas de apostas, rankings de força e notícias do dia, e saem com uma decisão em grupo.

👉 {next_game_header}

{next_game_line}

{rest_group_line}

O CAMINHO ATÉ O HEXA, adversário por adversário (no mata-mata não tem empate: ou passa, ou volta pra casa):

{path_blocks}
RESUMO DA CAMINHADA: o Brasil chega nos 16 avos em {r16_pct} dos cenários, oitavas em {r8_pct},  quartas em {qf_pct}, na semifinal em {sf_pct}, na final em {final_pct}... e levanta a taça em {title_pct}.

Esse mapa MUDA a cada rodada, pois o modelo calcula o resultado dos outros grupos e troca os adversários pelo caminho. Por isso o modelo roda de novo na véspera/dia de cada jogo e eu posto o mapa atualizado.

DOIS BASTIDORES DA REUNIÃO DE HOJE:

1️⃣  {beat_1}

2️⃣  {beat_2}
{beat_3}
E o supermodelo da OPTA, com 350 mil simulações? Coloca o Brasil em 5º. Minha sala concorda: candidato de verdade, favorito não. E as casas de apostas pagam 9 pra 1 no hexa, ou seja, uns 8-9% de chance. Três caminhos diferentes, mesma resposta.

Próximo post: véspera/dia de Brasil x {next_post_game}, com o mapa recalculado.

Galera do bolão: {palpite_bolao}. Usem com moderação.

#CopaComAchismo #Brasil #Brazil #WorldCup2026 #Futebol #Football #Soccer #Hexa
"""

PHASE_BLOCK = """➡️ {header} ({phase_date})
• Mais provável: {ml_opp} ({ml_scn} de chance desse cruzamento) → Brasil passa: {ml_br} | {ml_opp}: {ml_opp_pct}{ml_venue}
• Alternativa: {alt_opp} ({alt_scn}) → Brasil: {alt_br} | {alt_opp}: {alt_opp_pct}{alt_venue}

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
    cut = clean[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:.") + "…"


def _extract_beats(bundle: Any) -> list[str]:
    """Bastidores determinísticos a partir do transcript da sala.

    Prioridade: discordâncias válidas (a alma do debate), depois invalidações do
    facilitador. O editor append-only pode enriquecer; o esqueleto nunca inventa."""
    beats: list[str] = []
    transcript = list(getattr(bundle, "meeting_transcript", []) or [])
    for turn in transcript:
        if len(beats) >= 3:
            break
        round_index = turn.get("round") if isinstance(turn, dict) else None
        for response in (turn.get("responses", []) if isinstance(turn, dict) else []):
            if len(beats) >= 3:
                break
            if response.get("removed_from_main") or response.get("used_fallback"):
                continue
            if response.get("disagreed"):
                quote = _truncate_words(response.get("answer", ""), 150)
                if _normalize_beat(quote).startswith("concordo"):
                    continue
                beats.append(
                    f"Na rodada {round_index}, o {response.get('agent')} discordou do líder da mesa: \"{quote}\" "
                    "A discordância virou ajuste com fonte — é assim que o número se move."
                )
    if len(beats) < 3:
        for turn in transcript:
            invalidated = turn.get("invalidated_protagonist_question") if isinstance(turn, dict) else None
            if invalidated:
                beats.append(
                    f"A fala do {invalidated.get('agent')} foi anulada pela própria sala "
                    f"({_truncate_words(invalidated.get('reason', ''), 90)}). Regra é regra: sem fonte ou fora da chave, não vale."
                )
            if len(beats) >= 3:
                break
    while len(beats) < 2:
        beats.append(
            "Rodada sem briga: os modelos convergiram cedo e o consenso fechou estável — quando há evidência boa, ninguém inventa discordância."
        )
    return beats[:3]


def _knockout_pairs(bundle: Any) -> dict[str, dict[str, Any]]:
    pairs: dict[str, dict[str, Any]] = {}
    for match in getattr(bundle, "knockout_matches", []) or []:
        phase = str(getattr(match, "phase", "")).strip()
        if phase not in PHASE_HEADERS:
            continue
        slot = "ml" if bool(getattr(match, "most_likely", False)) else "alt"
        pairs.setdefault(phase, {})[slot] = match
    return pairs


def render_template_post(bundle: Any, *, post_index: int, run_date: date | None = None) -> str:
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
    loss_value = getattr(featured, "opponent_pct", None)
    parts = [f"{win} vitória"]
    if draw_value:
        parts.append(f"{_pct_int(draw_value)} empate")
    if loss_value:
        parts.append(f"{_pct_int(loss_value)} derrota")
    next_game_line = f"BRASIL x {str(getattr(featured, 'opponent', '')).upper()} — " + " | ".join(parts)

    remaining = [m for m, d in dated if m is not featured and d is not None and d > (featured_date or run_date)]
    group_summary = str(getattr(bundle, "group_summary", "") or "")
    first_place = re.search(r"1º:\s*~?(\d+)%", group_summary)
    first_place_pct = f"{first_place.group(1)}%" if first_place else "—"
    if remaining:
        listed = " e ".join(
            f"{getattr(m, 'opponent', '')} ({_pct_int(getattr(m, 'brazil_pct', None))} de vitória)" for m in remaining
        )
        rest_group_line = (
            f"Depois vêm {listed}. Brasil termina em 1º do grupo em {first_place_pct} dos cenários."
        )
    else:
        rest_group_line = f"Fase de grupos encerrada. Brasil terminou o grupo com 1º lugar projetado em {first_place_pct} dos cenários."

    pairs = _knockout_pairs(bundle)
    blocks: list[str] = []
    for phase in PHASE_ORDER:
        pair = pairs.get(phase, {})
        ml, alt = pair.get("ml"), pair.get("alt")
        if ml is None or alt is None:
            raise ValueError(f"template post requer cenário mais provável e alternativa para {phase}")
        blocks.append(
            PHASE_BLOCK.format(
                header=PHASE_HEADERS[phase],
                phase_date=_short_date(getattr(ml, "match_date", "")),
                ml_opp=getattr(ml, "opponent", ""),
                ml_scn=_pct_int(getattr(ml, "scenario_pct", None)),
                ml_br=_pct_int(getattr(ml, "brazil_pct", None)),
                ml_opp_pct=_pct_int(getattr(ml, "opponent_pct", None)),
                ml_venue=_venue_suffix(getattr(ml, "venue", "")),
                alt_opp=getattr(alt, "opponent", ""),
                alt_scn=_pct_int(getattr(alt, "scenario_pct", None)),
                alt_br=_pct_int(getattr(alt, "brazil_pct", None)),
                alt_opp_pct=_pct_int(getattr(alt, "opponent_pct", None)),
                alt_venue=_venue_suffix(getattr(alt, "venue", "")),
            )
        )

    mc_stages = ((getattr(bundle, "metadata", {}) or {}).get("monte_carlo") or {}).get("stage_probabilities") or {}
    stage = dict(getattr(bundle, "stage_probabilities", {}) or {})
    beats = _extract_beats(bundle)
    beat_3 = f"\n3️⃣  {beats[2]}\n" if len(beats) >= 3 else "\n"

    bolao = [win]
    if draw_value:
        bolao.append(_pct_int(draw_value))
    if loss_value:
        bolao.append(_pct_int(loss_value))
    palpite = " / ".join(value.rstrip("%") for value in bolao)

    text = TEMPLATE.format(
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
        title_pct=_pct(stage.get("titulo")),
        beat_1=beats[0],
        beat_2=beats[1],
        beat_3=beat_3,
        next_post_game=f"{getattr(featured, 'opponent', '')} ({_short_date(getattr(featured, 'match_date', ''))})",
        palpite_bolao=palpite,
    )

    return _trim_to_limit(text, bundle)


def _trim_to_limit(text: str, bundle: Any) -> str:
    if len(text) <= MAX_POST_CHARS:
        return text
    without_beat3 = re.sub(r"\n3️⃣ .*\n", "\n", text)
    if len(without_beat3) <= MAX_POST_CHARS:
        return without_beat3
    no_alt_venues = re.sub(r"(• Alternativa: [^\n]*?) - [^\n]+", r"\1", without_beat3)
    if len(no_alt_venues) <= MAX_POST_CHARS:
        return no_alt_venues
    no_venues = re.sub(r"(\| [^|\n]+?: \d+[,.]?\d*%) - [^\n]+", r"\1", no_alt_venues)
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
        "DOIS BASTIDORES DA REUNIÃO DE HOJE:",
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
    )

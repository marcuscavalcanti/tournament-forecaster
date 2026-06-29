from __future__ import annotations

import html
import math
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from worldcup_brazil.post_template import bundle_from_json

DEFAULT_CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)


def _parse_bundle_date(bundle: Any) -> datetime:
    raw = str(getattr(bundle, "generated_at_iso", "") or "")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min


def _date_label(bundle: Any) -> str:
    override = str(getattr(bundle, "infographic_label", "") or "").strip()
    if override:
        return override
    parsed = _parse_bundle_date(bundle)
    if parsed == datetime.min:
        return "s/data"
    return f"{parsed.day:02d}/{parsed.month:02d}"


def _pct(value: Any, *, digits: int = 1) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(number - round(number)) < 0.05:
        return f"{round(number)}%"
    return f"{number:.{digits}f}".replace(".", ",") + "%"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        if math.isnan(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def _stage(bundle: Any, key: str) -> float:
    return _num((getattr(bundle, "stage_probabilities", {}) or {}).get(key))


def _token_millions(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".", ",")
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return str(round(value))


def _money(value: float) -> str:
    return f"US$ {value:.1f}".replace(".", ",")


def _pct_one_decimal(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + "%"


def _participation(bundle: Any) -> dict[str, Any]:
    value = getattr(bundle, "model_participation", {}) or {}
    return value if isinstance(value, dict) else {}


def _cost(bundle: Any) -> dict[str, Any]:
    value = (getattr(bundle, "model_token_costs", {}) or {}).get("total") or {}
    return value if isinstance(value, dict) else {}


def _metric_totals(bundles: list[Any]) -> dict[str, float]:
    messages = valid = invalid = rounds = calls = fallback = tokens = cost = 0.0
    for bundle in bundles:
        part = _participation(bundle)
        total_messages = _num(part.get("total_messages"))
        invalid_responses = _num(part.get("invalid_responses"))
        valid_messages = part.get("valid_messages")
        messages += total_messages
        valid += _num(valid_messages, total_messages - invalid_responses)
        invalid += invalid_responses
        rounds += _num(part.get("total_rounds"))
        total_cost = _cost(bundle)
        calls += _num(total_cost.get("calls"))
        fallback += _num(total_cost.get("fallback_calls"))
        tokens += _num(total_cost.get("total_tokens"))
        cost += _num(total_cost.get("cost_usd"))
    return {
        "messages": messages,
        "valid": valid,
        "invalid": invalid,
        "rounds": rounds,
        "calls": calls,
        "fallback": fallback,
        "tokens": tokens,
        "cost": cost,
    }


def _model_rows(bundles: list[Any]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for bundle in bundles:
        influence = getattr(bundle, "model_influence_pct", {}) or {}
        by_model = _participation(bundle).get("by_model") or {}
        for model, pct in influence.items():
            row = rows.setdefault(
                str(model),
                {"model": str(model), "influence": [], "valid": 0.0, "invalid": 0.0, "messages": 0.0, "runs": 0},
            )
            row["influence"].append(_num(pct))
            row["runs"] += 1
        for model, stats in by_model.items():
            if not isinstance(stats, dict):
                continue
            row = rows.setdefault(
                str(model),
                {"model": str(model), "influence": [], "valid": 0.0, "invalid": 0.0, "messages": 0.0, "runs": 0},
            )
            row["valid"] += _num(stats.get("valid_responses"), _num(stats.get("responses")))
            row["invalid"] += _num(stats.get("invalid_responses"))
            row["messages"] += _num(stats.get("messages"))
    output: list[dict[str, Any]] = []
    for row in rows.values():
        influences = row["influence"]
        avg = sum(influences) / len(influences) if influences else 0.0
        output.append({**row, "avg_influence": avg})
    return sorted(output, key=lambda item: (-item["avg_influence"], -item["valid"], item["model"]))


def _locked_crossing(bundle: Any) -> str:
    for match in getattr(bundle, "knockout_matches", []) or []:
        phase = str(getattr(match, "phase", "") or "")
        scenario = _num(getattr(match, "scenario_pct", None))
        if phase == "16 avos" and bool(getattr(match, "most_likely", False)) and scenario >= 99.5:
            opponent = str(getattr(match, "opponent", "") or "adversário")
            brazil = _pct(getattr(match, "brazil_pct", None))
            return f"{opponent} 100% no cruzamento; Brasil passa {brazil}"
    return "Cruzamentos recalculados em cada run"


def _locked_crossing_short(bundle: Any) -> str:
    for match in getattr(bundle, "knockout_matches", []) or []:
        phase = str(getattr(match, "phase", "") or "")
        scenario = _num(getattr(match, "scenario_pct", None))
        if phase == "16 avos" and bool(getattr(match, "most_likely", False)) and scenario >= 99.5:
            opponent = str(getattr(match, "opponent", "") or "adversário")
            brazil = _pct(getattr(match, "brazil_pct", None))
            return f"{opponent} 100%; Brasil {brazil}"
    return "Cruzamentos recalculados"


def collect_recent_infographic_bundles(output_dir: Path, current_json_path: Path, *, limit: int = 4) -> list[Any]:
    paths = [
        path
        for path in sorted(output_dir.glob("linkedin_brazil_*.json"))
        if path.name <= current_json_path.name
    ]
    if current_json_path not in paths and current_json_path.exists():
        paths.append(current_json_path)
    selected = paths[-max(1, limit):]
    bundles = [bundle_from_json(path) for path in selected]
    return sorted(bundles, key=_parse_bundle_date)


def render_svg_to_png_with_chrome(
    svg_path: Path,
    png_path: Path,
    *,
    chrome_path: str | None = None,
    width: int = 1200,
    height: int = 1600,
) -> bool:
    candidates = [chrome_path] if chrome_path else [os.environ.get("CHROME_BIN"), *DEFAULT_CHROME_PATHS]
    executable = next((Path(candidate) for candidate in candidates if candidate and Path(candidate).exists()), None)
    if executable is None:
        return False
    png_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(executable),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--screenshot={png_path}",
        f"--window-size={width},{height}",
        svg_path.resolve().as_uri(),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
    except (OSError, subprocess.SubprocessError):
        return False
    return png_path.exists() and png_path.stat().st_size > 0


def _text(x: float, y: float, body: str, *, size: int = 24, color: str = "#f8fafc", weight: int = 600, anchor: str = "start") -> str:
    return (
        f'<text x="{x:.0f}" y="{y:.0f}" text-anchor="{anchor}" '
        f'font-size="{size}" fill="{color}" font-weight="{weight}">{html.escape(body)}</text>'
    )


def _line_text(x: float, y: float, lines: list[str], *, size: int = 22, color: str = "#dbeafe", gap: int = 31) -> str:
    return "\n".join(_text(x, y + index * gap, line, size=size, color=color, weight=500) for index, line in enumerate(lines))


def _card(x: int, y: int, w: int, h: int, *, stroke: str = "#2dd4bf", fill: str = "#071624", opacity: float = 0.92) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" '
        f'fill="{fill}" fill-opacity="{opacity}" stroke="{stroke}" stroke-opacity="0.72" stroke-width="2"/>'
    )


def _trend_chart(bundles: list[Any], *, x: int, y: int, w: int, h: int) -> str:
    values = [_stage(bundle, "titulo") for bundle in bundles]
    max_value = max(values + [1.0]) * 1.18
    points = []
    labels = []
    for index, (bundle, value) in enumerate(zip(bundles, values, strict=True)):
        px = x + (w * index / max(1, len(bundles) - 1))
        py = y + h - (value / max_value * h)
        points.append(f"{px:.1f},{py:.1f}")
        labels.append(_text(px, py - 18, _pct(value), size=23, color="#facc15", weight=800, anchor="middle"))
    grid = "\n".join(
        f'<line x1="{x}" y1="{y + h * i / 4:.1f}" x2="{x + w}" y2="{y + h * i / 4:.1f}" stroke="#1e3a5f" stroke-width="1"/>'
        for i in range(5)
    )
    circles = "\n".join(
        f'<circle cx="{point.split(",")[0]}" cy="{point.split(",")[1]}" r="7" fill="#facc15" stroke="#fff7ed" stroke-width="2"/>'
        for point in points
    )
    return (
        grid
        + f'<polyline points="{" ".join(points)}" fill="none" stroke="#2dd4bf" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
        + circles
        + "\n".join(labels)
    )


def _model_leaderboard(rows: list[dict[str, Any]], *, x: int, y: int, w: int) -> str:
    top_rows = rows[:5]
    max_influence = max([row["avg_influence"] for row in top_rows] + [1.0])
    parts: list[str] = []
    for index, row in enumerate(top_rows):
        yy = y + index * 55
        name = row["model"]
        pct = row["avg_influence"]
        bar_w = int((w - 250) * pct / max_influence)
        parts.append(_text(x, yy, name, size=22, color="#f8fafc", weight=700))
        parts.append(f'<rect x="{x + 190}" y="{yy - 19}" width="{w - 250}" height="24" rx="12" fill="#0f2a3d"/>')
        parts.append(f'<rect x="{x + 190}" y="{yy - 19}" width="{bar_w}" height="24" rx="12" fill="#2dd4bf"/>')
        parts.append(_text(x + w - 42, yy, _pct_one_decimal(pct), size=22, color="#facc15", weight=800, anchor="end"))
    return "\n".join(parts)


def _run_cards(bundles: list[Any], *, x: int, y: int, w: int) -> str:
    gap = 18
    card_w = int((w - gap * (len(bundles) - 1)) / len(bundles))
    parts: list[str] = []
    for index, bundle in enumerate(bundles):
        xx = x + index * (card_w + gap)
        parts.append(_card(xx, y, card_w, 142, stroke="#facc15" if index == len(bundles) - 1 else "#2dd4bf"))
        parts.append(_text(xx + 22, y + 36, _date_label(bundle), size=25, color="#e2e8f0", weight=800))
        parts.append(_text(xx + 22, y + 82, _pct(_stage(bundle, "titulo")), size=42, color="#facc15", weight=900))
        parts.append(_text(xx + 22, y + 118, f"final {_pct(_stage(bundle, 'final'))}", size=19, color="#bae6fd", weight=600))
    return "\n".join(parts)


def render_simulation_review_infographic_svg(bundles: list[Any]) -> str:
    if not bundles:
        raise ValueError("infográfico requer pelo menos um bundle")
    bundles = sorted(bundles, key=_parse_bundle_date)
    latest = bundles[-1]
    totals = _metric_totals(bundles)
    rows = _model_rows(bundles)
    title_start = _stage(bundles[0], "titulo")
    title_end = _stage(latest, "titulo")
    final_start = _stage(bundles[0], "final")
    final_end = _stage(latest, "final")
    valid_rate = (totals["valid"] / totals["messages"] * 100.0) if totals["messages"] else 0.0
    period = f"{_date_label(bundles[0])} → {_date_label(latest)}"
    locked_short = _locked_crossing_short(latest)
    width, height = 1200, 1600
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">CopaComAchismo Review das Simulações</title>
  <desc id="desc">Infográfico com evolução do funil, desempenho dos modelos e métricas operacionais das simulações do Brasil.</desc>
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#03111f"/>
      <stop offset="55%" stop-color="#071d2f"/>
      <stop offset="100%" stop-color="#020617"/>
    </linearGradient>
    <radialGradient id="gold" cx="50%" cy="50%" r="65%">
      <stop offset="0%" stop-color="#fde68a"/>
      <stop offset="100%" stop-color="#b45309"/>
    </radialGradient>
    <filter id="glow"><feGaussianBlur stdDeviation="3" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  </defs>
  <rect width="{width}" height="{height}" fill="url(#bg)"/>
  <circle cx="1060" cy="100" r="150" fill="#0f766e" opacity="0.12"/>
  <circle cx="132" cy="1420" r="220" fill="#facc15" opacity="0.06"/>
  {_text(54, 82, "CopaComAchismo", size=34, color="#facc15", weight=900)}
  {_text(54, 138, "Review das Simulações", size=54, color="#f8fafc", weight=900)}
  {_text(56, 184, f"Brasil 2026 · {period} · {len(bundles)} runs auditados", size=24, color="#99f6e4", weight=700)}
  {_text(1144, 82, "WC26", size=34, color="#facc15", weight=900, anchor="end")}
  {_card(54, 222, 1092, 112, stroke="#475569", fill="#081827", opacity=0.78)}
  {_text(98, 270, "1", size=34, color="#facc15", weight=900)}
  {_text(134, 270, "Dados FIFA", size=24, color="#f8fafc", weight=800)}
  {_text(348, 270, "2", size=34, color="#facc15", weight=900)}
  {_text(384, 270, "Monte Carlo", size=24, color="#f8fafc", weight=800)}
  {_text(628, 270, "3", size=34, color="#facc15", weight=900)}
  {_text(664, 270, "Sala multi-modelo", size=24, color="#f8fafc", weight=800)}
  {_text(946, 270, "4", size=34, color="#facc15", weight=900)}
  {_text(982, 270, "Mercado desafia", size=24, color="#f8fafc", weight=800)}
  {_text(600, 384, "1. EVOLUÇÃO DO FUNIL", size=30, color="#facc15", weight=900, anchor="middle")}
  {_card(54, 420, 1092, 360, stroke="#2dd4bf", fill="#061827")}
  {_trend_chart(bundles, x=110, y=490, w=980, h=160)}
  {_run_cards(bundles, x=92, y=610, w=1016)}
  {_text(600, 836, "2. PERFORMANCE DA SALA", size=30, color="#2dd4bf", weight=900, anchor="middle")}
  {_card(54, 872, 520, 352, stroke="#2dd4bf", fill="#061827")}
  {_text(88, 920, "Influência média válida", size=27, color="#f8fafc", weight=900)}
  {_model_leaderboard(rows, x=88, y=974, w=442)}
  {_card(626, 872, 520, 352, stroke="#facc15", fill="#061827")}
  {_text(660, 920, "Números do período", size=27, color="#f8fafc", weight=900)}
  {_line_text(660, 970, [
      f"{int(totals['messages'])} mensagens em {int(totals['rounds'])} rodadas",
      f"{_pct(valid_rate)} de mensagens válidas",
      f"{int(totals['invalid'])} respostas removidas pelos gates",
      f"{_token_millions(totals['tokens'])} tokens · {_money(totals['cost'])}",
      f"{int(totals['fallback'])} fallbacks em {int(totals['calls'])} chamadas",
  ], size=23, color="#dbeafe", gap=36)}
  {_text(600, 1244, "3. O QUE A SÉRIE APRENDEU", size=30, color="#facc15", weight=900, anchor="middle")}
  {_card(54, 1280, 340, 196, stroke="#2dd4bf", fill="#061827")}
  {_text(84, 1330, "Chaveamento", size=27, color="#f8fafc", weight=900)}
  {_line_text(84, 1372, [locked_short, "Probabilidade não vem de 1 caminho", "Ramos alternativos mudam o Hexa"], size=21, color="#dbeafe", gap=31)}
  {_card(430, 1280, 340, 196, stroke="#facc15", fill="#061827")}
  {_text(460, 1330, "Modelos", size=27, color="#f8fafc", weight=900)}
  {_line_text(460, 1372, ["Debate testa premissas", "Gates removem fonte fraca", "Consenso mostra quem moveu"], size=21, color="#dbeafe", gap=31)}
  {_card(806, 1280, 340, 196, stroke="#2dd4bf", fill="#061827")}
  {_text(836, 1330, "Operação", size=27, color="#f8fafc", weight=900)}
  {_line_text(836, 1372, ["FIFA alimenta placares reais", "Mercado desafia, não recalibra", "Cada run deixa rastro auditável"], size=21, color="#dbeafe", gap=31)}
  {_card(54, 1512, 1092, 56, stroke="#475569", fill="#020617", opacity=0.65)}
  {_text(600, 1548, "O melhor resultado veio de combinar simulação, debate adversarial e disciplina de auditoria.", size=25, color="#f8fafc", weight=800, anchor="middle")}
</svg>
"""

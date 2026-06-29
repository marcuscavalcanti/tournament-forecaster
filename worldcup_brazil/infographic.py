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


def _pp(value: float) -> str:
    return f"{value:+.1f}".replace(".", ",") + " p.p."


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


def render_html_to_png_with_chrome(
    html_path: Path,
    png_path: Path,
    *,
    chrome_path: str | None = None,
    width: int = 1400,
    height: int = 2500,
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
        html_path.resolve().as_uri(),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
    except (OSError, subprocess.SubprocessError):
        return False
    return png_path.exists() and png_path.stat().st_size > 0


MONTHS_PT = {
    "jan": 1,
    "fev": 2,
    "mar": 3,
    "abr": 4,
    "mai": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "set": 9,
    "out": 10,
    "nov": 11,
    "dez": 12,
}


def _match_date_tuple(raw: Any) -> tuple[int, int] | None:
    value = str(raw or "").strip().lower()
    if "/" not in value:
        return None
    day_raw, month_raw = value.split("/", 1)
    try:
        day = int(day_raw)
    except ValueError:
        return None
    month = MONTHS_PT.get(month_raw[:3])
    if month is None:
        return None
    return month, day


def _match_label(bundle: Any) -> str:
    generated = _parse_bundle_date(bundle)
    generated_key = (generated.month, generated.day) if generated != datetime.min else (0, 0)
    group_matches = list(getattr(bundle, "group_matches", []) or [])
    future_group_matches: list[tuple[tuple[int, int], Any]] = []
    for match in group_matches:
        date_key = _match_date_tuple(getattr(match, "match_date", None))
        if date_key is not None and date_key >= generated_key:
            future_group_matches.append((date_key, match))
    if future_group_matches:
        _, match = sorted(future_group_matches, key=lambda item: item[0])[0]
        return f"Brasil x {getattr(match, 'opponent', 'adversário')}"
    for match in getattr(bundle, "knockout_matches", []) or []:
        if str(getattr(match, "phase", "") or "") == "16 avos" and bool(getattr(match, "most_likely", False)):
            return f"Brasil x {getattr(match, 'opponent', 'adversário')}"
    return "Brasil"


def _short_model(model: str) -> str:
    replacements = {
        "DeepSeek V4 Pro": "DeepSeek",
        "Perplexity Pro": "Perplexity",
        "Gemini Pro": "Gemini",
        "GPT 5.5": "GPT 5.5",
        "Opus 4.8": "Opus 4.8",
    }
    return replacements.get(model, model)


def _leaders_from_influence(influence: dict[str, Any]) -> list[str]:
    if not influence:
        return []
    values = {str(model): _num(value) for model, value in influence.items()}
    top = max(values.values(), default=0.0)
    if top <= 0:
        return []
    return [model for model, value in values.items() if abs(value - top) < 0.05]


def _run_rows(bundles: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bundle in bundles:
        part = _participation(bundle)
        influence = getattr(bundle, "model_influence_pct", {}) or {}
        leaders = _leaders_from_influence(influence)
        total_messages = _num(part.get("total_messages"))
        invalid = _num(part.get("invalid_responses"))
        valid = _num(part.get("valid_messages"), total_messages - invalid)
        rows.append(
            {
                "date": _date_label(bundle),
                "match": _match_label(bundle),
                "title": _stage(bundle, "titulo"),
                "final": _stage(bundle, "final"),
                "messages": total_messages,
                "valid": valid,
                "invalid": invalid,
                "rounds": _num(part.get("total_rounds")),
                "leaders": leaders,
                "leader_text": " + ".join(_short_model(model) for model in leaders) if leaders else "sem líder",
                "locked_crossing": _locked_crossing_short(bundle),
            }
        )
    return rows


def _model_rows(bundles: list[Any]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    run_count = max(1, len(bundles))
    for bundle in bundles:
        influence = getattr(bundle, "model_influence_pct", {}) or {}
        by_model = _participation(bundle).get("by_model") or {}
        for model, pct in influence.items():
            row = rows.setdefault(
                str(model),
                {
                    "model": str(model),
                    "influence": [],
                    "influence_sum": 0.0,
                    "valid": 0.0,
                    "invalid": 0.0,
                    "messages": 0.0,
                    "questions": 0.0,
                    "responses": 0.0,
                    "runs": 0,
                },
            )
            value = _num(pct)
            row["influence"].append(value)
            row["influence_sum"] += value
            row["runs"] += 1
        for model, stats in by_model.items():
            if not isinstance(stats, dict):
                continue
            row = rows.setdefault(
                str(model),
                {
                    "model": str(model),
                    "influence": [],
                    "influence_sum": 0.0,
                    "valid": 0.0,
                    "invalid": 0.0,
                    "messages": 0.0,
                    "questions": 0.0,
                    "responses": 0.0,
                    "runs": 0,
                },
            )
            responses = _num(stats.get("responses"))
            row["valid"] += _num(stats.get("valid_responses"), responses)
            row["invalid"] += _num(stats.get("invalid_responses"))
            row["messages"] += _num(stats.get("messages"))
            row["questions"] += _num(stats.get("questions"))
            row["responses"] += responses
    output: list[dict[str, Any]] = []
    for row in rows.values():
        influences = row["influence"]
        avg_present = sum(influences) / len(influences) if influences else 0.0
        avg_all = row["influence_sum"] / run_count
        output.append({**row, "avg_influence": avg_present, "avg_all_runs": avg_all})
    return sorted(output, key=lambda item: (-item["influence_sum"], -item["valid"], item["invalid"], item["model"]))


def _h(value: Any) -> str:
    return html.escape(str(value))


def _html_stat(label: str, value: str, note: str = "") -> str:
    return (
        '<div class="stat-card">'
        f'<div class="stat-value">{_h(value)}</div>'
        f'<div class="stat-label">{_h(label)}</div>'
        f'<div class="stat-note">{_h(note)}</div>'
        "</div>"
    )


def _market_status(bundle: Any) -> str:
    challenge = ((getattr(bundle, "metadata", {}) or {}).get("market_title_challenge") or {})
    status = str(challenge.get("status") or "sem sinal")
    if bool(challenge.get("triggered")):
        low = _pct(challenge.get("market_low_pct"))
        high = _pct(challenge.get("market_high_pct"))
        return f"mercado desafia ({low}-{high})"
    if status == "within_threshold":
        return "mercado dentro da faixa"
    if status == "debate_claim_only":
        return "mercado só como alegação"
    return status.replace("_", " ")


def _opponent_room_status(bundle: Any) -> str:
    room = ((getattr(bundle, "metadata", {}) or {}).get("parallel_opponent_debriefing") or {})
    if not room:
        return "sem sala lateral"
    if bool(room.get("usable_for_main_room")):
        rounds = room.get("rounds") or 0
        return f"sala lateral útil ({rounds} rodada{'s' if rounds != 1 else ''})"
    exit_status = str(room.get("exit_status") or "não-usável")
    return f"sala lateral: {exit_status.replace('_', ' ')}"


def _sparkline_points(values: list[float], *, width: int = 900, height: int = 170) -> str:
    if not values:
        return ""
    max_value = max(values + [1.0]) * 1.18
    x_pad = 52
    y_pad = 26
    usable_width = width - x_pad * 2
    usable_height = height - y_pad * 2
    points: list[str] = []
    for index, value in enumerate(values):
        x = x_pad + (usable_width * index / max(1, len(values) - 1))
        y = y_pad + usable_height - (value / max_value * usable_height)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def render_simulation_review_infographic_html(bundles: list[Any]) -> str:
    if not bundles:
        raise ValueError("infográfico requer pelo menos um bundle")
    bundles = sorted(bundles, key=_parse_bundle_date)
    latest = bundles[-1]
    totals = _metric_totals(bundles)
    run_rows = _run_rows(bundles)
    model_rows = _model_rows(bundles)
    period = f"{_date_label(bundles[0])} → {_date_label(latest)}"
    valid_rate = (totals["valid"] / totals["messages"] * 100.0) if totals["messages"] else 0.0
    title_start = _stage(bundles[0], "titulo")
    title_end = _stage(latest, "titulo")
    final_start = _stage(bundles[0], "final")
    final_end = _stage(latest, "final")
    title_delta = title_end - title_start
    values = [_stage(bundle, "titulo") for bundle in bundles]
    max_influence = max([row["influence_sum"] for row in model_rows] + [1.0])
    rank_cards = []
    for index, row in enumerate(model_rows[:5], start=1):
        bar = max(4, int(100 * row["influence_sum"] / max_influence))
        rank_cards.append(
            '<div class="model-row">'
            f'<div class="rank-num">#{index}</div>'
            f'<div class="model-main"><div class="model-name">{_h(_short_model(row["model"]))}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{bar}%"></div></div>'
            f'<div class="model-sub">{int(row["valid"])} válidas · {int(row["invalid"])} removidas · {int(row["questions"])} protagonismos</div></div>'
            f'<div class="model-score">{_pct_one_decimal(row["influence_sum"])}</div>'
            "</div>"
        )
    run_cards = []
    for row in run_rows:
        run_cards.append(
            '<div class="run-card">'
            f'<div class="run-date">{_h(row["date"])}</div>'
            f'<div class="run-match">{_h(row["match"])}</div>'
            f'<div class="run-title">{_pct(row["title"])}</div>'
            f'<div class="run-meta">Hexa · final {_pct(row["final"])}</div>'
            f'<div class="leader-pill">{_h(row["leader_text"])}</div>'
            f'<div class="run-foot">{int(row["valid"])} válidas · {int(row["invalid"])} removidas</div>'
            "</div>"
        )
    leader_rows = []
    for row in run_rows:
        leader_rows.append(
            '<tr>'
            f'<td>{_h(row["date"])}</td>'
            f'<td>{_h(row["match"])}</td>'
            f'<td>{_h(row["leader_text"])}</td>'
            f'<td>{_pct(row["title"])}</td>'
            "</tr>"
        )
    insight_items = [
        f"Hexa saiu de {_pct(title_start)} para {_pct(title_end)} ({_pp(title_delta)}).",
        f"Final subiu de {_pct(final_start)} para {_pct(final_end)}.",
        f"{_locked_crossing_short(latest)}.",
        f"{_market_status(latest)}.",
        f"{_opponent_room_status(latest)}.",
    ]
    trend_points = _sparkline_points(values)
    circle_nodes = []
    if trend_points:
        for point, row in zip(trend_points.split(), run_rows, strict=True):
            x_raw, y_raw = point.split(",")
            circle_nodes.append(
                f'<circle cx="{x_raw}" cy="{y_raw}" r="8"></circle>'
                f'<text x="{x_raw}" y="{float(y_raw) - 18:.1f}">{_pct(row["title"])}</text>'
            )
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>CopaComAchismo - Review das Simulações</title>
<style>
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; background: #020617; }}
body {{ width: 1400px; min-height: 2500px; font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #f8fafc; }}
.poster {{ width: 1400px; min-height: 2500px; padding: 52px; background:
  radial-gradient(circle at 85% 6%, rgba(45,212,191,.18), transparent 26%),
  radial-gradient(circle at 8% 88%, rgba(250,204,21,.10), transparent 24%),
  linear-gradient(135deg, #04111f 0%, #071827 48%, #020617 100%); overflow: hidden; }}
.top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 32px; }}
.eyebrow {{ color: #facc15; font-size: 34px; font-weight: 900; letter-spacing: 0; text-transform: uppercase; }}
h1 {{ margin: 8px 0 10px; font-size: 68px; line-height: .95; letter-spacing: 0; }}
.subtitle {{ color: #99f6e4; font-size: 25px; font-weight: 700; }}
.badge {{ border: 2px solid rgba(250,204,21,.55); border-radius: 16px; padding: 18px 22px; min-width: 265px; text-align: right; background: rgba(2,6,23,.42); }}
.badge strong {{ display: block; color: #facc15; font-size: 44px; line-height: 1; }}
.badge span {{ color: #cbd5e1; font-size: 20px; }}
.method {{ margin-top: 34px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }}
.step {{ border: 1px solid rgba(148,163,184,.36); border-radius: 14px; padding: 16px 18px; background: rgba(8,24,39,.70); min-height: 82px; }}
.step b {{ color: #facc15; font-size: 24px; margin-right: 9px; }}
.step span {{ display: block; margin-top: 8px; color: #cbd5e1; font-size: 18px; line-height: 1.15; }}
.section-title {{ margin: 34px 0 16px; display: flex; align-items: center; gap: 18px; color: #facc15; font-size: 29px; font-weight: 900; text-transform: uppercase; }}
.section-title:before, .section-title:after {{ content: ""; height: 1px; flex: 1; background: linear-gradient(90deg, transparent, rgba(250,204,21,.55), transparent); }}
.run-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }}
.run-card, .panel, .stat-card {{ border: 1px solid rgba(45,212,191,.45); border-radius: 16px; background: rgba(6,24,39,.82); box-shadow: 0 16px 55px rgba(0,0,0,.28); }}
.run-card {{ min-height: 228px; padding: 20px; position: relative; }}
.run-date {{ color: #facc15; font-weight: 900; font-size: 25px; }}
.run-match {{ margin-top: 6px; color: #e2e8f0; font-size: 21px; font-weight: 800; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.run-title {{ margin-top: 14px; color: #facc15; font-size: 52px; line-height: .95; font-weight: 950; }}
.run-meta {{ color: #bae6fd; font-size: 18px; font-weight: 700; margin-top: 4px; }}
.leader-pill {{ margin-top: 16px; padding: 9px 12px; border-radius: 999px; background: rgba(45,212,191,.16); color: #99f6e4; font-size: 17px; font-weight: 800; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.run-foot {{ position: absolute; left: 20px; right: 20px; bottom: 16px; color: #cbd5e1; font-size: 16px; }}
.trend-panel {{ margin-top: 18px; padding: 18px 28px; min-height: 0; }}
.trend-svg {{ width: 100%; height: 210px; display: block; }}
.trend-svg polyline {{ fill: none; stroke: #2dd4bf; stroke-width: 7; stroke-linecap: round; stroke-linejoin: round; }}
.trend-svg circle {{ fill: #facc15; stroke: #fff7ed; stroke-width: 3; }}
.trend-svg text {{ fill: #facc15; font-size: 23px; font-weight: 900; text-anchor: middle; }}
.two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
.panel {{ padding: 24px; min-height: 420px; }}
.panel.trend-panel {{ min-height: 0; }}
.panel h2 {{ margin: 0 0 18px; font-size: 31px; line-height: 1; }}
.model-row {{ display: grid; grid-template-columns: 58px 1fr 112px; gap: 16px; align-items: center; padding: 14px 0; border-top: 1px solid rgba(148,163,184,.16); }}
.model-row:first-of-type {{ border-top: none; }}
.rank-num {{ color: #facc15; font-size: 28px; font-weight: 950; }}
.model-name {{ font-size: 23px; font-weight: 900; }}
.model-sub {{ margin-top: 6px; color: #cbd5e1; font-size: 16px; }}
.bar-track {{ margin-top: 8px; height: 12px; background: #0f2a3d; border-radius: 999px; overflow: hidden; }}
.bar-fill {{ height: 100%; background: linear-gradient(90deg, #2dd4bf, #facc15); border-radius: 999px; }}
.model-score {{ text-align: right; color: #facc15; font-size: 25px; font-weight: 950; }}
table {{ width: 100%; border-collapse: collapse; }}
td, th {{ padding: 12px 8px; border-top: 1px solid rgba(148,163,184,.16); text-align: left; font-size: 18px; }}
th {{ color: #99f6e4; font-size: 15px; text-transform: uppercase; letter-spacing: .04em; }}
td:nth-child(3), td:nth-child(4) {{ font-weight: 900; color: #f8fafc; }}
.stats {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; }}
.stat-card {{ min-height: 154px; padding: 19px 18px; border-color: rgba(250,204,21,.42); }}
.stat-value {{ color: #facc15; font-size: 42px; line-height: .98; font-weight: 950; }}
.stat-label {{ margin-top: 12px; color: #f8fafc; font-size: 18px; font-weight: 900; }}
.stat-note {{ margin-top: 8px; color: #cbd5e1; font-size: 15px; line-height: 1.22; }}
.insights {{ display: grid; grid-template-columns: 1.1fr .9fr; gap: 18px; }}
.quote {{ padding: 26px; border: 1px solid rgba(250,204,21,.44); border-radius: 16px; background: rgba(2,6,23,.58); min-height: 204px; }}
.quote h2 {{ margin: 0 0 14px; color: #facc15; font-size: 28px; }}
.quote p {{ margin: 0; color: #e2e8f0; font-size: 22px; line-height: 1.32; font-weight: 700; }}
.bullets {{ padding: 24px 28px; }}
.panel.bullets {{ min-height: 270px; }}
.bullets ul {{ margin: 0; padding-left: 22px; }}
.bullets li {{ margin: 0 0 14px; color: #dbeafe; font-size: 20px; line-height: 1.25; }}
.footer {{ margin-top: 22px; border: 1px solid rgba(148,163,184,.28); border-radius: 14px; padding: 17px 22px; color: #cbd5e1; font-size: 19px; display: flex; justify-content: space-between; gap: 18px; }}
</style>
</head>
<body>
<main class="poster">
  <section class="top">
    <div>
      <div class="eyebrow">CopaComAchismo</div>
      <h1>Review das<br>Simulações</h1>
      <div class="subtitle">Ranking dos modelos, influência por run e saúde operacional · {period}</div>
    </div>
    <div class="badge"><strong>{_pct(title_end)}</strong><span>Hexa no último run<br>{_h(_match_label(latest))}</span></div>
  </section>

  <section class="method">
    <div class="step"><b>1</b>Dados FIFA<span>placares e chaveamento oficiais entram antes do MC.</span></div>
    <div class="step"><b>2</b>Monte Carlo<span>calcula funil, adversários e título.</span></div>
    <div class="step"><b>3</b>Sala multi-modelo<span>modelos desafiam premissas e gates removem lixo.</span></div>
    <div class="step"><b>4</b>Auditoria contínua<span>mercado desafia; não reprecifica sozinho.</span></div>
  </section>

  <div class="section-title">1. Evolução por run</div>
  <section class="run-grid">
    {''.join(run_cards)}
  </section>
  <section class="panel trend-panel">
    <svg class="trend-svg" viewBox="0 0 900 210" preserveAspectRatio="none" aria-label="Evolução do Hexa">
      <line x1="0" y1="180" x2="900" y2="180" stroke="rgba(148,163,184,.22)" stroke-width="1"></line>
      <line x1="0" y1="90" x2="900" y2="90" stroke="rgba(148,163,184,.14)" stroke-width="1"></line>
      <polyline points="{trend_points}"></polyline>
      {''.join(circle_nodes)}
    </svg>
  </section>

  <div class="section-title">2. Ranking e liderança dos modelos</div>
  <section class="two-col">
    <div class="panel">
      <h2>Ranking geral dos modelos</h2>
      {''.join(rank_cards)}
    </div>
    <div class="panel">
      <h2>Mais influente por run</h2>
      <table>
        <thead><tr><th>Run</th><th>Jogo</th><th>Líder</th><th>Hexa</th></tr></thead>
        <tbody>{''.join(leader_rows)}</tbody>
      </table>
    </div>
  </section>

  <div class="section-title">3. Saúde operacional da série</div>
  <section class="stats">
    {_html_stat("Mensagens válidas", f"{int(totals['valid'])}/{int(totals['messages'])}", _pct(valid_rate))}
    {_html_stat("Respostas removidas", str(int(totals['invalid'])), "gates de qualidade")}
    {_html_stat("Tokens usados", _token_millions(totals["tokens"]), f"{_money(totals['cost'])} no período")}
    {_html_stat("Chamadas aos modelos", str(int(totals["calls"])), f"{int(totals['fallback'])} fallbacks")}
    {_html_stat("Maior swing", _pp(title_delta), f"Hexa {period}")}
  </section>

  <div class="section-title">4. Leituras que importam</div>
  <section class="insights">
    <div class="quote">
      <h2>O que a sala trouxe</h2>
      <p>O ranking não mede “modelo favorito”; mede contribuição real: influência acumulada, votos válidos, remoções e protagonismo. Quando um modelo cai no gate, ele perde peso no placar.</p>
    </div>
    <div class="panel bullets">
      <ul>
        {''.join(f'<li>{_h(item)}</li>' for item in insight_items)}
      </ul>
    </div>
  </section>

  <section class="footer">
    <span>Infográfico gerado automaticamente a partir dos bundles de simulação.</span>
    <strong>#CopaComAchismo</strong>
  </section>
</main>
</body>
</html>
"""


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

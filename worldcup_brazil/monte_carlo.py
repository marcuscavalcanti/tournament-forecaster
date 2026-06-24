from __future__ import annotations

import functools
import math
import random
import re
import unicodedata
from datetime import date
from itertools import combinations
from statistics import NormalDist, median
from typing import Any

from worldcup_brazil.bracket import PHASE_LABELS, PHASE_SEQUENCE, brazil_bracket_path


DEFAULT_ITERATIONS = 40000
DEFAULT_SEED = 20260626
DEFAULT_DRAW_PCT = 24.0
DEFAULT_RATING_SCALE = 400.0
DEFAULT_POSITION_RATINGS = (1600.0, 1500.0, 1450.0, 1400.0)
DEFAULT_PROBABILITY_PCT_TO_RATING_POINTS = 8.0
DEFAULT_MAX_SIGNAL_RATING_DELTA = 120.0
DEFAULT_MAX_TEAM_CONTEXT_RATING_DELTA = 180.0
DEFAULT_TEAM_CONTEXT_WARNING_DELTA = 40.0
DEFAULT_PATH_GATE_MIN_ITERATIONS = 10000
DEFAULT_PATH_GATE_MIN_RATING_COVERAGE_PCT = 65.0
DEFAULT_PATH_GATE_NARROW_UNCERTAINTY_THRESHOLD_PCT = 35.0
DEFAULT_CONFIDENCE_LEVEL = 0.95
DEFAULT_RATING_UNCERTAINTY_OUTER_SAMPLES = 200
DEFAULT_RATING_UNCERTAINTY_INNER_ITERATIONS = 200
DEFAULT_CONFIGURED_RATING_SIGMA = 50.0
DEFAULT_PRIOR_RATING_SIGMA = 150.0


@functools.lru_cache(maxsize=4096)
def _normalize_cached(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _normalize(value: Any) -> str:
    # Hot path: chamado ~5,8M vezes por run nos mesmos ~48 nomes de times. A NFKD
    # é a invariante mais cara do loop (auditoria 11/jun: cache sozinho = −49% do
    # runtime, output bit-idêntico). Stringify primeiro garante chave hashável.
    return _normalize_cached(str(value or ""))


def _mc_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("monte_carlo")
    return raw if isinstance(raw, dict) else {}


def _bounded_confidence_level(value: Any, *, default: float = DEFAULT_CONFIDENCE_LEVEL) -> float:
    try:
        confidence_level = float(value)
    except (TypeError, ValueError):
        confidence_level = float(default)
    return max(0.5, min(0.999, confidence_level))


def _confidence_level(config: dict[str, Any]) -> float:
    uncertainty = config.get("uncertainty")
    if isinstance(uncertainty, dict) and "confidence_level" in uncertainty:
        return _bounded_confidence_level(uncertainty.get("confidence_level"))
    return _bounded_confidence_level(_mc_config(config).get("confidence_level", DEFAULT_CONFIDENCE_LEVEL))


def _z_for_confidence_level(confidence_level: float) -> float:
    bounded = _bounded_confidence_level(confidence_level)
    return NormalDist().inv_cdf(0.5 + bounded / 2.0)


def _groups(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    groups_config = config.get("groups_config")
    if not isinstance(groups_config, dict):
        return {}
    groups = groups_config.get("groups")
    return groups if isinstance(groups, dict) else {}


def _team_name(team: dict[str, Any]) -> str:
    return str(team.get("name") or "").strip()


def _team_code(team: dict[str, Any]) -> str:
    return str(team.get("code") or "").strip()


def _all_team_names(config: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for teams in _groups(config).values():
        for team in teams:
            name = _team_name(team)
            if name:
                names.append(name)
    return names


def _configured_team_ratings(config: dict[str, Any]) -> dict[str, float]:
    raw = _mc_config(config).get("team_ratings") or {}
    if not isinstance(raw, dict):
        return {}
    ratings: dict[str, float] = {}
    for key, value in raw.items():
        try:
            rating = float(value)
        except (TypeError, ValueError):
            continue
        ratings[_normalize(key)] = rating
    return ratings


def _configured_team_context_signals(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw_context = _mc_config(config).get("team_context") or {}
    raw_flat = _mc_config(config).get("team_context_signals") or []
    signals: list[dict[str, Any]] = []

    if isinstance(raw_context, dict):
        for team, payload in raw_context.items():
            raw_signals = payload
            if isinstance(payload, dict):
                raw_signals = payload.get("signals", [payload])
            if not isinstance(raw_signals, list):
                continue
            for signal in raw_signals:
                if isinstance(signal, dict):
                    signals.append({"team": str(team), **signal})

    if isinstance(raw_flat, list):
        signals.extend(signal for signal in raw_flat if isinstance(signal, dict))

    return signals


def _signal_has_auditable_source(signal: dict[str, Any]) -> bool:
    for key in ("source_url", "source_query", "source", "source_urls", "source_queries"):
        value = signal.get(key)
        if isinstance(value, list) and any(str(item).strip() for item in value):
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _signal_category(signal: dict[str, Any]) -> str:
    return str(
        signal.get("category")
        or signal.get("family")
        or signal.get("source_family")
        or "contexto"
    ).strip() or "contexto"


def _canonical_signal_family(category: str) -> str:
    raw = str(category or "").strip()
    normalized = _normalize(raw).replace("-", "_").replace("/", "_").replace(" ", "_")
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if any(
        marker in normalized
        for marker in (
            "resultado_recente",
            "resultados_recentes",
            "recent_result",
            "recent_results",
            "match_result",
            "match_results",
            "resultado_de_jogo",
            "resultado_jogo",
        )
    ):
        return "recent_results"
    if (
        "caminho_16_avos" in normalized
        or "16_avos" in normalized
        or "knockout_path" in normalized
        or "path_context" in normalized
        or "caminho_mata_mata" in normalized
    ):
        return "path_context"
    if normalized in {"recent_news", "noticias_recentes", "recent_updates"}:
        return "recent_news"
    if any(marker in normalized for marker in ("injur", "lesao", "lesoes", "corte", "cuts_news")):
        return "injuries_cuts_news"
    if "performance" in normalized or "sofascore" in normalized:
        return "performance"
    if "transfermarkt" in normalized or "market_value" in normalized or "valor_de_mercado" in normalized:
        return "market_value"
    if "bet" in normalized or "prediction_market" in normalized or "mercado_de_apostas" in normalized:
        return "bets_prediction_markets"
    if "rating" in normalized or "ranking" in normalized:
        return "ratings"
    if "press" in normalized or "imprensa" in normalized or "especializada" in normalized:
        return "specialized_press"
    if "arbitragem" in normalized or "arbitration" in normalized or "var" in normalized or "cart" in normalized:
        return "arbitration_var_cards"
    if "amistoso" in normalized or "friendly" in normalized:
        return "recent_friendlies"
    return normalized or "context"


def _signal_confidence(signal: dict[str, Any]) -> float:
    try:
        value = float(signal.get("confidence", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, value))


def _signal_raw_rating_delta(signal: dict[str, Any], *, probability_pct_to_rating_points: float) -> float | None:
    rating_keys = (
        "rating_delta",
        "rating_delta_points",
        "elo_delta",
        "elo_delta_points",
        "market_rating_delta",
        "context_rating_delta",
    )
    for key in rating_keys:
        if key not in signal:
            continue
        try:
            return float(signal[key])
        except (TypeError, ValueError):
            return None
    probability_keys = (
        "probability_delta_pct",
        "win_probability_delta_pct",
        "advancement_probability_delta_pct",
        "scenario_probability_delta_pct",
    )
    for key in probability_keys:
        if key not in signal:
            continue
        try:
            return float(signal[key]) * probability_pct_to_rating_points
        except (TypeError, ValueError):
            return None
    return None


EVENT_REACTIVE_SIGNAL_FAMILIES = {
    "bets_prediction_markets",
    "ratings",
    "performance",
    "injuries_cuts_news",
    "recent_news",
    "specialized_press",
    "recent_friendlies",
    "recent_results",
    "path_context",
}

STRUCTURAL_SIGNAL_FAMILIES = {
    "elenco_talento",
    "squad_depth",
    "tactical_cycle",
    "managerial_structure",
    "market_value",
}

STRUCTURAL_CONTEXT_MARKERS = (
    "attrition",
    "squad",
    "elenco",
    "talent",
    "talento",
    "attack_structure",
    "estrutura",
    "estrutural",
    "cycle",
    "ciclo",
    "ausencias acumuladas",
    "ausencias",
    "ausencias estruturais",
    "ausencias acumuladas",
)

MATCH_EVENT_OPPONENT_ALIASES = {
    "marrocos": ("marrocos", "morocco", "morocco"),
    "haiti": ("haiti",),
    "escocia": ("escocia", "scotland", "escócia"),
    "holanda": ("holanda", "netherlands", "paises baixos", "países baixos"),
    "japao": ("japao", "japan", "japão"),
    "suecia": ("suecia", "sweden", "suécia"),
    "tunisia": ("tunisia", "tunísia", "tunis"),
    "argentina": ("argentina",),
    "portugal": ("portugal",),
    "franca": ("franca", "frança", "france"),
    "espanha": ("espanha", "spain"),
    "alemanha": ("alemanha", "germany"),
    "inglaterra": ("inglaterra", "england"),
    "croacia": ("croacia", "croácia", "croatia"),
}


def _signal_text(signal: dict[str, Any], *, explicit_group: Any = "") -> str:
    parts: list[str] = []
    for key in (
        "rationale",
        "summary",
        "answer",
        "headline",
        "title",
        "event",
        "match",
        "opponent",
        "fixture",
        "source",
        "source_url",
        "source_query",
        "source_urls",
        "source_queries",
    ):
        value = signal.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value is not None:
            parts.append(str(value))
    if explicit_group:
        parts.append(str(explicit_group))
    return _normalize(" ".join(parts))


def _match_date_token(text: str) -> str:
    match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    match = re.search(r"\b(\d{1,2})[-/](\d{1,2})(?:[-/](20\d{2}|\d{2}))?\b", text)
    if match:
        day, month, year = match.groups()
        if year and len(year) == 2:
            year = f"20{year}"
        return f"{year or 'undated'}-{int(month):02d}-{int(day):02d}"
    return "undated"


def _parse_match_date(value: Any) -> date | None:
    token = _match_date_token(_normalize(value))
    if not token.startswith("20"):
        return None
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def _signal_date(signal: dict[str, Any]) -> date | None:
    for key in ("event_date", "match_date", "date", "played_at"):
        parsed = _parse_match_date(signal.get(key))
        if parsed is not None:
            return parsed
    return None


def _is_structural_context_signal(
    signal: dict[str, Any],
    *,
    source_family: str,
    explicit_group: Any = "",
) -> bool:
    return source_family in STRUCTURAL_SIGNAL_FAMILIES


def _raw_correlation_group_hint(signal: dict[str, Any]) -> Any:
    for key in ("correlation_group", "shock_id", "event_id", "context_group"):
        raw = signal.get(key)
        if raw:
            return raw
    return None


def _completed_match_index(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_team: dict[str, list[dict[str, Any]]] = {}
    for match in _completed_group_matches(config):
        match_date = _parse_match_date(match.get("date"))
        if match_date is None:
            continue
        team_a = str(match.get("team_a") or "").strip()
        team_b = str(match.get("team_b") or "").strip()
        if not team_a or not team_b:
            continue
        team_a_key = _normalize(team_a)
        team_b_key = _normalize(team_b)
        indexed = {
            "date": match_date,
            "date_token": match_date.isoformat(),
            "teams": (team_a_key, team_b_key),
        }
        by_team.setdefault(team_a_key, []).append({**indexed, "opponent": team_b_key})
        by_team.setdefault(team_b_key, []).append({**indexed, "opponent": team_a_key})
    for matches in by_team.values():
        matches.sort(key=lambda item: item["date"])
    return by_team


def _completed_match_anchor_group(
    signal: dict[str, Any],
    *,
    team: str,
    completed_match_index: dict[str, list[dict[str, Any]]],
) -> str | None:
    team_key = _normalize(team)
    matches = completed_match_index.get(team_key) or []
    if not matches:
        return None
    signal_played_at = _signal_date(signal)
    candidates = [
        match for match in matches
        if signal_played_at is None or match["date"] <= signal_played_at
    ]
    if not candidates:
        return None
    match = candidates[-1]
    return f"match_event:{team_key}:{match['opponent']}:{match['date_token']}"


def _derived_match_event_group(signal: dict[str, Any], *, team: str, source_family: str, explicit_group: Any = "") -> str | None:
    if _is_structural_context_signal(signal, source_family=source_family, explicit_group=explicit_group):
        return None
    text = _signal_text(signal, explicit_group=explicit_group)
    team_key = _normalize(team)
    for opponent_key, aliases in MATCH_EVENT_OPPONENT_ALIASES.items():
        if opponent_key == team_key:
            continue
        if any(alias in text for alias in aliases):
            explicit_date = (
                signal.get("event_date")
                or signal.get("match_date")
                or signal.get("date")
                or signal.get("played_at")
            )
            date_token = _match_date_token(_normalize(explicit_date)) if explicit_date else _match_date_token(text)
            if date_token == "undated":
                date_token = ""
            suffix = f":{date_token}" if date_token else ""
            return f"match_event:{team_key}:{opponent_key}{suffix}"
    return None


def _signal_correlation_group(
    signal: dict[str, Any],
    *,
    source_family: str,
    team: str,
    completed_match_index: dict[str, list[dict[str, Any]]],
) -> tuple[str, str]:
    if _is_structural_context_signal(signal, source_family=source_family):
        for key in ("correlation_group", "shock_id", "event_id", "context_group"):
            raw = signal.get(key)
            if raw:
                normalized_raw = _normalize(raw)
                if normalized_raw.startswith("match_event"):
                    continue
                return normalized_raw, "structural"
        return source_family, "structural"

    completed_anchor = _completed_match_anchor_group(
        signal,
        team=team,
        completed_match_index=completed_match_index,
    )
    if completed_anchor:
        return completed_anchor, "completed_match"
    return source_family, "fallback_family"


def _team_context_group_rho(mc_config: dict[str, Any], correlation_group: str) -> float:
    default_rho = mc_config.get("team_context_correlation_default_rho", 0.0)
    raw_by_group = mc_config.get("team_context_correlation_rho_by_group") or {}
    raw_value = default_rho
    if isinstance(raw_by_group, dict):
        raw_value = raw_by_group.get(correlation_group, default_rho)
        if raw_value == default_rho and correlation_group.startswith("match_event:"):
            base_group = ":".join(correlation_group.split(":")[:3])
            raw_value = raw_by_group.get(base_group, raw_value)
    try:
        rho = float(raw_value)
    except (TypeError, ValueError):
        rho = 0.0
    return max(0.0, min(1.0, rho))


def _correlated_family_adjustments(
    family_adjustments: list[dict[str, Any]],
    *,
    mc_config: dict[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for adjustment in family_adjustments:
        correlation_group = str(adjustment.get("correlation_group") or adjustment["source_family"])
        grouped.setdefault(correlation_group, []).append(adjustment)

    rendered: list[dict[str, Any]] = []
    for correlation_group, members in sorted(grouped.items()):
        rho = _team_context_group_rho(mc_config, correlation_group)
        dominant = max(members, key=lambda item: abs(float(item["rating_delta"])))
        residual_delta = sum(
            float(item["rating_delta"]) for item in members if item is not dominant
        )
        rating_delta = float(dominant["rating_delta"]) + (1.0 - rho) * residual_delta
        rendered_adjustment = {
            "correlation_group": correlation_group,
            "rho": round(rho, 3),
            "rating_delta": round(rating_delta, 1),
            "dominant_family": str(dominant["source_family"]),
            "dominant_delta": round(float(dominant["rating_delta"]), 1),
            "residual_delta": round(residual_delta, 1),
            "member_families": sorted(str(item["source_family"]) for item in members),
        }
        if any(item.get("_correlation_group_source") == "completed_match" for item in members):
            rendered_adjustment["correlation_group_source"] = "completed_match"
        rendered.append(rendered_adjustment)
    return rendered


def _apply_team_context_adjustments(config: dict[str, Any], ratings: dict[str, float]) -> dict[str, Any]:
    mc_config = _mc_config(config)
    probability_pct_to_rating_points = float(
        mc_config.get("probability_pct_to_rating_points", DEFAULT_PROBABILITY_PCT_TO_RATING_POINTS)
    )
    max_signal_delta = float(mc_config.get("max_signal_rating_delta", DEFAULT_MAX_SIGNAL_RATING_DELTA))
    max_team_delta = float(mc_config.get("max_team_context_rating_delta", DEFAULT_MAX_TEAM_CONTEXT_RATING_DELTA))
    warning_delta = float(mc_config.get("team_context_warning_delta", DEFAULT_TEAM_CONTEXT_WARNING_DELTA))
    team_name_by_key = {_normalize(name): name for name in ratings}

    applied_signal_count = 0
    ignored_signal_count = 0
    source_families: set[str] = set()
    team_adjustments: dict[str, dict[str, Any]] = {}
    completed_matches_by_team = _completed_match_index(config)

    for signal in _configured_team_context_signals(config):
        team_key = _normalize(signal.get("team") or signal.get("selection") or signal.get("country"))
        team = team_name_by_key.get(team_key)
        category = _signal_category(signal)
        source_family = _canonical_signal_family(category)
        raw_delta = _signal_raw_rating_delta(
            signal,
            probability_pct_to_rating_points=probability_pct_to_rating_points,
        )
        if not team or raw_delta is None or not _signal_has_auditable_source(signal):
            ignored_signal_count += 1
            continue

        confidence = _signal_confidence(signal)
        bounded_raw_delta = max(-max_signal_delta, min(max_signal_delta, raw_delta))
        weighted_delta = bounded_raw_delta * confidence
        if abs(weighted_delta) < 0.01:
            ignored_signal_count += 1
            continue

        bucket = team_adjustments.setdefault(
            team,
            {
                "team": team,
                "rating_delta": 0.0,
                "signals": [],
                "source_families": set(),
                "family_deltas": {},
            },
        )
        bucket["source_families"].add(source_family)
        correlation_group, correlation_group_source = _signal_correlation_group(
            signal,
            source_family=source_family,
            team=team,
            completed_match_index=completed_matches_by_team,
        )
        bucket["family_deltas"].setdefault((source_family, correlation_group), []).append(weighted_delta)
        bucket.setdefault("family_group_sources", {})[(source_family, correlation_group)] = correlation_group_source
        rendered_signal = {
            "category": source_family,
            "correlation_group": correlation_group,
            "correlation_group_source": correlation_group_source,
            "rating_delta": round(weighted_delta, 1),
            "confidence": round(confidence, 2),
            "source": signal.get("source_url")
            or signal.get("source_query")
            or signal.get("source")
            or signal.get("source_urls")
            or signal.get("source_queries"),
            "agent": signal.get("agent"),
        }
        if category != source_family:
            rendered_signal["original_category"] = category
        raw_group_hint = _raw_correlation_group_hint(signal)
        derived_match_hint = _derived_match_event_group(
            signal,
            team=team,
            source_family=source_family,
            explicit_group=raw_group_hint or "",
        )
        if raw_group_hint and _normalize(raw_group_hint) != correlation_group:
            rendered_signal["model_correlation_group_hint"] = _normalize(raw_group_hint)
        if derived_match_hint and derived_match_hint != correlation_group:
            rendered_signal["model_match_event_hint"] = derived_match_hint
        if (
            correlation_group_source == "completed_match"
            and raw_group_hint
            and _normalize(raw_group_hint) != correlation_group
        ):
            rendered_signal["correlation_group_override_reason"] = "completed_match_overrode_model_hint"
        bucket["signals"].append(rendered_signal)
        source_families.add(source_family)
        applied_signal_count += 1

    rendered_adjustments: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for team, bucket in team_adjustments.items():
        family_adjustments: list[dict[str, Any]] = []
        internal_family_adjustments: list[dict[str, Any]] = []
        for (family, correlation_group), values in sorted(bucket["family_deltas"].items()):
            if not values:
                continue
            correlation_group_source = bucket.get("family_group_sources", {}).get(
                (family, correlation_group),
                "fallback_family",
            )
            adjustment = {
                "source_family": family,
                "rating_delta": round(float(median(values)), 1),
                "signal_count": len(values),
            }
            if correlation_group != family:
                adjustment["correlation_group"] = correlation_group
            family_adjustments.append(adjustment)
            internal_adjustment = dict(adjustment)
            internal_adjustment["_correlation_group_source"] = correlation_group_source
            internal_family_adjustments.append(internal_adjustment)
        correlation_adjustments = _correlated_family_adjustments(
            internal_family_adjustments,
            mc_config=mc_config,
        )
        completed_multi_family_groups = [
            item for item in correlation_adjustments
            if item.get("correlation_group_source") == "completed_match"
            and len(item.get("member_families") or []) > 1
        ]
        completed_anchor_signals = [
            signal for signal in bucket["signals"]
            if signal.get("correlation_group_source") == "completed_match"
            and signal.get("category") not in STRUCTURAL_SIGNAL_FAMILIES
        ]
        completed_anchor_families = {str(signal["category"]) for signal in completed_anchor_signals}
        if len(completed_anchor_families) > 1 and not completed_multi_family_groups:
            warnings.append(
                {
                    "team": team,
                    "reason": "team_context_event_reactive_under_merge_guard",
                    "source_families": sorted(completed_anchor_families),
                }
            )
        fallback_reactive_families = {
            str(signal["category"])
            for signal in bucket["signals"]
            if signal.get("category") not in STRUCTURAL_SIGNAL_FAMILIES
            and signal.get("correlation_group_source") != "completed_match"
        }
        if len(fallback_reactive_families) > 1 and not completed_anchor_signals:
            warnings.append(
                {
                    "team": team,
                    "reason": "team_context_reactive_families_without_calendar_anchor",
                    "source_families": sorted(fallback_reactive_families),
                }
            )
        for signal in bucket["signals"]:
            if (
                signal.get("category") not in STRUCTURAL_SIGNAL_FAMILIES
                and signal.get("correlation_group_source") != "completed_match"
                and signal.get("model_match_event_hint")
            ):
                warnings.append(
                    {
                        "team": team,
                        "reason": "team_context_model_match_shock_without_calendar_anchor",
                        "source_family": str(signal["category"]),
                        "model_correlation_group_hint": signal.get("model_correlation_group_hint"),
                        "derived_match_event": signal.get("model_match_event_hint"),
                    }
                )
        raw_team_delta = sum(float(item["rating_delta"]) for item in correlation_adjustments)
        # Evidence-weighted regression. The cap is applied FIRST so the evidence-weighting
        # operates on the bounded magnitude instead of being erased by the clamp on true
        # blow-ups. Independent evidence is min(material correlation groups, distinct verified
        # sources): a single shock relabelled into many group ids cannot inflate the count past
        # the number of real sources behind it, and sub-threshold groups from one source cannot
        # bypass it either. Only the excess above warning_delta is shrunk, by a Bayesian factor
        # n/(n+prior). Deterministic, zero added latency.
        evidence_regression_enabled = bool(mc_config.get("team_context_evidence_regression_enabled", True))
        evidence_prior = float(mc_config.get("team_context_evidence_regression_prior", 2.0))
        evidence_material_delta = float(mc_config.get("team_context_evidence_material_delta", 1.0))
        capped_raw_delta = max(-max_team_delta, min(max_team_delta, raw_team_delta))
        material_groups = sum(
            1
            for item in correlation_adjustments
            if abs(float(item["rating_delta"])) >= evidence_material_delta
        )
        distinct_sources = len(
            {
                str(signal.get("source") or signal.get("source_url") or "").strip()
                for signal in bucket["signals"]
            }
            - {""}
        )
        independent_evidence = min(material_groups, distinct_sources)
        regressed_team_delta = capped_raw_delta
        if evidence_regression_enabled and abs(capped_raw_delta) > warning_delta:
            factor = independent_evidence / (independent_evidence + evidence_prior)
            excess = abs(capped_raw_delta) - warning_delta
            sign = 1.0 if capped_raw_delta >= 0 else -1.0
            regressed_team_delta = sign * (warning_delta + excess * factor)
        bucket["evidence_regression"] = {
            "raw_delta": round(raw_team_delta, 1),
            "capped_delta": round(capped_raw_delta, 1),
            "regressed_delta": round(regressed_team_delta, 1),
            "independent_evidence": independent_evidence,
            "material_groups": material_groups,
            "distinct_sources": distinct_sources,
            "prior": evidence_prior,
        }
        bucket["rating_delta"] = regressed_team_delta
        bounded_team_delta = max(-max_team_delta, min(max_team_delta, float(bucket["rating_delta"])))
        if abs(bounded_team_delta) > warning_delta:
            warnings.append(
                {
                    "team": team,
                    "rating_delta": round(bounded_team_delta, 1),
                    "threshold": warning_delta,
                    "reason": "team_context_delta_above_warning_threshold",
                }
            )
        ratings[team] = ratings[team] + bounded_team_delta
        rendered_adjustments.append(
            {
                "team": team,
                "rating_delta": round(bounded_team_delta, 1),
                "evidence_regression": bucket["evidence_regression"],
                "source_families": sorted(bucket["source_families"]),
                "family_adjustments": family_adjustments,
                "correlation_adjustments": correlation_adjustments,
                "signals": bucket["signals"],
            }
        )

    rendered_adjustments.sort(key=lambda item: abs(float(item["rating_delta"])), reverse=True)
    return {
        "enabled": bool(applied_signal_count or ignored_signal_count),
        "applied_signal_count": applied_signal_count,
        "ignored_signal_count": ignored_signal_count,
        "teams_with_context_count": len(rendered_adjustments),
        "source_families": sorted(source_families),
        "team_adjustments": rendered_adjustments,
        "warnings": warnings,
        "team_context_warning_delta": warning_delta,
        "probability_pct_to_rating_points": probability_pct_to_rating_points,
        "max_signal_rating_delta": max_signal_delta,
        "max_team_context_rating_delta": max_team_delta,
    }


def _position_rating(position_index: int, mc_config: dict[str, Any]) -> float:
    raw = mc_config.get("default_position_ratings")
    if isinstance(raw, list) and raw:
        try:
            values = [float(value) for value in raw]
        except (TypeError, ValueError):
            values = list(DEFAULT_POSITION_RATINGS)
    else:
        values = list(DEFAULT_POSITION_RATINGS)
    if position_index < len(values):
        return values[position_index]
    return values[-1]


def _build_rating_table_with_sources(config: dict[str, Any]) -> tuple[dict[str, float], float, set[str]]:
    explicit = _configured_team_ratings(config)
    ratings: dict[str, float] = {}
    configured_count = 0
    explicit_team_names: set[str] = set()
    for teams in _groups(config).values():
        for index, team in enumerate(teams):
            name = _team_name(team)
            if not name:
                continue
            keys = [_normalize(name), _normalize(_team_code(team))]
            explicit_rating = next((explicit[key] for key in keys if key in explicit), None)
            if explicit_rating is not None:
                ratings[name] = explicit_rating
                configured_count += 1
                explicit_team_names.add(name)
            else:
                ratings[name] = _position_rating(index, _mc_config(config))
    total = max(1, len(ratings))
    return ratings, round(configured_count / total * 100.0, 1), explicit_team_names


def _build_rating_table(config: dict[str, Any]) -> tuple[dict[str, float], float]:
    ratings, coverage, _ = _build_rating_table_with_sources(config)
    return ratings, coverage


def _explicit_brazil_group_probabilities(config: dict[str, Any]) -> dict[tuple[str, str], tuple[float, float, float]]:
    probabilities: dict[tuple[str, str], tuple[float, float, float]] = {}
    brazil = str(config.get("brazil_team_name", "Brasil"))
    for match in config.get("group_matches", []) or []:
        if not isinstance(match, dict):
            continue
        opponent = str(match.get("opponent") or "").strip()
        if not opponent or "brazil_pct" not in match:
            continue
        try:
            brazil_pct = float(match.get("brazil_pct"))
            draw_pct = float(match.get("draw_pct", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        opponent_pct = max(0.0, 100.0 - brazil_pct - draw_pct)
        probabilities[(_normalize(brazil), _normalize(opponent))] = (
            max(0.0, min(100.0, brazil_pct)),
            max(0.0, min(100.0, draw_pct)),
            max(0.0, min(100.0, opponent_pct)),
        )
    return probabilities


def _team_group_map(config: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for group, teams in _groups(config).items():
        for team in teams:
            name = _team_name(team)
            if name:
                mapping[_normalize(name)] = str(group).strip().upper()
    return mapping


def _completed_pair_key(team_a: str, team_b: str) -> tuple[str, str]:
    return tuple(sorted((_normalize(team_a), _normalize(team_b))))


def _score_value(record: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key not in record:
            continue
        try:
            return int(record[key])
        except (TypeError, ValueError):
            return None
    return None


def _completed_group_matches(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw_matches = config.get("completed_group_matches") or config.get("group_results") or []
    if not isinstance(raw_matches, list):
        return []
    team_groups = _team_group_map(config)
    completed: list[dict[str, Any]] = []
    for raw in raw_matches:
        if not isinstance(raw, dict):
            continue
        team_a = str(raw.get("team_a") or raw.get("home") or raw.get("team1") or "").strip()
        team_b = str(raw.get("team_b") or raw.get("away") or raw.get("team2") or "").strip()
        if not team_a or not team_b:
            continue
        score_a = _score_value(raw, "score_a", "home_score", "goals_a", "team_a_score")
        score_b = _score_value(raw, "score_b", "away_score", "goals_b", "team_b_score")
        if score_a is None or score_b is None:
            score_text = str(raw.get("score") or "").strip()
            score_match = re.fullmatch(r"\s*(\d+)\s*[-xX]\s*(\d+)\s*", score_text)
            if score_match:
                score_a = int(score_match.group(1))
                score_b = int(score_match.group(2))
        if score_a is None or score_b is None:
            continue
        group = str(raw.get("group") or "").strip().upper()
        if not group:
            group_a = team_groups.get(_normalize(team_a), "")
            group_b = team_groups.get(_normalize(team_b), "")
            if group_a and group_a == group_b:
                group = group_a
        if not group:
            continue
        completed.append(
            {
                "group": group,
                "team_a": team_a,
                "team_b": team_b,
                "score_a": score_a,
                "score_b": score_b,
                "date": raw.get("date"),
                "source": raw.get("source") or raw.get("source_url"),
                "score": f"{team_a} {score_a}-{score_b} {team_b}",
            }
        )
    return completed


def _completed_group_match_lookup(completed_matches: list[dict[str, Any]]) -> dict[str, dict[tuple[str, str], dict[str, Any]]]:
    lookup: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for match in completed_matches:
        group = str(match.get("group") or "").strip().upper()
        team_a = str(match.get("team_a") or "")
        team_b = str(match.get("team_b") or "")
        if not group or not team_a or not team_b:
            continue
        lookup.setdefault(group, {})[_completed_pair_key(team_a, team_b)] = match
    return lookup


def _row_team_key(rows: dict[str, dict[str, Any]], team: str) -> str | None:
    if team in rows:
        return team
    team_key = _normalize(team)
    return next((name for name in rows if _normalize(name) == team_key), None)


def _apply_match_result_to_rows(rows: dict[str, dict[str, Any]], team_a: str, team_b: str, score_a: int, score_b: int) -> None:
    row_a = _row_team_key(rows, team_a)
    row_b = _row_team_key(rows, team_b)
    if row_a is None or row_b is None:
        return
    rows[row_a]["played"] = int(rows[row_a].get("played", 0)) + 1
    rows[row_b]["played"] = int(rows[row_b].get("played", 0)) + 1
    rows[row_a]["goals_for"] = int(rows[row_a].get("goals_for", 0)) + score_a
    rows[row_a]["goals_against"] = int(rows[row_a].get("goals_against", 0)) + score_b
    rows[row_b]["goals_for"] = int(rows[row_b].get("goals_for", 0)) + score_b
    rows[row_b]["goals_against"] = int(rows[row_b].get("goals_against", 0)) + score_a
    if score_a > score_b:
        rows[row_a]["points"] += 3
        rows[row_a]["wins"] += 1
    elif score_b > score_a:
        rows[row_b]["points"] += 3
        rows[row_b]["wins"] += 1
    else:
        rows[row_a]["points"] += 1
        rows[row_b]["points"] += 1


def _brazil_group(config: dict[str, Any]) -> str:
    configured = str(config.get("brazil_group") or "").strip().upper()
    if configured:
        return configured
    brazil = _normalize(config.get("brazil_team_name", "Brasil"))
    for group, teams in _groups(config).items():
        if any(_normalize(_team_name(team)) == brazil for team in teams):
            return str(group).strip().upper()
    return ""


def _completed_current_table(
    config: dict[str, Any],
    completed_matches: list[dict[str, Any]],
    *,
    ratings: dict[str, float],
    group: str | None = None,
) -> list[dict[str, Any]]:
    target_group = str(group or _brazil_group(config)).strip().upper()
    teams = _groups(config).get(target_group, [])
    rows = {
        _team_name(team): {
            "team": _team_name(team),
            "played": 0,
            "points": 0,
            "wins": 0,
            "goals_for": 0,
            "goals_against": 0,
            "rating": ratings.get(_team_name(team), 0.0),
        }
        for team in teams
        if _team_name(team)
    }
    for match in completed_matches:
        if str(match.get("group") or "").strip().upper() != target_group:
            continue
        _apply_match_result_to_rows(
            rows,
            str(match["team_a"]),
            str(match["team_b"]),
            int(match["score_a"]),
            int(match["score_b"]),
        )
    rendered = []
    for row in rows.values():
        goals_for = int(row.get("goals_for", 0))
        goals_against = int(row.get("goals_against", 0))
        rendered.append(
            {
                "team": row["team"],
                "played": int(row.get("played", 0)),
                "points": int(row.get("points", 0)),
                "wins": int(row.get("wins", 0)),
                "goals_for": goals_for,
                "goals_against": goals_against,
                "goal_difference": goals_for - goals_against,
            }
        )
    rendered.sort(
        key=lambda row: (
            row["points"],
            row["wins"],
            row["goal_difference"],
            row["goals_for"],
            ratings.get(str(row["team"]), 0.0),
        ),
        reverse=True,
    )
    return rendered


def _completed_results_for_group(completed_matches: list[dict[str, Any]], group: str) -> list[dict[str, Any]]:
    target_group = str(group or "").strip().upper()
    return [
        {
            "group": match.get("group"),
            "date": match.get("date"),
            "score": match.get("score"),
            "source": match.get("source"),
        }
        for match in completed_matches
        if str(match.get("group") or "").strip().upper() == target_group
    ]


def _completed_results_for_all_groups(completed_matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "group": match.get("group"),
            "date": match.get("date"),
            "score": match.get("score"),
            "source": match.get("source"),
        }
        for match in completed_matches
    ]


def _phase_relevant_groups(config: dict[str, Any]) -> dict[str, list[str]]:
    by_phase: dict[str, list[str]] = {}
    for entry in brazil_bracket_path(config):
        phase = str(entry.get("phase") or "").strip()
        groups = [
            str(group).strip().upper()
            for group in entry.get("allowed_opponent_groups", []) or []
            if str(group).strip()
        ]
        if phase and groups:
            by_phase[phase] = list(dict.fromkeys(groups))
    return by_phase


def _relevant_groups_for_brazil_path(config: dict[str, Any]) -> list[str]:
    ordered: list[str] = []

    def add(group: str) -> None:
        normalized = str(group or "").strip().upper()
        if normalized and normalized not in ordered:
            ordered.append(normalized)

    add(_brazil_group(config))
    for groups in _phase_relevant_groups(config).values():
        for group in groups:
            add(group)
    return ordered


def _relevant_group_states_summary(
    config: dict[str, Any],
    *,
    ratings: dict[str, float],
    completed_matches: list[dict[str, Any]],
) -> dict[str, Any]:
    phase_groups = _phase_relevant_groups(config)
    states: dict[str, Any] = {}
    for group in _relevant_groups_for_brazil_path(config):
        phases = [
            phase
            for phase, groups in phase_groups.items()
            if group in groups
        ]
        states[group] = {
            "group": group,
            "phases": phases,
            "current_table": _completed_current_table(
                config,
                completed_matches,
                ratings=ratings,
                group=group,
            ),
            "completed_results": _completed_results_for_group(completed_matches, group),
        }
    return states


@functools.lru_cache(maxsize=32768)
def _rating_win_probability(rating_a: float, rating_b: float, *, scale: float) -> float:
    # Pura: depende só de (rating_a, rating_b, scale). Por outer-sample os ratings
    # são fixos, então cada par recorre nas 200+ iterações internas — memo dá hit
    # alto e devolve o MESMO float (bit-idêntico ao recálculo).
    scale = max(1.0, scale)
    return 1.0 / (1.0 + 10.0 ** (-(rating_a - rating_b) / scale))


@functools.lru_cache(maxsize=8192)
def _rating_draw_probability(rating_a: float, rating_b: float, *, default_draw_pct: float, scale: float) -> float:
    mismatch = min(abs(rating_a - rating_b) / max(1.0, scale), 2.0)
    draw_pct = max(12.0, float(default_draw_pct) - mismatch * 5.0)
    return max(0.0, min(35.0, draw_pct)) / 100.0


def _sample_group_match(
    rng: random.Random,
    team_a: str,
    team_b: str,
    *,
    ratings: dict[str, float],
    explicit_brazil_probs: dict[tuple[str, str], tuple[float, float, float]],
    default_draw_pct: float,
    rating_scale: float,
) -> int:
    key_ab = (_normalize(team_a), _normalize(team_b))
    key_ba = (_normalize(team_b), _normalize(team_a))
    draw = rng.random()
    if key_ab in explicit_brazil_probs:
        a_pct, draw_pct, _ = explicit_brazil_probs[key_ab]
        if draw < a_pct / 100.0:
            return 1
        if draw < (a_pct + draw_pct) / 100.0:
            return 0
        return -1
    if key_ba in explicit_brazil_probs:
        b_pct, draw_pct, _ = explicit_brazil_probs[key_ba]
        if draw < b_pct / 100.0:
            return -1
        if draw < (b_pct + draw_pct) / 100.0:
            return 0
        return 1

    rating_a = ratings[team_a]
    rating_b = ratings[team_b]
    draw_probability = _rating_draw_probability(
        rating_a,
        rating_b,
        default_draw_pct=default_draw_pct,
        scale=rating_scale,
    )
    win_a_if_not_draw = _rating_win_probability(rating_a, rating_b, scale=rating_scale)
    win_a = (1.0 - draw_probability) * win_a_if_not_draw
    if draw < win_a:
        return 1
    if draw < win_a + draw_probability:
        return 0
    return -1


def _sample_knockout_winner(
    rng: random.Random,
    team_a: str,
    team_b: str,
    *,
    ratings: dict[str, float],
    rating_scale: float,
) -> str:
    win_a = _rating_win_probability(ratings[team_a], ratings[team_b], scale=rating_scale)
    return team_a if rng.random() < win_a else team_b


def _simulate_groups(
    rng: random.Random,
    config: dict[str, Any],
    *,
    ratings: dict[str, float],
    explicit_brazil_probs: dict[tuple[str, str], tuple[float, float, float]],
    completed_group_lookup: dict[str, dict[tuple[str, str], dict[str, Any]]] | None = None,
    default_draw_pct: float,
    rating_scale: float,
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    rankings: dict[str, list[str]] = {}
    third_rows: list[dict[str, Any]] = []
    for group, teams in _groups(config).items():
        names = [_team_name(team) for team in teams if _team_name(team)]
        rows = {
            name: {
                "team": name,
                "points": 0,
                "wins": 0,
                "played": 0,
                "goals_for": 0,
                "goals_against": 0,
                "rating": ratings[name],
                "tie_noise": rng.random(),
            }
            for name in names
        }
        group_completed = (completed_group_lookup or {}).get(str(group).strip().upper(), {})
        for match in group_completed.values():
            _apply_match_result_to_rows(
                rows,
                str(match["team_a"]),
                str(match["team_b"]),
                int(match["score_a"]),
                int(match["score_b"]),
            )
        for team_a, team_b in combinations(names, 2):
            if _completed_pair_key(team_a, team_b) in group_completed:
                continue
            outcome = _sample_group_match(
                rng,
                team_a,
                team_b,
                ratings=ratings,
                explicit_brazil_probs=explicit_brazil_probs,
                default_draw_pct=default_draw_pct,
                rating_scale=rating_scale,
            )
            if outcome > 0:
                rows[team_a]["points"] += 3
                rows[team_a]["wins"] += 1
            elif outcome < 0:
                rows[team_b]["points"] += 3
                rows[team_b]["wins"] += 1
            else:
                rows[team_a]["points"] += 1
                rows[team_b]["points"] += 1
        ordered_rows = sorted(
            rows.values(),
            key=lambda row: (row["points"], row["wins"], row["rating"], row["tie_noise"]),
            reverse=True,
        )
        rankings[group] = [str(row["team"]) for row in ordered_rows]
        if len(ordered_rows) >= 3:
            third = dict(ordered_rows[2])
            third["group"] = group
            third_rows.append(third)
    third_rows.sort(
        key=lambda row: (row["points"], row["wins"], row["rating"], row["tie_noise"]),
        reverse=True,
    )
    return rankings, third_rows[:8]


@functools.lru_cache(maxsize=256)
def _slot_kind(slot: str) -> dict[str, Any]:
    # Hot path: ~2,5M regex/run sobre os mesmos ~31 labels constantes de bracket.
    # Memoizado; o resultado é tratado como IMUTÁVEL pelos callers (somente leitura,
    # e `groups` é tupla justamente para o dict compartilhado do cache não corromper).
    label = str(slot or "").strip().upper().replace(" ", "")
    winner = re.fullmatch(r"W(\d+)", label)
    if winner:
        return {"type": "winner", "match_id": int(winner.group(1)), "label": label}
    rank = re.fullmatch(r"([123])([A-L])", label)
    if rank:
        return {"type": "group_rank", "rank": int(rank.group(1)), "group": rank.group(2), "label": label}
    third = re.fullmatch(r"3([A-L/]+)", label)
    if third:
        groups = tuple(group for group in third.group(1).replace("/", "") if group in "ABCDEFGHIJKL")
        return {"type": "best_third", "rank": 3, "groups": groups, "label": label}
    return {"type": "unknown", "label": label}


def _allocate_best_thirds(
    config: dict[str, Any],
    qualified_thirds: list[dict[str, Any]],
) -> tuple[dict[str, str], int]:
    """Aloca os melhores terceiros aos slots 3X/Y/Z por matching com backtracking.

    Regressão histórica: a alocação gulosa por ordem de chegada falhava em ~45% das
    simulações (slot sem terceiro compatível restante), o jogo era pulado em silêncio
    e a cascata chegava à final, que deixava de ser disputada em ~60% das simulações,
    esmagando o funil do Brasil. O matching garante alocação completa sempre que ela
    existe; se a configuração for inviável, o preenchimento relaxado evita pular jogo
    e o contador de relaxamentos fica auditável no resultado.
    """
    slots: list[tuple[str, list[str]]] = []
    for match in _bracket_matches(config, "round_of_32"):
        for slot in match.get("slots", []):
            kind = _slot_kind(str(slot))
            if kind["type"] == "best_third":
                slots.append((str(kind["label"]), list(kind["groups"])))
    if not slots:
        return {}, 0

    team_by_group: dict[str, str] = {}
    for row in qualified_thirds:
        group = str(row.get("group") or "").strip().upper()
        if group and group not in team_by_group:
            team_by_group[group] = str(row["team"])

    available = set(team_by_group)
    order = sorted(
        range(len(slots)),
        key=lambda index: len([group for group in slots[index][1] if group in available]),
    )
    assignment: dict[str, str] = {}

    def backtrack(position: int) -> bool:
        if position == len(order):
            return True
        label, allowed = slots[order[position]]
        for group in allowed:
            if group in available:
                available.discard(group)
                assignment[label] = group
                if backtrack(position + 1):
                    return True
                available.add(group)
                del assignment[label]
        return False

    relaxed = 0
    if not backtrack(0):
        assignment.clear()
        available = set(team_by_group)
        for label, allowed in slots:
            group = next((candidate for candidate in allowed if candidate in available), None)
            if group is not None:
                assignment[label] = group
                available.discard(group)
        for label, _allowed in slots:
            if label not in assignment and available:
                assignment[label] = available.pop()
                relaxed += 1

    return {label: team_by_group[group] for label, group in assignment.items()}, relaxed


def _resolve_slot(
    slot: str,
    *,
    rankings: dict[str, list[str]],
    qualified_thirds: list[dict[str, Any]],
    used_third_groups: set[str],
    match_winners: dict[int, str],
    third_assignment: dict[str, str] | None = None,
) -> str | None:
    kind = _slot_kind(slot)
    if kind["type"] == "winner":
        return match_winners.get(kind["match_id"])
    if kind["type"] == "group_rank":
        group_rankings = rankings.get(kind["group"], [])
        index = int(kind["rank"]) - 1
        if 0 <= index < len(group_rankings):
            return group_rankings[index]
        return None
    if kind["type"] == "best_third":
        assigned = (third_assignment or {}).get(str(kind["label"]))
        if assigned:
            return assigned
        for row in qualified_thirds:
            group = str(row.get("group"))
            if group in used_third_groups or group not in kind["groups"]:
                continue
            used_third_groups.add(group)
            return str(row["team"])
    return None


def _bracket_matches(config: dict[str, Any], phase_key: str) -> list[dict[str, Any]]:
    bracket = config.get("bracket_config")
    if not isinstance(bracket, dict):
        return []
    matches = bracket.get(phase_key)
    return matches if isinstance(matches, list) else []


def _empty_phase_bucket() -> dict[str, Any]:
    return {
        "reach_count": 0,
        "opponent_counts": {},
        "opponent_win_counts": {},
        "brazil_slot_counts": {},
    }


def _wilson_interval_pct(successes: int, total: int, *, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 100.0
    p = successes / total
    denom = 1.0 + z * z / total
    centre = (p + z * z / (2.0 * total)) / denom
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total) / denom
    return round(max(0.0, (centre - margin) * 100.0), 1), round(min(100.0, (centre + margin) * 100.0), 1)


def _merge_phase_bucket(target: dict[str, Any], source: dict[str, Any]) -> None:
    target["reach_count"] = int(target.get("reach_count") or 0) + int(source.get("reach_count") or 0)
    for key in ("opponent_counts", "opponent_win_counts", "brazil_slot_counts"):
        target_counts = target.setdefault(key, {})
        for item, count in (source.get(key) or {}).items():
            target_counts[item] = int(target_counts.get(item) or 0) + int(count)


def _merge_simulation_diagnostics(target: dict[str, Any], source: dict[str, Any]) -> None:
    for diagnostic_key, diagnostic_value in source.items():
        if isinstance(diagnostic_value, dict):
            bucket = target.setdefault(diagnostic_key, {})
            if not isinstance(bucket, dict):
                bucket = {}
                target[diagnostic_key] = bucket
            for item, count in diagnostic_value.items():
                bucket[str(item)] = int(bucket.get(str(item), 0) or 0) + int(count or 0)
            continue
        target[diagnostic_key] = int(target.get(diagnostic_key, 0) or 0) + int(diagnostic_value or 0)


def _stage_probabilities_from_buckets(
    phase_buckets: dict[str, dict[str, Any]],
    *,
    title_count: int,
    iterations: int,
) -> dict[str, float]:
    return {
        "16_avos": _round_pct(int(phase_buckets["16 avos"]["reach_count"]), iterations),
        "oitavas": _round_pct(int(phase_buckets["Oitavas"]["reach_count"]), iterations),
        "quartas": _round_pct(int(phase_buckets["Quartas"]["reach_count"]), iterations),
        "semifinal": _round_pct(int(phase_buckets["Semifinal"]["reach_count"]), iterations),
        "final": _round_pct(int(phase_buckets["Final"]["reach_count"]), iterations),
        "titulo": _round_pct(title_count, iterations),
    }


def _stage_sample_intervals_from_buckets(
    phase_buckets: dict[str, dict[str, Any]],
    *,
    title_count: int,
    iterations: int,
    z: float,
) -> dict[str, tuple[float, float]]:
    return {
        "16_avos": _wilson_interval_pct(int(phase_buckets["16 avos"]["reach_count"]), iterations, z=z),
        "oitavas": _wilson_interval_pct(int(phase_buckets["Oitavas"]["reach_count"]), iterations, z=z),
        "quartas": _wilson_interval_pct(int(phase_buckets["Quartas"]["reach_count"]), iterations, z=z),
        "semifinal": _wilson_interval_pct(int(phase_buckets["Semifinal"]["reach_count"]), iterations, z=z),
        "final": _wilson_interval_pct(int(phase_buckets["Final"]["reach_count"]), iterations, z=z),
        "titulo": _wilson_interval_pct(title_count, iterations, z=z),
    }


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, q)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _stage_uncertainty_intervals(
    stage_samples: dict[str, list[float]],
    stage_probabilities: dict[str, float],
    *,
    confidence_level: float,
    inner_variances: dict[str, list[float]] | None = None,
) -> dict[str, tuple[float, float]]:
    alpha = (1.0 - _bounded_confidence_level(confidence_level)) / 2.0
    z = _z_for_confidence_level(confidence_level)
    intervals: dict[str, tuple[float, float]] = {}
    for key, values in stage_samples.items():
        if not values:
            continue
        point = float(stage_probabilities.get(key, 0.0))
        if inner_variances and key in inner_variances and len(values) >= 2:
            mean = sum(float(value) for value in values) / len(values)
            observed_variance = sum((float(value) - mean) ** 2 for value in values) / (len(values) - 1)
            inner_values = [max(0.0, float(value)) for value in inner_variances.get(key, [])]
            mean_inner_variance = sum(inner_values) / len(inner_values) if inner_values else 0.0
            epistemic_variance = max(0.0, observed_variance - mean_inner_variance)
            margin = z * math.sqrt(epistemic_variance)
            low = point - margin
            high = point + margin
        else:
            low = min(_quantile(values, alpha), point)
            high = max(_quantile(values, 1.0 - alpha), point)
        intervals[key] = (round(max(0.0, low), 1), round(min(100.0, high), 1))
    return intervals


def _sample_rating_table(
    base_ratings: dict[str, float],
    *,
    explicit_team_names: set[str],
    rng: random.Random,
    configured_sigma: float,
    prior_sigma: float,
) -> dict[str, float]:
    sampled: dict[str, float] = {}
    for team, rating in base_ratings.items():
        sigma = configured_sigma if team in explicit_team_names else prior_sigma
        if sigma <= 0:
            sampled[team] = float(rating)
        else:
            sampled[team] = max(600.0, min(2400.0, rng.gauss(float(rating), float(sigma))))
    return sampled


def _path_uncertainty_pct(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total <= 0 or len(counts) <= 1:
        return 0.0
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log(p)
    max_entropy = math.log(len(counts))
    return round(entropy / max_entropy * 100.0, 1) if max_entropy > 0 else 0.0


def _round_pct(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count / total * 100.0, 1)


def _group_state_summary(
    config: dict[str, Any],
    *,
    ratings: dict[str, float],
    completed_matches: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    iterations: int,
) -> dict[str, Any]:
    position_counts = {
        str(position): int(count)
        for position, count in (diagnostics.get("brazil_group_position_counts") or {}).items()
    }
    position_pct = {
        str(position): _round_pct(count, iterations)
        for position, count in sorted(position_counts.items(), key=lambda item: int(item[0]))
    }
    brazil_group = _brazil_group(config)
    return {
        "brazil_group": brazil_group,
        "current_table": _completed_current_table(config, completed_matches, ratings=ratings),
        "completed_results": _completed_results_for_group(completed_matches, brazil_group),
        "brazil_position_counts": position_counts,
        "brazil_position_pct": position_pct,
        "brazil_first_pct": position_pct.get("1", 0.0),
        "brazil_top2_pct": round(position_pct.get("1", 0.0) + position_pct.get("2", 0.0), 1),
    }


def _simulate_tournament_counts(
    config: dict[str, Any],
    *,
    ratings: dict[str, float],
    iterations: int,
    rng: random.Random,
    explicit_brazil_probs: dict[tuple[str, str], tuple[float, float, float]],
    completed_group_lookup: dict[str, dict[tuple[str, str], dict[str, Any]]] | None,
    default_draw_pct: float,
    rating_scale: float,
    brazil: str,
) -> tuple[dict[str, dict[str, Any]], int, dict[str, Any]]:
    phase_buckets = {PHASE_LABELS[key]: _empty_phase_bucket() for key in PHASE_SEQUENCE}
    title_count = 0
    third_allocation_relaxed_count = 0
    unresolved_match_count = 0
    brazil_group_position_counts: dict[str, int] = {}
    team_groups = _team_group_map(config)
    brazil_group = team_groups.get(_normalize(brazil), str(config.get("brazil_group") or "").strip().upper())

    for _ in range(iterations):
        rankings, qualified_thirds = _simulate_groups(
            rng,
            config,
            ratings=ratings,
            explicit_brazil_probs=explicit_brazil_probs,
            completed_group_lookup=completed_group_lookup,
            default_draw_pct=default_draw_pct,
            rating_scale=rating_scale,
        )
        if brazil_group and brazil in rankings.get(brazil_group, []):
            position = rankings[brazil_group].index(brazil) + 1
            brazil_group_position_counts[str(position)] = brazil_group_position_counts.get(str(position), 0) + 1
        third_assignment, relaxed = _allocate_best_thirds(config, qualified_thirds)
        third_allocation_relaxed_count += relaxed
        used_third_groups: set[str] = set()
        match_winners: dict[int, str] = {}

        for phase_key in PHASE_SEQUENCE:
            phase = PHASE_LABELS[phase_key]
            for match in _bracket_matches(config, phase_key):
                slots = [str(slot) for slot in match.get("slots", [])]
                if len(slots) < 2:
                    continue
                team_a = _resolve_slot(
                    slots[0],
                    rankings=rankings,
                    qualified_thirds=qualified_thirds,
                    used_third_groups=used_third_groups,
                    match_winners=match_winners,
                    third_assignment=third_assignment,
                )
                team_b = _resolve_slot(
                    slots[1],
                    rankings=rankings,
                    qualified_thirds=qualified_thirds,
                    used_third_groups=used_third_groups,
                    match_winners=match_winners,
                    third_assignment=third_assignment,
                )
                if not team_a or not team_b:
                    unresolved_match_count += 1
                    continue

                match_id = int(match["match_id"])
                if brazil in {team_a, team_b}:
                    opponent = team_b if team_a == brazil else team_a
                    brazil_slot = (
                        slots[0].strip().upper().replace(" ", "")
                        if team_a == brazil
                        else slots[1].strip().upper().replace(" ", "")
                    )
                    bucket = phase_buckets[phase]
                    bucket["reach_count"] += 1
                    bucket["opponent_counts"][opponent] = bucket["opponent_counts"].get(opponent, 0) + 1
                    bucket["brazil_slot_counts"][brazil_slot] = bucket["brazil_slot_counts"].get(brazil_slot, 0) + 1

                winner = _sample_knockout_winner(
                    rng,
                    team_a,
                    team_b,
                    ratings=ratings,
                    rating_scale=rating_scale,
                )
                match_winners[match_id] = winner
                if brazil in {team_a, team_b} and winner == brazil:
                    opponent = team_b if team_a == brazil else team_a
                    wins = phase_buckets[phase]["opponent_win_counts"]
                    wins[opponent] = wins.get(opponent, 0) + 1
                if phase_key == "final" and winner == brazil:
                    title_count += 1

    return phase_buckets, title_count, {
        "third_allocation_relaxed_count": third_allocation_relaxed_count,
        "unresolved_match_count": unresolved_match_count,
        "brazil_group_position_counts": brazil_group_position_counts,
    }


RELAXED_THIRD_ALLOCATION_MAX_FRACTION = 0.005


class MonteCarloIntegrityError(RuntimeError):
    """Configuração de chave/grupos produziu simulação estruturalmente inválida.

    Jogos com slot não resolvido eram pulados em silêncio (continue) — a mesma
    classe de bug do alocador greedy de melhores-terceiros que deprimiu o funil
    em ~60%. Auditoria 11/jun reproduziu: um label quebrado em bracket.config.json
    levava o título a 0.0 com exit 0. Este gate transforma config quebrado em
    falha hard ANTES de publicar número errado."""


def _check_simulation_integrity(diagnostics: dict[str, Any], *, iterations: int) -> None:
    unresolved = int(diagnostics.get("unresolved_match_count", 0))
    relaxed = int(diagnostics.get("third_allocation_relaxed_count", 0))
    relaxed_cap = int(iterations * RELAXED_THIRD_ALLOCATION_MAX_FRACTION)
    problems: list[str] = []
    if unresolved > 0:
        problems.append(
            f"{unresolved} jogo(s) de mata-mata sem slot resolvido — revisar bracket.config.json/groups.config.json"
        )
    if relaxed > relaxed_cap:
        problems.append(
            f"alocação de melhores-terceiros relaxada {relaxed}x "
            f"(cap {relaxed_cap} = {RELAXED_THIRD_ALLOCATION_MAX_FRACTION:.1%} de {iterations} iterações)"
        )
    if problems:
        raise MonteCarloIntegrityError(
            "Monte Carlo abortado para não publicar funil errado: " + "; ".join(problems)
        )


def run_brazil_monte_carlo(config: dict[str, Any]) -> dict[str, Any]:
    mc_config = _mc_config(config)
    if not bool(mc_config.get("enabled", False)):
        return {"enabled": False}
    groups = _groups(config)
    if not groups or not config.get("bracket_config"):
        return {"enabled": False, "reason": "groups_config/bracket_config ausente"}

    configured_iterations = max(1, int(mc_config.get("iterations", DEFAULT_ITERATIONS)))
    seed = int(mc_config.get("seed", DEFAULT_SEED))
    rng = random.Random(seed)
    confidence_level = _confidence_level(config)
    z = _z_for_confidence_level(confidence_level)
    ratings, rating_coverage_pct, explicit_team_names = _build_rating_table_with_sources(config)
    team_context = _apply_team_context_adjustments(config, ratings)
    explicit_brazil_probs = _explicit_brazil_group_probabilities(config)
    completed_group_matches = _completed_group_matches(config)
    completed_group_lookup = _completed_group_match_lookup(completed_group_matches)
    default_draw_pct = float(mc_config.get("default_draw_pct", DEFAULT_DRAW_PCT))
    rating_scale = float(mc_config.get("rating_scale", DEFAULT_RATING_SCALE))
    brazil = str(config.get("brazil_team_name", "Brasil"))

    rating_uncertainty_enabled = bool(mc_config.get("rating_uncertainty_enabled", False))
    stage_uncertainty_intervals: dict[str, tuple[float, float]] = {}
    rating_uncertainty = {"enabled": False}
    simulation_diagnostics: dict[str, Any] = {
        "third_allocation_relaxed_count": 0,
        "unresolved_match_count": 0,
        "brazil_group_position_counts": {},
    }
    phase_buckets = {PHASE_LABELS[key]: _empty_phase_bucket() for key in PHASE_SEQUENCE}
    title_count = 0
    iterations = configured_iterations

    if rating_uncertainty_enabled:
        outer_samples = max(2, int(mc_config.get("rating_uncertainty_outer_samples", DEFAULT_RATING_UNCERTAINTY_OUTER_SAMPLES)))
        inner_iterations = max(
            1,
            int(
                mc_config.get(
                    "rating_uncertainty_inner_iterations",
                    max(1, configured_iterations // outer_samples),
                )
            ),
        )
        configured_sigma = float(mc_config.get("configured_rating_sigma", DEFAULT_CONFIGURED_RATING_SIGMA))
        prior_sigma = float(mc_config.get("prior_rating_sigma", DEFAULT_PRIOR_RATING_SIGMA))
        iterations = outer_samples * inner_iterations
        stage_samples: dict[str, list[float]] = {
            "16_avos": [],
            "oitavas": [],
            "quartas": [],
            "semifinal": [],
            "final": [],
            "titulo": [],
        }
        stage_inner_variances: dict[str, list[float]] = {key: [] for key in stage_samples}
        for _ in range(outer_samples):
            sampled_ratings = _sample_rating_table(
                ratings,
                explicit_team_names=explicit_team_names,
                rng=rng,
                configured_sigma=configured_sigma,
                prior_sigma=prior_sigma,
            )
            sample_buckets, sample_title_count, sample_diagnostics = _simulate_tournament_counts(
                config,
                ratings=sampled_ratings,
                iterations=inner_iterations,
                rng=rng,
                explicit_brazil_probs=explicit_brazil_probs,
                completed_group_lookup=completed_group_lookup,
                default_draw_pct=default_draw_pct,
                rating_scale=rating_scale,
                brazil=brazil,
            )
            _merge_simulation_diagnostics(simulation_diagnostics, sample_diagnostics)
            for phase in phase_buckets:
                _merge_phase_bucket(phase_buckets[phase], sample_buckets[phase])
            title_count += sample_title_count
            sample_stage_probabilities = _stage_probabilities_from_buckets(
                sample_buckets,
                title_count=sample_title_count,
                iterations=inner_iterations,
            )
            for key, value in sample_stage_probabilities.items():
                stage_samples[key].append(value)
                stage_inner_variances[key].append(max(0.0, float(value)) * max(0.0, 100.0 - float(value)) / inner_iterations)
        stage_probabilities = _stage_probabilities_from_buckets(
            phase_buckets,
            title_count=title_count,
            iterations=iterations,
        )
        stage_uncertainty_intervals = _stage_uncertainty_intervals(
            stage_samples,
            stage_probabilities,
            confidence_level=confidence_level,
            inner_variances=stage_inner_variances,
        )
        rating_uncertainty = {
            "enabled": True,
            "outer_samples": outer_samples,
            "inner_iterations": inner_iterations,
            "configured_rating_sigma": configured_sigma,
            "prior_rating_sigma": prior_sigma,
            "central_point_semantics": "posterior_mean_over_rating_scenarios",
            "variance_correction": "law_of_total_variance_subtracts_inner_binomial_noise",
        }
    else:
        phase_buckets, title_count, single_run_diagnostics = _simulate_tournament_counts(
            config,
            ratings=ratings,
            iterations=iterations,
            rng=rng,
            explicit_brazil_probs=explicit_brazil_probs,
            completed_group_lookup=completed_group_lookup,
            default_draw_pct=default_draw_pct,
            rating_scale=rating_scale,
            brazil=brazil,
        )
        _merge_simulation_diagnostics(simulation_diagnostics, single_run_diagnostics)
        stage_probabilities = _stage_probabilities_from_buckets(
            phase_buckets,
            title_count=title_count,
            iterations=iterations,
        )

    _check_simulation_integrity(simulation_diagnostics, iterations=iterations)

    phases: dict[str, Any] = {}
    for phase, bucket in phase_buckets.items():
        reach_count = int(bucket["reach_count"])
        opponents = []
        for opponent, count in sorted(
            bucket["opponent_counts"].items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            win_count = int(bucket["opponent_win_counts"].get(opponent, 0))
            opponents.append(
                {
                    "opponent": opponent,
                    "scenario_pct": _round_pct(count, reach_count),
                    "unconditional_pct": _round_pct(count, iterations),
                    "brazil_pct": _round_pct(win_count, count),
                    "count": count,
                    "ci": _wilson_interval_pct(count, reach_count, z=z),
                }
            )
        phases[phase] = {
            "reach_count": reach_count,
            "reach_pct": _round_pct(reach_count, iterations),
            "reach_ci": _wilson_interval_pct(reach_count, iterations, z=z),
            "opponents": opponents,
            "path_uncertainty_pct": _path_uncertainty_pct(bucket["opponent_counts"]),
            "brazil_slot_counts": dict(bucket["brazil_slot_counts"]),
        }

    stage_sample_intervals = _stage_sample_intervals_from_buckets(
        phase_buckets,
        title_count=title_count,
        iterations=iterations,
        z=z,
    )
    path_gate_min_iterations = int(mc_config.get("path_gate_min_iterations", DEFAULT_PATH_GATE_MIN_ITERATIONS))
    path_gate_min_rating_coverage_pct = float(
        mc_config.get("path_gate_min_rating_coverage_pct", DEFAULT_PATH_GATE_MIN_RATING_COVERAGE_PCT)
    )
    path_gate_reliable = (
        iterations >= path_gate_min_iterations
        and rating_coverage_pct >= path_gate_min_rating_coverage_pct
    )
    group_state = _group_state_summary(
        config,
        ratings=ratings,
        completed_matches=completed_group_matches,
        diagnostics=simulation_diagnostics,
        iterations=iterations,
    )
    phase_relevant_groups = _phase_relevant_groups(config)
    relevant_group_states = _relevant_group_states_summary(
        config,
        ratings=ratings,
        completed_matches=completed_group_matches,
    )
    return {
        "enabled": True,
        "iterations": iterations,
        "configured_iterations": configured_iterations,
        "seed": seed,
        "confidence_level": confidence_level,
        "z": round(z, 4),
        "rating_source": str(mc_config.get("rating_source") or "config team_ratings + weak draw-position prior"),
        "rating_coverage_pct": rating_coverage_pct,
        "explicit_rating_team_count": len(explicit_team_names),
        "team_context": team_context,
        "default_draw_pct": default_draw_pct,
        "rating_scale": rating_scale,
        "stage_probabilities": stage_probabilities,
        "stage_sample_intervals": stage_sample_intervals,
        "stage_uncertainty_intervals": stage_uncertainty_intervals,
        "rating_uncertainty": rating_uncertainty,
        "simulation_diagnostics": simulation_diagnostics,
        "completed_group_matches": {
            "count": len(completed_group_matches),
            "matches": _completed_results_for_all_groups(completed_group_matches),
        },
        "phase_relevant_groups": phase_relevant_groups,
        "relevant_group_states": relevant_group_states,
        "group_state": group_state,
        "title_count": title_count,
        "phases": phases,
        "path_gate": {
            "reliable": path_gate_reliable,
            "mode": "hard_gate" if path_gate_reliable else "weak_prior",
            "min_iterations": path_gate_min_iterations,
            "min_rating_coverage_pct": path_gate_min_rating_coverage_pct,
            "iterations": iterations,
            "rating_coverage_pct": rating_coverage_pct,
        },
    }


def monte_carlo_compact_summary(result: dict[str, Any], *, top_n: int = 3) -> dict[str, Any]:
    if not result.get("enabled"):
        return {"enabled": False, "reason": result.get("reason", "disabled")}
    phases: dict[str, Any] = {}
    for phase, payload in (result.get("phases") or {}).items():
        phases[phase] = {
            "reach_pct": payload.get("reach_pct"),
            "path_uncertainty_pct": payload.get("path_uncertainty_pct"),
            "top_opponents": list(payload.get("opponents", []))[:top_n],
        }
    return {
        "enabled": True,
        "iterations": result.get("iterations"),
        "configured_iterations": result.get("configured_iterations"),
        "seed": result.get("seed"),
        "confidence_level": result.get("confidence_level"),
        "z": result.get("z"),
        "rating_source": result.get("rating_source"),
        "rating_coverage_pct": result.get("rating_coverage_pct"),
        "explicit_rating_team_count": result.get("explicit_rating_team_count"),
        "team_context": result.get("team_context", {}),
        "stage_probabilities": result.get("stage_probabilities", {}),
        "stage_sample_intervals": result.get("stage_sample_intervals", {}),
        "stage_uncertainty_intervals": result.get("stage_uncertainty_intervals", {}),
        "rating_uncertainty": result.get("rating_uncertainty", {"enabled": False}),
        "simulation_diagnostics": result.get("simulation_diagnostics", {}),
        "completed_group_matches": result.get("completed_group_matches", {"count": 0, "matches": []}),
        "phase_relevant_groups": result.get("phase_relevant_groups", {}),
        "relevant_group_states": result.get("relevant_group_states", {}),
        "group_state": result.get("group_state", {}),
        "path_gate": result.get("path_gate", {}),
        "phases": phases,
    }


def monte_carlo_path_gate_is_reliable(
    result: dict[str, Any],
    *,
    min_iterations: int = DEFAULT_PATH_GATE_MIN_ITERATIONS,
    min_rating_coverage_pct: float = DEFAULT_PATH_GATE_MIN_RATING_COVERAGE_PCT,
) -> bool:
    if not result.get("enabled"):
        return False
    try:
        iterations = int(result.get("iterations") or 0)
        rating_coverage_pct = float(result.get("rating_coverage_pct") or 0.0)
    except (TypeError, ValueError):
        return False
    return iterations >= int(min_iterations) and rating_coverage_pct >= float(min_rating_coverage_pct)


def _shrink_interval(estimate: Any, *, narrow_pct: float) -> None:
    if narrow_pct <= 0.0:
        return
    low = float(estimate.brazil_ci_low)
    high = float(estimate.brazil_ci_high)
    width = high - low
    if width <= 0.0:
        return
    shrink = min(float(narrow_pct), max(0.0, width - 1.0))
    if shrink <= 0.0:
        return
    estimate.brazil_ci_low = round(low + shrink / 2.0, 1)
    estimate.brazil_ci_high = round(high - shrink / 2.0, 1)


def widen_ci_for_monte_carlo_path_uncertainty(
    estimate: Any,
    monte_carlo_result: dict[str, Any],
    *,
    max_widen_pct: float,
    min_iterations: int = DEFAULT_PATH_GATE_MIN_ITERATIONS,
    min_rating_coverage_pct: float = DEFAULT_PATH_GATE_MIN_RATING_COVERAGE_PCT,
    max_narrow_pct: float = 0.0,
    narrow_uncertainty_threshold_pct: float = DEFAULT_PATH_GATE_NARROW_UNCERTAINTY_THRESHOLD_PCT,
) -> None:
    if not monte_carlo_result.get("enabled"):
        return
    if estimate.brazil_ci_low is None or estimate.brazil_ci_high is None:
        return
    phase = str(getattr(estimate, "phase", ""))
    phase_payload = (monte_carlo_result.get("phases") or {}).get(phase) or {}
    uncertainty = float(phase_payload.get("path_uncertainty_pct") or 0.0)
    if uncertainty <= 0.0:
        return
    reliable = monte_carlo_path_gate_is_reliable(
        monte_carlo_result,
        min_iterations=min_iterations,
        min_rating_coverage_pct=min_rating_coverage_pct,
    )
    if reliable and max_narrow_pct > 0.0 and uncertainty <= float(narrow_uncertainty_threshold_pct):
        confidence_share = max(
            0.0,
            min(1.0, 1.0 - uncertainty / max(float(narrow_uncertainty_threshold_pct), 1.0)),
        )
        narrow = round(float(max_narrow_pct) * confidence_share, 1)
        _shrink_interval(estimate, narrow_pct=narrow)
        return
    widen = min(float(max_widen_pct), round(uncertainty / 100.0 * float(max_widen_pct), 1))
    estimate.brazil_ci_low = round(max(0.0, estimate.brazil_ci_low - widen / 2.0), 1)
    estimate.brazil_ci_high = round(min(100.0, estimate.brazil_ci_high + widen / 2.0), 1)

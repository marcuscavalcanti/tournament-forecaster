"""Offline rho calibration.

Sweeps the within-group correlation rho and reports which value brings the simulated
Brazil title closest to the de-vigged market anchor -- WITHOUT overfitting rho to absorb
base-rating error (the recommender flags a `structural_residual` when even max plausible
shrink cannot reach the market). No LLM calls, no per-run cost.

For each rho the team-context deltas are recomputed from the bundle's stored
`dominant_delta`/`residual_delta` per correlation group (mirroring
_apply_team_context_adjustments: dominant + (1-rho)*residual, cap-before-regress, evidence
weighting), applied to the base ratings, and the Monte Carlo is re-run.

Usage:
  python scripts/calibrate_rho.py outputs/linkedin_brazil_YYYY-MM-DD.json [--market 7.4] [--iterations 8000]
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldcup_brazil.monte_carlo import recommend_rho_against_market, run_brazil_monte_carlo
from worldcup_brazil.pipeline import _devig_outright_title_probabilities, load_config

RHO_SWEEP = [0.0, 0.5, 0.7, 0.85, 0.95]


def _engine_settings(mc_config: dict[str, Any]) -> dict[str, float]:
    """Read the same team-context knobs the engine uses, so the sweep cannot silently drift."""
    return {
        "warning_delta": float(mc_config.get("team_context_warning_delta", 40.0)),
        "max_team_delta": float(mc_config.get("max_team_context_rating_delta", 180.0)),
        "prior": float(mc_config.get("team_context_evidence_regression_prior", 2.0)),
        "material_delta": float(mc_config.get("team_context_evidence_material_delta", 1.0)),
    }


def _team_delta_at_rho(adjustment: dict[str, Any], rho: float, settings: dict[str, float]) -> float:
    """Recompute a team's rating_delta at a different rho, faithfully mirroring
    _apply_team_context_adjustments: dominant + (1-rho)*residual per group, cap BEFORE regression,
    evidence = min(material groups, distinct verified sources) where distinct sources come from the
    team's own signals (the bundle's evidence_regression block is absent on older runs, so it is
    deliberately NOT used). Constants come from the effective config, not hardcoded."""
    warning = settings["warning_delta"]
    max_delta = settings["max_team_delta"]
    prior = settings["prior"]
    material = settings["material_delta"]
    groups = adjustment.get("correlation_adjustments") or []
    deltas = [
        round(float(group.get("dominant_delta", 0.0)) + (1.0 - rho) * float(group.get("residual_delta", 0.0)), 1)
        for group in groups
    ]
    raw = sum(deltas)
    capped = max(-max_delta, min(max_delta, raw))
    material_groups = sum(1 for delta in deltas if abs(delta) >= material)
    sources = {
        str(signal.get("source") or signal.get("source_url") or "").strip()
        for signal in (adjustment.get("signals") or [])
    }
    distinct_sources = len(sources - {""})
    independent = min(material_groups, distinct_sources)
    if abs(capped) > warning:
        factor = independent / (independent + prior)
        sign = 1.0 if capped >= 0 else -1.0
        return sign * (warning + (abs(capped) - warning) * factor)
    return capped


def _resolve_market(bundle_meta: dict[str, Any], base_config: dict[str, Any], override: float | None) -> float | None:
    if override is not None:
        return override
    challenge = bundle_meta.get("market_title_challenge") or {}
    if challenge.get("market_source") == "devigged_odds" and challenge.get("market_mid_pct"):
        return float(challenge["market_mid_pct"])
    probs, _ = _devig_outright_title_probabilities(base_config.get("market_outright_odds"))
    if probs:
        return round(sum(probs) / len(probs), 1)
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: calibrate_rho.py <bundle.json> [--market PCT] [--iterations N]")
        return 2
    bundle_path = Path(argv[1])
    override = float(argv[argv.index("--market") + 1]) if "--market" in argv else None
    iterations = int(argv[argv.index("--iterations") + 1]) if "--iterations" in argv else 8000

    bundle = json.loads(bundle_path.read_text())["bundle"]
    meta = bundle["metadata"]
    config_path = Path("config/worldcup_brazil.json")
    if not config_path.exists():
        config_path = Path("config/worldcup_brazil.example.json")
    base_config = load_config(config_path)
    base_ratings = dict(base_config["monte_carlo"]["team_ratings"])
    settings = _engine_settings(base_config["monte_carlo"])
    adjustments = meta["monte_carlo"]["team_context"]["team_adjustments"]
    market = _resolve_market(meta, base_config, override)
    if market is None:
        print("no real (de-vigged) market anchor; pass --market PCT or use a post-devig bundle")
    current_rho = float(base_config["monte_carlo"].get("team_context_correlation_default_rho", 0.7))

    title_by_rho: dict[float, float] = {}
    for rho in RHO_SWEEP:
        config = copy.deepcopy(base_config)
        config["monte_carlo"]["enabled"] = True
        config["monte_carlo"]["iterations"] = iterations
        config["monte_carlo"]["team_context"] = {}  # deltas applied directly below
        ratings = dict(base_ratings)
        for adjustment in adjustments:
            team = str(adjustment.get("team") or "")
            if team in ratings:
                ratings[team] = ratings[team] + _team_delta_at_rho(adjustment, rho, settings)
        config["monte_carlo"]["team_ratings"] = ratings
        result = run_brazil_monte_carlo(config)
        title_by_rho[rho] = result["stage_probabilities"]["titulo"]

    recommendation = recommend_rho_against_market(title_by_rho, market, current_rho=current_rho)

    print("=== rho calibration ===")
    print(f"bundle: {bundle_path.name} | market (de-vig): {market}% | current rho: {current_rho}")
    for rho, title in title_by_rho.items():
        print(f"  rho {rho:<4} -> titulo {title}%")
    print("recommendation:")
    print(json.dumps(recommendation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

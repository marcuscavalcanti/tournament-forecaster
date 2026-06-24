"""Offline base-rating diagnostic.

Sweeps a team's base Elo and reports the rating that would reproduce the de-vigged market
title -- WITHOUT auto-fitting. It only flags the seed as `seed_plausibly_low` if the implied
rating stays within the peer top cluster; otherwise it reports a genuine `market_disagreement`.
The model is meant to be allowed to disagree with the market, so this never tells you to match
the market with an implausible rating. No LLM calls, no per-run cost.

SCOPE: this measures the raw MC title from the base ratings ALONE -- it does NOT apply the live
debate's team_context nor the 60/40 model blend that the published run adds on top. So it tells
you whether the seed ratings are market-consistent IN ISOLATION; a published title below market
on a market-consistent seed means the gap is the team_context/blend, not the seed.

Usage:
  python scripts/calibrate_base_rating.py --market 7.4 [--team Brasil] [--iterations 8000]
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldcup_brazil.monte_carlo import recommend_base_rating_against_market, run_brazil_monte_carlo
from worldcup_brazil.pipeline import load_config

RATING_SWEEP_DELTAS = [-100, -50, 0, 50, 100, 150, 200]


def main(argv: list[str]) -> int:
    team = argv[argv.index("--team") + 1] if "--team" in argv else "Brasil"
    market = float(argv[argv.index("--market") + 1]) if "--market" in argv else None
    iterations = int(argv[argv.index("--iterations") + 1]) if "--iterations" in argv else 8000
    if market is None:
        print("pass --market PCT (a de-vigged market title, e.g. from make calibrate or your odds)")
        return 2

    config_path = Path("config/worldcup_brazil.json")
    if not config_path.exists():
        config_path = Path("config/worldcup_brazil.example.json")
    base_config = load_config(config_path)
    ratings = dict(base_config["monte_carlo"]["team_ratings"])
    if team not in ratings:
        print(f"team {team!r} not in team_ratings")
        return 2
    current = float(ratings[team])
    peer_max = max(value for name, value in ratings.items() if name != team)

    title_by_rating: dict[float, float] = {}
    for delta in RATING_SWEEP_DELTAS:
        config = copy.deepcopy(base_config)
        config["monte_carlo"]["enabled"] = True
        config["monte_carlo"]["iterations"] = iterations
        config["monte_carlo"]["team_context"] = {}
        swept = dict(ratings)
        swept[team] = current + delta
        config["monte_carlo"]["team_ratings"] = swept
        result = run_brazil_monte_carlo(config)
        title_by_rating[current + delta] = result["stage_probabilities"]["titulo"]

    recommendation = recommend_base_rating_against_market(
        title_by_rating, market, current_rating=current, peer_max_rating=peer_max
    )

    print("=== base-rating diagnostic (raw MC, NO team_context, NO 60/40 blend) ===")
    print(f"team: {team} | seed: {current:.0f} | peer top: {peer_max:.0f} | market: {market}%")
    for rating, title in sorted(title_by_rating.items()):
        print(f"  rating {rating:.0f} -> titulo {title}% (no-context)")
    print("recommendation:")
    print(json.dumps(recommendation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

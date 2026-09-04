#!/usr/bin/env python3
"""Build a local UEFA Champions League 2026/27 example from UEFA fixtures."""

from __future__ import annotations
import argparse, json, re, unicodedata
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://match.uefa.com/v5/matches?competitionId=1&seasonYear=2027&offset=0&limit=500"
RATINGS = {
    "AEK Athens": 1530,
    "Arsenal": 1840,
    "Aston Villa": 1740,
    "Atleti": 1790,
    "B. Dortmund": 1770,
    "Barcelona": 1870,
    "Bayern München": 1880,
    "Bodø/Glimt": 1580,
    "Club Brugge": 1640,
    "Como": 1660,
    "Fenerbahçe": 1630,
    "Feyenoord": 1660,
    "Galatasaray": 1650,
    "Inter": 1840,
    "LASK": 1500,
    "Leipzig": 1720,
    "Lens": 1640,
    "Lille": 1660,
    "Liverpool": 1860,
    "Man City": 1880,
    "Man Utd": 1740,
    "Napoli": 1780,
    "PSV": 1690,
    "Paris": 1890,
    "Porto": 1710,
    "Real Betis": 1690,
    "Real Madrid": 1900,
    "Roma": 1730,
    "S. Bratislava": 1430,
    "Sabah": 1400,
    "Shakhtar": 1610,
    "Slavia Praha": 1560,
    "Sporting CP": 1730,
    "Stuttgart": 1700,
    "Viking": 1470,
    "Villarreal": 1710,
}


def slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def source(kind: str, value: int | str) -> dict[str, object]:
    if kind == "league_rank":
        return {"type": kind, "stage_id": "league-phase", "rank": value}
    return {"type": "match_winner", "match_id": value}


def ties(
    prefix: str, groups: list[tuple[str, list[object], list[object]]]
) -> list[dict[str, object]]:
    out = []
    n = 1
    for group, seeded, unseeded in groups:
        for a, b in zip(seeded, unseeded):
            out.append({"id": f"{prefix}-{n}", "draw_group": group, "entrants": [a, b]})
            n += 1
    return out


def knockout(stage_id, tie_values, *, mode="fixed", legs=2, terminal=None):
    value = {
        "id": stage_id,
        "type": "knockout",
        "pairing": {"mode": mode, "ties": tie_values},
        "legs": legs,
        "home_away_order": "seeded_team_second_leg_home"
        if mode == "seeded_draw"
        else "listed_team_first_leg_home",
        "aggregate_tiebreak": "extra_time_then_penalties",
        "away_goals_rule": False,
    }
    if terminal:
        value["terminal"] = terminal
    return value


def load(path: str | None):
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    with urlopen(
        Request(URL, headers={"User-Agent": "tournament-forecaster/0.1"}), timeout=30
    ) as response:
        return json.load(response)


def build(payload):
    matches = [
        m for m in payload if m.get("round", {}).get("metaData", {}).get("name") == "League Phase"
    ]
    if len(matches) != 144:
        raise ValueError(f"expected 144 league-phase fixtures, got {len(matches)}")
    clubs = {
        t["internationalName"]: slug(t["internationalName"])
        for m in matches
        for t in (m["homeTeam"], m["awayTeam"])
    }
    if len(clubs) != 36:
        raise ValueError(f"expected 36 clubs, got {len(clubs)}")
    counts = {name: [0, 0] for name in clubs}
    fixtures = []
    for m in sorted(matches, key=lambda x: (x["kickOffTime"]["dateTime"], int(x["id"]))):
        home = m["homeTeam"]["internationalName"]
        away = m["awayTeam"]["internationalName"]
        counts[home][0] += 1
        counts[away][1] += 1
        fixtures.append(
            {
                "match_id": f"uefa-{m['id']}",
                "home_team_id": clubs[home],
                "away_team_id": clubs[away],
                "metadata": {"date": m["kickOffTime"]["date"]},
            }
        )
    bad = {k: v for k, v in counts.items() if v != [4, 4]}
    if bad:
        raise ValueError(f"clubs must have four home and four away fixtures: {bad}")
    po_groups = []
    for i, (s1, s2, u1, u2) in enumerate(
        ((9, 10, 23, 24), (11, 12, 21, 22), (13, 14, 19, 20), (15, 16, 17, 18)), 1
    ):
        po_groups.append(
            (
                f"playoff-{i}",
                [source("league_rank", s1), source("league_rank", s2)],
                [source("league_rank", u1), source("league_rank", u2)],
            )
        )
    po = ties("playoff", po_groups)
    r16_groups = []
    for i, (a, b, p1, p2) in enumerate(((1, 2, 7, 8), (3, 4, 5, 6), (5, 6, 3, 4), (7, 8, 1, 2)), 1):
        r16_groups.append(
            (
                f"r16-{i}",
                [source("league_rank", a), source("league_rank", b)],
                [source("match_winner", f"playoff-{p1}"), source("match_winner", f"playoff-{p2}")],
            )
        )
    r16 = ties("round-of-16", r16_groups)

    def winners(prefix, prior, count):
        return [
            {
                "id": f"{prefix}-{i}",
                "entrants": [
                    source("match_winner", f"{prior}-{2 * i - 1}"),
                    source("match_winner", f"{prior}-{2 * i}"),
                ],
            }
            for i in range(1, count + 1)
        ]

    stages = [
        {
            "id": "league-phase",
            "type": "league_table",
            "fixtures": fixtures,
            "points": {"win": 3, "draw": 1, "loss": 0},
            "tiebreakers": ["goal_difference", "goals_for"],
            "qualification_bands": [
                {"ranks": [1, 8], "destination": "round-of-16"},
                {"ranks": [9, 24], "destination": "knockout-playoffs"},
            ],
        },
        knockout("knockout-playoffs", po, mode="seeded_draw"),
        knockout("round-of-16", r16, mode="seeded_draw"),
        knockout("quarter-finals", winners("quarter-final", "round-of-16", 4)),
        knockout("semi-finals", winners("semi-final", "quarter-final", 2)),
        knockout("final", winners("final", "semi-final", 1), legs=1, terminal="championship"),
    ]
    return {
        "schema_version": 2,
        "tournament": {
            "id": "uefa-champions-league-2026-27",
            "display_name": "UEFA Champions League 2026/27",
            "season": "2026/27",
        },
        "focus_team_id": "real-madrid",
        "teams": [{"id": clubs[n], "display_name": n} for n in sorted(clubs)],
        "stages": stages,
        "ratings": {clubs[n]: RATINGS[n] for n in clubs},
        "completed_matches": [],
        "metadata": {
            "data_status": "official UEFA league-phase fixtures fetched at build time; project-authored frozen ratings",
            "source_url": URL,
        },
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input")
    p.add_argument("--output", required=True)
    a = p.parse_args()
    output = Path(a.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build(load(a.input)), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output)


if __name__ == "__main__":
    main()

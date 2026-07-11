#!/usr/bin/env python3
"""Build the normalized live World Cup 2026 example from the FIFA calendar API."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tournament_forecaster.atomic_io import atomic_write_json, atomic_write_text
from tournament_forecaster.backtest import evaluate_backtest, ratings_sha256
from tournament_forecaster.errors import TournamentValidationError
from tournament_forecaster.group_fixtures import generate_group_fixture_specs


FIFA_ENDPOINT = "https://api.fifa.com/api/v3/calendar/matches"
FIFA_COMPETITION_ID = "17"
FIFA_SEASON_ID = "285023"
MODEL_VERSION = "poisson-elo-v1"
RATING_COMMIT = "a7b6e694"
RATING_CAPTURED_AT = "2026-06-09T23:27:23-03:00"
_FINAL_RESULT_TYPES = frozenset({1, 2, 3})
_STAGE_IDS = {
    "first stage": "group-stage",
    "group stage": "group-stage",
    "round of 32": "round-of-32",
    "round of 16": "round-of-16",
    "quarter-final": "quarter-finals",
    "quarter-finals": "quarter-finals",
    "quarter finals": "quarter-finals",
    "semi-final": "semi-finals",
    "semi-finals": "semi-finals",
    "semifinals": "semi-finals",
    "final": "final",
    "play-off for third place": "third-place",
}
_RATINGS_BY_CODE = {
    "MEX": 1690.0, "RSA": 1470.0, "KOR": 1630.0, "CZE": 1580.0,
    "CAN": 1585.0, "BIH": 1560.0, "QAT": 1500.0, "SUI": 1710.0,
    "BRA": 1850.0, "MAR": 1660.0, "HAI": 1320.0, "SCO": 1540.0,
    "USA": 1645.0, "PAR": 1600.0, "AUS": 1580.0, "TUR": 1640.0,
    "GER": 1860.0, "CUW": 1360.0, "CIV": 1605.0, "ECU": 1650.0,
    "NED": 1860.0, "JPN": 1690.0, "SWE": 1650.0, "TUN": 1480.0,
    "BEL": 1790.0, "EGY": 1615.0, "IRN": 1625.0, "NZL": 1430.0,
    "ESP": 1900.0, "CPV": 1505.0, "KSA": 1510.0, "URU": 1780.0,
    "FRA": 1920.0, "SEN": 1655.0, "IRQ": 1490.0, "NOR": 1660.0,
    "ARG": 1910.0, "ALG": 1595.0, "AUT": 1700.0, "JOR": 1440.0,
    "POR": 1870.0, "COD": 1500.0, "UZB": 1515.0, "COL": 1740.0,
    "ENG": 1880.0, "CRO": 1745.0, "GHA": 1560.0, "PAN": 1480.0,
}


@dataclass(frozen=True, slots=True)
class NormalizedFixture:
    completed: tuple[Mapping[str, object], ...]
    pending: tuple[Mapping[str, object], ...]


def _localized(value: object) -> str:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            if isinstance(item, Mapping):
                text = item.get("Description") or item.get("description")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    return value.strip() if isinstance(value, str) else ""


def _stage_id(row: Mapping[str, object]) -> str:
    label = _localized(row.get("StageName") or row.get("stageName") or row.get("stage"))
    normalized = label.casefold().strip()
    stage_id = _STAGE_IDS.get(normalized)
    if stage_id is None:
        raise TournamentValidationError(f"unsupported FIFA stage: {label or '<missing>'}")
    return stage_id


def _side(row: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = row.get(name)
    return value if isinstance(value, Mapping) else {}


def _code(row: Mapping[str, object], side: str) -> str:
    value = _side(row, side).get("Abbreviation")
    if not value:
        value = row.get("PlaceHolderA" if side == "Home" else "PlaceHolderB")
    return str(value or "").strip().upper()


def _team_id(row: Mapping[str, object], side: str) -> str:
    value = _side(row, side).get("IdTeam")
    return str(value or "").strip()


def _score(row: Mapping[str, object], side: str) -> int | None:
    value = row.get(f"{side}TeamScore")
    if value is None:
        value = _side(row, side).get("Score")
    if value is None:
        return None
    if isinstance(value, bool):
        raise TournamentValidationError("FIFA score must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise TournamentValidationError("FIFA score must be a non-negative integer") from error
    if parsed < 0:
        raise TournamentValidationError("FIFA score must be a non-negative integer")
    return parsed


def _result_type(row: Mapping[str, object]) -> int:
    value = row.get("ResultType") if "ResultType" in row else row.get("resultType")
    if isinstance(value, bool):
        raise TournamentValidationError("unsupported FIFA result type")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise TournamentValidationError("unsupported FIFA result type") from error


def _match_number(row: Mapping[str, object]) -> int:
    value = row.get("MatchNumber")
    if isinstance(value, bool):
        raise TournamentValidationError("FIFA match number must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise TournamentValidationError("FIFA match number must be a positive integer") from error
    if number < 1:
        raise TournamentValidationError("FIFA match number must be a positive integer")
    return number


def normalize_fifa_fixture(
    payload: object,
    *,
    known_codes: set[str] | frozenset[str],
) -> NormalizedFixture:
    """Normalize saved or fetched FIFA rows and never promote a non-final score."""

    if isinstance(payload, Mapping):
        rows = payload.get("Results") or payload.get("results")
    else:
        rows = payload
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise TournamentValidationError("FIFA fixture must contain a Results array")
    normalized: dict[str, Mapping[str, object]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise TournamentValidationError("FIFA match row must be an object")
        source_id = str(raw.get("IdMatch") or raw.get("idMatch") or "").strip()
        if not source_id:
            raise TournamentValidationError("FIFA match row requires IdMatch")
        stage_id = _stage_id(raw)
        result_type = _result_type(raw)
        home_code = _code(raw, "Home")
        away_code = _code(raw, "Away")
        for code in (home_code, away_code):
            if code not in known_codes and not re.fullmatch(r"(?:W|RU)\d+", code):
                raise TournamentValidationError(f"unknown FIFA team code: {code or '<missing>'}")
        home_score = _score(raw, "Home")
        away_score = _score(raw, "Away")
        winner_id = str(raw.get("Winner") or raw.get("winner") or "").strip()
        winner_code: str | None = None
        if winner_id:
            if winner_id == _team_id(raw, "Home"):
                winner_code = home_code
            elif winner_id == _team_id(raw, "Away"):
                winner_code = away_code
            else:
                raise TournamentValidationError(f"FIFA winner is not an entrant for match {source_id}")
        is_final = result_type in _FINAL_RESULT_TYPES
        if is_final:
            if home_score is None or away_score is None:
                raise TournamentValidationError(f"final FIFA match {source_id} has no score")
            inferred = home_code if home_score > away_score else away_code if away_score > home_score else None
            if winner_code is None:
                winner_code = inferred
            if winner_code is None and stage_id != "group-stage":
                raise TournamentValidationError(f"final drawn FIFA match {source_id} has no winner")
            if inferred is not None and winner_code != inferred:
                raise TournamentValidationError(f"FIFA winner conflicts with score for match {source_id}")
        elif result_type == 0:
            winner_code = None
        else:
            raise TournamentValidationError(f"unsupported FIFA result type: {result_type}")
        row = {
            "source_id": source_id,
            "stage_id": stage_id,
            "match_number": _match_number(raw),
            "kickoff_at": str(raw.get("Date") or raw.get("date") or "").strip(),
            "home_code": home_code,
            "away_code": away_code,
            "home_score": home_score,
            "away_score": away_score,
            "winner_code": winner_code,
            "result_type": result_type,
            "is_final": is_final,
        }
        existing = normalized.get(source_id)
        if existing is not None and existing != row:
            raise TournamentValidationError(f"conflicting FIFA match rows for {source_id}")
        normalized[source_id] = row
    ordered = tuple(normalized[key] for key in sorted(normalized, key=lambda value: int(value)))
    return NormalizedFixture(
        completed=tuple(row for row in ordered if row["is_final"]),
        pending=tuple(row for row in ordered if not row["is_final"]),
    )


def _slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")


def _team_name(row: Mapping[str, object], side: str) -> str:
    name = _localized(_side(row, side).get("TeamName"))
    if not name:
        raise TournamentValidationError("FIFA group row is missing a team name")
    return name


def _group_label(row: Mapping[str, object]) -> str:
    value = _localized(row.get("GroupName") or row.get("groupName"))
    match = re.fullmatch(r"Group ([A-L])", value)
    if not match:
        raise TournamentValidationError(f"unsupported FIFA group label: {value or '<missing>'}")
    return match.group(1)


def _raw_rows(payload: object) -> tuple[Mapping[str, object], ...]:
    rows = payload.get("Results") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise TournamentValidationError("FIFA fixture must contain a Results array")
    if not all(isinstance(row, Mapping) for row in rows):
        raise TournamentValidationError("FIFA match row must be an object")
    return tuple(rows)  # type: ignore[return-value]


def _extract_teams_and_groups(
    payload: object,
) -> tuple[dict[str, dict[str, str]], dict[str, list[str]]]:
    teams_by_code: dict[str, dict[str, str]] = {}
    groups_by_code: dict[str, set[str]] = {}
    for row in _raw_rows(payload):
        if _stage_id(row) != "group-stage":
            continue
        group = _group_label(row)
        for side in ("Home", "Away"):
            code = _code(row, side)
            name = _team_name(row, side)
            fifa_id = _team_id(row, side)
            candidate = {"id": _slug(name), "display_name": name, "fifa_team_id": fifa_id}
            if code in teams_by_code and teams_by_code[code] != candidate:
                raise TournamentValidationError(f"conflicting FIFA team identity for {code}")
            teams_by_code[code] = candidate
            groups_by_code.setdefault(group, set()).add(code)
    if set(teams_by_code) != set(_RATINGS_BY_CODE):
        raise TournamentValidationError("FIFA group topology does not contain the expected 48 teams")
    if set(groups_by_code) != set("ABCDEFGHIJKL") or any(
        len(codes) != 4 for codes in groups_by_code.values()
    ):
        raise TournamentValidationError("FIFA group topology must contain 12 groups of four")
    ids = [team["id"] for team in teams_by_code.values()]
    if len(ids) != len(set(ids)):
        raise TournamentValidationError("normalized FIFA team IDs must be unique")
    return teams_by_code, {
        group: sorted(codes)
        for group, codes in sorted(groups_by_code.items())
    }


def _source_metadata(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "source": "FIFA calendar API",
        "source_id": row["source_id"],
        "source_url": f"{FIFA_ENDPOINT}#{row['source_id']}",
        "kickoff_at": row["kickoff_at"],
        "result_type": row["result_type"],
    }


def _knockout_stage(
    stage_id: str,
    rows: Sequence[Mapping[str, object]],
    entrant_sources: Mapping[str, list[dict[str, object]]],
    *,
    terminal: str | None = None,
) -> dict[str, object]:
    stage: dict[str, object] = {
        "id": stage_id,
        "type": "knockout",
        "pairing": {
            "mode": "fixed",
            "ties": [
                {"id": row["source_id"], "entrants": entrant_sources[str(row["source_id"])]}
                for row in sorted(rows, key=lambda item: int(item["match_number"]))
            ],
        },
        "legs": 1,
        "home_away_order": "listed_team_first_leg_home",
        "aggregate_tiebreak": "extra_time_then_penalties",
        "away_goals_rule": False,
        "metadata": {"home_advantage_rating_points": 0},
    }
    if terminal is not None:
        stage["terminal"] = terminal
    return stage


def build_documents(
    payload: object,
    *,
    retrieved_at: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """Build tournament, backtest input, and backtest report documents."""

    try:
        parsed_retrieved_at = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise TournamentValidationError("retrieved_at must be an ISO-8601 timestamp") from error
    if parsed_retrieved_at.tzinfo is None:
        raise TournamentValidationError("retrieved_at must include a timezone")
    teams_by_code, group_codes = _extract_teams_and_groups(payload)
    normalized = normalize_fifa_fixture(payload, known_codes=set(teams_by_code))
    all_rows = (*normalized.completed, *normalized.pending)
    rows_by_stage = {
        stage_id: sorted(
            (row for row in all_rows if row["stage_id"] == stage_id),
            key=lambda item: int(item["match_number"]),
        )
        for stage_id in _STAGE_IDS.values()
    }
    expected_total = {"group-stage": 72, "round-of-32": 16, "round-of-16": 8,
                      "quarter-finals": 4, "semi-finals": 2, "final": 1}
    for stage_id, count in expected_total.items():
        if len(rows_by_stage[stage_id]) != count:
            raise TournamentValidationError(
                f"FIFA fixture must contain {count} rows for {stage_id}"
            )

    code_to_id = {code: team["id"] for code, team in teams_by_code.items()}
    groups = {
        group: [code_to_id[code] for code in codes]
        for group, codes in group_codes.items()
    }
    group_stage = {
        "id": "group-stage",
        "type": "round_robin_groups",
        "groups": groups,
        "rounds_per_pair": 1,
        "points": {"win": 3, "draw": 1, "loss": 0},
        "tiebreakers": ["points", "goal_difference", "goals_for", "wins", "rating", "team_id"],
        "metadata": {"home_advantage_rating_points": 0},
    }
    fixtures_by_pair = {
        frozenset((fixture.home_team_id, fixture.away_team_id)): fixture
        for fixture in generate_group_fixture_specs(group_stage)
    }

    entrants_by_stage: dict[str, dict[str, list[dict[str, object]]]] = {}
    r32_entrants: dict[str, list[dict[str, object]]] = {}
    for row in rows_by_stage["round-of-32"]:
        r32_entrants[str(row["source_id"])] = [
            {"type": "team", "team_id": code_to_id[str(row["home_code"])]},
            {"type": "team", "team_id": code_to_id[str(row["away_code"])]},
        ]
    entrants_by_stage["round-of-32"] = r32_entrants

    winners_by_stage: dict[str, dict[str, str]] = {}
    for stage_id in ("round-of-32", "round-of-16", "quarter-finals", "semi-finals"):
        winners_by_stage[stage_id] = {
            str(row["winner_code"]): str(row["source_id"])
            for row in rows_by_stage[stage_id]
            if row["winner_code"] is not None
        }
    for stage_id, source_stage_id in (
        ("round-of-16", "round-of-32"),
        ("quarter-finals", "round-of-16"),
    ):
        sources: dict[str, list[dict[str, object]]] = {}
        for row in rows_by_stage[stage_id]:
            try:
                sources[str(row["source_id"])] = [
                    {"type": "match_winner", "match_id": winners_by_stage[source_stage_id][str(row[side])]}
                    for side in ("home_code", "away_code")
                ]
            except KeyError as error:
                raise TournamentValidationError(
                    f"FIFA bracket entrant does not resolve for {row['source_id']}"
                ) from error
        entrants_by_stage[stage_id] = sources

    match_id_by_number = {
        int(row["match_number"]): str(row["source_id"])
        for row in all_rows
    }

    def prior_winner_source(token: object, prior_stage: str) -> dict[str, object]:
        code = str(token)
        if code in winners_by_stage[prior_stage]:
            return {"type": "match_winner", "match_id": winners_by_stage[prior_stage][code]}
        match = re.fullmatch(r"W(\d+)", code)
        if match and int(match.group(1)) in match_id_by_number:
            return {"type": "match_winner", "match_id": match_id_by_number[int(match.group(1))]}
        raise TournamentValidationError(f"FIFA bracket placeholder does not resolve: {code}")

    semi_sources = {
        str(row["source_id"]): [
            prior_winner_source(row["home_code"], "quarter-finals"),
            prior_winner_source(row["away_code"], "quarter-finals"),
        ]
        for row in rows_by_stage["semi-finals"]
    }
    final_sources = {
        str(row["source_id"]): [
            prior_winner_source(row["home_code"], "semi-finals"),
            prior_winner_source(row["away_code"], "semi-finals"),
        ]
        for row in rows_by_stage["final"]
    }

    stages = [
        group_stage,
        _knockout_stage("round-of-32", rows_by_stage["round-of-32"], r32_entrants),
        _knockout_stage("round-of-16", rows_by_stage["round-of-16"], entrants_by_stage["round-of-16"]),
        _knockout_stage("quarter-finals", rows_by_stage["quarter-finals"], entrants_by_stage["quarter-finals"]),
        _knockout_stage("semi-finals", rows_by_stage["semi-finals"], semi_sources),
        _knockout_stage("final", rows_by_stage["final"], final_sources, terminal="championship"),
    ]
    completed_matches: list[dict[str, object]] = []
    for row in normalized.completed:
        if row["stage_id"] == "third-place":
            continue
        home_id = code_to_id[str(row["home_code"])]
        away_id = code_to_id[str(row["away_code"])]
        score_home = row["home_score"]
        score_away = row["away_score"]
        if row["stage_id"] == "group-stage":
            fixture = fixtures_by_pair[frozenset((home_id, away_id))]
            match_id = fixture.match_id
            if (home_id, away_id) != (fixture.home_team_id, fixture.away_team_id):
                home_id, away_id = away_id, home_id
                score_home, score_away = score_away, score_home
        else:
            match_id = str(row["source_id"])
        match: dict[str, object] = {
            "match_id": match_id,
            "stage_id": row["stage_id"],
            "home_team_id": home_id,
            "away_team_id": away_id,
            "score": {"home": score_home, "away": score_away},
            "metadata": {
                **_source_metadata(row),
                "fifa_home_team_id": code_to_id[str(row["home_code"])],
                "fifa_away_team_id": code_to_id[str(row["away_code"])],
            },
        }
        if row["stage_id"] != "group-stage":
            match["winner_team_id"] = code_to_id[str(row["winner_code"])]
        completed_matches.append(match)
    stage_counts = {
        stage_id: sum(match["stage_id"] == stage_id for match in completed_matches)
        for stage_id in ("group-stage", "round-of-32", "round-of-16", "quarter-finals")
    }
    if stage_counts != {
        "group-stage": 72,
        "round-of-32": 16,
        "round-of-16": 8,
        "quarter-finals": 2,
    }:
        raise TournamentValidationError(
            f"retrieval boundary does not match the 2026-07-11 snapshot: {stage_counts}"
        )

    ratings = {code_to_id[code]: rating for code, rating in _RATINGS_BY_CODE.items()}
    rating_hash = ratings_sha256(ratings)
    tournament = {
        "schema_version": 2,
        "tournament": {
            "id": "fifa-world-cup-2026-live",
            "display_name": "FIFA World Cup 2026",
            "season": "2026",
        },
        "focus_team_id": "france",
        "teams": [
            {
                "id": team["id"],
                "display_name": team["display_name"],
                "metadata": {
                    "fifa_code": code,
                    "fifa_team_id": team["fifa_team_id"],
                },
            }
            for code, team in sorted(teams_by_code.items(), key=lambda item: item[1]["id"])
        ],
        "stages": stages,
        "ratings": ratings,
        "completed_matches": sorted(
            completed_matches,
            key=lambda match: (str(match["stage_id"]), str(match["match_id"])),
        ),
        "metadata": {
            "snapshot": {
                "retrieved_at": retrieved_at,
                "source": "official FIFA calendar API",
                "endpoint": FIFA_ENDPOINT,
                "parameters": {
                    "idCompetition": FIFA_COMPETITION_ID,
                    "idSeason": FIFA_SEASON_ID,
                },
                "completed_fact_count": len(completed_matches),
            },
            "ratings": {
                "source": "project-authored pre-tournament seed",
                "git_commit": RATING_COMMIT,
                "frozen_at": RATING_CAPTURED_AT,
                "sha256": rating_hash,
                "limitation": "Not an official rating source and not proof of universal calibration.",
            },
        },
    }
    group_rows = rows_by_stage["group-stage"]
    backtest = {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "home_advantage_rating_points": 0,
        "ratings": ratings,
        "ratings_sha256": rating_hash,
        "cases": [
            {
                "source_id": str(row["source_id"]),
                "captured_at": RATING_CAPTURED_AT,
                "kickoff_at": row["kickoff_at"],
                "home_team_id": code_to_id[str(row["home_code"])] ,
                "away_team_id": code_to_id[str(row["away_code"])] ,
                "result": {"home": row["home_score"], "away": row["away_score"]},
                "metadata": {"source": "official FIFA calendar API"},
            }
            for row in group_rows
        ],
        "metadata": {
            "purpose": "Out-of-sample 1X2 evaluation of the frozen pre-tournament rating seed.",
            "rating_provenance": {
                "git_commit": RATING_COMMIT,
                "captured_at": RATING_CAPTURED_AT,
                "limitation": "Project-authored, not an official FIFA rating source.",
            },
        },
    }
    report = evaluate_backtest(backtest, min_resolved=72).to_dict()
    return tournament, backtest, report


def _report_markdown(report: Mapping[str, object]) -> str:
    metrics = report["metrics"]
    baseline = report["uniform_baseline"]
    assert isinstance(metrics, Mapping) and isinstance(baseline, Mapping)
    return f"""# World Cup 2026 Group-Stage Backtest

- Status: `{report['status']}`
- Resolved cases: `{report['sample_size']}`
- Model: `{report['model_version']}`
- Ratings SHA-256: `{report['ratings_sha256']}`
- Home advantage: `0` rating points (neutral site)

| Metric | Model | Uniform baseline |
|---|---:|---:|
| RPS | {float(metrics['rps']):.6f} | {float(baseline['rps']):.6f} |
| Multiclass Brier | {float(metrics['brier']):.6f} | {float(baseline['brier']):.6f} |
| Natural log loss | {float(metrics['log_loss']):.6f} | {float(baseline['log_loss']):.6f} |
| Top-pick accuracy | {float(metrics['top_pick_accuracy']):.6f} | {float(baseline['top_pick_accuracy']):.6f} |

RPS is the mean squared cumulative error over the ordered outcomes home/draw/away,
divided by `K-1 = 2`. Brier is the unscaled sum of three squared class errors.
Log loss uses the natural logarithm. Model top-pick accuracy counts a case when the
observed class has the unique highest model probability. The uniform baseline uses
`1/3` for every class and an expected top-pick accuracy of `1/3`.
"""


def _readme(retrieved_at: str) -> str:
    return f"""# FIFA World Cup 2026 Live Snapshot

This reproducible example starts from the official state retrieved at `{retrieved_at}`.
It retains all 48 teams, all 72 completed group matches, 16 completed Round of 32
matches, eight completed Round of 16 matches, and the two completed quarter-finals.
France is the default focus team and is already locked into the semi-finals.

Run offline after installing the package:

```bash
tournament-forecast simulate --config tournament.json --iterations 10000
tournament-forecast simulate --config tournament.json --focus-team spain --iterations 10000
tournament-forecast backtest --input backtest.json --output backtest-report.json --min-resolved 72
```

The third-place match is omitted because the generic bracket contract has no loser
entrant. Runtime forecast output directories are intentionally not checked in.
"""


def _data_sources(retrieved_at: str, rating_hash: str) -> str:
    return f"""# Data Sources

## Results and bracket

- Source: official FIFA calendar API `{FIFA_ENDPOINT}`
- Parameters: `idCompetition=17`, `idSeason=285023`, `language=en`, `count=500`
- Retrieved at: `{retrieved_at}`
- Checked-in data: normalized match facts, source IDs, schedule IDs, and team IDs only
- Raw API response: never checked in
- Final result types accepted: `1`, `2`, and `3`; type `3` is completed extra time
- Singular FIFA stage label `Quarter-final` maps to `quarter-finals`

The snapshot has 98 completed facts. Norway–England and Argentina–Switzerland were
not final at retrieval and are bracket fixtures, not completed results.

## Ratings

- Source: project-authored `team_ratings` seed frozen in git commit `{RATING_COMMIT}`
- Exact git commit timestamp: `{RATING_CAPTURED_AT}`
- Canonical ratings object SHA-256: `{rating_hash}`

The ratings are leakage-free for the 72 group outcomes because they were frozen
before those matches. They are not an official FIFA rating source and do not prove
universal model calibration.

## Update procedure

```bash
python scripts/build_world_cup_2026_example.py --fetch \
  --output-dir examples/world-cup-2026-live
```

For deterministic fixture tests, use `--fixture PATH --retrieved-at TIMESTAMP`.
The builder rejects unknown teams, conflicting duplicate matches, invalid winners,
unsupported stages/result types, and any non-final row as a completed fact.
"""


def write_example(
    output_dir: Path,
    tournament: Mapping[str, object],
    backtest: Mapping[str, object],
    report: Mapping[str, object],
    *,
    retrieved_at: str,
) -> None:
    atomic_write_json(output_dir / "tournament.json", tournament)
    atomic_write_json(output_dir / "backtest.json", backtest)
    atomic_write_json(output_dir / "backtest-report.json", report)
    atomic_write_text(output_dir / "backtest-report.md", _report_markdown(report))
    atomic_write_text(output_dir / "README.md", _readme(retrieved_at))
    atomic_write_text(
        output_dir / "DATA_SOURCES.md",
        _data_sources(retrieved_at, str(report["ratings_sha256"])),
    )


def _fetch_payload() -> object:
    parameters = {
        "language": "en",
        "count": "500",
        "from": "2026-06-11T00:00:00Z",
        "to": "2026-07-20T23:59:59Z",
        "idCompetition": FIFA_COMPETITION_ID,
        "idSeason": FIFA_SEASON_ID,
    }
    request = urllib.request.Request(
        f"{FIFA_ENDPOINT}?{urllib.parse.urlencode(parameters)}",
        headers={
            "Accept": "application/json",
            "User-Agent": "tournament-forecaster/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path)
    source.add_argument("--fetch", action="store_true")
    parser.add_argument("--retrieved-at")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "examples" / "world-cup-2026-live",
    )
    arguments = parser.parse_args(argv)
    if arguments.fixture is not None:
        payload = json.loads(arguments.fixture.read_text(encoding="utf-8"))
        retrieved_at = arguments.retrieved_at
        if retrieved_at is None and isinstance(payload, Mapping):
            retrieved_at = payload.get("retrieved_at")
        if not isinstance(retrieved_at, str):
            parser.error("--fixture requires --retrieved-at or fixture retrieved_at")
    else:
        payload = _fetch_payload()
        retrieved_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    tournament, backtest, report = build_documents(payload, retrieved_at=retrieved_at)
    write_example(
        arguments.output_dir,
        tournament,
        backtest,
        report,
        retrieved_at=retrieved_at,
    )
    print(
        json.dumps(
            {
                "output_dir": str(arguments.output_dir),
                "retrieved_at": retrieved_at,
                "completed_facts": len(tournament["completed_matches"]),
                "backtest_cases": len(backtest["cases"]),
                "backtest_status": report["status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

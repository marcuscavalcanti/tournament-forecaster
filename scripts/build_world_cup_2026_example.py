#!/usr/bin/env python3
"""Build or verify the World Cup 2026 example from the CC0 OpenFootball snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tournament_forecaster.atomic_io import atomic_write_json, atomic_write_text  # noqa: E402
from tournament_forecaster.backtest import evaluate_backtest, ratings_sha256  # noqa: E402
from tournament_forecaster.errors import TournamentValidationError  # noqa: E402
from tournament_forecaster.group_fixtures import generate_group_fixture_specs  # noqa: E402


OPENFOOTBALL_SOURCE_COMMIT = "056c53ec82feb3fb68da63d1ce74ec59fc23e95d"
OPENFOOTBALL_SOURCE_URL = (
    "https://raw.githubusercontent.com/openfootball/worldcup.json/"
    f"{OPENFOOTBALL_SOURCE_COMMIT}/2026/worldcup.json"
)
OPENFOOTBALL_LICENSE_URL = (
    "https://raw.githubusercontent.com/openfootball/worldcup.json/"
    f"{OPENFOOTBALL_SOURCE_COMMIT}/LICENSE.md"
)
OPENFOOTBALL_SOURCE_NAME = "OpenFootball worldcup.json"
OPENFOOTBALL_LICENSE = "CC0 1.0 Universal"
MODEL_VERSION = "poisson-elo-v1"
RATING_COMMIT = "a7b6e694"
RATING_CAPTURED_AT = "2026-06-09T23:27:23-03:00"

_SOURCE_STAGE_BY_ROUND = {
    "Round of 32": "round-of-32",
    "Round of 16": "round-of-16",
    "Quarter-final": "quarter-finals",
    "Semi-final": "semi-finals",
    "Match for third place": "third-place",
    "Final": "final",
}
_SOURCE_STAGE_CAPACITIES = {
    "group-stage": 72,
    "round-of-32": 16,
    "round-of-16": 8,
    "quarter-finals": 4,
    "semi-finals": 2,
    "third-place": 1,
    "final": 1,
}
_CONFIG_STAGE_CAPACITIES = {
    stage_id: capacity
    for stage_id, capacity in _SOURCE_STAGE_CAPACITIES.items()
    if stage_id != "third-place"
}
_CONFIG_STAGE_ORDER = tuple(_CONFIG_STAGE_CAPACITIES)
_EXPECTED_SOURCE_STAGE_BY_NUMBER = {
    **{number: "group-stage" for number in range(1, 73)},
    **{number: "round-of-32" for number in range(73, 89)},
    **{number: "round-of-16" for number in range(89, 97)},
    **{number: "quarter-finals" for number in range(97, 101)},
    **{number: "semi-finals" for number in range(101, 103)},
    103: "third-place",
    104: "final",
}

# These identifiers predate this migration and are retained only as project-owned
# topology IDs so existing forecast paths and result references remain stable.
_STABLE_KNOCKOUT_ID_BY_SOURCE_NUMBER = {
    73: "400021518",
    74: "400021513",
    75: "400021522",
    76: "400021516",
    77: "400021523",
    78: "400021514",
    79: "400021520",
    80: "400021512",
    81: "400021524",
    82: "400021525",
    83: "400021526",
    84: "400021519",
    85: "400021527",
    86: "400021521",
    87: "400021517",
    88: "400021515",
    89: "400021533",
    90: "400021530",
    91: "400021532",
    92: "400021531",
    93: "400021529",
    94: "400021534",
    95: "400021528",
    96: "400021535",
    97: "400021536",
    98: "400021538",
    99: "400021539",
    100: "400021537",
    101: "400021541",
    102: "400021540",
    104: "400021543",
}
_BACKTEST_SOURCE_MATCH_ORDER = (
    1,
    2,
    7,
    19,
    14,
    20,
    13,
    8,
    26,
    25,
    31,
    32,
    44,
    43,
    38,
    37,
    49,
    50,
    55,
    56,
    68,
    67,
    61,
    62,
    3,
    9,
    10,
    4,
    16,
    15,
    22,
    21,
    27,
    28,
    33,
    34,
    46,
    45,
    39,
    40,
    52,
    51,
    57,
    58,
    69,
    70,
    63,
    64,
    17,
    18,
    11,
    12,
    5,
    6,
    29,
    30,
    35,
    36,
    23,
    24,
    53,
    54,
    41,
    42,
    47,
    48,
    71,
    72,
    59,
    60,
    65,
    66,
)


@dataclass(frozen=True, slots=True)
class TeamTopology:
    key: str
    team_id: str
    display_name: str
    source_name: str
    rating: float


_TEAMS = (
    TeamTopology("ALG", "algeria", "Algeria", "Algeria", 1595.0),
    TeamTopology("ARG", "argentina", "Argentina", "Argentina", 1910.0),
    TeamTopology("AUS", "australia", "Australia", "Australia", 1580.0),
    TeamTopology("AUT", "austria", "Austria", "Austria", 1700.0),
    TeamTopology("BEL", "belgium", "Belgium", "Belgium", 1790.0),
    TeamTopology(
        "BIH",
        "bosnia-and-herzegovina",
        "Bosnia and Herzegovina",
        "Bosnia & Herzegovina",
        1560.0,
    ),
    TeamTopology("BRA", "brazil", "Brazil", "Brazil", 1850.0),
    TeamTopology("CPV", "cabo-verde", "Cabo Verde", "Cape Verde", 1505.0),
    TeamTopology("CAN", "canada", "Canada", "Canada", 1585.0),
    TeamTopology("COL", "colombia", "Colombia", "Colombia", 1740.0),
    TeamTopology("COD", "congo-dr", "Congo DR", "DR Congo", 1500.0),
    TeamTopology("CIV", "cote-d-ivoire", "Côte d'Ivoire", "Ivory Coast", 1605.0),
    TeamTopology("CRO", "croatia", "Croatia", "Croatia", 1745.0),
    TeamTopology("CUW", "curacao", "Curaçao", "Curaçao", 1360.0),
    TeamTopology("CZE", "czechia", "Czechia", "Czech Republic", 1580.0),
    TeamTopology("ECU", "ecuador", "Ecuador", "Ecuador", 1650.0),
    TeamTopology("EGY", "egypt", "Egypt", "Egypt", 1615.0),
    TeamTopology("ENG", "england", "England", "England", 1880.0),
    TeamTopology("FRA", "france", "France", "France", 1920.0),
    TeamTopology("GER", "germany", "Germany", "Germany", 1860.0),
    TeamTopology("GHA", "ghana", "Ghana", "Ghana", 1560.0),
    TeamTopology("HAI", "haiti", "Haiti", "Haiti", 1320.0),
    TeamTopology("IRN", "ir-iran", "IR Iran", "Iran", 1625.0),
    TeamTopology("IRQ", "iraq", "Iraq", "Iraq", 1490.0),
    TeamTopology("JPN", "japan", "Japan", "Japan", 1690.0),
    TeamTopology("JOR", "jordan", "Jordan", "Jordan", 1440.0),
    TeamTopology("KOR", "korea-republic", "Korea Republic", "South Korea", 1630.0),
    TeamTopology("MEX", "mexico", "Mexico", "Mexico", 1690.0),
    TeamTopology("MAR", "morocco", "Morocco", "Morocco", 1660.0),
    TeamTopology("NED", "netherlands", "Netherlands", "Netherlands", 1860.0),
    TeamTopology("NZL", "new-zealand", "New Zealand", "New Zealand", 1430.0),
    TeamTopology("NOR", "norway", "Norway", "Norway", 1660.0),
    TeamTopology("PAN", "panama", "Panama", "Panama", 1480.0),
    TeamTopology("PAR", "paraguay", "Paraguay", "Paraguay", 1600.0),
    TeamTopology("POR", "portugal", "Portugal", "Portugal", 1870.0),
    TeamTopology("QAT", "qatar", "Qatar", "Qatar", 1500.0),
    TeamTopology("KSA", "saudi-arabia", "Saudi Arabia", "Saudi Arabia", 1510.0),
    TeamTopology("SCO", "scotland", "Scotland", "Scotland", 1540.0),
    TeamTopology("SEN", "senegal", "Senegal", "Senegal", 1655.0),
    TeamTopology("RSA", "south-africa", "South Africa", "South Africa", 1470.0),
    TeamTopology("ESP", "spain", "Spain", "Spain", 1900.0),
    TeamTopology("SWE", "sweden", "Sweden", "Sweden", 1650.0),
    TeamTopology("SUI", "switzerland", "Switzerland", "Switzerland", 1710.0),
    TeamTopology("TUN", "tunisia", "Tunisia", "Tunisia", 1480.0),
    TeamTopology("TUR", "turkiye", "Türkiye", "Turkey", 1640.0),
    TeamTopology("URU", "uruguay", "Uruguay", "Uruguay", 1780.0),
    TeamTopology("USA", "usa", "USA", "USA", 1645.0),
    TeamTopology("UZB", "uzbekistan", "Uzbekistan", "Uzbekistan", 1515.0),
)
_TEAM_BY_ID = {team.team_id: team for team in _TEAMS}


def _build_source_name_map() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for team in _TEAMS:
        for alias in (team.source_name, team.display_name):
            existing = aliases.get(alias)
            if existing is not None and existing != team.team_id:
                raise RuntimeError(f"conflicting project team alias: {alias}")
            aliases[alias] = team.team_id
    return aliases


_TEAM_ID_BY_SOURCE_NAME = _build_source_name_map()


@dataclass(frozen=True, slots=True)
class NormalizedMatch:
    match_number: int
    stage_id: str
    group_id: str | None
    kickoff_at: str
    home_team_id: str
    away_team_id: str
    score: tuple[int, int] | None
    winner_team_id: str | None
    score_basis: str | None

    @property
    def completed(self) -> bool:
        return self.score is not None


@dataclass(frozen=True, slots=True)
class NormalizedFixture:
    matches: tuple[NormalizedMatch, ...]
    completed: tuple[NormalizedMatch, ...]
    pending: tuple[NormalizedMatch, ...]


@dataclass(frozen=True, slots=True)
class Reconciliation:
    source_matches: int
    source_completed: int
    reconciled_facts: int
    backtest_cases: int

    def to_dict(self) -> dict[str, int]:
        return {
            "source_matches": self.source_matches,
            "source_completed": self.source_completed,
            "reconciled_facts": self.reconciled_facts,
            "backtest_cases": self.backtest_cases,
        }


def _strict_integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        qualifier = "positive" if minimum == 1 else "non-negative"
        raise TournamentValidationError(f"{label} must be a {qualifier} integer")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise TournamentValidationError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TournamentValidationError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TournamentValidationError(f"{label} must include a timezone")
    return parsed


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _kickoff_at(row: Mapping[str, object], match_number: int) -> tuple[datetime, str]:
    date_value = row.get("date")
    time_value = row.get("time")
    if not isinstance(date_value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date_value
    ):
        raise TournamentValidationError(
            f"OpenFootball match {match_number} date must use YYYY-MM-DD"
        )
    if not isinstance(time_value, str):
        raise TournamentValidationError(
            f"OpenFootball match {match_number} kickoff offset must use HH:MM UTC+/-H"
        )
    time_match = re.fullmatch(
        r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}) UTC(?:(?P<sign>[+-])(?P<offset>[0-9]{1,2}))?",
        time_value,
    )
    if time_match is None:
        raise TournamentValidationError(
            f"OpenFootball match {match_number} kickoff offset must use HH:MM UTC+/-H"
        )
    hour = int(time_match.group("hour"))
    minute = int(time_match.group("minute"))
    offset_hours = int(time_match.group("offset") or "0")
    if hour > 23 or minute > 59 or offset_hours > 14:
        raise TournamentValidationError(
            f"OpenFootball match {match_number} kickoff offset is out of range"
        )
    if time_match.group("sign") == "-":
        offset_hours *= -1
    try:
        local = datetime.fromisoformat(f"{date_value}T{hour:02d}:{minute:02d}:00").replace(
            tzinfo=timezone(timedelta(hours=offset_hours))
        )
    except ValueError as error:
        raise TournamentValidationError(
            f"OpenFootball match {match_number} date is invalid"
        ) from error
    return local, _utc_text(local)


def _stage_and_group(row: Mapping[str, object], match_number: int) -> tuple[str, str | None]:
    round_value = row.get("round")
    if not isinstance(round_value, str) or not round_value.strip():
        raise TournamentValidationError(f"OpenFootball match {match_number} requires a round label")
    group_value = row.get("group")
    if group_value is not None:
        if not isinstance(group_value, str):
            raise TournamentValidationError(
                f"OpenFootball match {match_number} group label must be text"
            )
        group_match = re.fullmatch(r"Group ([A-L])", group_value)
        if group_match is None or re.fullmatch(r"Matchday [1-9][0-9]*", round_value) is None:
            raise TournamentValidationError(f"unsupported OpenFootball stage: {round_value}")
        return "group-stage", group_match.group(1)
    stage_id = _SOURCE_STAGE_BY_ROUND.get(round_value)
    if stage_id is None:
        raise TournamentValidationError(f"unsupported OpenFootball stage: {round_value}")
    return stage_id, None


def _team_id(value: object, match_number: int, side: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TournamentValidationError(
            f"OpenFootball match {match_number} {side} team must be text"
        )
    team_id = _TEAM_ID_BY_SOURCE_NAME.get(value)
    if team_id is not None:
        return team_id
    if re.fullmatch(r"[WL][1-9][0-9]*", value):
        return value
    raise TournamentValidationError(f"unknown OpenFootball team: {value}")


def _score_pair(value: object, match_number: int, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 2
        or any(type(score) is not int or score < 0 for score in value)
    ):
        raise TournamentValidationError(
            f"OpenFootball match {match_number} {label} score must contain integers"
        )
    return int(value[0]), int(value[1])


def _winner_side(score: tuple[int, int]) -> int | None:
    if score[0] > score[1]:
        return 0
    if score[1] > score[0]:
        return 1
    return None


def _result(
    row: Mapping[str, object],
    *,
    match_number: int,
    stage_id: str,
) -> tuple[tuple[int, int] | None, int | None, str | None]:
    score_value = row.get("score")
    if score_value is None:
        return None, None, None
    if not isinstance(score_value, Mapping):
        raise TournamentValidationError(
            f"OpenFootball match {match_number} score must be an object or null"
        )
    unknown_fields = set(score_value) - {"ht", "ft", "et", "p"}
    if unknown_fields:
        raise TournamentValidationError(
            f"OpenFootball match {match_number} has unknown score fields: "
            f"{', '.join(sorted(str(field) for field in unknown_fields))}"
        )
    if "ft" not in score_value:
        raise TournamentValidationError(
            f"OpenFootball match {match_number} completed score requires ft"
        )
    full_time = _score_pair(score_value["ft"], match_number, "ft")
    if "ht" in score_value:
        _score_pair(score_value["ht"], match_number, "ht")
    extra_time = _score_pair(score_value["et"], match_number, "et") if "et" in score_value else None
    penalties = (
        _score_pair(score_value["p"], match_number, "penalty") if "p" in score_value else None
    )
    if stage_id == "group-stage":
        if extra_time is not None or penalties is not None:
            raise TournamentValidationError(
                f"OpenFootball group match {match_number} cannot use extra time or penalties"
            )
        return full_time, _winner_side(full_time), "full_time"
    if penalties is not None:
        if (
            extra_time is None
            or _winner_side(full_time) is not None
            or _winner_side(extra_time) is not None
        ):
            raise TournamentValidationError(
                f"OpenFootball match {match_number} penalties require tied ft and et scores"
            )
        winner = _winner_side(penalties)
        if winner is None:
            raise TournamentValidationError("penalty score must identify one winner")
        return extra_time, winner, "penalties"
    if extra_time is not None:
        if _winner_side(full_time) is not None:
            raise TournamentValidationError(
                f"OpenFootball match {match_number} extra time requires a tied ft score"
            )
        winner = _winner_side(extra_time)
        if winner is None:
            raise TournamentValidationError(
                f"OpenFootball match {match_number} extra-time score must identify one winner"
            )
        return extra_time, winner, "extra_time"
    winner = _winner_side(full_time)
    if winner is None:
        raise TournamentValidationError(
            f"OpenFootball knockout match {match_number} cannot finish level"
        )
    return full_time, winner, "full_time"


def _validate_chronological_frontier(matches: Sequence[NormalizedMatch]) -> None:
    pending_seen = False
    for match in sorted(matches, key=lambda item: item.match_number):
        if not match.completed:
            pending_seen = True
        elif pending_seen:
            raise TournamentValidationError(
                "completed matches cross the chronological completion frontier"
            )


def _validate_stage_completion_frontier(stage_counts: Mapping[str, int]) -> None:
    """Require completed facts to form one supported publication frontier."""

    for stage_id, capacity in _CONFIG_STAGE_CAPACITIES.items():
        count = stage_counts.get(stage_id, 0)
        if count < 0 or count > capacity:
            raise TournamentValidationError(
                f"invalid completed count for {stage_id}: {count}/{capacity}"
            )
    for stage_id in ("group-stage", "round-of-32", "round-of-16"):
        if stage_counts.get(stage_id, 0) != _CONFIG_STAGE_CAPACITIES[stage_id]:
            raise TournamentValidationError(
                f"live snapshot requires all {stage_id} results before publication"
            )
    frontier_open = False
    for stage_id in ("quarter-finals", "semi-finals", "final"):
        count = stage_counts.get(stage_id, 0)
        capacity = _CONFIG_STAGE_CAPACITIES[stage_id]
        if frontier_open and count:
            raise TournamentValidationError(
                "completed matches cross the chronological completion frontier"
            )
        if count < capacity:
            frontier_open = True


def _extract_groups(matches: Sequence[NormalizedMatch]) -> dict[str, list[str]]:
    groups: dict[str, set[str]] = defaultdict(set)
    team_group: dict[str, str] = {}
    pairs: dict[str, Counter[frozenset[str]]] = defaultdict(Counter)
    appearances: Counter[str] = Counter()
    for match in matches:
        if match.stage_id != "group-stage":
            continue
        if match.group_id is None:
            raise TournamentValidationError("OpenFootball group match requires a group")
        if match.home_team_id not in _TEAM_BY_ID or match.away_team_id not in _TEAM_BY_ID:
            raise TournamentValidationError("OpenFootball group topology cannot use placeholders")
        for team_id in (match.home_team_id, match.away_team_id):
            prior_group = team_group.setdefault(team_id, match.group_id)
            if prior_group != match.group_id:
                raise TournamentValidationError(
                    f"OpenFootball team {team_id} appears in multiple groups"
                )
            groups[match.group_id].add(team_id)
            appearances[team_id] += 1
        pairs[match.group_id][frozenset((match.home_team_id, match.away_team_id))] += 1
    if set(groups) != set("ABCDEFGHIJKL") or any(
        len(team_ids) != 4 for team_ids in groups.values()
    ):
        raise TournamentValidationError(
            "OpenFootball group topology must contain 12 groups of four"
        )
    if set(team_group) != set(_TEAM_BY_ID):
        raise TournamentValidationError(
            "OpenFootball group topology does not contain the expected 48 teams"
        )
    if any(count != 3 for count in appearances.values()):
        raise TournamentValidationError(
            "OpenFootball group topology must give every team three matches"
        )
    if any(
        len(group_pairs) != 6 or set(group_pairs.values()) != {1} for group_pairs in pairs.values()
    ):
        raise TournamentValidationError(
            "OpenFootball group topology must contain each pair exactly once"
        )
    return {
        group_id: sorted(team_ids, key=lambda team_id: _TEAM_BY_ID[team_id].key)
        for group_id, team_ids in sorted(groups.items())
    }


def _validate_full_source(payload: Mapping[str, object], fixture: NormalizedFixture) -> None:
    if payload.get("name") != "World Cup 2026":
        raise TournamentValidationError("OpenFootball source name must be World Cup 2026")
    if len(fixture.matches) != 104:
        raise TournamentValidationError(
            f"OpenFootball source must contain 104 matches, found {len(fixture.matches)}"
        )
    numbers = [match.match_number for match in fixture.matches]
    if numbers != list(range(1, 105)):
        raise TournamentValidationError("OpenFootball match numbers must be exactly 1 through 104")
    stage_counts = Counter(match.stage_id for match in fixture.matches)
    if stage_counts != Counter(_SOURCE_STAGE_CAPACITIES):
        raise TournamentValidationError(
            f"OpenFootball source stage counts conflict with the 104-match topology: {stage_counts}"
        )
    for match in fixture.matches:
        expected_stage = _EXPECTED_SOURCE_STAGE_BY_NUMBER[match.match_number]
        if match.stage_id != expected_stage:
            raise TournamentValidationError(
                f"OpenFootball match {match.match_number} must be {expected_stage}"
            )
    _extract_groups(fixture.matches)
    third_place = fixture.matches[102]
    final = fixture.matches[103]
    if (third_place.home_team_id, third_place.away_team_id) != ("L101", "L102"):
        raise TournamentValidationError(
            "OpenFootball third-place topology must be L101 versus L102"
        )
    if (final.home_team_id, final.away_team_id) != ("W101", "W102"):
        raise TournamentValidationError("OpenFootball final topology must be W101 versus W102")
    completed_stage_counts = {
        stage_id: sum(match.stage_id == stage_id for match in fixture.completed)
        for stage_id in _CONFIG_STAGE_ORDER
    }
    _validate_stage_completion_frontier(completed_stage_counts)


def normalize_openfootball_fixture(
    payload: object,
    *,
    retrieved_at: str | datetime,
    require_full_tournament: bool = False,
) -> NormalizedFixture:
    """Normalize OpenFootball rows and fail closed on ambiguous match facts."""

    if not isinstance(payload, Mapping):
        raise TournamentValidationError("OpenFootball source must be a JSON object")
    rows = payload.get("matches")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise TournamentValidationError("OpenFootball source must contain a matches array")
    observed_at = (
        retrieved_at
        if isinstance(retrieved_at, datetime)
        else _timestamp(retrieved_at, "retrieved_at")
    )
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise TournamentValidationError("retrieved_at must include a timezone")
    normalized: dict[int, NormalizedMatch] = {}
    for position, raw in enumerate(rows, start=1):
        if not isinstance(raw, Mapping):
            raise TournamentValidationError("OpenFootball match row must be an object")
        match_number = (
            _strict_integer(raw["num"], "OpenFootball match number", minimum=1)
            if "num" in raw
            else position
        )
        if match_number in normalized:
            raise TournamentValidationError(f"duplicate OpenFootball match number {match_number}")
        stage_id, group_id = _stage_and_group(raw, match_number)
        kickoff, kickoff_text = _kickoff_at(raw, match_number)
        home_team_id = _team_id(raw.get("team1"), match_number, "home")
        away_team_id = _team_id(raw.get("team2"), match_number, "away")
        if home_team_id == away_team_id:
            raise TournamentValidationError(
                f"OpenFootball match {match_number} entrants must be distinct"
            )
        score, winner_side, score_basis = _result(
            raw,
            match_number=match_number,
            stage_id=stage_id,
        )
        winner_team_id: str | None = None
        if score is not None:
            if home_team_id not in _TEAM_BY_ID or away_team_id not in _TEAM_BY_ID:
                raise TournamentValidationError(
                    f"completed OpenFootball match {match_number} cannot use placeholders"
                )
            if observed_at <= kickoff:
                raise TournamentValidationError(
                    f"OpenFootball match {match_number} retrieved_at must be after kickoff_at"
                )
            if winner_side is not None:
                winner_team_id = (home_team_id, away_team_id)[winner_side]
        normalized[match_number] = NormalizedMatch(
            match_number=match_number,
            stage_id=stage_id,
            group_id=group_id,
            kickoff_at=kickoff_text,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            score=score,
            winner_team_id=winner_team_id,
            score_basis=score_basis,
        )
    ordered = tuple(normalized[number] for number in sorted(normalized))
    _validate_chronological_frontier(ordered)
    fixture = NormalizedFixture(
        matches=ordered,
        completed=tuple(match for match in ordered if match.completed),
        pending=tuple(match for match in ordered if not match.completed),
    )
    if require_full_tournament:
        _validate_full_source(payload, fixture)
    return fixture


def _stable_match_id(match_number: int) -> str:
    try:
        return _STABLE_KNOCKOUT_ID_BY_SOURCE_NUMBER[match_number]
    except KeyError as error:
        raise TournamentValidationError(
            f"no project topology ID for OpenFootball match {match_number}"
        ) from error


def _knockout_stage(
    stage_id: str,
    rows: Sequence[NormalizedMatch],
    entrant_sources: Mapping[int, list[dict[str, object]]],
    *,
    terminal: str | None = None,
) -> dict[str, object]:
    stage: dict[str, object] = {
        "id": stage_id,
        "type": "knockout",
        "pairing": {
            "mode": "fixed",
            "ties": [
                {
                    "id": _stable_match_id(match.match_number),
                    "entrants": entrant_sources[match.match_number],
                }
                for match in sorted(rows, key=lambda item: item.match_number)
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


def _winner_match_numbers(
    rows_by_stage: Mapping[str, Sequence[NormalizedMatch]],
) -> dict[str, dict[str, int]]:
    winners: dict[str, dict[str, int]] = {}
    for stage_id in ("round-of-32", "round-of-16", "quarter-finals", "semi-finals"):
        stage_winners: dict[str, int] = {}
        for match in rows_by_stage[stage_id]:
            if match.winner_team_id is None:
                continue
            if match.winner_team_id in stage_winners:
                raise TournamentValidationError(
                    f"team {match.winner_team_id} wins two matches in {stage_id}"
                )
            stage_winners[match.winner_team_id] = match.match_number
        winners[stage_id] = stage_winners
    return winners


def _prior_winner_source(
    team_or_placeholder: str,
    *,
    prior_stage: str,
    winner_numbers: Mapping[str, Mapping[str, int]],
) -> dict[str, object]:
    if team_or_placeholder in _TEAM_BY_ID:
        try:
            match_number = winner_numbers[prior_stage][team_or_placeholder]
        except KeyError as error:
            raise TournamentValidationError(
                f"OpenFootball bracket entrant {team_or_placeholder} does not resolve from {prior_stage}"
            ) from error
    else:
        placeholder = re.fullmatch(r"W([1-9][0-9]*)", team_or_placeholder)
        if placeholder is None:
            raise TournamentValidationError(
                f"unsupported OpenFootball winner placeholder: {team_or_placeholder}"
            )
        match_number = int(placeholder.group(1))
        if _EXPECTED_SOURCE_STAGE_BY_NUMBER.get(match_number) != prior_stage:
            raise TournamentValidationError(
                f"OpenFootball placeholder {team_or_placeholder} does not reference {prior_stage}"
            )
    return {"type": "match_winner", "match_id": _stable_match_id(match_number)}


def _source_metadata(match: NormalizedMatch) -> dict[str, object]:
    return {
        "source": OPENFOOTBALL_SOURCE_NAME,
        "source_url": OPENFOOTBALL_SOURCE_URL,
        "source_match_number": match.match_number,
        "kickoff_at": match.kickoff_at,
        "result_basis": match.score_basis,
    }


def _snapshot_metadata(
    *,
    fixture: NormalizedFixture,
    retrieved_at: str,
    source_sha256: str,
    completed_fact_count: int,
) -> dict[str, object]:
    return {
        "source": OPENFOOTBALL_SOURCE_NAME,
        "source_commit": OPENFOOTBALL_SOURCE_COMMIT,
        "source_url": OPENFOOTBALL_SOURCE_URL,
        "license": OPENFOOTBALL_LICENSE,
        "license_url": OPENFOOTBALL_LICENSE_URL,
        "retrieved_at": retrieved_at,
        "source_sha256": source_sha256,
        "source_match_count": len(fixture.matches),
        "completed_fact_count": completed_fact_count,
        "transformation": (
            "Validated the 104-match structure; mapped OpenFootball team aliases and stages to "
            "project topology; converted kickoff offsets to UTC; selected et over ft for "
            "extra-time finals; used p only to identify shootout winners; omitted goals, grounds, "
            "half-time scores, and the unsupported third-place tie."
        ),
        "known_limitations": [
            "The community-maintained source is not an official live feed.",
            "The source has no trusted result-finalization timestamp.",
            "The generic project bracket cannot model the third-place loser entrants.",
        ],
        "stable_id_policy": (
            "Existing numeric knockout IDs are project-owned topology IDs, not source or provider IDs."
        ),
    }


def _build_documents_from_fixture(
    fixture: NormalizedFixture,
    *,
    retrieved_at: str,
    source_sha256: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    groups = _extract_groups(fixture.matches)
    rows_by_stage = {
        stage_id: tuple(match for match in fixture.matches if match.stage_id == stage_id)
        for stage_id in _SOURCE_STAGE_CAPACITIES
    }
    group_stage = {
        "id": "group-stage",
        "type": "round_robin_groups",
        "groups": groups,
        "rounds_per_pair": 1,
        "points": {"win": 3, "draw": 1, "loss": 0},
        "tiebreakers": [
            "points",
            "goal_difference",
            "goals_for",
            "wins",
            "rating",
            "team_id",
        ],
        "metadata": {"home_advantage_rating_points": 0},
    }
    fixtures_by_pair = {
        frozenset((group_fixture.home_team_id, group_fixture.away_team_id)): group_fixture
        for group_fixture in generate_group_fixture_specs(group_stage)
    }

    r32_entrants: dict[int, list[dict[str, object]]] = {}
    for match in rows_by_stage["round-of-32"]:
        if match.home_team_id not in _TEAM_BY_ID or match.away_team_id not in _TEAM_BY_ID:
            raise TournamentValidationError(
                f"OpenFootball Round of 32 match {match.match_number} must name both teams"
            )
        r32_entrants[match.match_number] = [
            {"type": "team", "team_id": match.home_team_id},
            {"type": "team", "team_id": match.away_team_id},
        ]

    winner_numbers = _winner_match_numbers(rows_by_stage)
    entrants_by_stage: dict[str, dict[int, list[dict[str, object]]]] = {"round-of-32": r32_entrants}
    for stage_id, prior_stage in (
        ("round-of-16", "round-of-32"),
        ("quarter-finals", "round-of-16"),
        ("semi-finals", "quarter-finals"),
        ("final", "semi-finals"),
    ):
        entrants_by_stage[stage_id] = {
            match.match_number: [
                _prior_winner_source(
                    match.home_team_id,
                    prior_stage=prior_stage,
                    winner_numbers=winner_numbers,
                ),
                _prior_winner_source(
                    match.away_team_id,
                    prior_stage=prior_stage,
                    winner_numbers=winner_numbers,
                ),
            ]
            for match in rows_by_stage[stage_id]
        }

    stages = [
        group_stage,
        _knockout_stage("round-of-32", rows_by_stage["round-of-32"], r32_entrants),
        _knockout_stage(
            "round-of-16",
            rows_by_stage["round-of-16"],
            entrants_by_stage["round-of-16"],
        ),
        _knockout_stage(
            "quarter-finals",
            rows_by_stage["quarter-finals"],
            entrants_by_stage["quarter-finals"],
        ),
        _knockout_stage(
            "semi-finals",
            rows_by_stage["semi-finals"],
            entrants_by_stage["semi-finals"],
        ),
        _knockout_stage(
            "final",
            rows_by_stage["final"],
            entrants_by_stage["final"],
            terminal="championship",
        ),
    ]

    completed_matches: list[dict[str, object]] = []
    for match in fixture.completed:
        if match.stage_id == "third-place":
            continue
        if match.score is None:
            raise TournamentValidationError(
                f"completed OpenFootball match {match.match_number} has no score"
            )
        home_team_id = match.home_team_id
        away_team_id = match.away_team_id
        score_home, score_away = match.score
        if match.stage_id == "group-stage":
            pair = frozenset((home_team_id, away_team_id))
            try:
                group_fixture = fixtures_by_pair[pair]
            except KeyError as error:
                raise TournamentValidationError(
                    f"OpenFootball group match {match.match_number} is absent from project topology"
                ) from error
            match_id = group_fixture.match_id
            if (home_team_id, away_team_id) != (
                group_fixture.home_team_id,
                group_fixture.away_team_id,
            ):
                home_team_id, away_team_id = away_team_id, home_team_id
                score_home, score_away = score_away, score_home
        else:
            match_id = _stable_match_id(match.match_number)
        completed: dict[str, object] = {
            "match_id": match_id,
            "stage_id": match.stage_id,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "score": {"home": score_home, "away": score_away},
            "metadata": _source_metadata(match),
        }
        if match.stage_id != "group-stage":
            if match.winner_team_id is None:
                raise TournamentValidationError(
                    f"OpenFootball knockout match {match.match_number} has no winner"
                )
            completed["winner_team_id"] = match.winner_team_id
        completed_matches.append(completed)
    completed_matches.sort(
        key=lambda completed: (str(completed["stage_id"]), str(completed["match_id"]))
    )
    completed_stage_counts = {
        stage_id: sum(match["stage_id"] == stage_id for match in completed_matches)
        for stage_id in _CONFIG_STAGE_ORDER
    }
    _validate_stage_completion_frontier(completed_stage_counts)

    ratings = {team.team_id: team.rating for team in _TEAMS}
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
            {"id": team.team_id, "display_name": team.display_name}
            for team in sorted(_TEAMS, key=lambda item: item.team_id)
        ],
        "stages": stages,
        "ratings": ratings,
        "completed_matches": completed_matches,
        "metadata": {
            "snapshot": _snapshot_metadata(
                fixture=fixture,
                retrieved_at=retrieved_at,
                source_sha256=source_sha256,
                completed_fact_count=len(completed_matches),
            ),
            "ratings": {
                "source": "project-authored pre-tournament seed",
                "git_commit": RATING_COMMIT,
                "frozen_at": RATING_CAPTURED_AT,
                "sha256": rating_hash,
                "limitation": (
                    "Not an official rating source and not proof of universal calibration."
                ),
            },
        },
    }

    group_rows_by_number = {match.match_number: match for match in rows_by_stage["group-stage"]}
    if set(group_rows_by_number) != set(_BACKTEST_SOURCE_MATCH_ORDER):
        raise TournamentValidationError(
            "OpenFootball group rows do not match the project backtest ordering"
        )
    group_rows = tuple(
        group_rows_by_number[match_number] for match_number in _BACKTEST_SOURCE_MATCH_ORDER
    )
    backtest = {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "home_advantage_rating_points": 0,
        "ratings": ratings,
        "ratings_sha256": rating_hash,
        "cases": [
            {
                "source_id": f"openfootball-worldcup-2026-match-{match.match_number:03d}",
                "captured_at": RATING_CAPTURED_AT,
                "kickoff_at": match.kickoff_at,
                "home_team_id": match.home_team_id,
                "away_team_id": match.away_team_id,
                "result": {
                    "home": match.score[0],
                    "away": match.score[1],
                },
                "metadata": {
                    "source": OPENFOOTBALL_SOURCE_NAME,
                    "source_url": OPENFOOTBALL_SOURCE_URL,
                    "source_match_number": match.match_number,
                    "license": OPENFOOTBALL_LICENSE,
                },
            }
            for match in group_rows
            if match.score is not None
        ],
        "metadata": {
            "purpose": "Out-of-sample 1X2 evaluation of the frozen pre-tournament rating seed.",
            "rating_provenance": {
                "git_commit": RATING_COMMIT,
                "captured_at": RATING_CAPTURED_AT,
                "limitation": "Project-authored, not an official rating source.",
            },
            "source_snapshot": _snapshot_metadata(
                fixture=fixture,
                retrieved_at=retrieved_at,
                source_sha256=source_sha256,
                completed_fact_count=len(completed_matches),
            ),
        },
    }
    report = evaluate_backtest(backtest, min_resolved=72).to_dict()
    reconcile_documents(fixture, tournament, backtest)
    return tournament, backtest, report


def _source_sha256(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise TournamentValidationError("source_sha256 must be 64 lowercase hexadecimal characters")
    return value


def build_documents(
    payload: object,
    *,
    retrieved_at: str,
    source_sha256: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """Build canonical artifacts from one exact OpenFootball source capture."""

    _timestamp(retrieved_at, "retrieved_at")
    source_hash = _source_sha256(source_sha256)
    fixture = normalize_openfootball_fixture(
        payload,
        retrieved_at=retrieved_at,
        require_full_tournament=True,
    )
    return _build_documents_from_fixture(
        fixture,
        retrieved_at=retrieved_at,
        source_sha256=source_hash,
    )


def _mapping_sequence(value: object, label: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TournamentValidationError(f"{label} must be an array")
    if not all(isinstance(item, Mapping) for item in value):
        raise TournamentValidationError(f"{label} must contain objects")
    return tuple(item for item in value if isinstance(item, Mapping))


def _metadata_match_number(value: object, label: str) -> int:
    if not isinstance(value, Mapping):
        raise TournamentValidationError(f"{label} metadata must be an object")
    return _strict_integer(
        value.get("source_match_number"),
        f"{label} source_match_number",
        minimum=1,
    )


def reconcile_documents(
    fixture: NormalizedFixture,
    tournament: Mapping[str, object],
    backtest: Mapping[str, object],
) -> Reconciliation:
    """Independently match normalized source facts to both distributable artifacts."""

    canonical_rows = _mapping_sequence(
        tournament.get("completed_matches"),
        "tournament completed_matches",
    )
    canonical_by_number: dict[int, Mapping[str, object]] = {}
    for row in canonical_rows:
        number = _metadata_match_number(row.get("metadata"), "tournament match")
        if number in canonical_by_number:
            raise TournamentValidationError(
                f"tournament repeats OpenFootball source match {number}"
            )
        canonical_by_number[number] = row

    source_completed = tuple(
        match for match in fixture.completed if match.stage_id != "third-place"
    )
    if set(canonical_by_number) != {match.match_number for match in source_completed}:
        raise TournamentValidationError(
            "tournament completed facts do not match the OpenFootball completion frontier"
        )
    for source_match in source_completed:
        row = canonical_by_number[source_match.match_number]
        metadata = row.get("metadata")
        if not isinstance(metadata, Mapping):
            raise TournamentValidationError("tournament match metadata must be an object")
        if metadata.get("source") != OPENFOOTBALL_SOURCE_NAME:
            raise TournamentValidationError(
                f"tournament match {source_match.match_number} has wrong source metadata"
            )
        if metadata.get("kickoff_at") != source_match.kickoff_at:
            raise TournamentValidationError(
                f"tournament match {source_match.match_number} kickoff conflicts with source"
            )
        if metadata.get("result_basis") != source_match.score_basis:
            raise TournamentValidationError(
                f"tournament match {source_match.match_number} result basis conflicts with source"
            )
        score = row.get("score")
        if not isinstance(score, Mapping) or source_match.score is None:
            raise TournamentValidationError(
                f"tournament match {source_match.match_number} score is invalid"
            )
        home_team_id = row.get("home_team_id")
        away_team_id = row.get("away_team_id")
        expected_by_team = {
            source_match.home_team_id: source_match.score[0],
            source_match.away_team_id: source_match.score[1],
        }
        actual_by_team = {
            home_team_id: score.get("home"),
            away_team_id: score.get("away"),
        }
        if actual_by_team != expected_by_team:
            raise TournamentValidationError(
                f"tournament match {source_match.match_number} entrants or score conflict with source"
            )
        if (
            source_match.stage_id != "group-stage"
            and row.get("winner_team_id") != source_match.winner_team_id
        ):
            raise TournamentValidationError(
                f"tournament match {source_match.match_number} winner conflicts with source"
            )

    backtest_rows = _mapping_sequence(backtest.get("cases"), "backtest cases")
    source_group_by_number = {
        match.match_number: match for match in fixture.matches if match.stage_id == "group-stage"
    }
    source_group_rows = tuple(
        source_group_by_number[match_number] for match_number in _BACKTEST_SOURCE_MATCH_ORDER
    )
    if len(backtest_rows) != len(source_group_rows):
        raise TournamentValidationError("backtest must contain all 72 OpenFootball group facts")
    for source_match, row in zip(source_group_rows, backtest_rows, strict=True):
        number = _metadata_match_number(row.get("metadata"), "backtest case")
        expected_result = (
            {"home": source_match.score[0], "away": source_match.score[1]}
            if source_match.score is not None
            else None
        )
        if (
            number != source_match.match_number
            or row.get("source_id")
            != f"openfootball-worldcup-2026-match-{source_match.match_number:03d}"
            or row.get("kickoff_at") != source_match.kickoff_at
            or row.get("home_team_id") != source_match.home_team_id
            or row.get("away_team_id") != source_match.away_team_id
            or row.get("result") != expected_result
        ):
            raise TournamentValidationError(
                f"backtest case {source_match.match_number} conflicts with OpenFootball"
            )
    return Reconciliation(
        source_matches=len(fixture.matches),
        source_completed=len(fixture.completed),
        reconciled_facts=len(source_completed),
        backtest_cases=len(backtest_rows),
    )


def _report_markdown(report: Mapping[str, object]) -> str:
    metrics = report["metrics"]
    baseline = report["uniform_baseline"]
    assert isinstance(metrics, Mapping) and isinstance(baseline, Mapping)
    return f"""# World Cup 2026 Group-Stage Backtest

- Status: `{report["status"]}`
- Resolved cases: `{report["sample_size"]}`
- Model: `{report["model_version"]}`
- Ratings SHA-256: `{report["ratings_sha256"]}`
- Home advantage: `0` rating points (neutral site)

| Metric | Model | Uniform baseline |
|---|---:|---:|
| RPS | {float(metrics["rps"]):.6f} | {float(baseline["rps"]):.6f} |
| Multiclass Brier | {float(metrics["brier"]):.6f} | {float(baseline["brier"]):.6f} |
| Natural log loss | {float(metrics["log_loss"]):.6f} | {float(baseline["log_loss"]):.6f} |
| Top-pick accuracy | {float(metrics["top_pick_accuracy"]):.6f} | {float(baseline["top_pick_accuracy"]):.6f} |

RPS is the mean squared cumulative error over the ordered outcomes home/draw/away,
divided by `K-1 = 2`. Brier is the unscaled sum of three squared class errors.
Log loss uses the natural logarithm. Model top-pick accuracy counts a case when the
observed class has the unique highest model probability. The uniform baseline uses
`1/3` for every class and an expected top-pick accuracy of `1/3`.
"""


def _readme(
    retrieved_at: str,
    source_sha256: str,
    *,
    completed_fact_count: int,
    stage_counts: Mapping[str, int],
) -> str:
    return f"""# FIFA World Cup 2026 Live Snapshot

This reproducible example uses the [OpenFootball World Cup 2026 JSON]({OPENFOOTBALL_SOURCE_URL})
snapshot pinned to upstream commit `{OPENFOOTBALL_SOURCE_COMMIT}`, retrieved at `{retrieved_at}`
with source SHA-256 `{source_sha256}`. The imported match facts remain available under the source
repository's CC0 1.0 license. It retains all 48 teams and {completed_fact_count} completed match
facts:

- `72/72` group-stage matches
- `16/16` Round of 32 matches
- `8/8` Round of 16 matches
- `{stage_counts["quarter-finals"]}/4` quarter-finals
- `{stage_counts["semi-finals"]}/2` semi-finals
- `{stage_counts["final"]}/1` final

France is the default focus team and is already locked into the semi-finals.

Run offline after installing the package:

```bash
tournament-forecast simulate --config tournament.json --iterations 10000
tournament-forecast simulate --config tournament.json --focus-team spain --iterations 10000
tournament-forecast backtest --input backtest.json --output backtest-report.json --min-resolved 72
```

The third-place match is omitted because the generic bracket contract has no loser
entrant. Runtime forecast output directories are intentionally not checked in. The
repository MIT license does not relicense the CC0 match facts.
"""


def _data_sources(
    retrieved_at: str,
    source_sha256: str,
    rating_hash: str,
    *,
    completed_fact_count: int,
    stage_counts: Mapping[str, int],
) -> str:
    return f"""# Data Sources

## Results and bracket

- Source: OpenFootball `worldcup.json` World Cup 2026 snapshot
- Source commit: `{OPENFOOTBALL_SOURCE_COMMIT}`
- Exact source URL: {OPENFOOTBALL_SOURCE_URL}
- Retrieved at: `{retrieved_at}`
- Source SHA-256: `{source_sha256}`
- Source rows: `104`; completed rows at retrieval: `{completed_fact_count}`
- Source license: `{OPENFOOTBALL_LICENSE}`
- Exact license URL: {OPENFOOTBALL_LICENSE_URL}
- License scope: the repository license expressly addresses extraction, dissemination,
  reuse of data, and database rights.

At retrieval, `{stage_counts["quarter-finals"]}/4` quarter-finals,
`{stage_counts["semi-finals"]}/2` semi-finals, and
`{stage_counts["final"]}/1` final were complete.

## Transformation

The deterministic builder validates all 104 rows and the chronological completion
frontier, maps OpenFootball labels to project team and stage IDs, converts each explicit
`UTC+/-H` kickoff offset to UTC, and retains only participants, stages, kickoff times,
and final match facts. For knockout matches, `et` is the final score when present;
`p` selects the winner but does not replace the tied football score. Goal events,
half-time scores, grounds, and the third-place tie are omitted. Existing numeric
knockout IDs are project-owned stable topology IDs, not source or provider IDs.

## Redistribution and license boundary

The normalized OpenFootball-derived match facts in `tournament.json` and
`backtest.json` retain CC0 1.0 status. The repository MIT license covers only
project-authored code, schemas, documentation, topology, transformations, synthetic
data, and ratings; it does not relicense the CC0 facts. The source and license links
remain recorded for reproducibility even though CC0 does not require attribution.

## Ratings

- Source: project-authored `team_ratings` seed frozen in git commit `{RATING_COMMIT}`
- Exact git commit timestamp: `{RATING_CAPTURED_AT}`
- Canonical ratings object SHA-256: `{rating_hash}`

The ratings are leakage-free for the 72 group outcomes because they were frozen
before those matches. They are not an official rating source and do not prove
universal model calibration.

## Known limitations

- OpenFootball is a community-maintained dataset, not an official live feed.
- The source does not include a trusted result-finalization timestamp. The builder
  requires `retrieved_at` to be after `kickoff_at`, but that cannot establish when a
  score first became final.
- The generic bracket cannot represent third-place loser entrants, so match 103 is
  verified against source topology but omitted from the distributable tournament.
- Team aliases are explicit and fail closed when source labels drift.

## Update and verification procedure

Use an ignored local source capture. For the checked-in frontier:

```bash
python scripts/build_world_cup_2026_example.py \\
  --source /private/tmp/openfootball-worldcup-2026.json \\
  --retrieved-at {retrieved_at} \\
  --expected-source-sha256 {source_sha256} \\
  --expected-completed-facts {completed_fact_count} \\
  --output-dir examples/world-cup-2026-live

python scripts/build_world_cup_2026_example.py \\
  --source /private/tmp/openfootball-worldcup-2026.json \\
  --retrieved-at {retrieved_at} \\
  --expected-source-sha256 {source_sha256} \\
  --expected-completed-facts {completed_fact_count} \\
  --output-dir examples/world-cup-2026-live \\
  --verify
```

For a future refresh, `--fetch` downloads the exact source URL above. Review the new
hash and frontier before replacing the checked-in artifacts. The updater rejects
unknown teams or stages, malformed offsets and scores, duplicate match numbers,
invalid extra-time or penalty outcomes, topology drift, and completion gaps.
"""


def _stage_counts(tournament: Mapping[str, object]) -> dict[str, int]:
    completed_matches = _mapping_sequence(
        tournament.get("completed_matches"),
        "example tournament completed_matches",
    )
    return {
        stage_id: sum(match.get("stage_id") == stage_id for match in completed_matches)
        for stage_id in _CONFIG_STAGE_ORDER
    }


def write_example(
    output_dir: Path,
    tournament: Mapping[str, object],
    backtest: Mapping[str, object],
    report: Mapping[str, object],
    *,
    retrieved_at: str,
    source_sha256: str,
) -> None:
    stage_counts = _stage_counts(tournament)
    completed_fact_count = sum(stage_counts.values())
    atomic_write_json(output_dir / "tournament.json", tournament)
    atomic_write_json(output_dir / "backtest.json", backtest)
    atomic_write_json(output_dir / "backtest-report.json", report)
    atomic_write_text(output_dir / "backtest-report.md", _report_markdown(report))
    atomic_write_text(
        output_dir / "README.md",
        _readme(
            retrieved_at,
            source_sha256,
            completed_fact_count=completed_fact_count,
            stage_counts=stage_counts,
        ),
    )
    atomic_write_text(
        output_dir / "DATA_SOURCES.md",
        _data_sources(
            retrieved_at,
            source_sha256,
            str(report["ratings_sha256"]),
            completed_fact_count=completed_fact_count,
            stage_counts=stage_counts,
        ),
    )


def _load_json_document(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TournamentValidationError(f"cannot read checked artifact {path}") from error
    if not isinstance(value, Mapping):
        raise TournamentValidationError(f"checked artifact {path} must be an object")
    return value


def verify_example(
    output_dir: Path,
    fixture: NormalizedFixture,
    tournament: Mapping[str, object],
    backtest: Mapping[str, object],
    report: Mapping[str, object],
    *,
    retrieved_at: str,
    source_sha256: str,
) -> Reconciliation:
    expected_json = {
        "tournament.json": tournament,
        "backtest.json": backtest,
        "backtest-report.json": report,
    }
    checked_json: dict[str, Mapping[str, object]] = {}
    for filename, expected_document in expected_json.items():
        actual_document = _load_json_document(output_dir / filename)
        if actual_document != expected_document:
            raise TournamentValidationError(
                f"checked artifact {filename} is not reproducible from OpenFootball"
            )
        checked_json[filename] = actual_document
    stage_counts = _stage_counts(tournament)
    completed_fact_count = sum(stage_counts.values())
    expected_text = {
        "backtest-report.md": _report_markdown(report),
        "README.md": _readme(
            retrieved_at,
            source_sha256,
            completed_fact_count=completed_fact_count,
            stage_counts=stage_counts,
        ),
        "DATA_SOURCES.md": _data_sources(
            retrieved_at,
            source_sha256,
            str(report["ratings_sha256"]),
            completed_fact_count=completed_fact_count,
            stage_counts=stage_counts,
        ),
    }
    for filename, expected_content in expected_text.items():
        try:
            actual_content = (output_dir / filename).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise TournamentValidationError(f"cannot read checked artifact {filename}") from error
        if actual_content != expected_content:
            raise TournamentValidationError(
                f"checked artifact {filename} is not reproducible from OpenFootball"
            )
    return reconcile_documents(
        fixture,
        checked_json["tournament.json"],
        checked_json["backtest.json"],
    )


def _reject_duplicate_json_keys(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise TournamentValidationError(f"OpenFootball source repeats JSON key {key!r}")
        value[key] = item
    return value


def _decode_source(raw: bytes) -> tuple[object, str]:
    source_hash = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise TournamentValidationError("OpenFootball source must be valid UTF-8 JSON") from error
    return payload, source_hash


def _fetch_source() -> bytes:
    request = urllib.request.Request(
        OPENFOOTBALL_SOURCE_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "tournament-forecaster/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    if not isinstance(raw, bytes):
        raise TournamentValidationError("OpenFootball response body must be bytes")
    return raw


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", "--fixture", dest="source_path", type=Path)
    source.add_argument("--fetch", action="store_true")
    parser.add_argument("--retrieved-at")
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--expected-completed-facts", type=int)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "examples" / "world-cup-2026-live",
    )
    arguments = parser.parse_args(argv)

    if arguments.source_path is not None:
        try:
            raw_source = arguments.source_path.read_bytes()
        except OSError as error:
            parser.error(f"cannot read --source: {error}")
        if arguments.retrieved_at is None:
            parser.error("--source requires --retrieved-at")
    else:
        raw_source = _fetch_source()
    retrieved_at = arguments.retrieved_at or _utc_text(datetime.now(UTC))
    _timestamp(retrieved_at, "retrieved_at")
    payload, source_hash = _decode_source(raw_source)
    if (
        arguments.expected_source_sha256 is not None
        and source_hash != arguments.expected_source_sha256
    ):
        raise TournamentValidationError(
            "OpenFootball source SHA-256 mismatch: "
            f"expected {arguments.expected_source_sha256}, got {source_hash}"
        )

    fixture = normalize_openfootball_fixture(
        payload,
        retrieved_at=retrieved_at,
        require_full_tournament=True,
    )
    if (
        arguments.expected_completed_facts is not None
        and len(fixture.completed) != arguments.expected_completed_facts
    ):
        raise TournamentValidationError(
            "OpenFootball completed frontier mismatch: "
            f"expected {arguments.expected_completed_facts}, got {len(fixture.completed)}"
        )
    tournament, backtest, report = _build_documents_from_fixture(
        fixture,
        retrieved_at=retrieved_at,
        source_sha256=source_hash,
    )
    if not arguments.verify:
        write_example(
            arguments.output_dir,
            tournament,
            backtest,
            report,
            retrieved_at=retrieved_at,
            source_sha256=source_hash,
        )
    reconciliation = verify_example(
        arguments.output_dir,
        fixture,
        tournament,
        backtest,
        report,
        retrieved_at=retrieved_at,
        source_sha256=source_hash,
    )
    print(
        json.dumps(
            {
                "mode": "verify" if arguments.verify else "update",
                "output_dir": str(arguments.output_dir),
                "retrieved_at": retrieved_at,
                "source_sha256": source_hash,
                **reconciliation.to_dict(),
                "backtest_status": report["status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

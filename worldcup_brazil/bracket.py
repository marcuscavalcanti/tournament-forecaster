from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any


PHASE_SEQUENCE = ("round_of_32", "round_of_16", "quarter_finals", "semi_finals", "final")
PHASE_LABELS = {
    "round_of_32": "16 avos",
    "round_of_16": "Oitavas",
    "quarter_finals": "Quartas",
    "semi_finals": "Semifinal",
    "final": "Final",
}


def _normalize(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _load_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def hydrate_canonical_configs(config: dict[str, Any], *, base_dir: Path) -> None:
    if "groups_config" not in config:
        groups_path = Path(str(config.get("groups_config_path", "groups.config.json")))
        if not groups_path.is_absolute():
            groups_path = base_dir / groups_path
        groups_config = _load_json_if_present(groups_path)
        if groups_config is not None:
            config["groups_config"] = groups_config
            config["_groups_config_path"] = str(groups_path)

    if "bracket_config" not in config:
        bracket_path = Path(str(config.get("bracket_config_path", "bracket.config.json")))
        if not bracket_path.is_absolute():
            bracket_path = base_dir / bracket_path
        bracket_config = _load_json_if_present(bracket_path)
        if bracket_config is not None:
            config["bracket_config"] = bracket_config
            config["_bracket_config_path"] = str(bracket_path)


def _groups(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    groups_config = config.get("groups_config")
    if not isinstance(groups_config, dict):
        return {}
    groups = groups_config.get("groups")
    return groups if isinstance(groups, dict) else {}


def _team_names(config: dict[str, Any], group: str) -> list[str]:
    teams = _groups(config).get(group.upper(), [])
    names: list[str] = []
    for team in teams:
        if not isinstance(team, dict):
            continue
        name = str(team.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _bracket_matches(config: dict[str, Any], phase_key: str) -> list[dict[str, Any]]:
    bracket = config.get("bracket_config")
    if not isinstance(bracket, dict):
        return []
    matches = bracket.get(phase_key)
    return matches if isinstance(matches, list) else []


def _all_matches(config: dict[str, Any]) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for phase_key in PHASE_SEQUENCE:
        for match in _bracket_matches(config, phase_key):
            try:
                match_id = int(match["match_id"])
            except (KeyError, TypeError, ValueError):
                continue
            indexed[match_id] = {**match, "_phase_key": phase_key}
    return indexed


def _slot_kind(slot: str) -> dict[str, Any]:
    label = str(slot or "").strip().upper().replace(" ", "")
    winner = re.fullmatch(r"W(\d+)", label)
    if winner:
        return {"type": "winner", "match_id": int(winner.group(1)), "label": label}
    loser = re.fullmatch(r"L(\d+)", label)
    if loser:
        return {"type": "loser", "match_id": int(loser.group(1)), "label": label}
    rank = re.fullmatch(r"([12])([A-L])", label)
    if rank:
        return {"type": "group_rank", "rank": int(rank.group(1)), "group": rank.group(2), "label": label}
    third = re.fullmatch(r"3([A-L/]+)", label)
    if third:
        groups = [group for group in third.group(1).replace("/", "") if group in "ABCDEFGHIJKL"]
        return {"type": "best_third", "rank": 3, "groups": groups, "label": label}
    return {"type": "unknown", "label": label}


def _slot_groups(slot: str, matches_by_id: dict[int, dict[str, Any]]) -> list[str]:
    kind = _slot_kind(slot)
    if kind["type"] == "group_rank":
        return [kind["group"]]
    if kind["type"] == "best_third":
        return list(kind["groups"])
    if kind["type"] == "winner":
        match = matches_by_id.get(kind["match_id"])
        if not match:
            return []
        return _groups_for_slots(match.get("slots", []), matches_by_id)
    return []


def _groups_for_slots(slots: list[str], matches_by_id: dict[int, dict[str, Any]]) -> list[str]:
    groups: list[str] = []
    for slot in slots:
        for group in _slot_groups(slot, matches_by_id):
            if group not in groups:
                groups.append(group)
    return groups


def _slot_opponents(
    config: dict[str, Any],
    slot: str,
    matches_by_id: dict[int, dict[str, Any]],
    *,
    exclude_teams: set[str],
) -> list[str]:
    kind = _slot_kind(slot)
    candidates: list[str] = []
    if kind["type"] == "group_rank":
        candidates.extend(_team_names(config, kind["group"]))
    elif kind["type"] == "best_third":
        for group in kind["groups"]:
            candidates.extend(_team_names(config, group))
    elif kind["type"] == "winner":
        match = matches_by_id.get(kind["match_id"])
        if match:
            for nested_slot in match.get("slots", []):
                candidates.extend(
                    _slot_opponents(config, nested_slot, matches_by_id, exclude_teams=exclude_teams)
                )

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _normalize(candidate)
        if not candidate or key in exclude_teams or key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _slot_matches_position(slot: str, *, group: str, position: int) -> bool:
    kind = _slot_kind(slot)
    if kind["type"] == "group_rank":
        return kind["group"] == group.upper() and kind["rank"] == position
    if kind["type"] == "best_third":
        return position == 3 and group.upper() in kind["groups"]
    return False


def _find_round_of_32_match(config: dict[str, Any], *, group: str, position: int) -> tuple[dict[str, Any], str]:
    matches: list[tuple[dict[str, Any], str]] = []
    for match in _bracket_matches(config, "round_of_32"):
        for slot in match.get("slots", []):
            if _slot_matches_position(str(slot), group=group, position=position):
                matches.append((match, str(slot).strip().upper().replace(" ", "")))
    if not matches:
        raise ValueError(f"No round-of-32 bracket slot found for {position}{group.upper()}")
    if len(matches) > 1:
        ids = ", ".join(str(match.get("match_id")) for match, _ in matches)
        raise ValueError(f"Ambiguous round-of-32 bracket slots for {position}{group.upper()}: {ids}")
    return matches[0]


def _find_next_match(
    config: dict[str, Any],
    *,
    current_match_id: int,
    after_phase_key: str,
) -> tuple[dict[str, Any], str] | None:
    current_index = PHASE_SEQUENCE.index(after_phase_key)
    expected_slot = f"W{current_match_id}"
    for phase_key in PHASE_SEQUENCE[current_index + 1 :]:
        for match in _bracket_matches(config, phase_key):
            slots = [str(slot).strip().upper().replace(" ", "") for slot in match.get("slots", [])]
            if expected_slot in slots:
                return {**match, "_phase_key": phase_key}, expected_slot
    return None


def brazil_bracket_path(
    config: dict[str, Any],
    *,
    brazil_group: str | None = None,
    brazil_group_position: int | None = None,
) -> list[dict[str, Any]]:
    if not config.get("bracket_config") or not config.get("groups_config"):
        return []

    group = str(brazil_group or config.get("brazil_group", "C")).strip().upper() or "C"
    position = int(brazil_group_position or config.get("brazil_expected_group_position", 1))
    matches_by_id = _all_matches(config)
    brazil_key = _normalize(config.get("brazil_team_name", "Brasil"))
    path: list[dict[str, Any]] = []

    match, brazil_slot = _find_round_of_32_match(config, group=group, position=position)
    match = {**match, "_phase_key": "round_of_32"}

    while True:
        phase_key = str(match["_phase_key"])
        match_id = int(match["match_id"])
        slots = [str(slot).strip().upper().replace(" ", "") for slot in match.get("slots", [])]
        opponent_slots = [slot for slot in slots if slot != brazil_slot]
        allowed_groups = _groups_for_slots(opponent_slots, matches_by_id)
        allowed_opponents: list[str] = []
        for slot in opponent_slots:
            for opponent in _slot_opponents(config, slot, matches_by_id, exclude_teams={brazil_key}):
                if opponent not in allowed_opponents:
                    allowed_opponents.append(opponent)

        path.append(
            {
                "phase": PHASE_LABELS[phase_key],
                "phase_key": phase_key,
                "match_id": match_id,
                "date": match.get("date"),
                "venue": match.get("venue"),
                "brazil_slot": brazil_slot,
                "opponent_slots": opponent_slots,
                "allowed_opponent_groups": allowed_groups,
                "allowed_opponents": allowed_opponents,
            }
        )

        next_match = _find_next_match(config, current_match_id=match_id, after_phase_key=phase_key)
        if next_match is None:
            break
        match, brazil_slot = next_match

    return path


def _placeholder_opponent(opponent: Any) -> bool:
    normalized = _normalize(opponent)
    return not normalized or "definir" in normalized or "adversario" in normalized


def annotate_knockout_matches_with_bracket(
    config: dict[str, Any],
    knockout_matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not config.get("enforce_bracket_constraints", True):
        return [dict(match) for match in knockout_matches]
    path = brazil_bracket_path(config)
    by_phase = {entry["phase"]: entry for entry in path}
    annotated: list[dict[str, Any]] = []
    for match in knockout_matches:
        item = dict(match)
        phase = str(item.get("phase", "")).strip()
        bracket_entry = by_phase.get(phase)
        if bracket_entry:
            item["bracket_match_id"] = bracket_entry["match_id"]
            item["bracket_brazil_slot"] = bracket_entry["brazil_slot"]
            item["bracket_opponent_slots"] = bracket_entry["opponent_slots"]
            item["allowed_opponent_groups"] = bracket_entry["allowed_opponent_groups"]
            item["allowed_opponents"] = bracket_entry["allowed_opponents"]
            if item.get("date") in (None, "", "A definir"):
                item["date"] = bracket_entry.get("date") or item.get("date")
            if item.get("venue") in (None, "", "A definir"):
                item["venue"] = bracket_entry.get("venue") or item.get("venue")
        annotated.append(item)
    return annotated


def invalid_configured_knockout_opponents(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    annotated = annotate_knockout_matches_with_bracket(config, list(config.get("knockout_matches", [])))
    for match in annotated:
        opponent = str(match.get("opponent", "")).strip()
        if _placeholder_opponent(opponent):
            continue
        allowed = [str(candidate) for candidate in match.get("allowed_opponents", []) if str(candidate)]
        if not allowed:
            continue
        allowed_norm = {_normalize(candidate) for candidate in allowed}
        if _normalize(opponent) in allowed_norm:
            continue
        phase = str(match.get("phase", "Mata-mata")).strip() or "Mata-mata"
        brazil_slot = str(match.get("bracket_brazil_slot", "")).strip()
        slots = ", ".join(str(slot) for slot in match.get("bracket_opponent_slots", []))
        candidates = ", ".join(allowed)
        errors.append(
            f"{phase}: {opponent} não é possível para Brasil {brazil_slot}; "
            f"slots oficiais: {slots}; candidatos: {candidates}."
        )
    return errors

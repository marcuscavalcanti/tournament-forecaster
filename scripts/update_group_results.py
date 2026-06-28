#!/usr/bin/env python3
"""Validate and merge completed World Cup group results into the config ledger."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import tempfile
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldcup_brazil.bracket import hydrate_canonical_configs
from worldcup_brazil.cli import _effective_config_path


FIFA_DATA_CENTRE_URL = "https://inside.fifa.com/data-centre/matches"
FIFA_CALENDAR_API_URL = "https://api.fifa.com/api/v3/calendar/matches"


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold().strip()


def _pair_key(team_a: str, team_b: str) -> tuple[str, str]:
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


def _read_json_or_csv(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        raw = payload.get("results") or payload.get("completed_group_matches") or payload.get("matches") or []
    else:
        raw = payload
    if not isinstance(raw, list):
        raise ValueError("arquivo de resultados deve conter uma lista ou uma chave results")
    return [item for item in raw if isinstance(item, dict)]


def _localized_description(values: Any) -> str:
    if isinstance(values, list):
        for item in values:
            if not isinstance(item, dict):
                continue
            description = str(item.get("description") or item.get("Description") or "").strip()
            if description:
                return description
    return str(values or "").strip()


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_dicts(child))
    return found


def _team_code_lookup(config: dict[str, Any]) -> dict[str, tuple[str, str]]:
    groups_config = config.get("groups_config") if isinstance(config.get("groups_config"), dict) else {}
    groups = groups_config.get("groups") if isinstance(groups_config.get("groups"), dict) else {}
    lookup: dict[str, tuple[str, str]] = {}
    for group, teams in groups.items():
        for team in teams if isinstance(teams, list) else []:
            if not isinstance(team, dict):
                continue
            code = str(team.get("code") or "").strip().upper()
            name = str(team.get("name") or "").strip()
            if code and name:
                lookup[code] = (str(group).strip().upper(), name)
    return lookup


def _fifa_html_text_from_url(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "worldcup2026-brazil-radar/0.1 (+https://github.com/marcuscavalcanti/worldcup2026)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _fifa_result_is_final(item: dict[str, Any]) -> bool:
    value = item.get("ResultType") if "ResultType" in item else item.get("resultType")
    if value is None:
        return True
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        return _normalize(str(value)) in {"final", "finished", "completed", "played"}


def _fixture_date_range(config: dict[str, Any]) -> tuple[str, str]:
    dates = sorted(
        str(fixture.get("date") or "")[:10]
        for fixture in config.get("group_fixtures") or []
        if isinstance(fixture, dict) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(fixture.get("date") or "")[:10])
    )
    if not dates:
        return "2026-06-11T00:00:00Z", "2026-06-27T23:59:59Z"
    end_date = date.fromisoformat(dates[-1]) + timedelta(days=1)
    return f"{dates[0]}T00:00:00Z", f"{end_date.isoformat()}T23:59:59Z"


def _fifa_calendar_api_url(base_url: str, config: dict[str, Any]) -> str:
    if "?" in base_url:
        return base_url
    start, end = _fixture_date_range(config)
    params = {
        "language": "en",
        "count": "500",
        "from": start,
        "to": end,
        "idCompetition": "17",
        "idSeason": "285023",
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"


def _fifa_json_text_from_url(url: str, config: dict[str, Any]) -> tuple[str, str]:
    effective_url = _fifa_calendar_api_url(url, config)
    request = urllib.request.Request(
        effective_url,
        headers={
            "User-Agent": "worldcup2026-brazil-radar/0.1 (+https://github.com/marcuscavalcanti/worldcup2026)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return effective_url, response.read().decode("utf-8", errors="replace")


def _fifa_records_from_html(html_text: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html_text, flags=re.DOTALL)
    if not match:
        raise ValueError("HTML da FIFA sem __NEXT_DATA__; não dá para extrair placares com segurança")
    payload = json.loads(html.unescape(match.group(1)))
    code_lookup = _team_code_lookup(config)
    records: list[dict[str, Any]] = []
    seen_match_ids: set[str] = set()
    for item in _walk_dicts(payload):
        if "idMatch" not in item or "teamACountryCode" not in item or "teamBCountryCode" not in item:
            continue
        season = _localized_description(item.get("seasonName"))
        stage = _localized_description(item.get("stageName"))
        if "2026" not in season or "World Cup" not in season:
            continue
        if _normalize(stage) not in {"first stage", "fase de grupos"}:
            continue
        if not _fifa_result_is_final(item):
            continue
        if item.get("teamAScore") is None or item.get("teamBScore") is None:
            continue
        try:
            score_a = int(item.get("teamAScore"))
            score_b = int(item.get("teamBScore"))
        except (TypeError, ValueError):
            continue
        code_a = str(item.get("teamACountryCode") or "").strip().upper()
        code_b = str(item.get("teamBCountryCode") or "").strip().upper()
        if code_a not in code_lookup or code_b not in code_lookup:
            continue
        group_a, team_a = code_lookup[code_a]
        group_b, team_b = code_lookup[code_b]
        if group_a != group_b:
            continue
        match_id = str(item.get("idMatch") or "").strip()
        if match_id and match_id in seen_match_ids:
            continue
        if match_id:
            seen_match_ids.add(match_id)
        records.append(
            {
                "group": group_a,
                "team_a": team_a,
                "score_a": score_a,
                "team_b": team_b,
                "score_b": score_b,
                "date": item.get("matchDate"),
                "source": f"{FIFA_DATA_CENTRE_URL}#{match_id}" if match_id else FIFA_DATA_CENTRE_URL,
            }
        )
    if not records:
        raise ValueError("nenhum placar final da fase de grupos da Copa 2026 encontrado no HTML da FIFA")
    return records


def _nested_score(item: dict[str, Any], side: str, score_key: str) -> int | None:
    value = item.get(score_key)
    if value is None and isinstance(item.get(side), dict):
        value = item[side].get("Score") or item[side].get("score")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nested_team_code(item: dict[str, Any], side: str) -> str:
    side_value = item.get(side)
    if not isinstance(side_value, dict):
        side_value = {}
    for key in ("Abbreviation", "IdCountry", "IdAssociation", "code", "countryCode"):
        code = str(side_value.get(key) or "").strip().upper()
        if code:
            return code
    return ""


def _fifa_records_from_api_payload(payload: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        raw_items = payload.get("Results") or payload.get("results") or payload.get("matches") or []
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    code_lookup = _team_code_lookup(config)
    records: list[dict[str, Any]] = []
    seen_match_ids: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        stage = _localized_description(item.get("StageName") or item.get("stageName") or item.get("stage"))
        if stage and _normalize(stage) not in {"first stage", "fase de grupos", "group stage"}:
            continue
        if not _fifa_result_is_final(item):
            continue
        score_a = _nested_score(item, "Home", "HomeTeamScore")
        score_b = _nested_score(item, "Away", "AwayTeamScore")
        if score_a is None or score_b is None:
            continue
        code_a = _nested_team_code(item, "Home")
        code_b = _nested_team_code(item, "Away")
        if code_a not in code_lookup or code_b not in code_lookup:
            continue
        group_a, team_a = code_lookup[code_a]
        group_b, team_b = code_lookup[code_b]
        if group_a != group_b:
            continue
        match_id = str(item.get("IdMatch") or item.get("idMatch") or item.get("id") or "").strip()
        if match_id and match_id in seen_match_ids:
            continue
        if match_id:
            seen_match_ids.add(match_id)
        raw_date = str(item.get("Date") or item.get("LocalDate") or item.get("matchDate") or item.get("utcDate") or "").strip()
        records.append(
            {
                "group": group_a,
                "team_a": team_a,
                "score_a": score_a,
                "team_b": team_b,
                "score_b": score_b,
                "date": raw_date[:10] if raw_date else None,
                "source": f"{FIFA_DATA_CENTRE_URL}#{match_id}" if match_id else FIFA_DATA_CENTRE_URL,
            }
        )
    if not records:
        raise ValueError("nenhum placar final da fase de grupos da Copa 2026 encontrado no JSON da FIFA")
    return records


def _load_records_from_args(args: argparse.Namespace, config: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    if args.results:
        return "local", str(args.results), _read_json_or_csv(args.results)
    if args.fifa_api_json:
        payload = json.loads(args.fifa_api_json.read_text(encoding="utf-8"))
        return "fifa", str(args.fifa_api_json), _fifa_records_from_api_payload(payload, config)
    if args.fifa_html:
        html_text = args.fifa_html.read_text(encoding="utf-8")
        return "fifa", str(args.fifa_html), _fifa_records_from_html(html_text, config)
    if args.from_fifa:
        effective_url, json_text = _fifa_json_text_from_url(args.fifa_url, config)
        return "fifa", effective_url, _fifa_records_from_api_payload(json.loads(json_text), config)
    raise ValueError("informe --results, --fifa-api-json, --fifa-html ou --from-fifa")


def _result_teams_and_scores(record: dict[str, Any]) -> tuple[str, int, str, int] | None:
    team_a = str(record.get("team_a") or record.get("home") or record.get("team1") or "").strip()
    team_b = str(record.get("team_b") or record.get("away") or record.get("team2") or "").strip()
    if not team_a or not team_b:
        return None
    score_a = _score_value(record, "score_a", "home_score", "goals_a", "team_a_score")
    score_b = _score_value(record, "score_b", "away_score", "goals_b", "team_b_score")
    if score_a is None or score_b is None:
        score_text = str(record.get("score") or "").strip()
        score_match = re.fullmatch(r"\s*(\d+)\s*[-xX]\s*(\d+)\s*", score_text)
        if score_match:
            score_a = int(score_match.group(1))
            score_b = int(score_match.group(2))
    if score_a is None or score_b is None:
        return None
    return team_a, score_a, team_b, score_b


def _fixture_lookup(config: dict[str, Any]) -> dict[tuple[str, tuple[str, str]], list[dict[str, Any]]]:
    lookup: dict[tuple[str, tuple[str, str]], list[dict[str, Any]]] = {}
    for fixture in config.get("group_fixtures") or []:
        if not isinstance(fixture, dict):
            continue
        group = str(fixture.get("group") or "").strip().upper()
        team_a = str(fixture.get("team_a") or "").strip()
        team_b = str(fixture.get("team_b") or "").strip()
        if not group or not team_a or not team_b:
            continue
        lookup.setdefault((group, _pair_key(team_a, team_b)), []).append(fixture)
    return lookup


def _existing_lookup(config: dict[str, Any]) -> dict[tuple[str, tuple[str, str]], dict[str, Any]]:
    existing: dict[tuple[str, tuple[str, str]], dict[str, Any]] = {}
    for result in config.get("completed_group_matches") or []:
        if not isinstance(result, dict):
            continue
        group = str(result.get("group") or "").strip().upper()
        team_a = str(result.get("team_a") or "").strip()
        team_b = str(result.get("team_b") or "").strip()
        if group and team_a and team_b:
            existing[(group, _pair_key(team_a, team_b))] = result
    return existing


def _canonical_result(record: dict[str, Any], fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    parsed = _result_teams_and_scores(record)
    if parsed is None:
        raise ValueError(f"resultado sem times/placar legíveis: {record}")
    raw_team_a, raw_score_a, raw_team_b, raw_score_b = parsed
    fixture = fixtures[0]
    fixture_team_a = str(fixture.get("team_a") or "").strip()
    fixture_team_b = str(fixture.get("team_b") or "").strip()
    if _normalize(raw_team_a) == _normalize(fixture_team_a):
        score_a, score_b = raw_score_a, raw_score_b
    elif _normalize(raw_team_b) == _normalize(fixture_team_a):
        score_a, score_b = raw_score_b, raw_score_a
    else:
        raise ValueError(f"resultado não casa com a ordem do fixture: {record}")
    canonical = {
        "group": str(fixture.get("group") or "").strip().upper(),
        "team_a": fixture_team_a,
        "score_a": score_a,
        "team_b": fixture_team_b,
        "score_b": score_b,
        "date": fixture.get("date"),
    }
    source = record.get("source") or record.get("source_url")
    if source:
        canonical["source"] = source
    return canonical


def _same_score_in_fixture_order(current: dict[str, Any], canonical: dict[str, Any], fixtures: list[dict[str, Any]]) -> bool:
    try:
        current_canonical = _canonical_result(current, fixtures)
    except Exception:
        return False
    return current_canonical["score_a"] == canonical["score_a"] and current_canonical["score_b"] == canonical["score_b"]


def _merge_results(config: dict[str, Any], records: list[dict[str, Any]], *, replace: bool = False) -> dict[str, Any]:
    fixtures = _fixture_lookup(config)
    existing = _existing_lookup(config)
    additions: list[dict[str, Any]] = []
    replacements: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    conflicts: list[str] = []
    unmatched: list[str] = []

    for record in records:
        parsed = _result_teams_and_scores(record)
        if parsed is None:
            unmatched.append(f"resultado sem times/placar legíveis: {record}")
            continue
        team_a, _, team_b, _ = parsed
        group = str(record.get("group") or "").strip().upper()
        candidates: list[tuple[tuple[str, tuple[str, str]], list[dict[str, Any]]]] = []
        if group:
            key = (group, _pair_key(team_a, team_b))
            candidates = [(key, fixtures[key])] if key in fixtures else []
        else:
            pair = _pair_key(team_a, team_b)
            candidates = [(key, value) for key, value in fixtures.items() if key[1] == pair]
        if not candidates:
            unmatched.append(f"{team_a} x {team_b} não casa com group_fixtures")
            continue
        if len(candidates) > 1:
            unmatched.append(f"{team_a} x {team_b} é ambíguo; informe group")
            continue
        key, fixture_matches = candidates[0]
        canonical = _canonical_result(record, fixture_matches)
        current = existing.get(key)
        if current:
            same_score = _same_score_in_fixture_order(current, canonical, fixture_matches)
            if same_score:
                skipped.append(canonical)
                continue
            if not replace:
                conflicts.append(
                    f"{canonical['group']}: {canonical['team_a']} x {canonical['team_b']} já existe com placar "
                    f"{current.get('score_a')}-{current.get('score_b')}; novo {canonical['score_a']}-{canonical['score_b']}"
                )
                continue
            current.update(canonical)
            replacements.append(canonical)
            continue
        additions.append(canonical)
        existing[key] = canonical

    return {
        "additions": additions,
        "replacements": replacements,
        "skipped": skipped,
        "conflicts": conflicts,
        "unmatched": unmatched,
    }


def _apply_completed_updates(raw_config: dict[str, Any], additions: list[dict[str, Any]], replacements: list[dict[str, Any]]) -> None:
    completed = list(raw_config.get("completed_group_matches") or [])
    replacement_lookup = {
        (str(item.get("group") or "").strip().upper(), _pair_key(str(item.get("team_a") or ""), str(item.get("team_b") or ""))): item
        for item in replacements
    }
    updated: list[dict[str, Any]] = []
    for item in completed:
        if not isinstance(item, dict):
            updated.append(item)
            continue
        key = (
            str(item.get("group") or "").strip().upper(),
            _pair_key(str(item.get("team_a") or ""), str(item.get("team_b") or "")),
        )
        updated.append(replacement_lookup.pop(key, item))
    updated.extend(replacement_lookup.values())
    updated.extend(additions)
    raw_config["completed_group_matches"] = updated


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        tmp_path = Path(handle.name)
    tmp_path.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and merge completed group results into World Cup config")
    parser.add_argument("--config", type=Path, default=Path("config/worldcup_brazil.json"))
    parser.add_argument("--results", type=Path)
    parser.add_argument("--from-fifa", action="store_true", help="fetch completed group results from FIFA Data Centre")
    parser.add_argument("--fifa-url", default=FIFA_CALENDAR_API_URL, help="FIFA calendar API URL")
    parser.add_argument("--fifa-api-json", type=Path, help="read a saved FIFA calendar API JSON snapshot")
    parser.add_argument("--fifa-html", type=Path, help="read a saved FIFA Data Centre HTML snapshot")
    parser.add_argument("--apply", action="store_true", help="write completed_group_matches to the effective config")
    parser.add_argument("--replace", action="store_true", help="replace existing score conflicts")
    args = parser.parse_args(argv)

    config_path = _effective_config_path(args.config)
    if not config_path.exists():
        print(f"config não encontrado: {args.config}", file=sys.stderr)
        return 2
    write_config_path = args.config if args.apply and args.config != config_path else config_path
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    hydrate_canonical_configs(config, base_dir=config_path.parent)

    try:
        source_kind, source_input, records = _load_records_from_args(args, config)
        merge = _merge_results(config, records, replace=args.replace)
    except Exception as exc:
        print(f"erro ao processar resultados: {exc}", file=sys.stderr)
        return 2

    summary = {
        "dry_run": not args.apply,
        "source_kind": source_kind,
        "requested_config": str(args.config),
        "effective_config": str(config_path),
        "write_config": str(write_config_path) if args.apply else None,
        "results_input": source_input,
        "would_add": len(merge["additions"]),
        "would_replace": len(merge["replacements"]),
        "skipped_existing": len(merge["skipped"]),
        "conflicts": merge["conflicts"],
        "unmatched": merge["unmatched"],
        "additions": merge["additions"],
    }
    if merge["conflicts"] or merge["unmatched"]:
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    if args.apply and (merge["additions"] or merge["replacements"]):
        _apply_completed_updates(raw_config, merge["additions"], merge["replacements"])
        _atomic_write_json(write_config_path, raw_config)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
